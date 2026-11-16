#!/usr/bin/env python
"""
soundtry.py — DSH-SoundTry 的本地音频理解后端。

放在插件里的原因：它只用「本插件打包的 runtime venv + 借来的 ComfyUI torch/CUDA」，
不碰系统 Python、不装第二个 torch。

子命令：
    transcribe   语音/歌词 → 带时间码的文本（faster-whisper）
    rhythm       BPM（网格回套+相位扫描）、拍点、鼓组进场、切点对位
    melody       旋律线：音域/调性/音高轮廓（pyin，可选 torchcrepe / Demucs）
    all          一次给上面三样的摘要（面向"看懂一条视频"）

约定：
    * 音频一律用 ffmpeg 解码成 16k 单声道再交给 numpy——
      刻意不走 PyAV：faster-whisper 传的 metadata_errors 会随 av 版本变而崩。
    * 机器可读结果打在最后一行：@@JSON@@{...}
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
STATE_ROOT = Path(os.environ.get("SOUNDTRY_STATE_DIR", str(PLUGIN_ROOT)))
MODELS_DIR = STATE_ROOT / "models"
DEFAULT_MODEL = os.environ.get("SOUNDTRY_MODEL", "medium")   # 1.0：默认 medium（1.4 GB），不再用 large-v3（2.9 GB）
CACHE_DIR = STATE_ROOT / "cache"
JSON_MARK = "@@JSON@@"

# demucs 的权重缓存也放插件里；huggingface 直连被拦，别在那儿等 10 秒超时
os.environ.setdefault("TORCH_HOME", str(MODELS_DIR / "torch"))
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("DEMUCS_OFFLINE", "1")

NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
MAJOR = [6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88]
MINOR = [6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17]


# ─────────────────────────── 环境 ───────────────────────────
def find_ffmpeg() -> str:
    for key in ("DSH_FFMPEG_PATH", "DSH_VIDEO_FFMPEG", "FFMPEG_PATH", "SOUNDTRY_FFMPEG"):
        p = os.environ.get(key)
        if p and os.path.isfile(p):
            return p
    p = shutil.which("ffmpeg")
    if p:
        return p
    for cand in (
        PLUGIN_ROOT / "vendor" / "ffmpeg" / "ffmpeg.exe",
        Path.home() / ".dsh/plugins/dsh-video-vision/node_modules/@ffmpeg-installer/win32-x64/ffmpeg.exe",
    ):
        if Path(cand).is_file():
            return str(cand)
    raise SystemExit("找不到 ffmpeg：设 DSH_FFMPEG_PATH，或把 ffmpeg 放进 <插件>/vendor/ffmpeg/")


def load_audio(path: str, sr: int = 16000):
    import numpy as np
    import soundfile as sf

    fd, wav = tempfile.mkstemp(suffix=".wav")
    os.close(fd)
    try:
        subprocess.run(
            [find_ffmpeg(), "-hide_banner", "-nostdin", "-loglevel", "error",
             "-i", path, "-vn", "-ac", "1", "-ar", str(sr),
             "-c:a", "pcm_s16le", "-y", wav],
            check=True, capture_output=True,
        )
        data, got = sf.read(wav, dtype="float32", always_2d=False)
        if data.size == 0:
            raise SystemExit("音轨为空（这条视频可能没有音频流）")
        if got != sr:
            raise SystemExit(f"重采样失败：得到 {got}Hz")
        if data.ndim > 1:
            data = data.mean(axis=1)
        return np.ascontiguousarray(data)
    finally:
        try:
            os.remove(wav)
        except OSError:
            pass


def has_audio(path: str) -> bool:
    ff = find_ffmpeg()
    try:
        p = subprocess.run(
            [ff, "-hide_banner", "-nostdin", "-i", path],
            capture_output=True, text=True, errors="ignore", timeout=60,
        )
    except Exception:
        return False
    return "Stream #" in p.stderr and "Audio:" in p.stderr


def has_video(path: str) -> bool:
    ff = find_ffmpeg()
    try:
        p = subprocess.run(
            [ff, "-hide_banner", "-nostdin", "-i", path],
            capture_output=True, text=True, errors="ignore", timeout=60,
        )
    except Exception:
        return False
    return "Stream #" in p.stderr and "Video:" in p.stderr


# whisper 在无人声段最常"背"出来的几句话（训练数据里的固定套路）。
# 这些句子本身完全像人话，靠长度/语速判据抓不到，只能点名。
HALLUCINATION_PATTERNS = [
    r"请?不?吝?点赞", r"订阅", r"转发", r"打赏", r"明镜与点点", r"点点栏目",
    r"字幕由.{0,8}(提供|制作)", r"感谢(观看|收看)", r"字幕组",
    r"中文字幕", r"字幕提供", r"字幕制作", r"翻译[：: ]", r"校对[：: ]",
    r"Amara\.org", r"subtitle", r"Thanks? for watching",
    r"作曲[：: ]", r"作词[：: ]", r"编曲[：: ]", r"制作人[：: ]", r"录音[：: ]",
    r"出品[：: ]", r"发行[：: ]", r"监制[：: ]", r"混音[：: ]",
]


def looks_hallucinated(text: str) -> bool:
    import re
    t = (text or "").strip()
    if not t:
        return True
    return any(re.search(p, t, flags=re.I) for p in HALLUCINATION_PATTERNS)
_stem_cache = {}


def _stems_dir(path: str):
    st = os.stat(path)
    tag = f"{st.st_size}-{int(st.st_mtime)}"
    return CACHE_DIR / "stems" / tag


def _read_stems(outdir):
    """只读缓存，不碰 torch。"""
    got = {}
    for n in ("vocals", "bass", "other", "drums"):
        p = outdir / f"{n}.flac"
        if not p.is_file():
            return None
        y, _ = _sf_read_16k(str(p))
        got[n] = y
    return got


def stems_for(path: str, use_gpu: bool = True, separator: str = None):
    """Demucs 分轨，缓存到 <插件>/cache/stems/<size>-<mtime>/。

    **进程隔离**：Demucs（torch）默认在子进程里跑，只把 flac 写到磁盘；主进程再读回来。
    原因是实测到的一类原生崩溃——torch 和 ctranslate2 同进程时，在某些机器上会在
    解释器退出阶段堆损坏（Windows 0xC0000409），而 whisper 和 demucs 恰好都要用。
    子进程隔离之后，转写进程里根本没有 torch，这类崩溃就不可能发生。

    想退回同进程（省一次 python 启动，约 2–3 秒）：设 SOUNDTRY_INPROC_SEPARATE=1。
    """
    key = (path, use_gpu)
    if key in _stem_cache:
        return _stem_cache[key]

    outdir = _stems_dir(path)
    sep = (separator or os.environ.get("SOUNDTRY_SEPARATOR") or "auto").strip().lower()

    # separator=none 表示"明确不分离"：连缓存都不该用，否则这个开关等于没写
    # （缓存里有别的后端留下的轨时，它会静默生效，和用户意图相反）。
    if sep == "none":
        _stem_cache[key] = None
        return None

    # 缓存命中：只读文件，此路径完全不 import torch
    got = _read_stems(outdir)
    if got:
        _stem_cache[key] = got
        return got

    # ① ONNX（MDX-Net）：默认后端，全程不碰 torch
    if sep in ("auto", "onnx") and mdx_model_path():
        try:
            got = _separate_onnx(path, outdir)
            if got:
                _stem_cache[key] = got
                print("# （分离后端：MDX-Net ONNX，onnxruntime，无 torch）")
                return got
        except Exception as e:
            print(f"# （MDX 分离失败：{type(e).__name__}: {e}；尝试下一个后端）")

    # ② Demucs：子进程隔离
    if sep in ("auto", "demucs") and _demucs_available():
        if os.environ.get("SOUNDTRY_INPROC_SEPARATE") != "1":
            try:
                cmd = [sys.executable, str(Path(__file__).resolve()), "stems", path]
                if not use_gpu:
                    cmd.append("--no-gpu")
                r = subprocess.run(cmd, capture_output=True, timeout=1800)
                if r.returncode == 0:
                    got = _read_stems(outdir)
                    if got:
                        _stem_cache[key] = got
                        print("# （分离后端：Demucs，子进程）")
                        return got
                else:
                    # 子进程输出按 UTF-8 解、坏字节替换掉：Windows 上管道默认走 GBK，
                    # 中文日志会直接抛 UnicodeDecodeError，把"分离成功"误判成失败。
                    raw = (r.stderr or b"") + (r.stdout or b"")
                    tail = raw.decode("utf-8", "replace").strip().splitlines()[-3:]
                    print("# （子进程分离失败，改在本进程内做：" + " | ".join(tail) + "）")
            except Exception as e:
                print(f"# （子进程分离异常，改在本进程内做：{type(e).__name__}）")
        got = _stems_inproc(path, outdir, key, use_gpu)
        if got:
            return got

    # ③ HPSS：零依赖兜底（质量差，只保证不空手）
    if sep in ("auto", "hpss"):
        got = _stems_hpss(path, outdir)
        if got:
            _stem_cache[key] = got
            print("# （分离后端：HPSS，仅谐波/打击分离 —— 质量有限，和弦与旋律会被污染）")
            return got

    _stem_cache[key] = None
    return None


def mdx_model_path():
    """MDX-Net 权重在哪（deps 会下到 models/uvr/）。"""
    for name in ("Kim_Vocal_2.onnx", "UVR-MDX-NET-Voc_FT.onnx"):
        p = MODELS_DIR / "uvr" / name
        if p.is_file():
            return p
    return None


def _demucs_available():
    try:
        import importlib.util
        return importlib.util.find_spec("demucs") is not None
    except Exception:
        return False


def _separate_onnx(path, outdir, n_fft=7680, hop=1024, sr=44100, dim_t=256):
    """MDX-Net（UVR）分离，纯 onnxruntime —— 不用 torch。

    参数不是猜的：dim_f/dim_t 来自模型输入形状；n_fft=7680 是用已知歌词实测选出来的
    （7680 → 逐行 49/58 = 85%，6144 → 42/58 = 72%）。
    做法是整轨 STFT + 每 256 帧一块过模型，避免 UVR 那套 chunk 边界补偿。
    """
    import librosa
    import numpy as np
    import onnxruntime as ort

    model = mdx_model_path()
    if not model:
        raise RuntimeError("没有 MDX 权重（models/uvr/*.onnx）")

    so = ort.SessionOptions()
    so.log_severity_level = 3
    so.intra_op_num_threads = max(1, (os.cpu_count() or 4) - 1)
    sess = ort.InferenceSession(str(model), so, providers=["CPUExecutionProvider"])
    dim_f = int(sess.get_inputs()[0].shape[2])

    # 解码成 44.1k 立体声
    with tempfile.TemporaryDirectory() as td:
        wav = os.path.join(td, "in.wav")
        subprocess.run(
            [find_ffmpeg(), "-hide_banner", "-nostdin", "-loglevel", "error",
             "-i", path, "-vn", "-ac", "2", "-ar", str(sr), "-c:a", "pcm_s16le", "-y", wav],
            check=True, capture_output=True,
        )
        import soundfile as sf
        y, _ = sf.read(wav, dtype="float32", always_2d=True)
    if y.size == 0:
        raise RuntimeError("解出来的音轨为空")
    if y.shape[1] == 1:
        y = np.repeat(y, 2, axis=1)
    y = np.ascontiguousarray(y[:, :2].T)          # (2, N)

    specs = [librosa.stft(y[c], n_fft=n_fft, hop_length=hop, window="hann", center=True)
             for c in range(2)]
    bins, T = specs[0].shape
    outs = [np.zeros((bins, T), dtype=np.complex64) for _ in range(2)]

    n_blocks = int(np.ceil(T / dim_t))
    for b in range(n_blocks):
        t0, t1 = b * dim_t, min((b + 1) * dim_t, T)
        w = t1 - t0
        block = np.zeros((4, dim_f, dim_t), dtype=np.float32)
        f = min(dim_f, bins)
        for c in range(2):
            s = specs[c][:, t0:t1]
            block[c * 2, :f, :w] = s[:f].real
            block[c * 2 + 1, :f, :w] = s[:f].imag
        res = sess.run(None, {"input": block[None]})[0][0]
        for c in range(2):
            outs[c][:f, t0:t1] = res[c * 2, :f, :w] + 1j * res[c * 2 + 1, :f, :w]

    voc = np.stack([librosa.istft(outs[c], hop_length=hop, window="hann",
                                  center=True, length=y.shape[1]) for c in range(2)])
    voc = voc[:, :y.shape[1]]
    inst = y - voc

    def rms(x):
        return float(np.sqrt(np.mean(np.asarray(x, dtype="float64") ** 2)))

    r_mix, r_voc = rms(y), rms(voc)
    if r_voc > 1e-9 and r_mix > 1e-9:
        voc *= (r_mix / r_voc) * 0.9     # 电平对齐（模型自带的 compensate 未知）

    outdir.mkdir(parents=True, exist_ok=True)
    got = {}
    import soundfile as sf
    for name, arr in (("vocals", voc), ("other", inst)):
        mono = arr.mean(axis=0)
        y16 = _resample_to_16k(mono, sr)
        sf.write(str(outdir / f"{name}.flac"), y16, 16000, format="FLAC")
        got[name] = y16
    # MDX 是 2-stem：鼓与贝斯没有单独分离。
    # 鼓：从**原始混音**取打击分量 —— 不能从去掉人声的伴奏取，那样鼓会被削掉大半，
    #     结构分析会把"有鼓"误判成"没鼓"。贝斯留空（和弦只用 other）。
    import librosa as _lr
    perc = _lr.effects.hpss(_resample_to_16k(y.mean(axis=0), sr), margin=(1.0, 5.0))[1]
    sf.write(str(outdir / "drums.flac"), perc.astype("float32"), 16000, format="FLAC")
    got["drums"] = perc.astype("float32")
    bass = np.zeros_like(got["other"])
    sf.write(str(outdir / "bass.flac"), bass, 16000, format="FLAC")
    got["bass"] = bass
    (outdir / "backend.txt").write_text("mdx-onnx\n", encoding="utf-8")
    return got


def _stems_hpss(path, outdir):
    """零依赖兜底：只分谐波/打击。质量明显不如 MDX/Demucs，仅用于"至少能跑"。"""
    import librosa
    import numpy as np
    import soundfile as sf

    y16 = load_audio(path)
    y = librosa.resample(y16, orig_sr=16000, target_sr=22050)
    harm, perc = librosa.effects.hpss(y, margin=(1.0, 5.0))
    harm16 = _resample_to_16k(harm, 22050)
    perc16 = _resample_to_16k(perc, 22050)
    outdir.mkdir(parents=True, exist_ok=True)
    got = {"vocals": harm16, "other": harm16,
           "drums": perc16, "bass": np.zeros_like(harm16)}
    for name, arr in got.items():
        sf.write(str(outdir / f"{name}.flac"), arr.astype("float32"), 16000, format="FLAC")
    (outdir / "backend.txt").write_text("hpss\n", encoding="utf-8")
    return got


def _stems_inproc(path, outdir, key, use_gpu):
    try:
        import torch
        from demucs.api import Separator
    except Exception:
        _stem_cache[key] = None
        return None

    device = pick_device(use_gpu, need_gb=4.0)
    try:
        import soundfile as sf
        sep = Separator(model="htdemucs", device=device, progress=False)
        # 视频文件不能直接交给 soundfile（只认音频容器），先让 ffmpeg 解成 wav。
        with tempfile.TemporaryDirectory() as td:
            src = path
            if Path(path).suffix.lower() not in (".wav", ".flac", ".ogg", ".mp3", ".m4a", ".aiff"):
                src = os.path.join(td, "in.wav")
                subprocess.run(
                    [find_ffmpeg(), "-hide_banner", "-nostdin", "-loglevel", "error",
                     "-i", path, "-vn", "-ac", "2", "-ar", "44100",
                     "-c:a", "pcm_s16le", "-y", src],
                    check=True, capture_output=True,
                )
            wav, sr = sf.read(src, dtype="float32", always_2d=True)
        if wav.size == 0:
            raise RuntimeError("解出来的音轨为空")
        tensor = torch.from_numpy(wav.T).contiguous()
        _, out = sep.separate_tensor(tensor, sr)
        outdir.mkdir(parents=True, exist_ok=True)
        got = {}
        for name, data in out.items():
            mono = data.mean(dim=0).cpu().numpy().astype("float32")
            # 存 16k 单声道，够 ASR / pyin / chroma 用，且体积小
            y16 = _resample_to_16k(mono, sr)
            sf.write(str(outdir / f"{name}.flac"), y16, 16000, format="FLAC")
            got[name] = y16
        _stem_cache[key] = got
        return got
    except Exception as e:
        print(f"# （分离失败：{type(e).__name__}: {e}；退回混合信号）")
        _stem_cache[key] = None
        return None


def cmd_stems(args):
    """只做分离、只写 flac —— 给 stems_for 的子进程用。"""
    path = args.input
    outdir = _stems_dir(path)
    outdir.mkdir(parents=True, exist_ok=True)
    got = _stems_inproc(path, outdir, (path, bool(getattr(args, "gpu", True))), bool(getattr(args, "gpu", True)))
    if got is None:
        print("# 分离失败")
        return {"ok": False, "dir": str(outdir)}
    print(f"# 分离完成：{outdir}")
    for n in got:
        print(f"  {n}: {len(got[n]) / 16000:.1f}s")
    return {"ok": True, "dir": str(outdir), "stems": list(got)}


def nvidia_vram_gb():
    """查第一块 NVIDIA 显卡的显存（GB）。查不到返回 None —— 不假装知道。"""
    try:
        r = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.total", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=10,
        )
        if r.returncode != 0:
            return None
        first = (r.stdout or "").strip().splitlines()[0].strip()
        return round(float(first) / 1024.0, 1)
    except Exception:
        return None


def pick_device(want_gpu: bool, need_gb: float = 4.0) -> str:
    """有 GPU 且显存 > need_gb 才用 cuda，否则 cpu。"""
    if not want_gpu:
        return "cpu"
    vram = nvidia_vram_gb()
    if vram is None:
        return "cpu"
    if vram <= need_gb:
        print(f"# （显存 {vram} GB ≤ {need_gb} GB，改用 CPU）")
        return "cpu"
    try:
        import torch
        return "cuda" if torch.cuda.is_available() else "cpu"
    except Exception:
        return "cpu"


def _resample_to_16k(y, sr):
    import numpy as np
    if sr == 16000:
        return np.ascontiguousarray(y, dtype="float32")
    try:
        import resampy
        return resampy.resample(y.astype("float32"), sr, 16000).astype("float32")
    except Exception:
        import librosa
        return librosa.resample(y.astype("float32"), orig_sr=sr, target_sr=16000)


def _sf_read_16k(path):
    import soundfile as sf
    y, sr = sf.read(path, dtype="float32", always_2d=False)
    if y.ndim > 1:
        y = y.mean(axis=1)
    return y, sr


def vocal_energy(vocals, start, end, hop_ms=50):
    """某段时间里人声轨的短时能量中位数 —— 用来判"这段到底有人在唱吗"。"""
    import numpy as np
    a = max(0, int(start * 16000))
    b = min(len(vocals), int(end * 16000))
    if b <= a:
        return 0.0
    seg = vocals[a:b]
    if seg.size == 0:
        return 0.0
    # 段比一个 50ms 窗口还短时（歌尾、句尾），要退化成整段 RMS，
    # 否则 reshape(1, 800) 会因为只有 427 个采样而抛 ValueError。
    win = min(int(0.05 * 16000) or 1, seg.size)
    n = max(1, seg.size // win)
    frames = seg[: n * win].reshape(n, win)
    rms = np.sqrt((frames.astype("float64") ** 2).mean(axis=1))
    return float(np.median(rms))


# ─────────────────────── 和弦 ───────────────────────
CHORD_TEMPLATES = {
    "": [0, 4, 7], "m": [0, 3, 7], "dim": [0, 3, 6], "aug": [0, 4, 8],
    "sus2": [0, 2, 7], "sus4": [0, 5, 7],
    "maj7": [0, 4, 7, 11], "m7": [0, 3, 7, 10], "7": [0, 4, 7, 10],
    "6": [0, 4, 7, 9], "m6": [0, 3, 7, 9], "m7b5": [0, 3, 6, 10],
}


def chord_candidates():
    out = []
    for root in range(12):
        for quality, tones in CHORD_TEMPLATES.items():
            out.append((f"{NAMES[root]}{quality}", [(root + t) % 12 for t in tones]))
    return out


def detect_chords(vocals, harmonic_mix, sr=16000, hop=512, frame_s=0.25):
    """chroma + 模板 + Viterbi。

    用"去掉人声后的谐波混合"（bass+other）算 chroma —— 人声会往和弦里塞非和弦音，
    鼓会污染低频，两样都不该参与和声判断。

    两个关键处理，缺一个结果就没法看：
      1. 先把 chroma 抽稀到 0.25 秒一帧。逐帧判定会让和弦每 1 秒跳一次
         （252 秒能切出 200 多段），真实流行歌是每小节或半小节换一次。
      2. Viterbi 的换和弦代价要够大，并且偏好三和弦 —— 否则会在大三和弦与
         大七和弦之间反复横跳（C#maj7 / C#m7 抖动是同一个根音）。
    """
    import librosa
    import numpy as np

    y = librosa.resample(harmonic_mix, orig_sr=sr, target_sr=22050)
    chroma = librosa.feature.chroma_cqt(y=y, sr=22050, hop_length=hop)
    chroma = chroma / (chroma.sum(axis=0, keepdims=True) + 1e-9)
    frame_dur = hop / 22050.0
    step = max(1, int(round(frame_s / frame_dur)))
    if step > 1:
        n = chroma.shape[1] // step
        chroma = chroma[:, : n * step].reshape(12, n, step).mean(axis=2)
    times = np.arange(chroma.shape[1]) * step * frame_dur

    cands = chord_candidates()
    labels = [c[0] for c in cands]
    scores = np.zeros((len(cands), chroma.shape[1]), dtype="float32")
    for i, (_, tones) in enumerate(cands):
        inside = chroma[tones].sum(axis=0)
        # 三和弦优先：每多一个音就扣一点，避免 7 和弦乱跳
        bonus = 0.03 * (len(tones) - 3)
        scores[i] = inside - 0.5 * (1.0 - inside) - bonus

    n_c, n_t = scores.shape
    switch_pen = 0.8
    dp = np.zeros((n_c, n_t), dtype="float32")
    back = np.zeros((n_c, n_t), dtype="int16")
    dp[:, 0] = scores[:, 0]
    for t in range(1, n_t):
        prev = dp[:, t - 1]
        best_prev = int(prev.argmax())
        for c in range(n_c):
            stay = prev[c]
            move = prev[best_prev] - switch_pen
            if stay >= move:
                dp[c, t] = stay + scores[c, t]
                back[c, t] = c
            else:
                dp[c, t] = move + scores[c, t]
                back[c, t] = best_prev
    path = np.zeros(n_t, dtype="int16")
    path[-1] = int(dp[:, -1].argmax())
    for t in range(n_t - 1, 0, -1):
        path[t - 1] = back[path[t], t]

    # 合并：连续同标签 + 相邻同根音（不管品质）+ 丢掉短于 0.6 秒的
    def root_of(label):
        return label[0] if len(label) > 1 and label[1] in "#b" else label[:1] if label else "?"

    segs = []
    start_i = 0
    for t in range(1, n_t + 1):
        if t == n_t or path[t] != path[start_i]:
            a, b = float(times[start_i]), float(times[min(t, n_t - 1)])
            if b - a >= 0.6:
                segs.append({"start": round(a, 2), "end": round(b, 2), "chord": labels[path[start_i]]})
            start_i = t
    merged = []
    for s in segs:
        if merged and (merged[-1]["chord"] == s["chord"]
                       or root_of(merged[-1]["chord"]) == root_of(s["chord"])) \
                and abs(merged[-1]["end"] - s["start"]) < 0.06:
            merged[-1]["end"] = s["end"]
        else:
            merged.append(dict(s))

    from collections import Counter
    hist = Counter()
    for s in merged:
        hist[s["chord"]] += s["end"] - s["start"]
    return {"timeline": merged, "histogram": hist.most_common(6)}


def build_prompt_from_lyrics(lyrics, limit=180):
    """whisper 的 initial_prompt 约 223 token，整篇歌词塞不下，取最有代表性的前几行。"""
    out, n = [], 0
    for line in [x.strip() for x in lyrics.splitlines() if x.strip()]:
        if n + len(line) > limit:
            break
        out.append(line)
        n += len(line)
    return " ".join(out)


def lyric_line_recall(guess, lyrics, thr=0.72):
    """逐行看"这行歌词有没有被认出来"。

    比对整段的全文字错率在这里没意义：视频里还有对白、笑声、重复的副歌，
    多出来的字会把错误率撑起来。这个指标只问"每行歌词找到没有"。
    """
    import re as _re
    from difflib import SequenceMatcher

    def norm(s):
        return _re.sub(r"[\s\W_]+", "", s, flags=_re.UNICODE)

    g = norm(guess)
    hits, misses = 0, []
    for line in [x.strip() for x in lyrics.splitlines() if x.strip()]:
        k = norm(line)
        if len(k) < 4:
            continue
        best = 0.0
        for i in range(0, max(1, len(g) - len(k) + 1)):
            r = SequenceMatcher(None, k, g[i:i + len(k)], autojunk=False).ratio()
            if r > best:
                best = r
                if best > 0.95:
                    break
        if best >= thr:
            hits += 1
        else:
            misses.append({"line": line, "best": round(best, 2)})
    total = hits + len(misses)
    return {"hits": hits, "total": total, "ratio": round(hits / max(1, total), 3), "misses": misses[:8]}


def _union_length(rows):
    """覆盖时长要按区间并集算，不能把每条时长相加 ——
    词级重切会产生轻微重叠，相加会算出 206% 这种数。"""
    spans = sorted((float(r["start"]), float(r["end"])) for r in rows)
    total, a0, b0 = 0.0, None, None
    for a, b in spans:
        if a0 is None:
            a0, b0 = a, b
        elif a <= b0:
            b0 = max(b0, b)
        else:
            total += b0 - a0
            a0, b0 = a, b
    if a0 is not None:
        total += b0 - a0
    return total


# ─────────────────────────── 语音 ───────────────────────────
def pick_model(name: str) -> str:
    local = MODELS_DIR / name
    if local.is_dir() and (local / "model.bin").is_file():
        return str(local)
    return name  # 交给 faster-whisper 去缓存里找


def vocal_regions(vocals, sr=16000, win_s=0.05, rel=0.25, gap_s=0.35, min_s=0.4, pad_s=0.15):
    """在人声轨上自己找"有人在唱"的区间。

    这是从源头堵幻觉的做法：把没人声的地方整个跳过，whisper 根本没有机会编。
    混合信号上做 VAD 会把唱段判成音乐（整首歌被吃掉），但人声轨上做这件事很稳，
    因为唱歌是持续的、能量稳定的谐波，而静音/间奏那里这条轨几乎是零。

    阈值取第 90 百分位的 25%，再按 0.35 秒的小间隙合并 —— 比固定阈值抗得住
    段落之间响度差异（主歌小声、副歌大声）。
    """
    import numpy as np
    win = max(1, int(win_s * sr))
    n = len(vocals) // win
    if n < 4:
        return []
    rms = np.sqrt((vocals[: n * win].reshape(n, win).astype("float64") ** 2).mean(axis=1))
    thr = max(float(np.percentile(rms, 90)) * rel, 1e-4)
    on = rms > thr

    raw = []
    i = 0
    while i < n:
        if not on[i]:
            i += 1
            continue
        j = i
        while j < n and on[j]:
            j += 1
        raw.append([i * win_s, j * win_s])
        i = j

    merged = []
    for a, b in raw:
        if merged and a - merged[-1][1] < gap_s:
            merged[-1][1] = b
        else:
            merged.append([a, b])

    total = len(vocals) / sr
    out = []
    for a, b in merged:
        if b - a < min_s:
            continue
        out.append((max(0.0, a - pad_s), min(total, b + pad_s)))
    return out


def cmd_transcribe(args):
    import numpy as np
    from faster_whisper import WhisperModel

    t0 = time.time()
    model = WhisperModel(pick_model(args.model), device=args.device,
                         compute_type=args.compute)
    load_s = time.time() - t0

    y = load_audio(args.input)
    dur = len(y) / 16000.0

    # 分离出人声再转写：伴奏去掉之后，唱词准确率明显上升，
    # 而且人声轨还能反过来当"这段真的有人在唱吗"的证据。
    vocals = None
    if getattr(args, "separate", True):
        stems = stems_for(args.input, use_gpu=(args.device != "cpu"))
        if stems:
            vocals = stems["vocals"]
            if len(vocals) < len(y):
                vocals = np.pad(vocals, (0, len(y) - len(vocals)))

    # 窗口：只在内存里切片，时间戳补回绝对偏移
    offset = float(getattr(args, "start", 0) or 0)
    end = getattr(args, "end", None)
    if offset or end:
        a = max(0, int(offset * 16000))
        b = int(end * 16000) if end else len(y)
        y = y[a:b]
        if y.size == 0:
            raise SystemExit(f"窗口 {offset}-{end}s 里没有音频")
        dur = len(y) / 16000.0   # 覆盖率按窗口算，不按全片算
        if vocals is not None:
            vocals = vocals[a:b]

    # 转写：默认只在"有人声的区间"上跑。
    # 这是从源头堵幻觉：whisper 看不到前奏/间奏，就没有机会编演职员表或"点赞订阅"。
    mode = getattr(args, "vad_mode", "auto")
    if mode == "auto":
        mode = "vocals" if vocals is not None else "off"

    chunks = []          # [(audio, offset_seconds)]
    if mode == "vocals" and vocals is not None:
        regions = vocal_regions(vocals)
        # 光切碎不够：1-2 秒的碎片会让 whisper 失去上下文（"暧昧告诉我不能这样"
        # 会被识别成"美国是我本人最爱"）。所以把间隔小于 block_gap 的区间合并成
        # 30 秒量级的块：既跳过纯器乐段，又保住语言模型需要的上下文。
        block_gap = float(getattr(args, "block_gap", 3.5) or 3.5)
        blocks = []
        for a, b in regions:
            if blocks and a - blocks[-1][1] < block_gap:
                blocks[-1][1] = b
            else:
                blocks.append([a, b])
        regions = [tuple(x) for x in blocks]
        if regions:
            for a, b in regions:
                chunks.append((vocals[int(a * 16000): int(b * 16000)], a))
            print(f"  （人声切分：整段 {dur:.1f}s → {len(regions)} 块、共 "
                  f"{sum(b-a for a,b in regions):.1f}s，其余跳过）")
    if not chunks:
        chunks = [(vocals if vocals is not None else y, 0.0)]

    # 门控被绕过时必须吼一声：三条路径（未分离 / separator=none / vad-mode=off）
    # 都会让 whisper 直接看到器乐段，于是它开始编"Zither Harp""点赞订阅"这类东西。
    # 实测同一首歌：默认起于 10.10s 且干净；--no-separate 会在 8.68s 编出 "Zither Harp"。
    gate_on = bool(mode == "vocals" and vocals is not None and chunks
                   and any(c[1] > 0 for c in chunks))
    gate_reason = []
    if not getattr(args, "separate", True):
        gate_reason.append("separate=false")
    if (os.environ.get("SOUNDTRY_SEPARATOR") or "auto").strip().lower() == "none":
        gate_reason.append("separator=none")
    if getattr(args, "vad_mode", "auto") == "off":
        gate_reason.append("vad_mode=off")
    if not gate_on:
        reason = " / ".join(gate_reason) or "没有可用的人声轨"
        print(f"# ⚠ 未启用人声门控（{reason}）：whisper 会看到器乐段，可能在无人处编词。"
              f"要防幻觉请用默认的 separate=true + vad_mode=auto。")

    # 有真歌词时：自动生成 prompt（whisper 有它，唱词准确率天差地别），结尾再报覆盖率
    lyrics_text = None
    if getattr(args, "lyrics", None):
        try:
            lyrics_text = Path(args.lyrics).read_text(encoding="utf-8")
        except OSError as e:
            print(f"  （读歌词失败：{e}）")
        if lyrics_text and not args.prompt:
            args.prompt = build_prompt_from_lyrics(lyrics_text)
            print(f"  （已用歌词前 {len(args.prompt)} 字作为 prompt）")

    t1 = time.time()
    raw_segments = []
    info = None
    for audio_chunk, base in chunks:
        segs, info = model.transcribe(
            audio_chunk,
            language=None if args.lang == "auto" else args.lang,
            beam_size=args.beam,
            vad_filter=(mode == "mix"),           # 混合信号上的 VAD 只在显式要求时用
            word_timestamps=True,
            condition_on_previous_text=False,
            initial_prompt=args.prompt,
        )
        for s in segs:
            words = [(float(w.start) + base, float(w.end) + base, w.word) for w in (s.words or [])]
            raw_segments.append((float(s.start) + base, float(s.end) + base, (s.text or "").strip(), words))

    # 提示词会让 whisper 变自信，把整段主歌并成一条 20 多秒的字幕 —— SRT 不能这么用。
    # 用词级时间戳把它按标点/长度重新断开：每条不超过 max_line 秒、max_chars 字。
    max_line = float(getattr(args, "max_line", 8.0) or 8.0)
    max_chars = int(getattr(args, "max_chars", 26) or 26)
    split_segments = []
    for a, b, text, words in raw_segments:
        if b - a <= max_line or not words or len(words) < 3:
            split_segments.append((a, b, text))
            continue
        cur, cur_txt, cur_start = [], "", None
        for w0, w1, wt in words:
            over = bool(cur) and ((len(cur_txt) + len(wt) > max_chars) or (w1 - cur_start > max_line))
            if over:
                # 只在"像句读"的位置断：上一个 token 收在标点，或下一个 token 以空格开头
                # （whisper 用空格标歌词的短语边界）。否则宁可变长，也不要在词中间切开。
                at_boundary = cur_txt.strip()[-1:] in "。！？，、；：…" or wt.startswith(" ")
                if at_boundary or (w1 - cur_start > max_line * 1.6):
                    split_segments.append((round(cur_start, 2), round(cur[-1][1], 2), cur_txt.strip()))
                    cur, cur_txt, cur_start = [], "", None
            if cur_start is None:
                cur_start = w0
            cur.append((w0, w1, wt))
            cur_txt += wt
            if wt.strip()[-1:] in "。！？" and w1 - cur_start >= 2.0:
                split_segments.append((round(cur_start, 2), round(w1, 2), cur_txt.strip()))
                cur, cur_txt, cur_start = [], "", None
        if cur and cur_txt.strip():
            split_segments.append((round(cur_start, 2), round(cur[-1][1], 2), cur_txt.strip()))
    raw_segments = split_segments
    # 幻觉过滤：whisper 在纯器乐段会编出整段"作曲/编曲 ×××"或"点赞订阅"这种象话的假台词，
    # 只靠"太长且太稀疏"抓不住。两道闸：
    #   1. 已知幻觉词表（这些句子长度和语速都很正常，只能点名）；
    #   2. 人声轨的逐秒占比 —— 一段字幕里大部分秒数没有能量，就不是在唱。
    import numpy as _np
    ref = None
    if vocals is not None:
        wins = [float(_np.median([vocal_energy(vocals, t, t + 1.0)])) for t in _np.arange(0, dur, 1.0)]
        ref = float(_np.median(wins)) or 1e-6
    floor = 0.20 * ref if ref else None

    rows = []
    dropped = []
    for s_start, s_end, text in raw_segments:
        if not text:
            continue
        cue = {"start": round(s_start + offset, 2),
               "end": round(s_end + offset, 2), "text": text}
        why = None
        if looks_hallucinated(text):
            why = "pattern"
        elif vocals is not None and floor and mode != "vocals":
            # 逐秒看：这段里有多大比例真的有声音（只在没做前置切分时兜底）
            n_sec = max(1, int(round(s_end - s_start)))
            secs = [float(_np.median([vocal_energy(vocals, s_start + i, s_start + i + 1.0)]))
                    for i in range(n_sec)]
            frac = sum(1 for v in secs if v >= floor) / len(secs)
            cue["voice_frac"] = round(frac, 2)
            if frac < 0.4:
                why = "no-voice"
        if why:
            cue["why"] = why
            dropped.append(cue)
            continue
        rows.append(cue)
    decode_s = time.time() - t1

    if args.srt:
        out = Path(args.srt)
        out.parent.mkdir(parents=True, exist_ok=True)
        def fmt(t):
            ms = int(round(t * 1000)); h, ms = divmod(ms, 3600000)
            m, ms = divmod(ms, 60000); s, ms = divmod(ms, 1000)
            return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"
        with out.open("w", encoding="utf-8") as f:
            for i, r in enumerate(rows, 1):
                f.write(f"{i}\n{fmt(r['start'])} --> {fmt(r['end'])}\n{r['text']}\n\n")

    covered = _union_length(rows)
    src = "人声轨" if vocals is not None else "混合信号"
    print(f"# 语音：{dur:.1f}s · {len(rows)} 条 · 覆盖 {min(100.0, covered/dur*100):.0f}% · "
          f"{info.language}({info.language_probability:.2f}) · 源={src} · "
          f"加载 {load_s:.1f}s 解码 {decode_s:.1f}s ({dur/max(decode_s,.01):.1f}x)")
    if dropped:
        print(f"  ⚠ 丢掉 {len(dropped)} 段「没人声却识别出了字」的疑似幻觉：")
        for d in dropped[:4]:
            print(f"    [{d['start']:7.2f} → {d['end']:7.2f}] {d['text']}  (人声能量 {d.get('vocal_rms')})")
    for r in rows[:args.head]:
        print(f"[{r['start']:7.2f} → {r['end']:7.2f}] {r['text']}")
    if len(rows) > args.head:
        print(f"... 还有 {len(rows)-args.head} 条（用 srt 参数落盘看全）")
    recall = None
    if lyrics_text:
        recall = lyric_line_recall("".join(x["text"] for x in rows), lyrics_text)
        print(f"  歌词覆盖：{recall['hits']}/{recall['total']} 行 = {recall['ratio']*100:.0f}%（≥0.72 相似度）")
        for m in recall["misses"][:3]:
            print(f"    未认出：{m['line']}（最高相似 {m['best']}）")

    return {
        "ok": True,
        "duration": round(dur, 2),
        "language": info.language,
        "language_probability": round(float(info.language_probability), 3),
        "segments": len(rows),
        "dropped_hallucinations": len(dropped),
        "source": src,
        "gate": "on" if gate_on else "off",
        "gate_reason": None if gate_on else (" / ".join(gate_reason) or "no-vocal-stem"),
        "coverage": round(min(1.0, covered / max(dur, .01)), 3),
        "cues": rows if args.include_cues else rows[:args.head],
        "srt": args.srt or None,
        "device": args.device, "compute": args.compute,
        "lyric_recall": recall,
    }


# ─────────────────────────── 节奏 ───────────────────────────
def _comb(onset, sr, hop, bpm, phases=64):
    """相位扫描的对比度分数。用 (max-median)/std，避免偏袒慢速 BPM。"""
    import numpy as np
    frame_t = hop / sr
    idx = np.arange(len(onset))
    period = (60.0 / bpm) / frame_t
    scores = []
    for k in range(phases):
        pos = (idx - (k / phases) * period) / period
        grid = np.round(pos).astype(int)
        sel = (grid >= 0) & (grid < len(onset))
        if sel.sum() < 8:
            scores.append(0.0); continue
        pick = idx[sel][np.unique(grid[sel], return_index=True)[1]]
        scores.append(float(onset[pick].mean()))
    s = np.asarray(scores)
    k_best = int(s.argmax())
    return (float(s.max()) - float(np.median(s))) / (float(s.std()) + 1e-9), (k_best / phases) * period * frame_t


def cmd_rhythm(args):
    import librosa
    import numpy as np

    y16 = load_audio(args.input)
    sr, hop = 22050, 512
    y = librosa.resample(y16, orig_sr=16000, target_sr=sr)
    dur = len(y) / sr
    onset = librosa.onset.onset_strength(y=y, sr=sr, hop_length=hop)

    t_lib, _ = librosa.beat.beat_track(onset_envelope=onset, sr=sr, hop_length=hop)
    t_lib = float(np.asarray(t_lib).reshape(-1)[0])

    tg = librosa.feature.tempogram(onset_envelope=onset, sr=sr, hop_length=hop)
    bpms = librosa.tempo_frequencies(tg.shape[0], sr=sr, hop_length=hop)
    mean_tg = tg.mean(axis=1)
    cand = set()
    for i in range(1, len(mean_tg) - 1):
        if mean_tg[i] > mean_tg[i - 1] and mean_tg[i] >= mean_tg[i + 1] and 55 <= bpms[i] <= 210:
            cand.add(round(float(bpms[i]), 2))
    for base in list(cand) + [t_lib]:
        for mul in (0.5, 2.0):
            if 55 <= base * mul <= 210:
                cand.add(round(base * mul, 2))

    # 半/倍速判据：偶数拍与奇数拍的 onset 强度比。
    # 只有"真正的那一层"每一拍都有击打，比值才接近 1；取它的半速或倍速都会一强一弱。
    # 所以选 BPM 不能只看对比度，必须把这个比值乘进去——否则整曲平均会把 126 判成 83。
    def odd_even(b, ph):
        period = 60.0 / b
        t = ph
        hits = []
        while t < dur:
            f = int(round(t / (hop / sr)))
            if 0 <= f < len(onset):
                hits.append(onset[f])
            t += period
        h = np.asarray(hits)
        if len(h) < 8:
            return None
        return float(h[1::2].mean() / (h[0::2].mean() + 1e-9))

    import math as _math

    half = len(onset) // 2
    t_off = half * hop / sr
    scored = []
    for b in cand:
        s_full, ph = _comb(onset, sr, hop, b)
        c1, p1 = _comb(onset[:half], sr, hop, b)
        c2, p2 = _comb(onset[half:], sr, hop, b)
        period = 60.0 / b
        gap = ((p2 + t_off) - p1) % period
        gap = min(gap, period - gap) / period          # 0 = 前后半段相位完全接上
        r = odd_even(b, ph)
        r = 1.0 if r is None else r
        # 三个信号：前后半段都要有能量（minC）、相位要接得上、每拍能量要接近（odd_even）
        composite = min(c1, c2) * _math.exp(-2.0 * abs(r - 1.0)) * _math.exp(-1.5 * gap)
        scored.append({"bpm": b, "contrast": s_full, "half1": c1, "half2": c2,
                       "phase_gap": gap, "odd_even": r, "phase": ph, "composite": composite})
    scored.sort(key=lambda x: -x["composite"])
    best = scored[0]
    bpm, score, phase, ratio = best["bpm"], best["contrast"], best["phase"], best["odd_even"]
    # 不确定度：#1 与 #2 的综合分差不到 10% 时，这个数不能当结论
    runner_up = scored[1] if len(scored) > 1 else None
    margin = (best["composite"] - runner_up["composite"]) / best["composite"] if runner_up else 1.0
    ambiguous = margin < 0.10

    harm, perc = librosa.effects.hpss(y, margin=(1.0, 5.0))
    rms_p = librosa.feature.rms(y=perc, hop_length=hop)[0]
    seg = []
    for a, b in [(0, 30), (30, 60), (60, 110), (110, 180), (180, 240), (240, dur)]:
        m = (librosa.frames_to_time(np.arange(len(rms_p)), sr=sr, hop_length=hop) >= a) & \
            (librosa.frames_to_time(np.arange(len(rms_p)), sr=sr, hop_length=hop) < b)
        if m.any():
            seg.append({"from": a, "to": round(min(b, dur), 1), "percussive": round(float(rms_p[m].mean()), 4)})

    print(f"# 节奏：{dur:.1f}s")
    print(f"  librosa 自报 {t_lib:.2f} BPM（仅参考）")
    verdict = "（≈1，每拍都有击打）" if abs(ratio - 1) < 0.15 else "（偏离 1，可能是半/倍速层）"
    print(f"  候选第一 {bpm:.2f} BPM · 相位 {phase:.3f} · 对比度 {score:.2f} · 偶奇拍比 {ratio:.2f}{verdict}")
    if ambiguous:
        alts = " / ".join(f"{x['bpm']:.2f}" for x in scored[1:4])
        print(f"  ⚠ 不确定：与次选只差 {margin*100:.0f}%（{alts}），这个 BPM 不能当结论用")

    # 两个方法互相校：同一条片子给出两个互不相干的数，比"没算出来"更危险 ——
    # 前者会被当成确定结论引用。所以分歧必须显式报出来。
    #
    # 歧义不止 2:1。实测遇到过 3:2（112.35 vs 73.83，比值 1.522）—— 那是三连音
    # 层级，只判 half/double 会把它误报成"不可靠"。所以按一组有理数比去套。
    METRICAL = [(2, 1), (1, 2), (3, 2), (2, 3), (4, 3), (3, 4), (5, 4), (4, 5), (5, 2), (2, 5)]
    agreement = {"mine": round(bpm, 2), "librosa": round(t_lib, 2), "status": "agree"}
    if bpm > 0 and t_lib > 0:
        d = abs(t_lib - bpm) / bpm
        level = None
        for p, q in METRICAL:
            if abs(t_lib - bpm * p / q) / bpm < 0.05:
                level = (p, q)
                break
        if d <= 0.05:
            agreement["status"] = "agree"
        elif level:
            p, q = level
            agreement["status"] = "level"
            agreement["ratio"] = f"{p}:{q}"
            label = "半/倍速" if (p, q) in ((2, 1), (1, 2)) else "节拍层级"
            print(f"  · librosa 的 {t_lib:.1f} 与本地 {bpm:.1f} 是 {p}:{q} 关系（{label}）—— "
                  f"两个数都可能是对的，引用时必须说明是哪个层级")
        else:
            agreement["status"] = "conflict"
            print(f"  ⚠ BPM 不可靠：librosa {t_lib:.1f} vs 本地 {bpm:.1f}（差 {d*100:.0f}%），"
                  f"别拿这个数做结论（可能与变速/段落速度变化有关）")
        agreement["delta_pct"] = round(d * 100, 1)
    for row in scored[1:4]:
        print(f"    次选 {row['bpm']:.2f}（综合 {row['composite']:.2f}，半段 {row['half1']:.2f}/{row['half2']:.2f}，"
              f"偶奇比 {row['odd_even']:.2f}，相位差 {row['phase_gap']*100:.0f}%）")

    out = {"ok": True, "duration": round(dur, 2), "bpm": bpm, "phase": phase,
           "contrast": round(score, 3), "odd_even": round(ratio, 3) if ratio else None,
           "ambiguous": ambiguous, "margin": round(margin, 3),
           "bpm_agreement": agreement,
           "librosa_bpm": round(t_lib, 2),
           "candidates": [{"bpm": x["bpm"], "contrast": round(x["contrast"], 3),
                           "half1": round(x["half1"], 3), "half2": round(x["half2"], 3),
                           "odd_even": round(x["odd_even"], 3),
                           "phase_gap": round(x["phase_gap"], 3),
                           "composite": round(x["composite"], 3)} for x in scored[:6]],
           "percussive_by_span": seg}

    if args.video and has_video(args.video):
        # 切点数强依赖阈值，这件事必须打印出来，否则会和别的工具的数字打架。
        # 同一部片子：阈值 0.30 → 230 刀、0.40 → 130、0.50 → 76。
        # dsh-video-vision 报的 119 刀大致相当于这里的 0.42–0.45。
        period = 60.0 / bpm

        def _stats(thr):
            cs = cuts_of(args.video, thr)
            if not cs:
                return None
            o = []
            for c in cs:
                r = ((c - phase) / period) % 1.0
                o.append(min(r, 1 - r) * period)
            o = np.asarray(o)
            return {"count": len(cs), "threshold": thr,
                    "hit_40ms": round(float((o <= 0.04).mean()), 3),
                    "median_offset_ms": round(float(np.median(o) * 1000), 1)}

        hard_t = float(getattr(args, "cut_threshold", 0.45) or 0.45)
        hard = _stats(hard_t)
        loose = _stats(0.30) if hard_t > 0.31 else None
        if hard:
            print(f"  硬切 {hard['count']} 个（阈值 {hard_t:.2f}）· ≤40ms 命中 "
                  f"{hard['hit_40ms']*100:.0f}% · 中位偏移 {hard['median_offset_ms']:.0f}ms")
            out["cuts"] = hard
        if loose:
            print(f"  所有变化 {loose['count']} 个（阈值 0.30，含软过渡/溶解）· ≤40ms 命中 "
                  f"{loose['hit_40ms']*100:.0f}% · 中位偏移 {loose['median_offset_ms']:.0f}ms")
            out["cuts_loose"] = loose
        if not hard and not loose:
            print(f"  （阈值 {hard_t:.2f} 下没检出切点：可能是软过渡，或阈值太严；"
                  f"试小一点的 --cut-threshold）")
    elif args.video:
        print("  （这个文件没有视频流，跳过切点对位）")

    return out


def cuts_of(media, threshold=0.30):
    p = subprocess.run(
        [find_ffmpeg(), "-hide_banner", "-nostdin", "-i", media,
         "-filter:v", f"select='gt(scene,{threshold})',showinfo", "-f", "null", "-"],
        capture_output=True, text=True, errors="ignore",
    )
    out = []
    for line in p.stderr.splitlines():
        if "pts_time:" in line:
            try:
                out.append(float(line.split("pts_time:")[1].split()[0]))
            except (ValueError, IndexError):
                pass
    return out


# ─────────────────────────── 旋律 ───────────────────────────
def hz_note(f):
    import numpy as np
    if f is None or f <= 0:
        return "?"
    n = int(round(69 + 12 * np.log2(f / 440.0)))
    return f"{NAMES[n % 12]}{n // 12 - 1}"


def cmd_melody(args):
    import librosa
    import numpy as np

    y16 = load_audio(args.input)

    # 优先用 Demucs 拆出来的 vocal 轨：这才是"人声旋律"，
    # 否则 pyin 会一直锁在贝斯的基频上（音域会掉到 E2 一带）。
    source = "混合信号（含伴奏）"
    stems = None
    if getattr(args, "separate", True):
        stems = stems_for(args.input, use_gpu=True)
    if stems and "vocals" in stems:
        y16 = stems["vocals"]
        source = "人声轨（Demucs）"

    sr, hop = 22050, 512
    y = librosa.resample(y16, orig_sr=16000, target_sr=sr)
    if source.startswith("混合"):
        y = librosa.effects.hpss(y, margin=(1.0, 5.0))[0]

    # 跟踪器选择：默认自动 —— 有 torchcrepe 就用它（更准），没有就用 pyin（纯 librosa）。
    # 但 torchcrepe 会 import torch，而 torch 和 whisper 的 ctranslate2 同进程在某些机器上
    # 会崩（0xC0000409 堆损坏）。所以集中调用（sound_read = all）默认强制 pyin，
    # 单跑 melody 才用 torchcrepe。想改：SOUNDTRY_TRACKER=pyin|torchcrepe|auto。
    want = (os.environ.get("SOUNDTRY_TRACKER") or "auto").strip().lower()
    tracker = "pyin"
    if want != "pyin":
        try:
            import torchcrepe  # noqa: F401
            tracker = "torchcrepe"
        except Exception:
            pass
    if want == "torchcrepe" and tracker != "torchcrepe":
        print("# （要求用 torchcrepe 但导入失败，退回 pyin）")

    if tracker == "pyin":
        f0, _, _ = librosa.pyin(y, fmin=librosa.note_to_hz("E2"),
                                fmax=librosa.note_to_hz("C6"), sr=sr,
                                hop_length=hop, frame_length=2048)
    else:
        import torch
        import torchcrepe
        t = torch.from_numpy(y).unsqueeze(0)
        fmin, fmax = 82.0, 1046.0
        f0t, pd = torchcrepe.predict(t, sr, hop_length=hop, fmin=fmin, fmax=fmax,
                                     model="full", batch_size=1024,
                                     device="cuda" if torch.cuda.is_available() else "cpu",
                                     return_periodicity=True)
        f0t = torchcrepe.threshold.Silence(-60.0)(f0t, t, sr, hop_length=hop)
        f0 = f0t.squeeze(0).cpu().numpy()
        f0 = np.where(pd.squeeze(0).cpu().numpy() < 0.5, np.nan, f0)

    # 有的跟踪器会返回 0 或负数（无效帧），不清掉会让音域显示成 "?–G#5"
    f0 = np.where(f0 > 0, f0, np.nan)
    voiced = ~np.isnan(f0)
    times = librosa.times_like(f0, sr=sr, hop_length=hop)
    rng = (hz_note(np.nanmin(f0)), hz_note(np.nanmax(f0))) if voiced.any() else ("?", "?")
    notes = np.array([hz_note(f) for f in f0[voiced]]) if voiced.any() else np.array([])

    top = []
    if len(notes):
        vals, cnts = np.unique(notes, return_counts=True)
        for i in np.argsort(-cnts)[:6]:
            top.append({"note": str(vals[i]), "frames": int(cnts[i])})

    # 调性：人声轨只反映旋律音，不合适做调性；用伴奏的谐波内容更稳。
    key_src = y
    if stems and "other" in stems and "bass" in stems:
        mix = stems["other"] + stems["bass"]
        key_src = librosa.resample(mix, orig_sr=16000, target_sr=sr)
    chroma = librosa.feature.chroma_cqt(y=key_src, sr=sr, hop_length=hop).mean(axis=1)
    key, kcorr = "?", 0.0
    for i in range(12):
        for nm, prof in (("major", MAJOR), ("minor", MINOR)):
            r = float(np.corrcoef(np.roll(prof, i), chroma)[0, 1])
            if r > kcorr:
                kcorr, key = r, f"{NAMES[i]} {nm}"

    print(f"# 旋律：{Path(args.input).name} · 跟踪器 {tracker} · 源={source}")
    print(f"  有声帧 {voiced.mean()*100:.0f}% · 音域 {rng[0]}–{rng[1]} · 调性 {key}（相关 {kcorr:.2f}）")
    print("  出现最多的音：" + "  ".join(f"{t['note']}({t['frames']})" for t in top))

    windows = []
    if args.window:
        for w in args.window:
            label, a, b = w.split(":")
            a, b = float(a), float(b)
            m = (times >= a) & (times <= b) & voiced
            if not m.any():
                windows.append({"label": label, "from": a, "to": b, "notes": []})
                continue
            seg = f0[m]
            seq = [hz_note(x) for x in seg[::max(1, len(seg) // 8)]]
            rel = (12 * np.log2(seg / np.nanmedian(seg))).tolist()
            windows.append({"label": label, "from": a, "to": b, "notes": seq,
                            "relative_semitones": [round(x, 1) for x in rel[::max(1, len(rel)//10)]]})
            print(f"  {label} [{a}-{b}s] " + " → ".join(seq))

    return {"ok": True, "tracker": tracker, "voiced_ratio": round(float(voiced.mean()), 3),
            "range": {"low": rng[0], "high": rng[1]}, "key": key, "key_correlation": round(kcorr, 2),
            "top_notes": top, "windows": windows}


# ─────────────────────────── 和弦 ───────────────────────────
def cmd_chords(args):
    import librosa
    import numpy as np

    y16 = load_audio(args.input)
    stems = stems_for(args.input, use_gpu=True) if getattr(args, "separate", True) else None

    if stems and "other" in stems and "bass" in stems:
        harmonic = np.asarray(stems["other"], dtype="float32") + np.asarray(stems["bass"], dtype="float32")
        source = "伴奏（Demucs：bass+other，去掉人声与鼓）"
    else:
        y = librosa.resample(y16, orig_sr=16000, target_sr=22050)
        harmonic = librosa.effects.hpss(y, margin=(1.0, 5.0))[0]
        source = "混合信号的谐波分量（未分离，和弦会被歌声污染）"

    res = detect_chords(None, harmonic)
    tl = res["timeline"]
    dur = len(y16) / 16000.0

    print(f"# 和弦：{dur:.1f}s · {len(tl)} 段 · 源={source}")
    # 每 10 秒一行，便于扫读
    for a in range(0, int(dur) + 1, 10):
        seg = [s for s in tl if a <= s["start"] < a + 10]
        if seg:
            print(f"  {a:>3}s  " + "  ".join(f"{s['chord']}({s['start']:.1f})" for s in seg))
    print("  和弦时长占比：" + "  ".join(f"{c} {t:.0f}s" for c, t in res["histogram"]))

    return {"ok": True, "duration": round(dur, 1), "source": source,
            "segments": len(tl), "timeline": tl,
            "histogram": [{"chord": c, "seconds": round(t, 1)} for c, t in res["histogram"]]}


# ─────────────────────────── 结构 ───────────────────────────
def cmd_structure(args):
    """段落边界：0.25 秒窗、不做中值滤波。

    之前用临时脚本做这件事时用了 1 秒窗 + 3 秒中值滤波 + 「短于 4 秒并入邻居」，
    边界因此偏了 1–3 秒，叠加后最多到 5 秒。这里改成细窗、不滤波、只合并
    「短于 min_len 且标签相同」的碎片，边界按 0.25 秒精度报。
    """
    import librosa
    import numpy as np

    y = load_audio(args.input)
    dur = len(y) / 16000.0
    stems = stems_for(args.input, use_gpu=True) if getattr(args, "separate", True) else None
    zp = np.zeros_like(y)
    voc = np.asarray(stems["vocals"], dtype="float32") if stems and "vocals" in stems else zp
    dru = np.asarray(stems["drums"], dtype="float32") if stems and "drums" in stems else zp
    src = "人声轨/鼓轨（Demucs）" if stems else "混合信号（未分离，只有整体能量）"

    W = float(getattr(args, "window", 0.25) or 0.25)
    n = int(W * 16000)
    k = int(dur / W)

    def env(x):
        x = np.asarray(x, dtype="float32")
        if len(x) < k * n:
            x = np.pad(x, (0, k * n - len(x)))
        return np.sqrt((x[:k * n].reshape(k, n).astype("float64") ** 2).mean(axis=1))

    mix, vr, dr = env(y), env(voc), env(dru)
    if stems:
        vthr = max(np.percentile(vr, 10), 0.12 * np.percentile(vr, 95))
        dthr = max(np.percentile(dr, 10), 0.12 * np.percentile(dr, 95))
    else:
        vthr = dthr = float("inf")

    raws = []
    for i in range(k):
        has_v, has_d = vr[i] > vthr, dr[i] > dthr
        seg = "full" if (has_v and has_d) else "vocal" if has_v else "inst" if has_d else "quiet"
        raws.append(seg)

    runs = []
    for i, L in enumerate(raws):
        if runs and runs[-1][0] == L:
            runs[-1][2] = i + 1
        else:
            runs.append([L, i, i + 1])

    min_len = float(getattr(args, "min_len", 2.0) or 2.0)
    changed = True
    while changed and len(runs) > 1:
        changed = False
        for j, r in enumerate(runs):
            if (r[2] - r[1]) * W < min_len:
                nbr = runs[j - 1] if j > 0 else runs[j + 1]
                nbr[2] = max(nbr[2], r[2]); nbr[1] = min(nbr[1], r[1])
                runs.pop(j); changed = True
                break
        merged = []
        for r in runs:
            if merged and merged[-1][0] == r[0]:
                merged[-1][2] = r[2]
            else:
                merged.append(list(r))
        runs = merged

    chords = detect_chords(None, np.asarray(stems["bass"], dtype="float32") + np.asarray(stems["other"], dtype="float32"))["timeline"] if stems else []

    def mmss(t, tenth=False):
        m, s = int(t // 60), t - 60 * int(t // 60)
        return f"{m}:{s:04.1f}" if tenth else f"{m}:{int(s):02d}"

    LAB = {"quiet": "无人声", "inst": "器乐", "vocal": "人声", "full": "全编"}
    rows = []
    for idx, (L, a, b) in enumerate(runs):
        t0, t1 = a * W, b * W
        m = float(mix[a:b].mean())
        name = LAB[L]
        if L == "quiet":
            name = "前奏" if idx == 0 else "尾奏" if idx == len(runs) - 1 else "断口"
        elif L == "inst":
            name = "间奏"
        elif L == "full":
            name = "副歌"
        elif L == "vocal":
            name = "主歌"
        rows.append({"label": name, "start": round(t0, 2), "end": round(t1, 2),
                     "mix": round(m, 3),
                     "vocal": round(float(vr[a:b].mean()), 3),
                     "drums": round(float(dr[a:b].mean()), 3),
                     "chord_changes": int(sum(1 for c in chords if t0 <= c["start"] < t1))})

    # 标签事后修正：没有独立人声/鼓、但整体很响的段，不是"断口"。
    # 基准必须用"段平均能量"的最大值 —— 用 0.25 秒窗的瞬时最高值会被一个镲片
    # 瞬态抬高，于是 0.277 的响段仍被标成静音。
    sec_max = max((r["mix"] for r in rows), default=0.0)
    for r in rows:
        if r["label"] == "断口" and sec_max > 0 and r["mix"] > 0.6 * sec_max:
            r["label"] = "器乐段"

    # 峰值要在平滑曲线上找：0.25 秒窗下，一个 2.5 秒的瞬态（镲片/混响尾巴）
    # 会冒充全曲最高点。用 1.5 秒滑动平均再取最大。
    sm = np.convolve(mix, np.ones(max(1, int(1.5 / W))) / max(1, int(1.5 / W)), mode="same")
    pi = int(np.argmax(sm))
    peak_t = pi * W
    peak = next((r for r in rows if r["start"] <= peak_t < r["end"]), rows[0])
    peak["label"] += "（峰值）"
    print(f"# 结构：{dur:.1f}s · 窗 {W:.2f}s · 最小段 {min_len:.1f}s · 源={src}")
    if stems:
        print(f"  判定阈值：人声 {vthr:.4f} / 鼓 {dthr:.4f}（= 各自 P95 的 12%）")
    print(f"{'区间':>15} {'标记':<10} {'总能量':>7} {'人声':>6} {'鼓':>6} {'换手':>4}")
    for r in rows:
        print(f"{mmss(r['start'], True):>6}–{mmss(r['end'], True):<8} {r['label']:<10} "
              f"{r['mix']:>7.3f} {r['vocal']:>6.3f} {r['drums']:>6.3f} {r['chord_changes']:>4}")

    # 三个"硬事件"：人声进入 / 鼓进入 / 能量峰值
    def first_sustained(arr, thr, hold=1.0):
        need = max(1, int(hold / W))
        for i in range(len(arr) - need + 1):
            if all(arr[i + j] > thr for j in range(need)):
                return round(i * W, 2)
        return None

    events = {"vocal_in": first_sustained(vr, vthr) if stems else None,
              "drums_in": first_sustained(dr, dthr) if stems else None,
              "peak": peak["start"], "peak_end": peak["end"]}
    bits = []
    if events["vocal_in"] is not None:
        bits.append(f"人声进 {mmss(events['vocal_in'], True)}")
    if events["drums_in"] is not None:
        bits.append(f"鼓进 {mmss(events['drums_in'], True)}")
    bits.append(f"峰值 {mmss(peak['start'], True)}–{mmss(peak['end'], True)}（{peak['mix']:.3f}）")
    print("\n# " + " · ".join(bits))
    return {"ok": True, "duration": round(dur, 1), "window": W, "source": src,
            "sections": rows, "events": events}


# ─────────────────────────── 汇总 ───────────────────────────
def cmd_all(args):
    out = {"ok": True, "file": args.input, "audio": has_audio(args.input)}
    if not out["audio"]:
        print("# 这条文件没有音频流（bilibili 的 DASH 下载通常把音视频分成两个文件）")
        print("  提示：把 30xxx 视频流和 302xx 音频流用 ffmpeg -c copy 合并后再调用")
        return out

    sep = getattr(args, "separate", True)
    # sound_read 一个进程里要同时跑 whisper（ctranslate2）和旋律跟踪（torchcrepe/torch），
    # 这正是会触发 0xC0000409 的组合。默认把跟踪器钉死在 pyin，让这个进程完全碰不到 torch；
    # 想要 torchcrepe 的精度就单独调 sound_melody，或显式设 SOUNDTRY_TRACKER=torchcrepe。
    os.environ.setdefault("SOUNDTRY_TRACKER", "pyin")
    print("=" * 60)
    tr = cmd_transcribe(argparse.Namespace(
        input=args.input, model=args.model, device=args.device, compute=args.compute,
        lang=args.lang, beam=args.beam, vad=args.vad, prompt=args.prompt, srt=args.srt,
        head=args.head, include_cues=False, separate=sep, vad_mode=args.vad_mode))
    print("=" * 60)
    rh = cmd_rhythm(argparse.Namespace(input=args.input, video=args.input))
    print("=" * 60)
    me = cmd_melody(argparse.Namespace(input=args.input, window=args.window or [], separate=sep))
    print("=" * 60)
    ch = cmd_chords(argparse.Namespace(input=args.input, separate=sep))

    out.update({"transcript": tr, "rhythm": rh, "melody": me, "chords": ch})

    # 一句话结论
    agree = (rh.get("bpm_agreement") or {}).get("status")
    bpm_note = ("（两法分歧，别引用）" if agree == "conflict"
                else "（半/倍速歧义）" if agree == "octave"
                else "（不确定）" if rh.get("ambiguous") else "")
    bits = [f"{tr['duration']:.0f} 秒",
            f"{tr['segments']} 条语音/歌词（覆盖 {tr['coverage']*100:.0f}%）",
            f"{rh['bpm']:.0f} BPM{bpm_note}",
            f"人声音域 {me['range']['low']}–{me['range']['high']}",
            f"调性 {me['key']}"]
    if tr.get("dropped_hallucinations"):
        bits.append(f"滤掉 {tr['dropped_hallucinations']} 段幻觉")
    if rh.get("cuts"):
        bits.append(f"{rh['cuts']['count']} 个切点、踩拍 {rh['cuts']['hit_40ms']*100:.0f}%")
    if ch.get("histogram"):
        bits.append("主要和弦 " + "/".join(h["chord"] for h in ch["histogram"][:3]))
    print("\n# 摘要：" + " · ".join(bits))
    out["summary"] = " · ".join(bits)
    return out


# ─────────────────────────── 体检 ───────────────────────────
def cmd_doctor(args):
    """把"借来的东西在不在"一次说清楚。"""
    import importlib

    rows = []

    def add(name, ok, detail):
        rows.append((name, bool(ok), detail))

    add("python", True, f"{sys.version.split()[0]}  {sys.executable}")
    for mod in ("numpy", "soundfile", "librosa", "faster_whisper", "ctranslate2",
                "demucs", "torchcrepe", "torchaudio"):
        try:
            m = importlib.import_module(mod)
            add(mod, True, getattr(m, "__version__", "ok"))
        except Exception as e:
            add(mod, False, f"{type(e).__name__}: {e}")

    try:
        import torch
        add("torch (借用 ComfyUI)", True,
            f"{torch.__version__} cuda={torch.version.cuda} available={torch.cuda.is_available()}")
        if torch.cuda.is_available():
            add("gpu", True, torch.cuda.get_device_name(0))
    except Exception as e:
        add("torch (借用 ComfyUI)", False, f"{type(e).__name__}: {e}")

    try:
        import ctranslate2
        add("ctranslate2 CUDA 设备数", ctranslate2.get_cuda_device_count() > 0,
            str(ctranslate2.get_cuda_device_count()))
    except Exception as e:
        add("ctranslate2", False, str(e))

    try:
        ff = find_ffmpeg()
        add("ffmpeg", True, ff)
    except SystemExit as e:
        add("ffmpeg", False, str(e))

    for mname in (DEFAULT_MODEL, "large-v3"):
        mdir = MODELS_DIR / mname
        have = mdir.is_dir() and (mdir / "model.bin").is_file()
        size = f"{(mdir / 'model.bin').stat().st_size / 1024**2:.0f} MB" if have else ""
        if mname == DEFAULT_MODEL:
            add(f"模型 {mname}（默认）", have, f"{mdir} {size}".strip())
        else:
            # 备用模型缺失不算失败——1.0 默认只用 medium，large-v3 是可选的。
            add(f"模型 {mname}（备用，可选）" + ("" if have else " — 未下载，用不到可以不管"),
                True, f"{mdir} {size}".strip())

    for name, ok, detail in rows:
        print(f"{'ok  ' if ok else 'FAIL'} {name} — {detail}")
    bad = [r for r in rows if not r[1] and r[0] not in ()]
    print(f"\n{len(rows) - len(bad)}/{len(rows)} 项就绪")
    return {"ok": not bad, "checks": [{"name": n, "ok": o, "detail": d} for n, o, d in rows]}


# ─────────────────────────── 入口 ───────────────────────────
def main():
    ap = argparse.ArgumentParser(prog="soundtry")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p):
        p.add_argument("input")
        p.add_argument("--model", default=os.environ.get("SOUNDTRY_MODEL", "medium"))
        p.add_argument("--device", default=os.environ.get("SOUNDTRY_DEVICE", "cuda"))
        p.add_argument("--compute", default=os.environ.get("SOUNDTRY_COMPUTE", "float16"))
        p.add_argument("--lang", default="zh")
        p.add_argument("--beam", type=int, default=5)
        p.add_argument("--vad", action="store_true",
                       help="兼容旧参数：等价于 --vad-mode mix（在混合信号上用 Silero VAD，对唱歌不可靠）")
        p.add_argument("--vad-mode", choices=["auto", "off", "mix", "vocals"], default=None,
                       help="auto=有人声轨时用 vocals（先切分再转写，从源头防幻觉）；"
                            "vocals=只转写人声区间；mix=混合信号上的 Silero VAD；off=整条转写")
        p.add_argument("--prompt", default=os.environ.get("SOUNDTRY_PROMPT"))
        p.add_argument("--srt", default=None)
        p.add_argument("--head", type=int, default=20)
        p.add_argument("--start", type=float, default=0.0)
        p.add_argument("--end", type=float, default=None)
        p.add_argument("--lyrics", default=None,
                       help="真歌词文本文件：自动用它生成 prompt，并在结尾报告逐行覆盖率")
        p.add_argument("--separator", choices=["auto", "onnx", "demucs", "hpss", "none"], default=None,
                       help="分离后端：auto=onnx>demucs>hpss；onnx=MDX-Net(无torch)；demucs；hpss=零依赖兜底")
        p.add_argument("--separate", action=argparse.BooleanOptionalAction, default=True,
                       help="先用分离器分出人声（默认开；--no-separate 退回混合信号）")

    p = sub.add_parser("transcribe"); common(p); p.set_defaults(func=cmd_transcribe)
    p.add_argument("--include-cues", action="store_true")

    p = sub.add_parser("rhythm")
    p.add_argument("input"); p.add_argument("--video", default=None)
    p.add_argument("--cut-threshold", type=float, default=0.45,
                   help="场景检测阈值：0.45≈硬切；0.30 会把软过渡/溶解也算进来（切点数差近 3 倍）")
    p.set_defaults(func=cmd_rhythm)

    p = sub.add_parser("melody")
    p.add_argument("input"); p.add_argument("--window", action="append", default=[])
    p.add_argument("--separator", choices=["auto", "onnx", "demucs", "hpss", "none"], default=None)
    p.add_argument("--separate", action=argparse.BooleanOptionalAction, default=True)
    p.set_defaults(func=cmd_melody)

    p = sub.add_parser("chords")
    p.add_argument("input")
    p.add_argument("--separator", choices=["auto", "onnx", "demucs", "hpss", "none"], default=None)
    p.add_argument("--separate", action=argparse.BooleanOptionalAction, default=True)
    p.set_defaults(func=cmd_chords)

    p = sub.add_parser("structure")
    p.add_argument("input")
    p.add_argument("--window", type=float, default=0.25, help="分析窗长（秒），默认 0.25")
    p.add_argument("--min-len", type=float, default=2.0, help="短于此长度的碎片并入邻居，默认 2 秒")
    p.add_argument("--separator", choices=["auto", "onnx", "demucs", "hpss", "none"], default=None)
    p.add_argument("--separate", action=argparse.BooleanOptionalAction, default=True)
    p.set_defaults(func=cmd_structure)

    # 内部命令：只做分离、只写 flac，供 stems_for 的子进程调用。
    p = sub.add_parser("stems")
    p.add_argument("input")
    p.add_argument("--gpu", action=argparse.BooleanOptionalAction, default=True)
    p.set_defaults(func=cmd_stems)

    p = sub.add_parser("all"); common(p)
    p.add_argument("--window", action="append", default=[])
    p.set_defaults(func=cmd_all)

    p = sub.add_parser("doctor")
    p.add_argument("input", nargs="?", default="")
    p.set_defaults(func=cmd_doctor)

    # 防御：`input` 是位置参数。若调用方写成 `--input <path>`（历史 bug），这里改回位置参数，
    # 而不是让 argparse 报 "unrecognized arguments: --input" 把整个工具打挂。
    argv = sys.argv[1:]
    while "--input" in argv:
        i = argv.index("--input")
        if i + 1 >= len(argv):
            argv = argv[:i]
            break
        val = argv[i + 1]
        argv = argv[:i] + [val] + argv[i + 2:]

    args = ap.parse_args(argv)

    # 设备语义：设置页写 auto/gpu/cpu；这里统一成 cuda/cpu，并在降级时说清楚。
    _dev = (os.environ.get("SOUNDTRY_DEVICE") or "auto").strip().lower()
    if _dev in ("auto", "gpu", "cpu"):
        _vram = nvidia_vram_gb()
        if _dev == "cpu":
            _eff = "cpu"
        elif _dev == "gpu":
            if _vram is None:
                _eff = "cpu"
                print("# （没检测到 NVIDIA 显卡，已改用 CPU）")
            elif _vram < 4:
                _eff = "cpu"
                print(f"# ⚠ 显存 {_vram} GB，小于 4 GB，暂不支持 GPU 推理加速（已强制 CPU）")
            else:
                _eff = "cuda"
        else:
            _eff = "cuda" if (_vram is not None and _vram >= 4) else "cpu"
        os.environ["SOUNDTRY_DEVICE"] = _eff
        args.device = _eff

    if getattr(args, "separator", None):
        os.environ["SOUNDTRY_SEPARATOR"] = args.separator
    if getattr(args, "vad_mode", None) is None:
        args.vad_mode = "mix" if getattr(args, "vad", False) else "auto"
    if args.cmd != "doctor" and not Path(args.input).is_file():
        raise SystemExit(f"文件不存在：{args.input}")

    result = args.func(args)
    print(JSON_MARK + json.dumps(result, ensure_ascii=False))
    sys.stdout.flush()
    sys.stderr.flush()
    # 已知问题：某些机器上 torch 的 CUDA 清理会和 ctranslate2 打架，崩溃点在
    # "结果已打印、解释器退出时"（Windows 上是 0xC0000409 堆损坏，没有 Python 栈）。
    # 结果已经写进 stdout 了，这里直接跳过后面的 atexit/GC，等于绕开崩溃现场。
    # 想保留正常退出：设 SOUNDTRY_SAFE_EXIT=0。
    if os.environ.get("SOUNDTRY_SAFE_EXIT", "1") == "1":
        os._exit(0)
    return 0


if __name__ == "__main__":
    sys.exit(main())
