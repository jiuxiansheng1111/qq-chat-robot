"""失败时留一点检查数据，方便定位缺声；音频照常清理。"""

from __future__ import annotations

import json
import math
import re
from datetime import UTC, datetime
from pathlib import Path

from app.services.singing_sources import SINGING_DATA_ROOT

_METRICS = frozenset({
    "pitch_median_cents", "pitch_within_semitone_ratio", "voiced_recall",
    "voiced_precision", "expected_semitone_shift", "voice_similarity",
    "identity_reference_similarity", "duration_ratio", "source_voiced_frames",
    "converted_rms", "clipping_ratio", "alignment_delay_ms",
    "vocal_energy_recall", "max_missing_vocal_seconds",
    "source_active_energy_windows", "source_vocal_energy_windows",
    "pitch_residual_step_median_cents", "pitch_residual_isolated_jump_count",
    "pitch_residual_isolated_jumps_per_minute", "pitch_residual_adjacent_pair_count",
})


def save_failed_singing_diagnostics(
    job_id: str, job_directory: Path, profile_id: str, error_type: str,
) -> None:
    """留必要的失败信息，最多保留二十次。"""
    if not re.fullmatch(r"[a-f0-9]{32}", job_id):
        raise ValueError("翻唱任务编号无效")
    singing_root = SINGING_DATA_ROOT.resolve()
    jobs_root = (singing_root / "jobs").resolve()
    directory = job_directory.resolve()
    if jobs_root.parent != singing_root or directory != jobs_root / job_id:
        raise ValueError("翻唱诊断目录无效")
    root = (singing_root / "failures").resolve()
    if root.parent != singing_root:
        raise ValueError("翻唱诊断目录无效")
    reports = []
    parts_root = directory / "parts"
    if parts_root.is_dir() and not parts_root.is_symlink():
        for part in sorted(parts_root.iterdir()):
            if not re.fullmatch(r"\d{3}", part.name) or not part.is_dir():
                continue
            if part.is_symlink() or not part.resolve().is_relative_to(directory):
                continue
            for name in ("quality.json", "quality_retry.json"):
                source = part / name
                if not source.is_file() or source.is_symlink() or source.stat().st_size > 256_000:
                    continue
                try:
                    report = json.loads(source.read_text(encoding="utf-8"))
                except (OSError, UnicodeError, ValueError):
                    continue
                if not isinstance(report, dict):
                    continue
                metrics = {}
                for key in _METRICS:
                    value = report.get(key)
                    if value is None or (type(value) in (int, float) and math.isfinite(value)):
                        metrics[key] = value
                reports.append({
                    "part": int(part.name), "retry": name == "quality_retry.json",
                    "accepted": report.get("accepted") is True, "metrics": metrics,
                    "energy_coverage_basis": report.get("energy_coverage_basis")
                    if isinstance(report.get("energy_coverage_basis"), str)
                    and report.get("energy_coverage_basis") in {"source_f0_context", "source_energy"}
                    else "unknown",
                    "missing_vocal_intervals": _missing_intervals(report),
                })
    payload = {
        "created_at": datetime.now(UTC).isoformat(),
        "profile_id": profile_id if re.fullmatch(r"[a-z0-9_-]{1,64}", profile_id) else "unknown",
        "error_type": error_type if re.fullmatch(r"[A-Za-z_]{1,64}", error_type) else "unknown",
        "checks": reports,
    }
    selection_path = directory / "selection.json"
    if selection_path.is_file() and not selection_path.is_symlink() and selection_path.stat().st_size < 32_000:
        try:
            selection = json.loads(selection_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValueError):
            selection = {}
        if isinstance(selection, dict):
            title = selection.get("song_title")
            if isinstance(title, str) and 0 < len(title) <= 200:
                payload["song_title"] = title
            if isinstance(selection.get("mode"), str) and selection.get("mode") in {"full", "clip"}:
                payload["mode"] = selection["mode"]
    root.mkdir(parents=True, exist_ok=True)
    target = (root / f"{job_id}.json").resolve()
    if target.parent != root:
        raise ValueError("翻唱诊断文件位置无效")
    with target.open("x", encoding="utf-8") as output:
        json.dump(payload, output, ensure_ascii=False, allow_nan=False, indent=2)
    saved = sorted(
        (path for path in root.iterdir() if re.fullmatch(r"[a-f0-9]{32}\.json", path.name)
         and path.is_file() and not path.is_symlink()),
        key=lambda path: path.stat().st_mtime, reverse=True,
    )
    for path in saved[20:]:
        if path.resolve().parent == root:
            path.unlink(missing_ok=True)


def _missing_intervals(report: dict) -> list[dict[str, float]]:
    """留最多八处段内缺声位置，不保存歌词或账号。"""
    result = []
    intervals = report.get("missing_vocal_intervals")
    if not isinstance(intervals, list):
        return result
    for item in intervals[:8]:
        if not isinstance(item, dict):
            continue
        start, end = item.get("start_seconds"), item.get("end_seconds")
        if (
            type(start) in (int, float) and type(end) in (int, float)
            and math.isfinite(start) and math.isfinite(end)
            and 0 <= start < end <= 600
        ):
            result.append({"start_seconds": start, "end_seconds": end})
    return result
