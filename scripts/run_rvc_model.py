"""独立 RVC 子进程，只加载本次翻唱选定的音色。"""
import argparse
import json
import math
import os
import random
import sys
from pathlib import Path


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rvc-root", type=Path, required=True)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--index", type=Path)
    parser.add_argument("--index-rate", type=float, default=0)
    parser.add_argument("--speaker-id", type=int, default=0)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--semitone-shift", type=int, choices=range(-12, 13), default=0)
    parser.add_argument("--seed", type=int, default=20261004)
    parser.add_argument("--phrase-plan", type=Path)
    return parser.parse_args()


def conversion_sections(duration_seconds):
    """短窗口避开 RVC 内部多次切分累积的音频帧偏差。"""
    if not math.isfinite(duration_seconds) or not 0 < duration_seconds <= 120:
        raise ValueError("Invalid RVC section duration")
    return [(start, min(start + 20, duration_seconds))
            for start in range(0, math.ceil(duration_seconds), 20)]


def convert_sections(source_audio, rate, plan, convert, work):
    """带上下文转换，按原曲采样位置交叉淡化，保持整段长度。"""
    import numpy as np
    import soundfile as sf

    audio = np.zeros(source_audio.size, dtype=np.float32)
    weights = np.zeros(source_audio.size, dtype=np.float32)
    context, blend = round(rate * 0.4), round(rate * 0.05)
    work.mkdir(exist_ok=True)
    for number, (start, end) in enumerate(plan):
        first, last = round(start * rate), min(source_audio.size, round(end * rate))
        input_first, input_last = max(0, first - context), min(source_audio.size, last + context)
        fragment = work / f"{number:03d}.wav"
        sf.write(fragment, source_audio[input_first:input_last], rate, subtype="FLOAT")
        converted = convert(fragment, input_last - input_first)
        left, right = max(0, first - blend), min(source_audio.size, last + blend)
        envelope = np.ones(right - left, dtype=np.float32)
        if number:
            fade_end = min(right, first + blend)
            envelope[:fade_end-left] = np.linspace(0, 1, fade_end-left, dtype=np.float32)
        if number + 1 < len(plan):
            fade_start = max(left, last - blend)
            envelope[fade_start-left:] = np.linspace(1, 0, right-fade_start, dtype=np.float32)
        audio[left:right] += converted[left-input_first:right-input_first] * envelope
        weights[left:right] += envelope
    if np.any(weights <= 0):
        raise ValueError("RVC phrase plan left uncovered audio")
    return audio / weights


def main():
    args = parse_args()
    root, model, source = (path.resolve(strict=True) for path in (args.rvc_root, args.model, args.source))
    index = args.index.resolve(strict=True) if args.index else None
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    phrase_plan = args.phrase_plan.resolve(strict=True) if args.phrase_plan else None
    if not 0 <= args.index_rate <= 1:
        raise ValueError("Invalid RVC index rate")
    os.environ["RVC_CUDA_GRAPH"] = "0"
    os.environ["weight_root"] = str(model.parent)
    os.environ["rmvpe_root"] = str(root / "assets" / "rmvpe")
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    os.environ["PATH"] = str(root) + os.pathsep + os.environ.get("PATH", "")
    os.chdir(root)
    sys.path.insert(0, str(root))
    # Config 的参数解析只读取它自己的参数，不接收当前 worker 参数。
    sys.argv = ["rvc-singing-worker", "--noautoopen"]
    import librosa
    import numpy as np
    import soundfile as sf
    import torch
    from configs.config import Config
    from infer.vc.modules import VC

    config = Config()
    if not torch.cuda.is_available() or not str(config.device).startswith("cuda"):
        raise RuntimeError("RVC 翻唱需要可用的 CUDA 显卡")
    random.seed(args.seed)
    np.random.seed(args.seed % (2**32))
    torch.manual_seed(args.seed)
    torch.cuda.manual_seed_all(args.seed)
    voice = VC(config)
    voice.get_vc(model.name)
    if not 0 <= args.speaker_id < int(voice.cpt["config"][-3]):
        raise ValueError("RVC speaker ID is outside the checkpoint range")
    source_audio, rate = sf.read(source, dtype="float32", always_2d=True)
    source_audio = source_audio.mean(axis=1)
    if not source_audio.size or not np.all(np.isfinite(source_audio)):
        raise ValueError("Invalid source audio")
    if source_audio.size / rate > 120:
        raise ValueError("RVC worker input must be split into sections under 120 seconds")
    source_audio = librosa.resample(source_audio, orig_sr=rate, target_sr=44100)
    rate = 44100

    def convert(path, expected_frames):
        status, result = voice.vc_single(
            args.speaker_id, str(path), args.semitone_shift, "rmvpe",
            str(index) if index else "", args.index_rate if index else 0.0,
            44100, 0.0, 0.33,
        )
        if result is None or result[0] is None or result[1] is None:
            raise RuntimeError("RVC conversion failed: " + str(status)[-1500:])
        converted_rate, audio = result
        audio = np.asarray(audio)
        if audio.dtype.kind in "iu":
            audio = audio.astype(np.float32) / 32768.0
        else:
            audio = audio.astype(np.float32)
        if converted_rate != rate or not audio.size or not np.all(np.isfinite(audio)):
            raise ValueError("RVC returned invalid audio")
        if abs(audio.size - expected_frames) > round(rate * 0.03):
            raise ValueError(
                f"RVC returned an unexpected audio duration: expected={expected_frames}, "
                f"actual={audio.size}, rate={rate}"
            )
        return np.pad(audio[:expected_frames], (0, max(0, expected_frames - audio.size)))

    if phrase_plan:
        from singing_phrase_inference import _read_plan
        plan = _read_plan(phrase_plan, output, source_audio.size / rate)
    else:
        plan = conversion_sections(source_audio.size / rate)
    if len(plan) > 1:
        audio = convert_sections(source_audio, rate, plan, convert, output / "phrase_work")
    else:
        audio = convert(source, source_audio.size)
    sf.write(output / "rvc_vocals.wav", audio, rate, subtype="FLOAT")
    print(json.dumps({"backend": "rvc", "index_used": index is not None,
                      "frames": int(audio.size), "section_count": len(plan)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
