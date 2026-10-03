"""Song-to-character singing pipeline, separate from conversational TTS."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import math
import re
import shutil
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from app.config import Settings
from app.services.audio_chunks import (
    AudioChunk,
    locate_audio_tool,
    run_audio_command,
    split_audio_for_qq,
)
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
    """Confine job files, including resolved symlinks, to the jobs directory."""
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
    """Keep a bounded number of completed covers for local listening."""
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
    for name in ("cover.wav", "lyrics.lrc", "quality.json", "quality_retry.json", "pitch_plan.json"):
        path = directory / name
        if path.is_file():
            shutil.move(str(path), str(review / name))
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
    """A safe, actionable error that may be shown to a group member."""


@dataclass(frozen=True)
class SingingCommand:
    action: str
    query: str = ""
    profile_id: str | None = None


@dataclass(frozen=True)
class SingingCover:
    song: SingingSong
    profile_id: str
    label: str
    full_path: Path
    chunks: tuple[AudioChunk, ...]
    quality: dict
    pitch_shift_semitones: int = 0


def parse_singing_command(
    text: str, profiles: Mapping[str, Mapping[str, object]], *, addressed: bool
) -> SingingCommand | None:
    """Require a slash command or a real bot mention; keep titles intact."""
    candidate = text.strip()
    controls = {
        "唱歌状态": "status", "翻唱状态": "status",
        "取消唱歌": "cancel", "停止唱歌": "cancel", "取消翻唱": "cancel",
        "唱歌音色": "voices", "翻唱音色": "voices",
    }
    lowered = candidate.removeprefix("/")
    if (addressed or candidate.startswith("/")) and lowered in controls:
        return SingingCommand(controls[lowered])
    prefixes = ("/唱歌", "/翻唱", "/sing", "/cover")
    if addressed:
        prefixes += ("帮我唱", "唱一首", "唱歌", "翻唱")
    for prefix in prefixes:
        if candidate.casefold().startswith(prefix.casefold()):
            if prefix.isascii() and len(candidate) > len(prefix) and not candidate[len(prefix)].isspace():
                continue
            query = candidate[len(prefix):].strip(" ：:")[:160]
            profile_id = None
            # An explicit voice requires a separating space/colon so a title
            # containing a character's name cannot silently lose its prefix.
            match = re.match(r"([^\s:：]+)[\s:：]+(.+)", query, flags=re.DOTALL)
            if match:
                requested = match.group(1)
                profile_id = resolve_voice_persona_alias(requested, profiles)
                if profile_id is None:
                    matches = [
                        profile_id for profile_id, profile in profiles.items()
                        if requested.casefold() in {
                            profile_id.casefold(), str(profile.get("label") or "").casefold()
                        }
                    ]
                    profile_id = matches[0] if len(matches) == 1 else None
                if profile_id:
                    query = match.group(2).strip()
            return SingingCommand("sing", query, profile_id)
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
    # An explicit manual value, including zero, takes precedence.
    _voice_pitch_shift_for_profile(settings, profile_id)
    if profile_id in json.loads(settings.singing_semitone_shift_by_profile_json):
        return None
    value = targets.get(profile_id)
    return float(value) if value is not None else None


def _vocal_mix_gain(report: dict, settings: Settings) -> float:
    rms = report.get("converted_rms")
    if isinstance(rms, bool) or not isinstance(rms, (int, float)) or not math.isfinite(rms) or rms <= 0:
        raise SingingPipelineError("歌声音量检查结果无效，已停止混音。")
    # A constant gain keeps phrasing and dynamics; cap boosts for very quiet
    # tracks rather than amplifying their noise without a bound.
    return max(0.1, min(4.0, settings.singing_vocal_target_rms / rms))


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
        + "\n@我 唱歌状态 / 取消唱歌"
        + "\n完整歌曲自动切为每段不超过 55 秒的语音。"
    )


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
    # Training candidates stay inactive until an operator accepts a sample.
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
    if (
        settings.singing_use_trained_tts_reference
        and profile_id not in real_reference_profiles
        and override is None
    ):
        # The trained speech model produces the timbre reference only. The
        # song's timing and pitch come from the original singing, never TTS.
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
        "--inference-cfg-rate", str(settings.singing_inference_cfg_rate),
        "--semitone-shift", str(semitone_shift),
    ]
    checkpoint = _checkpoint_for_profile(profile_id)
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
         "--expected-semitone-shift", str(expected_semitone_shift),
         *(["--offline"] if settings.singing_hf_offline else [])],
        cwd=seed_root, timeout_seconds=settings.singing_model_timeout_seconds,
    )
    report = json.loads(output.read_text(encoding="utf-8"))
    from app.services.singing_quality import quality_failures

    problems = quality_failures(
        report, max_pitch_median_cents=settings.singing_max_pitch_error_cents,
        min_voice_similarity=settings.singing_min_voice_similarity,
    )
    if problems:
        raise SingingPipelineError("这次歌声未通过音准/音色检查：" + "；".join(problems))
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
            or abs(seconds - expected_seconds) > max(1, expected_seconds * 0.03)):
        raise SingingPipelineError("模型生成的音频时长异常，已停止生成。")


async def generate_singing_cover(
    query: str,
    profile_id: str,
    job_id: str,
    settings: Settings,
    progress: Progress,
    *,
    bot_self_id: str,
) -> SingingCover:
    voice_pitch_shift = _voice_pitch_shift_for_profile(settings, profile_id)
    pitch_target = _automatic_pitch_target(settings, profile_id)
    job_dir = singing_job_directory(job_id)
    python, seed_root, ffmpeg, ffprobe = runtime_paths(settings)
    job_dir.mkdir(parents=True, exist_ok=False)
    try:
        await progress("正在查找原曲和歌词")
        song = await resolve_singing_song(query, settings)
        if song.track.duration_seconds > settings.singing_max_song_seconds:
            raise SingingPipelineError("歌曲超过当前唱歌时长上限，请选择更短的歌曲。")
        source = await download_singing_source(song, job_dir, settings)
        # Decode with a hard limit before GPU work; reject, never silently clip.
        info = json.loads(await run_audio_command(
            [ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "json", source]
        ))
        duration = float(info["format"]["duration"])
        if not 1 <= duration <= settings.singing_max_song_seconds:
            raise SingingPipelineError("歌曲实际时长超出范围，已停止生成。")
        reference = await prepare_voice_reference(profile_id, job_dir, settings, bot_self_id)
        await progress(f"已找到《{song.track.title}》，正在分离原唱人声和伴奏")
        # Separate first, unload Demucs, then run the singing model. Only one
        # GPU task is admitted by SingingJobManager, limiting laptop VRAM.
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
        pitch_plan = {"mode": "fixed", "expected_semitone_shift": voice_pitch_shift}
        if pitch_target is not None:
            await progress("正在为角色选择适合这首歌的音区")
            pitch_plan = await _plan_automatic_pitch(
                vocals, pitch_target, job_dir / "pitch_plan.json", settings
            )
            voice_pitch_shift = pitch_plan["expected_semitone_shift"]
        accompaniment_pitch_shift = _accompaniment_pitch_shift(voice_pitch_shift)
        await progress("正在按原曲节奏和角色音区生成歌声")
        converted = await convert_vocals(
            vocals, reference, job_dir / "converted", profile_id, settings,
            semitone_shift=voice_pitch_shift,
        )
        await _validate_model_audio(converted, ffprobe, duration, settings)
        await progress("正在检查音准、音色和音频完整性")
        quality_path = job_dir / "quality.json"
        try:
            report = await check_cover_quality(
                vocals, converted, reference, quality_path, settings,
                expected_semitone_shift=voice_pitch_shift,
            )
        except SingingPipelineError:
            await progress("正在用更高精度重试歌声转换")
            converted = await convert_vocals(
                vocals, reference, job_dir / "retry", profile_id, settings,
                steps=min(80, settings.singing_diffusion_steps + 15),
                semitone_shift=voice_pitch_shift,
            )
            await _validate_model_audio(converted, ffprobe, duration, settings)
            quality_path = job_dir / "quality_retry.json"
            report = await check_cover_quality(
                vocals, converted, reference, quality_path, settings,
                expected_semitone_shift=voice_pitch_shift,
            )
        report["voice_pitch_shift_semitones"] = voice_pitch_shift
        report["accompaniment_pitch_shift_semitones"] = accompaniment_pitch_shift
        report["pitch_plan"] = pitch_plan
        report["reference_audio"] = str(reference)
        report["has_timed_lyrics"] = bool(song.lyric_lines)
        vocal_gain = _vocal_mix_gain(report, settings)
        report["vocal_mix_gain"] = vocal_gain
        report["vocal_target_rms"] = settings.singing_vocal_target_rms
        report["master_gain"] = settings.singing_master_gain
        quality_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        mix_accompaniment = accompaniment
        if accompaniment_pitch_shift:
            mix_accompaniment = job_dir / "accompaniment_pitch_shifted.wav"
            await run_audio_command(
                [python, PROJECT_ROOT / "scripts" / "transpose_singing_audio.py",
                 "--input", accompaniment, "--output", mix_accompaniment,
                 "--semitones", str(accompaniment_pitch_shift)],
                cwd=seed_root, timeout_seconds=settings.singing_model_timeout_seconds,
            )
            await _validate_model_audio(mix_accompaniment, ffprobe, duration, settings)
        full = job_dir / "cover.wav"
        await run_audio_command(
            [ffmpeg, "-v", "error", "-nostdin", "-y", "-i", converted,
             "-i", mix_accompaniment, "-filter_complex",
             (f"[0:a]volume={vocal_gain:.10f}[v];[1:a]volume=0.85[b];"
              "[v][b]amix=inputs=2:duration=longest:normalize=0,"
              f"alimiter=limit=0.95:latency=1:level=false,volume={settings.singing_master_gain}"),
             "-t", str(duration), "-fs", str(settings.singing_max_source_bytes * 2),
             "-ar", "44100", "-c:a", "pcm_s16le", full],
            timeout_seconds=120,
        )
        await _validate_model_audio(full, ffprobe, duration, settings)
        (job_dir / "lyrics.lrc").write_text(song.lyrics_text, encoding="utf-8")
        await progress("正在按歌词/停顿切分 QQ 语音" if song.lyric_lines else "正在按停顿切分 QQ 语音")
        chunks = await split_audio_for_qq(
            full, job_dir / "chunks", ffmpeg_path=ffmpeg, ffprobe_path=ffprobe,
            max_chunk_seconds=settings.singing_chunk_seconds,
            max_total_seconds=settings.singing_max_song_seconds + 1,
            max_input_bytes=settings.singing_max_source_bytes * 2,
            preferred_boundaries=[line.time_seconds for line in song.lyric_lines],
        )
        label = str(voice_profiles(settings)[profile_id].get("label") or profile_id)
        return SingingCover(
            song, profile_id, label, full, tuple(chunks), report,
            pitch_shift_semitones=voice_pitch_shift,
        )
    except BaseException:
        # All descendants are stopped by run_audio_command before cleanup.
        await asyncio.to_thread(cleanup_singing_job, job_id)
        raise
