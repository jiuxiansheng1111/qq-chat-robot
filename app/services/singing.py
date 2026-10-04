"""歌曲转角色翻唱流程，独立于对话 TTS。"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import math
import re
import shutil
from collections.abc import Awaitable, Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal

from app.config import Settings
from app.services.audio_chunks import (
    AudioChunk,
    locate_audio_tool,
    run_audio_command,
)
from app.services.singing_diagnostics import save_failed_singing_diagnostics
from app.services.singing_excerpt import (
    SingingExcerpt,
    format_excerpt_lrc,
    select_singing_excerpt,
    shift_excerpt_lyrics,
)
from app.services.singing_gpu import async_gpu_lock
from app.services.singing_mix import vocal_clarity_filter, vocal_forward_mix_filter, wav_rms
from app.services.singing_sections import plan_paused_sections
from app.services.singing_sources import (
    SINGING_DATA_ROOT,
    SingingSong,
    download_singing_source,
    resolve_singing_song,
)
from app.services.voice import (
    synthesize_voice,
    voice_profile_authorized,
    voice_profiles,
)
from app.services.voice_persona import resolve_voice_persona_alias

logger = logging.getLogger(__name__)
PROJECT_ROOT = Path(__file__).resolve().parents[2]
Progress = Callable[[str], Awaitable[None]]


def singing_job_directory(job_id: str) -> Path:
    """将作业文件及其解析后的符号链接都限制在 jobs 目录内。"""
    if not re.fullmatch(r"[a-f0-9]{32}", job_id):
        raise ValueError("Invalid singing job ID")
    singing_root = SINGING_DATA_ROOT.resolve()
    root = (singing_root / "jobs").resolve()
    if root.parent != singing_root:
        raise ValueError("Singing jobs root escapes its data directory")
    directory = (root / job_id).resolve()
    if directory.parent != root:
        raise ValueError("Singing job directory escapes its root")
    return directory


def cleanup_singing_job(job_id: str) -> None:
    directory = singing_job_directory(job_id)
    if directory.exists():
        shutil.rmtree(directory, ignore_errors=True)


def archive_singing_job(job_id: str, *, keep: int = 5) -> None:
    """限制本地试听时保留的已完成翻唱数量。"""
    directory = singing_job_directory(job_id)
    if not directory.is_dir():
        return
    singing_root = SINGING_DATA_ROOT.resolve()
    root = (singing_root / "results").resolve()
    if root.parent != singing_root:
        raise ValueError("Singing results root escapes its data directory")
    review = (root / job_id).resolve()
    if review.parent != root:
        raise ValueError("Singing result directory escapes its root")
    review.mkdir(parents=True, exist_ok=True)
    for name in (
        "cover.wav", "lyrics.lrc", "quality.json", "quality_retry.json",
        "pitch_plan.json", "selection.json", "voice_reference.wav",
        "vocal_before.wav", "vocal_after.wav",
    ):
        path = directory / name
        if path.is_file():
            shutil.move(str(path), str(review / name))
    chunks = directory / "chunks"
    if chunks.is_dir() and not (review / "chunks").exists():
        shutil.move(str(chunks), str(review / "chunks"))
    previous = sorted(
        (path for path in root.iterdir() if re.fullmatch(r"[a-f0-9]{32}", path.name)
         and path.is_dir() and not path.is_symlink()),
        key=lambda path: path.stat().st_mtime, reverse=True,
    )
    for path in previous[max(1, keep):]:
        target = path.resolve()
        if target.parent == root:
            shutil.rmtree(target, ignore_errors=True)


class SingingPipelineError(RuntimeError):
    """可安全展示给群成员的明确错误提示。"""


@dataclass(frozen=True)
class SingingCommand:
    action: str
    query: str = ""
    profile_id: str | None = None
    mode: Literal["full", "clip"] = "full"


@dataclass(frozen=True)
class SingingCover:
    song: SingingSong
    profile_id: str
    label: str
    full_path: Path
    chunks: tuple[AudioChunk, ...]
    quality: dict
    pitch_shift_semitones: int = 0
    mode: Literal["full", "clip"] = "full"
    source_start_seconds: float = 0
    source_end_seconds: float | None = None


@dataclass(frozen=True)
class SingingDelivery:
    song: SingingSong
    label: str
    chunk: AudioChunk
    index: int
    total: int
    mode: Literal["full", "clip"]
    pitch_shift_semitones: int


ChunkDelivery = Callable[[SingingDelivery], Awaitable[None]]


def _take_singing_mode(query: str, mode: Literal["full", "clip"]) -> tuple[str, Literal["full", "clip"]]:
    # 模式词要单独写，别把歌名里的“片段”误删了。
    match = re.match(r"^(片段|部分|完整|整首|整曲|clip|full)(?=$|[\s:：])", query, re.IGNORECASE)
    if not match:
        return query, mode
    selected = "clip" if match.group(1).casefold() in {"片段", "部分", "clip"} else "full"
    return query[match.end():].strip(" ：:"), selected


def _resolve_singing_profile_alias(
    requested: str, profiles: Mapping[str, Mapping[str, object]]
) -> str | None:
    profile_id = resolve_voice_persona_alias(requested, profiles)
    if profile_id is not None:
        return profile_id
    matches = [
        profile_id for profile_id, profile in profiles.items()
        if requested.casefold() in {
            profile_id.casefold(), str(profile.get("label") or "").casefold()
        }
    ]
    return matches[0] if len(matches) == 1 else None


def _take_singing_profile(
    query: str, profiles: Mapping[str, Mapping[str, object]]
) -> tuple[str | None, str]:
    # 有分隔符时沿用原语法，音色名必须完整匹配。
    separated = re.match(r"([^\s:：]+)[\s:：]+(.+)", query, flags=re.DOTALL)
    if separated:
        profile_id = _resolve_singing_profile_alias(separated.group(1), profiles)
        if profile_id is not None:
            return profile_id, separated.group(2).strip()

    # 没有分隔符时只在命令开头找完整音色别名，最长别名优先。
    # 短英文别名必须带空格或冒号，避免吞掉英文歌名的开头。
    for end in range(len(query), 0, -1):
        requested = query[:end]
        profile_id = _resolve_singing_profile_alias(requested, profiles)
        if profile_id is None:
            continue
        alias = requested.strip()
        if alias.isascii() and len(alias) < 6:
            continue
        song = query[end:].strip(" ：:")
        if song:
            return profile_id, song
    return None, query


def parse_singing_command(
    text: str, profiles: Mapping[str, Mapping[str, object]], *, addressed: bool
) -> SingingCommand | None:
    """需要斜杠命令或真正的机器人 @；歌曲标题保持原样。"""
    candidate = text.strip()
    controls = {
        "唱歌状态": "status", "翻唱状态": "status",
        "取消唱歌": "cancel", "停止唱歌": "cancel", "取消翻唱": "cancel",
        "唱歌音色": "voices", "翻唱音色": "voices",
    }
    lowered = candidate.removeprefix("/")
    if (addressed or candidate.startswith("/")) and lowered in controls:
        return SingingCommand(controls[lowered])
    prefixes = (
        ("/唱歌片段", "clip"), ("/翻唱片段", "clip"),
        ("/唱歌完整", "full"), ("/翻唱完整", "full"),
        ("/唱歌", "full"), ("/翻唱", "full"), ("/sing", "full"), ("/cover", "full"),
    )
    if addressed:
        prefixes += (
            ("帮我唱一段", "clip"), ("帮我唱几句", "clip"),
            ("唱歌片段", "clip"), ("翻唱片段", "clip"),
            ("唱歌完整", "full"), ("翻唱完整", "full"),
            ("唱一段", "clip"), ("唱几句", "clip"),
            ("帮我唱", "full"), ("唱一首", "full"), ("唱歌", "full"), ("翻唱", "full"),
        )
    for prefix, mode in prefixes:
        if candidate.casefold().startswith(prefix.casefold()):
            if prefix.isascii() and len(candidate) > len(prefix) and not candidate[len(prefix)].isspace():
                continue
            query = " ".join(candidate[len(prefix):].split()).strip(" ：:")[:160]
            query, mode = _take_singing_mode(query, mode)
            profile_id, query = _take_singing_profile(query, profiles)
            if profile_id:
                query, mode = _take_singing_mode(query, mode)
            return SingingCommand("sing", query, profile_id, mode)
    return None


def _project_path(value: str) -> Path:
    path = Path(value).expanduser()
    return (PROJECT_ROOT / path).resolve() if not path.is_absolute() else path.resolve()


def _voice_pitch_shift_for_profile(settings: Settings, profile_id: str) -> int:
    raw = settings.singing_semitone_shift_by_profile_json
    try:
        shifts = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise SingingPipelineError("角色音区配置无效，请管理员检查。") from exc
    if not isinstance(shifts, dict) or any(
        not isinstance(key, str)
        or type(value) is not int
        or not -12 <= value <= 12
        for key, value in shifts.items()
    ):
        raise SingingPipelineError("角色音区配置无效，请管理员检查。")
    return shifts.get(profile_id, 0)


def _accompaniment_pitch_shift(voice_shift: int) -> int:
    if voice_shift > 6:
        return voice_shift - 12
    if voice_shift < -6:
        return voice_shift + 12
    return voice_shift


def _automatic_pitch_target(settings: Settings, profile_id: str) -> float | None:
    try:
        targets = json.loads(settings.singing_target_median_f0_by_profile_json)
    except (TypeError, ValueError) as exc:
        raise SingingPipelineError("自动音区配置无效，请管理员检查。") from exc
    if not isinstance(targets, dict) or any(
        not isinstance(key, str)
        or isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or not 80 <= value <= 1000
        for key, value in targets.items()
    ):
        raise SingingPipelineError("自动音区配置无效，请管理员检查。")
    # 手动指定的音高优先，包括明确设置为 0 的情况。
    _voice_pitch_shift_for_profile(settings, profile_id)
    if profile_id in json.loads(settings.singing_semitone_shift_by_profile_json):
        return None
    value = targets.get(profile_id)
    return float(value) if value is not None else None


def _vocal_mix_gain(report: dict, settings: Settings, background_rms: float = 0) -> float:
    rms = report.get("vocal_mix_input_rms", report.get("converted_rms"))
    if isinstance(rms, bool) or not isinstance(rms, (int, float)) or not math.isfinite(rms) or rms <= 0:
        raise SingingPipelineError("歌声音量检查结果无效，已停止混音。")
    # 伴奏保留原音量，弱人声才补增益，最多放大四倍。
    target = max(
        settings.singing_vocal_target_rms,
        background_rms * settings.singing_accompaniment_gain
        * 10 ** (settings.singing_vocal_background_gap_db / 20),
    )
    return max(1.0, min(4.0, target / rms))


async def _plan_automatic_pitch(
    vocals: Path, target_hz: float, output: Path, settings: Settings
) -> dict:
    python, seed_root, _, _ = runtime_paths(settings)
    await run_audio_command(
        [python, PROJECT_ROOT / "scripts" / "plan_singing_pitch.py",
         "--seed-root", seed_root, "--source", vocals, "--target-hz", str(target_hz),
         "--output", output, *(["--offline"] if settings.singing_hf_offline else [])],
        cwd=seed_root, timeout_seconds=settings.singing_model_timeout_seconds,
    )
    if not output.is_file() or output.stat().st_size > 65536:
        raise SingingPipelineError("未取得歌曲音区分析结果。")
    try:
        plan = json.loads(output.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise SingingPipelineError("歌曲音区分析结果无法读取。") from exc
    shift = plan.get("expected_semitone_shift") if isinstance(plan, dict) else None
    if type(shift) is not int or shift not in (-12, 0, 12):
        raise SingingPipelineError("歌曲音区分析未返回可用的八度设置。")
    return {**plan, "mode": "automatic_octave"}


def runtime_paths(settings: Settings) -> tuple[Path, Path, Path, Path]:
    seed_root = _project_path(settings.singing_seed_root)
    python = _project_path(settings.singing_python)
    if not python.is_file() or not (seed_root / "inference.py").is_file():
        raise SingingPipelineError("唱歌模型尚未安装，请先运行 scripts/setup_singing.ps1。")
    ffmpeg = locate_audio_tool("ffmpeg", settings.singing_ffmpeg_path)
    ffprobe = locate_audio_tool("ffprobe", settings.singing_ffprobe_path)
    return python, seed_root, ffmpeg, ffprobe


def singing_voice_menu(settings: Settings, bot_self_id: str) -> str:
    names = [
        str(profile.get("label") or profile_id)
        for profile_id, profile in voice_profiles(settings).items()
        if voice_profile_authorized(settings, profile_id, bot_self_id)
    ]
    return (
        "角色翻唱音色：" + "、".join(names)
        + "\n@我 唱歌 歌名：使用当前选定的角色"
        + "\n@我 翻唱 芳乃 歌名：临时指定音色"
        + f"\n@我 翻唱片段 芳乃 歌名：唱约 {settings.singing_clip_seconds:g} 秒的几句歌词"
        + "\n@我 翻唱完整 芳乃 歌名：唱整首"
        + "\n@我 唱歌状态 / 取消唱歌"
        + "\n整首歌在句间停顿处分段，生成一段就发一段，每段不超过 115 秒。"
    )


def uses_base_singing_model(profile_id: str, settings: Settings) -> bool:
    """个别角色可以回到基础模型，无需删除微调权重。"""
    return profile_id in {
        value.strip() for value in settings.singing_base_model_profile_ids.split(",")
        if value.strip()
    }


def _checkpoint_for_profile(profile_id: str) -> tuple[Path, Path] | None:
    manifest = SINGING_DATA_ROOT / "voices.json"
    if not manifest.exists():
        return None
    if manifest.stat().st_size > 1024 * 1024:
        raise SingingPipelineError("唱歌音色配置过大，请管理员检查。")
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
        entry = data.get(profile_id) if isinstance(data, dict) else None
    except (OSError, UnicodeError, ValueError) as exc:
        raise SingingPipelineError("唱歌音色配置无法读取，请管理员检查。") from exc
    if entry is None:
        return None
    # 训练候选模型在操作者验收样音前保持未启用状态。
    if not isinstance(entry, dict) or entry.get("accepted") is not True:
        raise SingingPipelineError("这个唱歌音色的训练结果尚未验收，请先验收后启用。")
    result = []
    for field in ("checkpoint", "config"):
        value = entry.get(field)
        if not isinstance(value, str) or not value:
            raise SingingPipelineError("唱歌音色模型配置缺失。")
        path = _project_path(value)
        if not path.is_file():
            raise SingingPipelineError("这个角色的唱歌模型文件暂不可用。")
        result.append(path)
    return result[0], result[1]


def singing_checkpoint(profile_id: str, settings: Settings) -> tuple[Path, Path] | None:
    """返回这次实际使用的权重；空值表示基础模型。"""
    return None if uses_base_singing_model(profile_id, settings) else _checkpoint_for_profile(profile_id)


def inference_cfg_rate_for_profile(profile_id: str, settings: Settings) -> float:
    """先用角色单独配置，没填就沿用通用值。"""
    raw = settings.singing_inference_cfg_rate_by_profile_json
    try:
        if len(raw) > 8192:
            raise ValueError("配置过长")
        rates = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise SingingPipelineError("角色翻唱引导参数无效，请管理员检查。") from exc
    if not isinstance(rates, dict) or any(
        not isinstance(key, str) or not key
        or type(value) not in (int, float) or not 0 <= value <= 2
        for key, value in rates.items()
    ):
        raise SingingPipelineError("角色翻唱引导参数无效，请管理员检查。")
    return float(rates.get(profile_id, settings.singing_inference_cfg_rate))


async def prepare_voice_reference(
    profile_id: str, job_dir: Path, settings: Settings, bot_self_id: str
) -> Path:
    profiles = voice_profiles(settings)
    if profile_id not in profiles or not voice_profile_authorized(
        settings, profile_id, bot_self_id
    ):
        raise SingingPipelineError("当前机器人不可使用这个角色音色。")
    profile = profiles[profile_id]
    real_reference_profiles = {
        item.strip()
        for item in settings.singing_real_reference_profile_ids.split(",")
        if item.strip()
    }
    try:
        reference_overrides = json.loads(settings.singing_reference_audio_by_profile_json or "{}")
    except (TypeError, ValueError) as exc:
        raise SingingPipelineError("翻唱参考录音配置不是有效的 JSON。") from exc
    if not isinstance(reference_overrides, dict):
        raise SingingPipelineError("翻唱参考录音配置应为角色到录音路径的映射。")
    override = reference_overrides.get(profile_id)
    if override is not None and (not isinstance(override, str) or not override.strip()):
        raise SingingPipelineError("这个角色的翻唱参考录音路径无效。")
    recorded = str(profile.get("ref_audio_path") or "")
    if override is None and settings.singing_prefer_recorded_reference and recorded:
        recorded_path = _project_path(recorded)
        if recorded_path.is_file():
            return recorded_path
    if (
        settings.singing_use_trained_tts_reference
        and profile_id not in real_reference_profiles
        and override is None
    ):
        # 训练好的语音模型只提供音色参考。
        # 歌曲的节奏和音高仍来自原唱，不会使用 TTS。
        supported = profile.get("supported_languages") or ["zh"]
        language = "zh" if "zh" in supported else str(supported[0])
        phrase = {
            "zh": "今天想给你唱一首歌，希望你能喜欢我的声音。",
            "ja": "今日はあなたに歌を届けます。私の声を聴いてください。",
            "en": "I would like to sing a song for you. Please listen to my voice.",
            "yue": "今日想唱首歌畀你聽，希望你鍾意我嘅聲。",
        }.get(language)
        if phrase is None:
            raise SingingPipelineError("这个角色暂未配置可合成的参考语言。")
        record = await synthesize_voice(
            phrase, settings, profile_id, target_language=language, bot_self_id=bot_self_id
        )
        if not record.startswith("base64://"):
            raise SingingPipelineError("训练音色未返回可用的参考音频。")
        try:
            data = base64.b64decode(record.removeprefix("base64://"), validate=True)
        except ValueError as exc:
            raise SingingPipelineError("训练音色的参考音频无法解码。") from exc
        if not data or len(data) > 10 * 1024 * 1024:
            raise SingingPipelineError("训练音色的参考音频大小异常。")
        target = job_dir / "voice_reference.wav"
        target.write_bytes(data)
        return target
    value = str(override or profile.get("ref_audio_path") or "")
    path = _project_path(value)
    if not value or not path.is_file():
        raise SingingPipelineError("这个角色缺少可用的音色参考录音。")
    return path


async def convert_vocals(
    source: Path,
    reference: Path,
    output_dir: Path,
    profile_id: str,
    settings: Settings,
    *,
    steps: int | None = None,
    semitone_shift: int = 0,
) -> Path:
    python, seed_root, ffmpeg, _ = runtime_paths(settings)
    output_dir.mkdir(parents=True, exist_ok=True)
    args = [
        str(python), str(PROJECT_ROOT / "scripts" / "run_singing_model.py"),
        "--seed-root", str(seed_root), "--ffmpeg", str(ffmpeg),
        "--source", str(source), "--target", str(reference), "--output", str(output_dir),
        "--diffusion-steps", str(steps or settings.singing_diffusion_steps),
        "--inference-cfg-rate", str(inference_cfg_rate_for_profile(profile_id, settings)),
        "--seed", str(settings.singing_seed),
        "--repair-f0-spikes" if settings.singing_repair_f0_spikes else "--no-repair-f0-spikes",
        "--semitone-shift", str(semitone_shift),
    ]
    checkpoint = singing_checkpoint(profile_id, settings)
    if settings.singing_hf_offline:
        args += ["--offline"]
    if checkpoint:
        args += ["--checkpoint", str(checkpoint[0]), "--config", str(checkpoint[1])]
    await run_audio_command(args, cwd=seed_root, timeout_seconds=settings.singing_model_timeout_seconds)
    files = list(output_dir.glob("*.wav"))
    if len(files) != 1 or files[0].stat().st_size < 1000:
        raise SingingPipelineError("歌声模型没有生成完整音频。")
    return files[0]


async def check_cover_quality(
    source: Path,
    converted: Path,
    reference: Path,
    output: Path,
    settings: Settings,
    *,
    expected_semitone_shift: int = 0,
) -> dict:
    python, seed_root, _, _ = runtime_paths(settings)
    await run_audio_command(
        [python, PROJECT_ROOT / "scripts" / "check_singing_quality.py",
         "--seed-root", seed_root, "--source", source, "--converted", converted,
         "--reference", reference, "--output", output,
         "--max-pitch-cents", str(settings.singing_max_pitch_error_cents),
         "--min-voice-similarity", str(settings.singing_min_voice_similarity),
         "--max-duration-error", "0.01",
         "--expected-semitone-shift", str(expected_semitone_shift),
         "--min-voiced-recall", str(settings.singing_min_voiced_recall),
         "--min-energy-recall", str(settings.singing_min_energy_recall),
         "--max-missing-vocal-seconds", str(settings.singing_max_missing_vocal_seconds),
         *(["--offline"] if settings.singing_hf_offline else [])],
        cwd=seed_root, timeout_seconds=settings.singing_model_timeout_seconds,
    )
    report = json.loads(output.read_text(encoding="utf-8"))
    from app.services.singing_quality import quality_failures

    problems = quality_failures(
        report, max_pitch_median_cents=settings.singing_max_pitch_error_cents,
        min_voice_similarity=settings.singing_min_voice_similarity,
        max_duration_error_ratio=0.01,
        min_voiced_recall=settings.singing_min_voiced_recall,
        min_energy_recall=settings.singing_min_energy_recall,
        max_missing_vocal_seconds=settings.singing_max_missing_vocal_seconds,
    )
    if problems:
        raise SingingPipelineError("这次翻唱的人声检查没通过：" + "；".join(problems))
    return report


async def _validate_model_audio(
    path: Path, ffprobe: Path, expected_seconds: float, settings: Settings
) -> None:
    if not path.is_file() or not 1000 <= path.stat().st_size <= settings.singing_max_source_bytes * 2:
        raise SingingPipelineError("模型生成的音频文件大小异常，已停止生成。")
    payload = json.loads(await run_audio_command(
        [ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "json", path]
    ))
    seconds = float(payload["format"]["duration"])
    if (not 0.1 <= seconds <= settings.singing_max_song_seconds + 1
            or abs(seconds - expected_seconds) > max(0.15, expected_seconds * 0.005)):
        raise SingingPipelineError("模型生成的音频时长异常，已停止生成。")


async def _render_singing_section(
    vocals: Path, accompaniment: Path, reference: Path, directory: Path,
    profile_id: str, seconds: float, voice_shift: int, settings: Settings,
    ffmpeg: Path, ffprobe: Path, progress: Progress,
    *, record_pause_seconds: float = 0,
) -> tuple[Path, Path, dict]:
    directory.mkdir(parents=True, exist_ok=True)
    source_rms = await asyncio.to_thread(wav_rms, vocals)
    background_rms = await asyncio.to_thread(wav_rms, accompaniment)
    if source_rms < 0.0003:
        # 纯伴奏段也保留，别让整首歌少一截。
        converted = vocals
        report = {"accepted": True, "kind": "instrumental", "converted_rms": 0.0}
        vocal_gain = 1.0
    else:
        converted = await convert_vocals(
            vocals, reference, directory / "converted", profile_id, settings,
            semitone_shift=voice_shift,
        )
        await _validate_model_audio(converted, ffprobe, seconds, settings)
        try:
            report = await check_cover_quality(
                vocals, converted, reference, directory / "quality.json", settings,
                expected_semitone_shift=voice_shift,
            )
        except SingingPipelineError:
            await progress("这一段有音准或人声缺失问题，正在提高精度重试")
            converted = await convert_vocals(
                vocals, reference, directory / "retry", profile_id, settings,
                steps=min(80, settings.singing_diffusion_steps + 15),
                semitone_shift=voice_shift,
            )
            await _validate_model_audio(converted, ffprobe, seconds, settings)
            report = await check_cover_quality(
                vocals, converted, reference, directory / "quality_retry.json", settings,
                expected_semitone_shift=voice_shift,
            )
    # 原转换结果保留给音准检查；混音单独处理，不再压缩放大后的人声。
    mix_vocal = directory / "vocal_mix_input.wav"
    await run_audio_command(
        [ffmpeg, "-v", "error", "-nostdin", "-y", "-i", converted,
         "-af", vocal_clarity_filter(), "-ar", "44100", "-ac", "1",
         "-c:a", "pcm_s16le", mix_vocal],
    )
    await _validate_model_audio(mix_vocal, ffprobe, seconds, settings)
    mix_vocal_rms = await asyncio.to_thread(wav_rms, mix_vocal)
    report["vocal_mix_input_rms"] = mix_vocal_rms
    if source_rms >= 0.0003:
        vocal_gain = _vocal_mix_gain(report, settings, background_rms)
    vocal_level = mix_vocal_rms * vocal_gain
    background_gain = settings.singing_accompaniment_gain if background_rms > 0 else 0
    accompaniment_shift = _accompaniment_pitch_shift(voice_shift)
    if accompaniment_shift:
        shifted = directory / "accompaniment_shifted.wav"
        python, seed_root, _, _ = runtime_paths(settings)
        await run_audio_command(
            [python, PROJECT_ROOT / "scripts" / "transpose_singing_audio.py",
             "--input", accompaniment, "--output", shifted,
             "--semitones", str(accompaniment_shift)],
            cwd=seed_root, timeout_seconds=settings.singing_model_timeout_seconds,
        )
        await _validate_model_audio(shifted, ffprobe, seconds, settings)
        accompaniment = shifted
    if directory.name == "001":
        for input_audio, name in ((vocals, "vocal_before.wav"), (converted, "vocal_after.wav")):
            await run_audio_command(
                [ffmpeg, "-v", "error", "-nostdin", "-y", "-i", input_audio,
                 "-t", "20", "-ar", "32000", "-ac", "1", "-c:a", "pcm_s16le",
                 directory.parents[1] / name],
            )
    mixed = directory / "mixed.wav"
    await run_audio_command(
        [ffmpeg, "-v", "error", "-nostdin", "-y", "-i", mix_vocal, "-i", accompaniment,
         "-filter_complex", vocal_forward_mix_filter(
             vocal_gain, background_gain, settings.singing_master_gain, seconds,
             prepared_vocal=True,
         ), "-t", f"{seconds:.6f}", "-fs", str(settings.singing_max_source_bytes * 2),
         "-ar", "44100", "-ac", "2", "-c:a", "pcm_s16le", mixed],
    )
    await _validate_model_audio(mixed, ffprobe, seconds, settings)
    record = directory / "record.wav"
    record_seconds = seconds + record_pause_seconds
    # 先重采样再限幅，避免转成 QQ 格式时出现新的尖峰。
    record_filter = (
        "aresample=32000,pan=mono|c0=0.5*c0+0.5*c1,"
        "alimiter=limit=0.95:latency=1:level=false"
    )
    if record_pause_seconds:
        record_filter += f",apad=pad_dur={record_pause_seconds:.6f}"
    await run_audio_command(
        [ffmpeg, "-v", "error", "-nostdin", "-y", "-i", mixed,
         "-af", record_filter, "-t", f"{record_seconds:.6f}",
         "-ar", "32000", "-ac", "1", "-c:a", "pcm_s16le", record],
    )
    await _validate_model_audio(record, ffprobe, record_seconds, settings)
    record_info = json.loads(await run_audio_command(
        [ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "json", record]
    ))
    if float(record_info["format"]["duration"]) >= 120:
        raise SingingPipelineError("这一段超过 QQ 两分钟上限，已停止发送。")
    report.update({
        "profile_id": profile_id, "voice_pitch_shift_semitones": voice_shift,
        "accompaniment_pitch_shift_semitones": accompaniment_shift,
        "vocal_mix_gain": vocal_gain, "accompaniment_mix_gain": background_gain,
        "vocal_estimated_rms": vocal_level,
        "vocal_clarity_filter": vocal_clarity_filter(),
        "inference_cfg_rate": inference_cfg_rate_for_profile(profile_id, settings),
        "singing_model_backend": (
            "instrumental" if source_rms < 0.0003 else
            "base" if singing_checkpoint(profile_id, settings) is None else "profile"
        ),
        "accompaniment_rms": background_rms,
        "vocal_target_rms": settings.singing_vocal_target_rms,
        "vocal_background_gap_db": settings.singing_vocal_background_gap_db,
        "master_gain": settings.singing_master_gain,
        "record_pause_seconds": record_pause_seconds,
    })
    (directory / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    return mixed, record, report


async def generate_singing_cover(
    query: str,
    profile_id: str,
    job_id: str,
    settings: Settings,
    progress: Progress,
    *,
    bot_self_id: str,
    mode: Literal["full", "clip"] = "full",
    on_chunk: ChunkDelivery | None = None,
) -> SingingCover:
    """训练和翻唱轮流用显卡，避免同时生成时爆显存。"""
    async with async_gpu_lock():
        return await _generate_singing_cover_unlocked(
            query, profile_id, job_id, settings, progress,
            bot_self_id=bot_self_id, mode=mode, on_chunk=on_chunk,
        )


async def _generate_singing_cover_unlocked(
    query: str,
    profile_id: str,
    job_id: str,
    settings: Settings,
    progress: Progress,
    *,
    bot_self_id: str,
    mode: Literal["full", "clip"] = "full",
    on_chunk: ChunkDelivery | None = None,
) -> SingingCover:
    if mode not in {"full", "clip"}:
        raise SingingPipelineError("翻唱模式无效，请选择完整或片段。")
    voice_pitch_shift = _voice_pitch_shift_for_profile(settings, profile_id)
    pitch_target = _automatic_pitch_target(settings, profile_id)
    job_dir = singing_job_directory(job_id)
    python, seed_root, ffmpeg, ffprobe = runtime_paths(settings)
    job_dir.mkdir(parents=True, exist_ok=False)
    delivered = 0
    try:
        await progress("正在查找原曲和歌词")
        song = await resolve_singing_song(query, settings)
        if song.track.duration_seconds > settings.singing_max_song_seconds:
            raise SingingPipelineError("歌曲超过当前唱歌时长上限，请选择更短的歌曲。")
        source = await download_singing_source(song, job_dir, settings)
        # 先核对完整原曲，再按用户选的模式截取。
        info = json.loads(await run_audio_command(
            [ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "json", source]
        ))
        duration = float(info["format"]["duration"])
        if not 1 <= duration <= settings.singing_max_song_seconds:
            raise SingingPipelineError("歌曲实际时长超出范围，已停止生成。")
        expected = song.track.duration_seconds
        if expected > 0 and abs(duration - expected) > max(2, expected * 0.03):
            raise SingingPipelineError("原曲文件时长与歌曲信息不一致，已停止生成。")
        selection = SingingExcerpt(0, duration, "完整歌曲")
        if mode == "clip":
            selection = select_singing_excerpt(
                song.lyric_lines, duration, settings.singing_clip_seconds,
                settings.singing_chunk_seconds,
                ignore_texts=(song.track.title, *song.track.artists),
                song_title=song.track.title,
                song_artists=song.track.artists,
            )
            clipped = job_dir / "excerpt.wav"
            await progress(f"已选出约 {selection.duration_seconds:.0f} 秒的几句歌词")
            await run_audio_command(
                [ffmpeg, "-v", "error", "-nostdin", "-y", "-i", source,
                 "-ss", f"{selection.start_seconds:.6f}",
                 "-t", f"{selection.duration_seconds:.6f}", "-ar", "44100", "-ac", "2",
                 "-c:a", "pcm_s16le", clipped],
            )
            source = clipped
            duration = selection.duration_seconds
            lines = shift_excerpt_lyrics(song.lyric_lines, selection)
            song = replace(song, lyric_lines=lines, lyrics_text=format_excerpt_lrc(lines))
        (job_dir / "selection.json").write_text(json.dumps({
            "mode": mode, "song_id": song.track.song_id, "profile_id": profile_id,
            "source_start_seconds": selection.start_seconds,
            "source_end_seconds": selection.end_seconds, "reason": selection.reason,
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        raw_reference = await prepare_voice_reference(profile_id, job_dir, settings, bot_self_id)
        reference = job_dir / "voice_reference.wav"
        if raw_reference.resolve() == reference.resolve():
            raw_reference = job_dir / "voice_reference_input.wav"
            reference.rename(raw_reference)
        await run_audio_command(
            [ffmpeg, "-v", "error", "-nostdin", "-y", "-i", raw_reference,
             "-t", str(settings.singing_reference_max_seconds), "-ar", "44100",
             "-ac", "1", "-c:a", "pcm_s16le", reference],
        )
        await progress(f"已找到《{song.track.title}》，正在分离原唱人声和伴奏")
        # 先分离音轨并卸载 Demucs，再运行翻唱模型。
        # SingingJobManager 每次只允许一个 GPU 任务，减少笔记本显存压力。
        await run_audio_command(
            [python, PROJECT_ROOT / "scripts" / "run_singing_separation.py",
             "--ffmpeg", ffmpeg, "--source", source, "--output", job_dir / "separated",
             "--model", settings.singing_separation_model],
            cwd=seed_root, timeout_seconds=settings.singing_model_timeout_seconds,
        )
        stem_dir = job_dir / "separated" / settings.singing_separation_model / source.stem
        vocals, accompaniment = stem_dir / "vocals.wav", stem_dir / "no_vocals.wav"
        if not vocals.is_file() or not accompaniment.is_file():
            raise SingingPipelineError("歌曲的人声/伴奏分离失败。")
        for stem in (vocals, accompaniment):
            await _validate_model_audio(stem, ffprobe, duration, settings)
        analysis = job_dir / "vocal_pauses.wav"
        await run_audio_command(
            [ffmpeg, "-v", "error", "-nostdin", "-y", "-i", vocals,
             "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", analysis],
        )
        try:
            sections = await asyncio.to_thread(
                plan_paused_sections, analysis, song.lyric_lines,
                duration, settings.singing_chunk_seconds,
            )
        except ValueError as exc:
            raise SingingPipelineError(str(exc)) from exc
        pitch_plan = {"mode": "fixed", "expected_semitone_shift": voice_pitch_shift}
        if pitch_target is not None:
            await progress("正在为角色选择适合这首歌的音区")
            pitch_plan = await _plan_automatic_pitch(
                vocals, pitch_target, job_dir / "pitch_plan.json", settings
            )
            voice_pitch_shift = pitch_plan["expected_semitone_shift"]
        label = str(voice_profiles(settings)[profile_id].get("label") or profile_id)
        chunks: list[AudioChunk] = []
        reports: list[dict] = []
        mixes: list[Path] = []
        record_dir = job_dir / "chunks"
        record_dir.mkdir()
        (job_dir / "lyrics.lrc").write_text(song.lyrics_text, encoding="utf-8")
        report = {
            "accepted": True, "mode": mode, "profile_id": profile_id,
            "voice_pitch_shift_semitones": voice_pitch_shift,
            "accompaniment_pitch_shift_semitones": _accompaniment_pitch_shift(voice_pitch_shift),
            "pitch_plan": pitch_plan, "reference_audio": str(reference),
            "has_timed_lyrics": bool(song.lyric_lines), "segments": reports,
            "source_start_seconds": selection.start_seconds,
            "source_end_seconds": selection.end_seconds,
        }
        for index, section in enumerate(sections, 1):
            await progress(f"正在生成第 {index}/{len(sections)} 段，完成后立即发送")
            directory = job_dir / "parts" / f"{index:03d}"
            directory.mkdir(parents=True)
            part_vocals, part_background = directory / "vocals.wav", directory / "background.wav"
            # 人声和伴奏使用同一起止点，不丢失句间停顿。
            for original, output, channels in (
                (vocals, part_vocals, 1), (accompaniment, part_background, 2),
            ):
                await run_audio_command(
                    [ffmpeg, "-v", "error", "-nostdin", "-y", "-i", original,
                     "-ss", f"{section.start_seconds:.6f}",
                     "-t", f"{section.duration_seconds:.6f}",
                     "-af", "alimiter=limit=0.98:latency=1:level=false",
                     "-ar", "44100", "-ac", str(channels), "-c:a", "pcm_s16le", output],
                )
                await _validate_model_audio(output, ffprobe, section.duration_seconds, settings)
            mixed, record, part_report = await _render_singing_section(
                part_vocals, part_background, reference, directory, profile_id,
                section.duration_seconds, voice_pitch_shift, settings, ffmpeg, ffprobe, progress,
                record_pause_seconds=getattr(section, "pause_seconds", 0),
            )
            saved_record = record_dir / f"{index:03d}.wav"
            shutil.move(str(record), str(saved_record))
            chunk = AudioChunk(
                saved_record, selection.start_seconds + section.start_seconds,
                selection.start_seconds + section.end_seconds,
                section.duration_seconds + getattr(section, "pause_seconds", 0),
                saved_record.stat().st_size,
            )
            part_report.update({
                "index": index, "source_start_seconds": chunk.start_seconds,
                "source_end_seconds": chunk.end_seconds, "boundary_reason": section.reason,
            })
            reports.append(part_report)
            mixes.append(mixed)
            chunks.append(chunk)
            (job_dir / "quality.json").write_text(
                json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8",
            )
            if on_chunk is not None:
                await on_chunk(SingingDelivery(
                    song, label, chunk, index, len(sections), mode, voice_pitch_shift,
                ))
                delivered = index
        # QQ 已逐段收到；合并文件只用于本地回听和归档。
        concat = job_dir / "mixes.txt"
        concat.write_text("".join(
            f"file 'parts/{index:03d}/mixed.wav'\n" for index in range(1, len(mixes) + 1)
        ), encoding="utf-8")
        full = job_dir / "cover.wav"
        await run_audio_command(
            [ffmpeg, "-v", "error", "-nostdin", "-y", "-f", "concat", "-safe", "1",
             "-i", concat, "-t", f"{duration:.6f}", "-ar", "44100", "-ac", "2",
             "-c:a", "pcm_s16le", "-fs", str(settings.singing_max_source_bytes * 2), full],
        )
        await _validate_model_audio(full, ffprobe, duration, settings)
        return SingingCover(
            song, profile_id, label, full, tuple(chunks), report,
            pitch_shift_semitones=voice_pitch_shift,
            mode=mode, source_start_seconds=selection.start_seconds,
            source_end_seconds=selection.end_seconds,
        )
    except BaseException as exc:
        with suppress(OSError, ValueError):
            await asyncio.to_thread(
                save_failed_singing_diagnostics, job_id, job_dir, profile_id, type(exc).__name__,
            )
        if delivered:
            with suppress(OSError, ValueError):
                await asyncio.to_thread(archive_singing_job, job_id)
        await asyncio.to_thread(cleanup_singing_job, job_id)
        raise
