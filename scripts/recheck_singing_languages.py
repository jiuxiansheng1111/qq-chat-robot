"""本机三语唱歌复验；只写 acceptance 目录，不接触 QQ。"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import shutil
import sys
import tempfile
import uuid
import wave
from contextlib import ExitStack
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import patch

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.config import Settings
from app.services import singing
from app.services.singing_excerpt import (
    SingingExcerpt,
    _clean_lyrics,
    format_excerpt_lrc,
    select_singing_excerpt,
    shift_excerpt_lyrics,
)
from app.services.singing_gpu import async_gpu_lock
from app.services.singing_sources import (
    SINGING_DATA_ROOT,
    download_singing_source,
    resolve_singing_song,
)
from app.services.voice import voice_profiles

PROFILES = ("murasame", "yoshino", "mako", "aimisi", "lena", "roka", "koharu")
LANGUAGES = {
    "zh": {"query": "朋友的酒DJ版 / 泽亦轩", "song_id": "1939837729", "label": "中文"},
    "ja": {"query": "春泥棒 / ヨルシカ", "song_id": "1810759765", "label": "日语"},
    "en": {"query": "Shape of You / Ed Sheeran", "song_id": "451703096", "label": "英语"},
}
_SECRET_RE = re.compile(
    r"(?i)(token|cookie|authorization|password|api[_-]?key|access[_-]?token)"
    r"(\s*[:=]\s*)[^\s,;&]+"
)
_MIN_SOURCE_VOCAL_RMS = 0.003
_SOURCE_VOCAL_WINDOW_RMS = 0.002
_MIN_SOURCE_VOCAL_SECONDS = 2.5
_MIN_SOURCE_VOCAL_COVERAGE = 0.12
_ACTIVITY_WINDOW_SECONDS = 0.25
_MIN_EXCERPT_LYRIC_LINES = 2
_FALLBACK_SCAN_SECONDS = 30.0


class RecheckError(RuntimeError):
    """复验前置条件或某个单项失败。"""


@dataclass(frozen=True)
class PreparedSource:
    language: str
    song_id: str
    title: str
    artist: str
    source: Path
    excerpt: Path
    vocals: Path
    accompaniment: Path
    lyrics: Path
    start_seconds: float
    end_seconds: float
    lyric_count: int
    separation_model: str
    selection_reason: str = ""
    vocal_activity: dict[str, Any] | None = None
    lrc_timeline_unverified: bool = False

    @property
    def seconds(self) -> float:
        return self.end_seconds - self.start_seconds


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _safe_error(error: BaseException) -> str:
    value = f"{type(error).__name__}: {error}"
    return _safe_text(value)[:1200]


def _safe_text(value: str) -> str:
    return _SECRET_RE.sub(r"\1\2[redacted]", value)


def _diagnostic_tail(error: BaseException) -> str | None:
    value = getattr(error, "diagnostic_tail", "")
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")
    if not isinstance(value, str) or not value:
        return None
    return _safe_text(value)[-4096:]


def _with_diagnostic(
    report: dict[str, Any], error: BaseException, field: str = "diagnostic_tail"
) -> dict[str, Any]:
    tail = _diagnostic_tail(error)
    if tail:
        report[field] = tail
    return report


def _archive_prior_report(
    report_path: Path, old_prefix: str | None = None, new_prefix: str | None = None
) -> None:
    if not report_path.is_file():
        return
    try:
        previous = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        previous = {"status": "unreadable", "report_path": str(report_path)}
    if old_prefix and new_prefix:
        previous = _rewrite_archived_paths(previous, old_prefix, new_prefix)
    attempt_dir = report_path.parent / "attempts"
    attempt_dir.mkdir(parents=True, exist_ok=True)
    attempts = [
        int(match.group(1))
        for path in attempt_dir.glob("attempt-*.json")
        if (match := re.fullmatch(r"attempt-(\d+)\.json", path.name))
    ]
    number = max(attempts, default=0) + 1
    _write_json(attempt_dir / f"attempt-{number:03d}.json", previous)


def _saved_report_status(phase_root: Path, item: dict[str, Any]) -> str | None:
    value = item.get("report")
    if not isinstance(value, str):
        return None
    path = Path(value)
    path = path if path.is_absolute() else PROJECT_ROOT / path
    path = path.resolve()
    if not path.is_relative_to(phase_root.resolve()) or not path.is_file():
        return None
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    return report.get("status") if isinstance(report, dict) else None


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def _relative(path: Path | None) -> str | None:
    if path is None:
        return None
    resolved = path.resolve()
    try:
        return resolved.relative_to(PROJECT_ROOT.resolve()).as_posix()
    except ValueError:
        return str(resolved)


def _valid_audio(path: Path) -> bool:
    return path.is_file() and path.stat().st_size >= 1000


def _vocal_activity_report(path: Path) -> dict[str, Any]:
    """用短窗能量拒绝伴奏/分离残留，避免静音段被算作通过。"""
    with wave.open(str(path), "rb") as audio:
        width = audio.getsampwidth()
        rate = audio.getframerate()
        channels = audio.getnchannels()
        if audio.getcomptype() != "NONE" or width not in (2, 4) or rate < 1 or channels < 1:
            raise ValueError("人声 stem 必须是 16/32 位 PCM WAV")
        dtype = np.dtype("<i2" if width == 2 else "<i4")
        scale = float(1 << (width * 8 - 1))
        frames_per_window = max(1, int(rate * _ACTIVITY_WINDOW_SECONDS))
        total_square = 0.0
        total_samples = 0
        active_frames = 0
        frame_count = 0
        longest_silent_frames = 0
        silent_frames = 0
        while True:
            payload = audio.readframes(frames_per_window)
            if not payload:
                break
            samples = np.frombuffer(payload, dtype=dtype).astype(np.float64) / scale
            if samples.size == 0 or samples.size % channels:
                raise ValueError("人声 stem 的 PCM 数据无效")
            frames = samples.size // channels
            sample_rms = float(np.sqrt(np.mean(np.square(samples))))
            total_square += float(np.dot(samples, samples))
            total_samples += int(samples.size)
            frame_count += frames
            if sample_rms >= _SOURCE_VOCAL_WINDOW_RMS:
                active_frames += frames
                longest_silent_frames = max(longest_silent_frames, silent_frames)
                silent_frames = 0
            else:
                silent_frames += frames
        longest_silent_frames = max(longest_silent_frames, silent_frames)
        if total_samples == 0 or frame_count == 0:
            raise ValueError("人声 stem 没有音频样本")
        duration = frame_count / rate
        rms = float(np.sqrt(total_square / total_samples))
        active_seconds = active_frames / rate
        coverage = active_seconds / duration
        longest_silence = longest_silent_frames / rate
    return {
        "rms": round(rms, 7),
        "active_window_rms_threshold": _SOURCE_VOCAL_WINDOW_RMS,
        "active_seconds": round(active_seconds, 3),
        "active_coverage": round(coverage, 4),
        "longest_silence_seconds": round(longest_silence, 3),
        "minimum_rms": _MIN_SOURCE_VOCAL_RMS,
        "minimum_active_seconds": _MIN_SOURCE_VOCAL_SECONDS,
        "minimum_active_coverage": _MIN_SOURCE_VOCAL_COVERAGE,
        "accepted": (
            rms >= _MIN_SOURCE_VOCAL_RMS
            and active_seconds >= _MIN_SOURCE_VOCAL_SECONDS
            and coverage >= _MIN_SOURCE_VOCAL_COVERAGE
        ),
    }


def _find_cached_original(directory: Path, language: str, settings: Settings) -> Path | None:
    """按 source manifest 的歌曲 ID 找已下载原曲，不重复下同一首。"""
    try:
        manifest = json.loads(_source_manifest_path(directory).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    if not isinstance(manifest, dict):
        return None
    files = manifest.get("files")
    value = files.get("source") if isinstance(files, dict) else None
    if manifest.get("song_id") != LANGUAGES[language]["song_id"] or not isinstance(value, str):
        return None
    relative = Path(value)
    if relative.is_absolute() or ".." in relative.parts:
        return None
    root = directory.resolve()
    source = (directory / relative).resolve()
    if not source.is_relative_to(root) or not source.is_file():
        return None
    size = source.stat().st_size
    if not 1000 <= size <= settings.singing_max_source_bytes:
        return None
    return source


def _source_manifest_path(directory: Path) -> Path:
    return directory / "source.json"


def _load_reusable_source(
    directory: Path, language: str, settings: Settings
) -> PreparedSource | None:
    """只复用清单完整、歌曲和分离模型均匹配的源文件。"""
    manifest_path = _source_manifest_path(directory)
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    if not isinstance(manifest, dict):
        return None
    definition = LANGUAGES[language]
    activity = manifest.get("vocal_activity")
    if (
        manifest.get("status") != "ready"
        or manifest.get("language") != language
        or manifest.get("song_id") != definition["song_id"]
        or manifest.get("separation_model") != settings.singing_separation_model
        or not isinstance(activity, dict)
        or activity.get("accepted") is not True
    ):
        return None
    files = manifest.get("files")
    if not isinstance(files, dict):
        return None
    resolved: dict[str, Path] = {}
    for field in ("source", "excerpt", "vocals", "accompaniment", "lyrics"):
        name = files.get(field)
        if not isinstance(name, str):
            return None
        relative = Path(name)
        if relative.is_absolute() or ".." in relative.parts:
            return None
        path = (directory / relative).resolve()
        if not path.is_relative_to(directory.resolve()):
            return None
        resolved[field] = path
    if not all(_valid_audio(resolved[field]) for field in ("source", "excerpt", "vocals", "accompaniment")):
        return None
    if not resolved["lyrics"].is_file() or resolved["lyrics"].stat().st_size == 0:
        return None
    try:
        start = float(manifest["start_seconds"])
        end = float(manifest["end_seconds"])
        lyric_count = int(manifest["lyric_count"])
    except (KeyError, TypeError, ValueError):
        return None
    if end <= start or not 1 <= lyric_count <= 500:
        return None
    return PreparedSource(
        language=language,
        song_id=definition["song_id"],
        title=str(manifest.get("title") or definition["query"]),
        artist=str(manifest.get("artist") or ""),
        source=resolved["source"],
        excerpt=resolved["excerpt"],
        vocals=resolved["vocals"],
        accompaniment=resolved["accompaniment"],
        lyrics=resolved["lyrics"],
        start_seconds=start,
        end_seconds=end,
        lyric_count=lyric_count,
        separation_model=settings.singing_separation_model,
        selection_reason=str(manifest.get("selection_reason") or ""),
        vocal_activity=activity,
        lrc_timeline_unverified=manifest.get("lrc_timeline_unverified") is True,
    )


def _archive_language_source(output_root: Path, language: str) -> Path | None:
    """把旧源目录移到同一 runroot 的 previous-N，绝不递归删除。"""
    run_root = output_root.resolve()
    sources_root = (run_root / "sources").resolve()
    if not sources_root.is_relative_to(run_root):
        raise RecheckError("sources 目录解析到了 runroot 外")
    source_directory = sources_root / language
    if source_directory.is_symlink():
        raise RecheckError(f"拒绝归档符号链接源目录：{language}")
    if not source_directory.exists():
        return None
    resolved_source = source_directory.resolve()
    if not resolved_source.is_relative_to(sources_root) or resolved_source.parent != sources_root:
        raise RecheckError(f"{language} 源目录解析到了 runroot 外")
    index = 1
    while True:
        destination = sources_root / f"{language}-previous-{index}"
        resolved_destination = destination.resolve()
        if not resolved_destination.is_relative_to(sources_root):
            raise RecheckError("previous 源目录解析到了 runroot 外")
        if not destination.exists():
            os.replace(source_directory, destination)
            return destination
        index += 1


def _rewrite_archived_paths(value: Any, old_prefix: str, new_prefix: str) -> Any:
    if isinstance(value, str):
        return value.replace(old_prefix, new_prefix)
    if isinstance(value, list):
        return [_rewrite_archived_paths(item, old_prefix, new_prefix) for item in value]
    if isinstance(value, dict):
        return {
            key: _rewrite_archived_paths(item, old_prefix, new_prefix)
            for key, item in value.items()
        }
    return value


async def _prepare_source(
    language: str,
    directory: Path,
    settings: Settings,
    original_source: Path | None = None,
) -> PreparedSource:
    reused = _load_reusable_source(directory, language, settings)
    if reused is not None:
        return reused

    directory.mkdir(parents=True, exist_ok=True)
    definition = LANGUAGES[language]
    song = await resolve_singing_song(definition["query"], settings)
    if song.track.song_id != definition["song_id"]:
        raise RecheckError(
            f"网易云匹配到歌曲 {song.track.song_id}，期望 {definition['song_id']}；请检查搜索结果"
        )
    python, seed_root, ffmpeg, ffprobe = singing.runtime_paths(settings)
    if original_source is None:
        original_source = _find_cached_original(directory, language, settings)
    if original_source is None:
        source = await download_singing_source(song, directory, settings)
    else:
        source = original_source.resolve()
        if not source.is_relative_to((directory.parent.parent).resolve()):
            raise RecheckError("复用原曲路径必须位于本轮 runroot 内")
        if not source.is_file() or not 1000 <= source.stat().st_size <= settings.singing_max_source_bytes:
            raise RecheckError("归档原曲不存在或文件大小无效")
    local_destination = directory / f"original{source.suffix.lower()}"
    if source.resolve() != local_destination.resolve():
        local_destination.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(shutil.copy2, source, local_destination)
        source = local_destination
    source_info = json.loads(await singing.run_audio_command(
        [ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "json", source]
    ))
    duration = float(source_info["format"]["duration"])
    if not 1 <= duration <= settings.singing_max_song_seconds:
        raise RecheckError("原曲实际时长超出设置范围")
    expected = song.track.duration_seconds
    if expected > 0 and abs(duration - expected) > max(2, expected * 0.03):
        raise RecheckError("原曲文件时长与网易云歌曲信息不一致")
    valid_lines = _clean_lyrics(
        song.lyric_lines,
        duration,
        ignore_texts=(song.track.title, *song.track.artists),
        song_title=song.track.title,
        song_artists=song.track.artists,
    )
    if not valid_lines:
        raise RecheckError("原曲没有清理后可用的定时歌词，无法选择有人声的复验片段")

    initial = select_singing_excerpt(
        valid_lines,
        duration,
        20,
        settings.singing_chunk_seconds,
        ignore_texts=(song.track.title, *song.track.artists),
        song_title=song.track.title,
        song_artists=song.track.artists,
    )
    candidates: list[SingingExcerpt] = []
    seen_starts: set[int] = set()

    def add_candidate(start_seconds: float, reason: str) -> None:
        start = max(0.0, float(start_seconds))
        end = min(duration, start + 20.0)
        if end - start < 15.0:
            return
        key = round(start * 1000)
        if key in seen_starts:
            return
        line_count = sum(start <= line.time_seconds < end for line in valid_lines)
        if line_count < _MIN_EXCERPT_LYRIC_LINES:
            return
        seen_starts.add(key)
        candidates.append(SingingExcerpt(start, end, reason))

    add_candidate(initial.start_seconds, initial.reason)
    scan_start = max(_FALLBACK_SCAN_SECONDS, initial.start_seconds + _FALLBACK_SCAN_SECONDS)
    for start in range(int(scan_start // _FALLBACK_SCAN_SECONDS * _FALLBACK_SCAN_SECONDS), int(duration), int(_FALLBACK_SCAN_SECONDS)):
        add_candidate(float(start), "每30秒扫描有人声歌词片段")
    # 每个扫描区间最多补一个歌词起点，避免密集 LRC 触发大量重复分离。
    bucket_start = scan_start
    while bucket_start < duration:
        bucket_lines = [
            line for line in valid_lines
            if bucket_start <= line.time_seconds < bucket_start + _FALLBACK_SCAN_SECONDS
        ]
        if bucket_lines:
            add_candidate(max(0.0, bucket_lines[0].time_seconds - 0.1), "按后续歌词时间重选")
        bucket_start += _FALLBACK_SCAN_SECONDS

    if not candidates:
        raise RecheckError("原曲歌词时间轴内找不到至少两句、长度15秒以上的候选片段")

    attempts: list[dict[str, Any]] = []
    chosen: tuple[SingingExcerpt, int, dict[str, Any], Path] | None = None
    for attempt_number, selection in enumerate(candidates, start=1):
        excerpt_lines = tuple(
            line for line in valid_lines
            if selection.start_seconds <= line.time_seconds < selection.end_seconds
        )
        attempt: dict[str, Any] = {
            "start_seconds": selection.start_seconds,
            "end_seconds": selection.end_seconds,
            "lyric_count": len(excerpt_lines),
            "selection_reason": selection.reason,
        }
        attempts.append(attempt)
        try:
            with tempfile.TemporaryDirectory(prefix="singing-source-trial-", dir=directory) as temp_name:
                temp_dir = Path(temp_name)
                excerpt_trial = temp_dir / "excerpt.wav"
                await singing.run_audio_command(
                    [ffmpeg, "-v", "error", "-nostdin", "-y", "-i", source,
                     "-ss", f"{selection.start_seconds:.6f}",
                     "-t", f"{selection.duration_seconds:.6f}",
                     "-ar", "44100", "-ac", "2", "-c:a", "pcm_s16le", excerpt_trial],
                )
                if not _valid_audio(excerpt_trial):
                    raise RecheckError("原曲候选片段文件未生成或大小异常")
                separated_trial = temp_dir / "separated"
                async with async_gpu_lock():
                    await singing.run_audio_command(
                        [python, PROJECT_ROOT / "scripts" / "run_singing_separation.py",
                         "--ffmpeg", ffmpeg, "--source", excerpt_trial, "--output", separated_trial,
                         "--model", settings.singing_separation_model],
                        cwd=seed_root,
                        timeout_seconds=settings.singing_model_timeout_seconds,
                    )
                stem_dir = separated_trial / settings.singing_separation_model / excerpt_trial.stem
                vocals_trial = stem_dir / "vocals.wav"
                accompaniment_trial = stem_dir / "no_vocals.wav"
                await singing._validate_model_audio(vocals_trial, ffprobe, selection.duration_seconds, settings)
                await singing._validate_model_audio(accompaniment_trial, ffprobe, selection.duration_seconds, settings)
                activity = _vocal_activity_report(vocals_trial)
                attempt["vocal_activity"] = activity
                if activity["accepted"]:
                    chosen = (selection, len(excerpt_lines), activity, temp_dir)
                    excerpt = directory / "excerpt.wav"
                    vocals = directory / "separated" / settings.singing_separation_model / "excerpt" / "vocals.wav"
                    accompaniment = directory / "separated" / settings.singing_separation_model / "excerpt" / "no_vocals.wav"
                    excerpt.parent.mkdir(parents=True, exist_ok=True)
                    vocals.parent.mkdir(parents=True, exist_ok=True)
                    await asyncio.to_thread(shutil.copy2, excerpt_trial, excerpt)
                    await asyncio.to_thread(shutil.copy2, vocals_trial, vocals)
                    await asyncio.to_thread(shutil.copy2, accompaniment_trial, accompaniment)
            if chosen is not None:
                break
        except Exception as exc:  # noqa: BLE001 - 继续扫描后续歌词窗口。
            attempt["error"] = _safe_error(exc)
            tail = _diagnostic_tail(exc)
            if tail:
                attempt["diagnostic_tail"] = tail

    if chosen is None:
        error = RecheckError("扫描的歌词候选片段都没有足够的人声能量")
        error.diagnostic_tail = json.dumps(attempts[-12:], ensure_ascii=False)[-4096:]
        raise error

    selection, lyric_count, activity, _ = chosen
    rescanned = bool(attempts[0]["start_seconds"] != initial.start_seconds or len(attempts) > 1)
    selection_reason = selection.reason
    if rescanned:
        selection_reason = f"音频重选，LRC时轴待ASR核验；{selection.reason}"
    excerpt = directory / "excerpt.wav"
    vocals = directory / "separated" / settings.singing_separation_model / "excerpt" / "vocals.wav"
    accompaniment = directory / "separated" / settings.singing_separation_model / "excerpt" / "no_vocals.wav"
    shifted_lines = shift_excerpt_lyrics(valid_lines, selection)
    lyrics = directory / "excerpt.lrc"
    lyrics.write_text(format_excerpt_lrc(shifted_lines), encoding="utf-8")
    prepared = PreparedSource(
        language=language,
        song_id=song.track.song_id,
        title=song.track.title,
        artist=song.track.artist,
        source=source,
        excerpt=excerpt,
        vocals=vocals,
        accompaniment=accompaniment,
        lyrics=lyrics,
        start_seconds=selection.start_seconds,
        end_seconds=selection.end_seconds,
        lyric_count=lyric_count,
        separation_model=settings.singing_separation_model,
        selection_reason=selection_reason,
        vocal_activity=activity,
        lrc_timeline_unverified=rescanned,
    )
    _write_json(_source_manifest_path(directory), {
        "status": "ready",
        "language": language,
        "song_id": song.track.song_id,
        "query": definition["query"],
        "title": song.track.title,
        "artist": song.track.artist,
        "selection_reason": selection_reason,
        "start_seconds": selection.start_seconds,
        "end_seconds": selection.end_seconds,
        "lyric_count": lyric_count,
        "separation_model": settings.singing_separation_model,
        "vocal_activity": activity,
        "lrc_timeline_unverified": rescanned,
        "audio_rescue_lrc_timeline_unverified": rescanned,
        "selection_attempts": attempts,
        "files": {
            "source": source.name,
            "excerpt": excerpt.name,
            "vocals": vocals.relative_to(directory).as_posix(),
            "accompaniment": accompaniment.relative_to(directory).as_posix(),
            "lyrics": lyrics.name,
        },
        "prepared_at_utc": _now(),
    })
    return prepared


def _load_candidate_entry(registry_path: Path, profile_id: str) -> tuple[Path, Path]:
    """候选缺失或无效时失败关闭，绝不切回 accepted/base。"""
    if not registry_path.is_file() or registry_path.stat().st_size > 1024 * 1024:
        raise RecheckError("candidate registry 不存在或大小异常")
    try:
        registry = json.loads(registry_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RecheckError(f"candidate registry 无法读取：{_safe_error(exc)}") from exc
    entry = registry.get(profile_id) if isinstance(registry, dict) else None
    if not isinstance(entry, dict):
        raise RecheckError(f"candidate registry 缺少 {profile_id} 条目")
    if entry.get("accepted") is True or entry.get("status") == "accepted":
        raise RecheckError(f"{profile_id} 条目已标为 accepted，拒绝当作待复验候选")
    result: list[Path] = []
    for field in ("checkpoint", "config"):
        value = entry.get(field)
        if not isinstance(value, str) or not value.strip():
            raise RecheckError(f"{profile_id} 候选缺少 {field}")
        path = Path(value).expanduser()
        path = (PROJECT_ROOT / path).resolve() if not path.is_absolute() else path.resolve()
        if not path.is_file():
            raise RecheckError(f"{profile_id} 候选 {field} 文件不存在：{_relative(path)}")
        result.append(path)
    return result[0], result[1]


def _candidate_registry_path() -> Path:
    return PROJECT_ROOT / "data" / "singing" / "candidates.json"


async def _make_reference(
    profile_id: str, profile_directory: Path, settings: Settings, ffmpeg: Path
) -> tuple[Path, Path]:
    output = profile_directory / "reference.wav"
    metadata_path = profile_directory / "reference.json"
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        metadata = None
    if (
        _valid_audio(output)
        and isinstance(metadata, dict)
        and metadata.get("settings_based_reference") is True
        and metadata.get("normalized_audio") == _relative(output)
        and isinstance(metadata.get("source_audio"), str)
        and metadata.get("max_seconds") == settings.singing_reference_max_seconds
        and metadata.get("reference_settings") == {
            "overrides": settings.singing_reference_audio_by_profile_json,
            "profiles": settings.voice_profiles_json,
            "prefer_recorded": settings.singing_prefer_recorded_reference,
            "use_trained_tts": settings.singing_use_trained_tts_reference,
            "real_reference_ids": settings.singing_real_reference_profile_ids,
        }
    ):
        source_value = Path(metadata["source_audio"])
        source = source_value if source_value.is_absolute() else PROJECT_ROOT / source_value
        if source.is_file() and metadata.get("source_size") == source.stat().st_size \
                and metadata.get("source_mtime_ns") == source.stat().st_mtime_ns:
            return output, source
    raw = await singing.prepare_voice_reference(
        profile_id, profile_directory / "reference_work", settings, settings.onebot_self_id
    )
    await singing.run_audio_command(
        [ffmpeg, "-v", "error", "-nostdin", "-y", "-i", raw,
         "-t", str(settings.singing_reference_max_seconds), "-ar", "44100",
         "-ac", "1", "-c:a", "pcm_s16le", output],
    )
    if not _valid_audio(output):
        raise RecheckError("角色参考音频未生成或大小异常")
    _write_json(profile_directory / "reference.json", {
        "source_audio": _relative(raw),
        "normalized_audio": _relative(output),
        "settings_based_reference": True,
        "max_seconds": settings.singing_reference_max_seconds,
        "source_size": raw.stat().st_size,
        "source_mtime_ns": raw.stat().st_mtime_ns,
        "reference_settings": {
            "overrides": settings.singing_reference_audio_by_profile_json,
            "profiles": settings.voice_profiles_json,
            "prefer_recorded": settings.singing_prefer_recorded_reference,
            "use_trained_tts": settings.singing_use_trained_tts_reference,
            "real_reference_ids": settings.singing_real_reference_profile_ids,
        },
    })
    return output, raw


async def _save_failed_previews(
    vocals: Path, item_directory: Path, ffmpeg: Path
) -> None:
    before = item_directory / "vocal_before.wav"
    if not before.exists() and vocals.is_file():
        await singing.run_audio_command(
            [ffmpeg, "-v", "error", "-nostdin", "-y", "-i", vocals,
             "-t", "20", "-ar", "32000", "-ac", "1", "-c:a", "pcm_s16le", before],
        )
    after = item_directory / "vocal_after.wav"
    if after.exists():
        return
    candidates = [
        path
        for area in (item_directory / "parts" / "001" / "retry", item_directory / "parts" / "001" / "converted")
        for path in area.glob("*.wav")
        if path.is_file()
    ]
    candidates.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    converted = next((path for path in candidates if path != before), None)
    if converted is not None:
        await singing.run_audio_command(
            [ffmpeg, "-v", "error", "-nostdin", "-y", "-i", converted,
             "-t", "20", "-ar", "32000", "-ac", "1", "-c:a", "pcm_s16le", after],
        )


async def _run_item(
    profile_id: str,
    language: str,
    source: PreparedSource,
    profile_directory: Path,
    reference: Path,
    reference_source: Path,
    checkpoint: tuple[Path, Path] | None,
    settings: Settings,
    ffmpeg: Path,
    ffprobe: Path,
    steps: int | None,
) -> dict[str, Any]:
    item_directory = profile_directory / language
    item_directory.mkdir(parents=True, exist_ok=True)
    report_path = item_directory / "report.json"
    report: dict[str, Any] = {
        "status": "running",
        "profile_id": profile_id,
        "language": language,
        "language_label": LANGUAGES[language]["label"],
        "song_id": source.song_id,
        "song_title": source.title,
        "song_artist": source.artist,
        "source": _relative(source.source),
        "excerpt": _relative(source.excerpt),
        "vocals": _relative(source.vocals),
        "lyrics": _relative(source.lyrics),
        "source_start_seconds": source.start_seconds,
        "source_end_seconds": source.end_seconds,
        "lyric_count": source.lyric_count,
        "selection_reason": source.selection_reason,
        "vocal_activity": source.vocal_activity,
        "lrc_timeline_unverified": source.lrc_timeline_unverified,
        "audio_rescue_lrc_timeline_unverified": source.lrc_timeline_unverified,
        "phase_checkpoint": [_relative(path) for path in checkpoint] if checkpoint else None,
        "reference_audio": _relative(reference),
        "reference_audio_source": _relative(reference_source),
        "diffusion_steps_override": steps,
        "started_at_utc": _now(),
    }
    _write_json(report_path, report)
    try:
        activity = _vocal_activity_report(source.vocals)
        report["vocal_activity"] = activity
        if not activity["accepted"]:
            raise RecheckError("拒绝复验：源人声能量或有声覆盖不足，不能以伴奏通过")
        voice_shift = singing._voice_pitch_shift_for_profile(settings, profile_id)
        pitch_target = singing._automatic_pitch_target(settings, profile_id)
        pitch_plan: dict[str, Any] = {"mode": "fixed", "expected_semitone_shift": voice_shift}
        if pitch_target is not None:
            pitch_plan = await singing._plan_automatic_pitch(
                source.vocals,
                pitch_target,
                item_directory / "pitch_plan.json",
                settings,
            )
            voice_shift = pitch_plan["expected_semitone_shift"]
        part_directory = item_directory / "parts" / "001"

        async def progress(message: str) -> None:
            print(f"[{profile_id}/{language}] {message}", flush=True)

        original_convert = singing.convert_vocals

        async def fixed_step_convert(*args: Any, **kwargs: Any) -> Path:
            if steps is not None:
                kwargs["steps"] = steps
            return await original_convert(*args, **kwargs)

        def selected_checkpoint(selected_profile: str) -> tuple[Path, Path] | None:
            if selected_profile == profile_id and checkpoint is not None:
                return checkpoint
            return original_checkpoint(selected_profile)

        original_checkpoint = singing._checkpoint_for_profile
        with ExitStack() as stack:
            if checkpoint is not None:
                stack.enter_context(patch.object(singing, "_checkpoint_for_profile", selected_checkpoint))
            if steps is not None:
                stack.enter_context(patch.object(singing, "convert_vocals", fixed_step_convert))
            async with async_gpu_lock():
                mixed, record, quality = await singing._render_singing_section(
                    source.vocals,
                    source.accompaniment,
                    reference,
                    part_directory,
                    profile_id,
                    source.seconds,
                    voice_shift,
                    settings,
                    ffmpeg,
                    ffprobe,
                    progress,
                )
        instrumental = quality.get("kind") == "instrumental"
        report.update({
            "status": "passed" if quality.get("accepted") is True and not instrumental else "failed",
            "pitch_plan": pitch_plan,
            "voice_pitch_shift_semitones": voice_shift,
            "accompaniment_pitch_shift_semitones": quality.get("accompaniment_pitch_shift_semitones"),
            "quality": quality,
            "quality_report": _relative(part_directory / "quality.json"),
            "mixed_audio": _relative(mixed),
            "record_audio": _relative(record),
            "vocal_before": _relative(item_directory / "vocal_before.wav"),
            "vocal_after": _relative(item_directory / "vocal_after.wav"),
        })
        if instrumental:
            report["error"] = "渲染器将该片段标记为 instrumental，不能算唱歌通过"
    except Exception as exc:  # noqa: BLE001 - 每项落盘，之后继续其他歌曲和角色。
        report.update({"status": "failed", "error": _safe_error(exc)})
        _with_diagnostic(report, exc)
        report["quality_reports"] = [
            _relative(path)
            for path in (item_directory / "parts" / "001" / "quality.json",
                         item_directory / "parts" / "001" / "quality_retry.json")
            if path.is_file()
        ]
        try:
            await _save_failed_previews(source.vocals, item_directory, ffmpeg)
        except Exception as preview_error:  # noqa: BLE001 - 预览故障也写入单项报告。
            report["preview_error"] = _safe_error(preview_error)
            _with_diagnostic(report, preview_error, "preview_diagnostic_tail")
        report["vocal_before"] = _relative(item_directory / "vocal_before.wav")
        report["vocal_after"] = _relative(item_directory / "vocal_after.wav")
    report["finished_at_utc"] = _now()
    _write_json(report_path, report)
    return report


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="本机三语唱歌基线/候选复验")
    parser.add_argument("--phase", choices=("baseline", "candidate"), default="baseline")
    parser.add_argument("--run-id", help="供 baseline/candidate 共用同一份源音频")
    parser.add_argument(
        "--retry-failed", "--resume", dest="retry_failed", action="store_true",
        help="只重跑已结束 phase 中失败或待处理项",
    )
    parser.add_argument(
        "--refresh-language", "--reprepare-language", dest="refresh_languages",
        action="append", choices=tuple(LANGUAGES),
        help="重做此语言的全部 profile，并把旧源留在 sources/<lang>-previous-N",
    )
    parser.add_argument("--profile", choices=PROFILES, help="只复验一个角色")
    parser.add_argument("--language", choices=tuple(LANGUAGES), help="只复验一种语言")
    parser.add_argument("--steps", type=int, choices=(35, 50), help="只用于单个 profile/language 的 A/B")
    args = parser.parse_args(argv)
    if args.steps is not None and (args.profile is None or args.language is None):
        parser.error("--steps 仅用于单项 A/B，请同时指定 --profile 和 --language")
    if args.retry_failed and not args.run_id:
        parser.error("--retry-failed 需要指定原来的 --run-id")
    if args.refresh_languages and not args.retry_failed:
        parser.error("--refresh-language 需要配合 --retry-failed 使用")
    if args.language and args.refresh_languages and args.language not in args.refresh_languages:
        parser.error("--language 必须包含在 --refresh-language 指定范围内")
    return args


def _new_run_id(value: str | None) -> str:
    if value is None:
        return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", value) or value in {".", ".."}:
        raise RecheckError("--run-id 只接受 1-64 位字母、数字、点、下划线和连字符")
    return value


def _phase_root(args: argparse.Namespace) -> Path:
    run_id = _new_run_id(args.run_id)
    output_root = SINGING_DATA_ROOT / "acceptance" / run_id
    phase_name = f"{args.phase}-steps{args.steps}" if args.steps is not None else args.phase
    return output_root / phase_name


async def run_phase(args: argparse.Namespace, settings: Settings) -> Path:
    if not getattr(args, "retry_failed", False):
        return await _run_phase_impl(args, settings)
    if not args.run_id:
        raise RecheckError("--retry-failed 需要指定原来的 --run-id")
    phase_root = _phase_root(args)
    manifest_path = phase_root / "manifest.json"
    if not phase_root.is_dir() or not manifest_path.is_file():
        raise RecheckError(f"找不到可重试的 phase：{_relative(phase_root)}")
    marker = phase_root / ".retry-running"
    try:
        with marker.open("x", encoding="utf-8") as stream:
            stream.write(f"pid={os.getpid()} started={_now()}\n")
            stream.flush()
            os.fsync(stream.fileno())
    except FileExistsError as exc:
        raise RecheckError("该 phase 已有 retry 进程；确认它结束后再重试") from exc
    try:
        return await _run_phase_impl(args, settings)
    finally:
        marker.unlink(missing_ok=True)


async def _run_phase_impl(args: argparse.Namespace, settings: Settings) -> Path:
    run_id = _new_run_id(args.run_id)
    output_root = SINGING_DATA_ROOT / "acceptance" / run_id
    phase_name = f"{args.phase}-steps{args.steps}" if args.steps is not None else args.phase
    phase_root = output_root / phase_name
    # 已有 phase 永不覆盖；baseline 与 candidate 各用一个独立目录。
    retrying = getattr(args, "retry_failed", False)
    refresh_languages = set(getattr(args, "refresh_languages", None) or ())
    if retrying:
        if not phase_root.is_dir():
            raise RecheckError(f"找不到可重试的 phase：{_relative(phase_root)}")
        manifest_path = phase_root / "manifest.json"
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise RecheckError(f"phase manifest 无法读取：{_safe_error(exc)}") from exc
        if not isinstance(manifest, dict) or not isinstance(manifest.get("items"), dict):
            raise RecheckError("phase manifest 格式无效")
        if manifest.get("status") == "running":
            raise RecheckError("该 phase 仍标记为 running；等待原任务结束后再重试")
        if manifest.get("status") not in {"passed", "completed_with_failures"}:
            raise RecheckError(f"phase 状态不可重试：{manifest.get('status')!r}")
        if manifest.get("run_id") != run_id or manifest.get("phase") != args.phase:
            raise RecheckError("phase manifest 与请求的 run-id/phase 不一致")
        if manifest.get("steps_override") != args.steps:
            raise RecheckError("phase 的 --steps 与重试请求不一致")
        existing_items = manifest["items"]
        requested_profiles = {args.profile} if args.profile else None
        requested_languages = {args.language} if args.language else None
        for key, item in existing_items.items():
            if not isinstance(key, str) or not isinstance(item, dict):
                raise RecheckError("phase manifest 的 items 条目无效")
            if item.get("status") == "running":
                raise RecheckError(f"phase 中 {key} 仍标记为 running，拒绝并发重试")
        keys = []
        for key, item in existing_items.items():
            profile_id, separator, language = key.partition(":")
            if not separator or profile_id not in PROFILES or language not in LANGUAGES:
                raise RecheckError(f"phase manifest 的 item key 无效：{key!r}")
            if requested_profiles is not None and profile_id not in requested_profiles:
                continue
            if requested_languages is not None and language not in requested_languages:
                continue
            force_refresh = language in refresh_languages
            if not force_refresh and item.get("status") not in {"failed", "pending", "queued"}:
                continue
            if not force_refresh and _saved_report_status(phase_root, item) == "passed":
                item["status"] = "passed"
                continue
            keys.append(key)
        if not keys:
            raise RecheckError("phase 中没有匹配的 failed/pending 或 refresh-language 项可重试")
        profiles = tuple(dict.fromkeys(key.split(":", 1)[0] for key in keys))
        languages = tuple(dict.fromkeys(key.split(":", 1)[1] for key in keys))
        manifest["status"] = "running"
        manifest["retry_started_at_utc"] = _now()
        manifest["retry_progress"] = {"completed": 0, "total": len(keys)}
        if refresh_languages:
            manifest["refresh_languages"] = sorted(refresh_languages)
        manifest_path = phase_root / "manifest.json"
    else:
        phase_root.mkdir(parents=True, exist_ok=False)
        profiles = (args.profile,) if args.profile else PROFILES
        languages = (args.language,) if args.language else tuple(LANGUAGES)
        keys = [f"{profile_id}:{language}" for profile_id in profiles for language in languages]
        manifest = {
            "status": "running",
            "run_id": run_id,
            "phase": args.phase,
            "steps_override": args.steps,
            "started_at_utc": _now(),
            "output_root": _relative(output_root),
            "sources_root": _relative(output_root / "sources"),
            "items": {key: {"status": "queued"} for key in keys},
        }
        manifest_path = phase_root / "manifest.json"
    sources_root = output_root / "sources"
    _write_json(manifest_path, manifest)

    source_archives: dict[str, Path] = {}
    refreshed_originals: dict[str, Path | None] = {}
    source_refresh_errors: dict[str, BaseException] = {}
    for language in refresh_languages:
        try:
            archive = _archive_language_source(output_root, language)
            if archive is not None:
                source_archives[language] = archive
                refreshed_originals[language] = _find_cached_original(archive, language, settings)
                manifest.setdefault("source_archives", {})[language] = _relative(archive)
            else:
                refreshed_originals[language] = None
                manifest.setdefault("source_archives", {})[language] = None
        except Exception as exc:  # noqa: BLE001 - 路径安全错误记录为该语言失败。
            source_refresh_errors[language] = exc
            refreshed_originals[language] = None
            manifest.setdefault("source_archives", {})[language] = None
            manifest.setdefault("source_errors", {})[language] = _safe_error(exc)
            tail = _diagnostic_tail(exc)
            if tail:
                manifest.setdefault("source_diagnostic_tails", {})[language] = tail
    if refresh_languages:
        _write_json(manifest_path, manifest)

    try:
        _, _, ffmpeg, ffprobe = singing.runtime_paths(settings)
    except Exception as exc:  # noqa: BLE001 - 将前置错误写入逐项报告。
        error = _safe_error(exc)
        for key in keys:
            profile_id, language = key.split(":", 1)
            item_dir = phase_root / profile_id / language
            if retrying:
                archive = source_archives.get(language)
                _archive_prior_report(
                    item_dir / "report.json",
                    _relative(sources_root / language) if archive else None,
                    _relative(archive) if archive else None,
                )
            report = {"status": "failed", "profile_id": profile_id, "language": language,
                      "error": error}
            _with_diagnostic(report, exc)
            _write_json(item_dir / "report.json", report)
            manifest["items"][key] = {
                "status": "failed",
                "report": _relative(item_dir / "report.json"),
                "error": error,
                "diagnostic_tail": report.get("diagnostic_tail"),
            }
        manifest.update({
            "status": "completed_with_failures",
            "finished_at_utc": _now(),
            "summary": {
                "passed": sum(item.get("status") == "passed" for item in manifest["items"].values()),
                "failed": sum(item.get("status") != "passed" for item in manifest["items"].values()),
                "total": len(manifest["items"]),
            },
        })
        if retrying:
            manifest["retry_finished_at_utc"] = _now()
        _write_json(manifest_path, manifest)
        return phase_root

    source_results: dict[str, PreparedSource | BaseException] = {}
    for language in languages:
        force_refresh = language in refresh_languages
        was_reusable = (
            not force_refresh
            and _load_reusable_source(sources_root / language, language, settings) is not None
        )
        manifest.setdefault("source_progress", {})[language] = "reusing" if was_reusable else "preparing"
        _write_json(manifest_path, manifest)
        print(f"[source/{language}] {'reusing' if was_reusable else 'preparing'}", flush=True)
        try:
            if language in source_refresh_errors:
                raise source_refresh_errors[language]
            source_results[language] = await _prepare_source(
                language, sources_root / language, settings,
                refreshed_originals.get(language) if force_refresh else None,
            )
            manifest.get("source_errors", {}).pop(language, None)
            manifest.get("source_diagnostic_tails", {}).pop(language, None)
            status = "reused" if was_reusable else "prepared"
            print(f"[source/{language}] {status}", flush=True)
        except Exception as exc:  # noqa: BLE001 - 逐首记录解析、网络和本地文件错误。
            source_results[language] = exc
            manifest.setdefault("source_errors", {})[language] = _safe_error(exc)
            tail = _diagnostic_tail(exc)
            if tail:
                manifest.setdefault("source_diagnostic_tails", {})[language] = tail
            print(f"[source/{language}] failed: {_safe_error(exc)}", flush=True)
        manifest.setdefault("sources", {}).update({
            lang: {
                "status": "ready" if isinstance(result, PreparedSource) else "failed",
                "source": _relative(result.source) if isinstance(result, PreparedSource) else None,
                "excerpt": _relative(result.excerpt) if isinstance(result, PreparedSource) else None,
                "vocal_activity": result.vocal_activity if isinstance(result, PreparedSource) else None,
                "selection_reason": result.selection_reason if isinstance(result, PreparedSource) else None,
                "lrc_timeline_unverified": result.lrc_timeline_unverified if isinstance(result, PreparedSource) else None,
                "audio_rescue_lrc_timeline_unverified": result.lrc_timeline_unverified if isinstance(result, PreparedSource) else None,
                "error": _safe_error(result) if isinstance(result, BaseException) else None,
            }
            for lang, result in source_results.items()
        })
        _write_json(manifest_path, manifest)

    candidate_registry = _candidate_registry_path()
    completed = 0
    total = len(keys)
    for profile_id in profiles:
        profile_directory = phase_root / profile_id
        profile_directory.mkdir(parents=True, exist_ok=True)
        checkpoint: tuple[Path, Path] | None = None
        checkpoint_error: BaseException | None = None
        model_settings = settings
        try:
            if args.phase == "candidate":
                checkpoint = _load_candidate_entry(candidate_registry, profile_id)
                # 候选复验必须使用候选权重，不能被基础模型选项跳过。
                model_settings = settings.model_copy(update={
                    "singing_base_model_profile_ids": ",".join(
                        value.strip() for value in settings.singing_base_model_profile_ids.split(",")
                        if value.strip() and value.strip() != profile_id
                    ),
                })
            else:
                checkpoint = singing.singing_checkpoint(profile_id, settings)
        except Exception as exc:  # noqa: BLE001 - 各 profile 独立失败并继续。
            checkpoint_error = exc

        reference: Path | None = None
        reference_source: Path | None = None
        reference_error: BaseException | None = None
        if checkpoint_error is None:
            try:
                available_profiles = voice_profiles(settings)
                if profile_id not in available_profiles:
                    raise RecheckError(f"settings 未配置 {profile_id} 音色")
                reference, reference_source = await _make_reference(
                    profile_id, profile_directory, settings, ffmpeg
                )
            except Exception as exc:  # noqa: BLE001 - 单个参考音频失败不阻断其他 profile。
                reference_error = exc

        for language in languages:
            key = f"{profile_id}:{language}"
            if key not in keys:
                continue
            completed += 1
            result_source = source_results[language]
            report_file = profile_directory / language / "report.json"
            if retrying:
                archive = source_archives.get(language)
                old_prefix = _relative(sources_root / language) if archive else None
                new_prefix = _relative(archive) if archive else None
                _archive_prior_report(report_file, old_prefix, new_prefix)
            manifest["items"][key] = {
                "status": "running",
                "report": _relative(report_file),
            }
            manifest["progress"] = {"completed": completed - 1, "total": total}
            if retrying:
                manifest["retry_progress"] = {"completed": completed - 1, "total": total}
            _write_json(manifest_path, manifest)
            print(f"[{args.phase} {completed}/{total}] {key}: running", flush=True)
            if not isinstance(result_source, PreparedSource):
                item_errors = [f"source preparation failed: {_safe_error(result_source)}"]
                if checkpoint_error is not None:
                    item_errors.append(f"checkpoint selection failed: {_safe_error(checkpoint_error)}")
                report = {
                    "status": "failed", "profile_id": profile_id, "language": language,
                    "error": "; ".join(item_errors),
                }
                _with_diagnostic(report, result_source)
                if checkpoint_error is not None:
                    _with_diagnostic(report, checkpoint_error, "checkpoint_diagnostic_tail")
                _write_json(profile_directory / language / "report.json", report)
            elif checkpoint_error is not None:
                report = {
                    "status": "failed", "profile_id": profile_id, "language": language,
                    "song_id": result_source.song_id,
                    "error": _safe_error(checkpoint_error),
                    "phase_checkpoint": [_relative(path) for path in checkpoint] if checkpoint else None,
                    "raw_vocal_source": _relative(result_source.vocals),
                }
                _with_diagnostic(report, checkpoint_error)
                item_dir = profile_directory / language
                item_dir.mkdir(parents=True, exist_ok=True)
                try:
                    await _save_failed_previews(result_source.vocals, item_dir, ffmpeg)
                except Exception as preview_error:  # noqa: BLE001 - 继续后续语言。
                    report["preview_error"] = _safe_error(preview_error)
                    _with_diagnostic(report, preview_error, "preview_diagnostic_tail")
                _write_json(item_dir / "report.json", report)
            elif reference_error is not None or reference is None:
                report = {
                    "status": "failed", "profile_id": profile_id, "language": language,
                    "song_id": result_source.song_id,
                    "error": _safe_error(reference_error or RecheckError("参考音频不可用")),
                    "raw_vocal_source": _relative(result_source.vocals),
                }
                if reference_error is not None:
                    _with_diagnostic(report, reference_error)
                item_dir = profile_directory / language
                item_dir.mkdir(parents=True, exist_ok=True)
                try:
                    await _save_failed_previews(result_source.vocals, item_dir, ffmpeg)
                except Exception as preview_error:  # noqa: BLE001 - 继续后续语言。
                    report["preview_error"] = _safe_error(preview_error)
                    _with_diagnostic(report, preview_error, "preview_diagnostic_tail")
                _write_json(item_dir / "report.json", report)
            else:
                report = await _run_item(
                    profile_id, language, result_source, profile_directory, reference,
                    reference_source or reference,
                    checkpoint, model_settings, ffmpeg, ffprobe, args.steps,
                )
            if language in refresh_languages:
                report["source_reprepared"] = True
                report["previous_source_archive"] = _relative(source_archives.get(language))
                _write_json(report_file, report)
            manifest["items"][key] = {
                "status": report.get("status", "failed"),
                "report": _relative(profile_directory / language / "report.json"),
                "error": report.get("error"),
                "diagnostic_tail": report.get("diagnostic_tail"),
            }
            manifest["progress"] = {"completed": completed, "total": total}
            if retrying:
                manifest["retry_progress"] = {"completed": completed, "total": total}
            _write_json(manifest_path, manifest)
            print(f"[{args.phase} {completed}/{total}] {key}: {report.get('status', 'failed')}", flush=True)

    statuses = [item.get("status") for item in manifest["items"].values()]
    manifest["status"] = "passed" if statuses and all(value == "passed" for value in statuses) else "completed_with_failures"
    manifest["finished_at_utc"] = _now()
    if retrying:
        manifest["retry_finished_at_utc"] = _now()
    manifest["summary"] = {
        "passed": sum(value == "passed" for value in statuses),
        "failed": sum(value == "failed" for value in statuses),
        "total": len(statuses),
    }
    _write_json(manifest_path, manifest)
    return phase_root


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        output = asyncio.run(run_phase(args, Settings()))
    except Exception as exc:  # noqa: BLE001 - 报告致命前置错误，不显示环境 traceback。
        print(_safe_error(exc), file=sys.stderr, flush=True)
        return 2
    print(f"报告目录：{_relative(output)}", flush=True)
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    return 0 if manifest.get("status") == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
