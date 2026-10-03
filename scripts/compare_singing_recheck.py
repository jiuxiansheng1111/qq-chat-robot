"""对照两轮唱歌复验报告，不改音色映射。"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
QUALITY_METRICS = (
    "pitch_median_cents",
    "pitch_within_semitone_ratio",
    "voiced_recall",
    "vocal_energy_recall",
    "max_missing_vocal_seconds",
    "source_pitch_step_p95_cents",
    "converted_pitch_step_p95_cents",
    "pitch_residual_step_p95_cents",
    "pitch_residual_isolated_jump_count",
    "pitch_residual_isolated_jumps_per_minute",
    "voice_similarity",
    "clipping_ratio",
)
LYRICS_METRICS = (
    "transcript_consistency",
    "source_vs_lyrics",
    "converted_vs_lyrics",
)


def _safe_path(value: Any, matrix_root: Path) -> Path | None:
    if not isinstance(value, str) or not value:
        return None
    raw = Path(value)
    candidates = [raw] if raw.is_absolute() else [PROJECT_ROOT / raw, matrix_root / raw]
    for candidate in candidates:
        resolved = candidate.resolve()
        try:
            resolved.relative_to(matrix_root.resolve())
        except ValueError:
            continue
        if resolved.is_file():
            return resolved
    return None


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        if path.stat().st_size > 2 * 1024 * 1024:
            return None
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _manifest_items(matrix_root: Path, phase: str) -> dict[str, dict[str, Any]]:
    phase_root = (matrix_root / phase).resolve()
    try:
        phase_root.relative_to(matrix_root.resolve())
    except ValueError as exc:
        raise ValueError("phase directory is outside matrix root") from exc
    manifest = _read_json(phase_root / "manifest.json")
    if manifest is None or not isinstance(manifest.get("items"), dict):
        raise ValueError(f"{phase} manifest is missing or invalid")
    result: dict[str, dict[str, Any]] = {}
    for key, item in manifest["items"].items():
        if not isinstance(item, dict):
            continue
        report_path = _safe_path(item.get("report"), matrix_root)
        report = _read_json(report_path) if report_path else None
        result[str(key)] = {
            "manifest_status": item.get("status"),
            "report": report,
        }
    return result


def _lyrics_rows(matrix_root: Path, phase: str) -> dict[tuple[str, str], dict[str, Any]]:
    path = matrix_root / phase / "lyrics_qa.json"
    report = _read_json(path)
    if report is None or not isinstance(report.get("items"), list):
        return {}
    rows = {}
    for item in report["items"]:
        if not isinstance(item, dict):
            continue
        profile = item.get("profile_id")
        language = item.get("language")
        if isinstance(profile, str) and isinstance(language, str):
            rows[(profile, language)] = item
    return rows


def _finite_number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) else None


def _metric_pair(baseline: Any, candidate: Any) -> dict[str, float | None]:
    before = _finite_number(baseline)
    after = _finite_number(candidate)
    return {
        "baseline": before,
        "candidate": after,
        "delta_candidate_minus_baseline": (
            after - before if before is not None and after is not None else None
        ),
    }


def _path_identity(value: Any, matrix_root: Path) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    resolved = _safe_path(value, matrix_root)
    if resolved is not None:
        return os.path.normcase(str(resolved))
    normalized = os.path.normpath(value.replace("/", os.sep))
    return os.path.normcase(normalized)


def _source_fields_match(
    baseline: dict[str, Any] | None,
    candidate: dict[str, Any] | None,
    matrix_root: Path,
) -> bool:
    if baseline is None or candidate is None:
        return False
    if not baseline.get("song_id") or baseline.get("song_id") != candidate.get("song_id"):
        return False
    for field in ("lyrics", "vocals"):
        before = _path_identity(baseline.get(field), matrix_root)
        after = _path_identity(candidate.get(field), matrix_root)
        if before is None or before != after:
            return False
    for field in ("source", "lyric_count"):
        before = baseline.get(field)
        after = candidate.get(field)
        if before is None and after is None:
            continue
        if field == "source":
            before = _path_identity(before, matrix_root)
            after = _path_identity(after, matrix_root)
        if before is None or before != after:
            return False
    return True


def _input_audio_sha256(report: dict[str, Any] | None, matrix_root: Path) -> str | None:
    if report is None:
        return None
    path = _safe_path(report.get("vocal_before"), matrix_root)
    if path is None:
        return None
    digest = hashlib.sha256()
    try:
        with path.open("rb") as audio_file:
            for chunk in iter(lambda: audio_file.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError:
        return None
    return digest.hexdigest()


def _source_window(report: dict[str, Any] | None) -> tuple[float, float] | None:
    if report is None:
        return None
    start = _finite_number(report.get("source_start_seconds"))
    end = _finite_number(report.get("source_end_seconds"))
    return (start, end) if start is not None and end is not None else None


def _source_evidence(
    baseline: dict[str, Any] | None,
    candidate: dict[str, Any] | None,
    matrix_root: Path,
) -> dict[str, Any]:
    fields_match = _source_fields_match(baseline, candidate, matrix_root)
    before_window = _source_window(baseline)
    after_window = _source_window(candidate)
    window_matches = (
        before_window is not None
        and after_window is not None
        and all(math.isclose(left, right, abs_tol=0.001) for left, right in zip(before_window, after_window))
    )
    before_hash = _input_audio_sha256(baseline, matrix_root)
    after_hash = _input_audio_sha256(candidate, matrix_root)
    hashes_available = before_hash is not None and after_hash is not None
    hashes_match = hashes_available and before_hash == after_hash

    # 同一份逐项保存的模型输入可以确认音频相同，覆盖旧报告的时间元数据差异。
    if fields_match and hashes_match:
        same_source = True
        basis = "phase_input_audio_sha256"
    elif fields_match and not hashes_available and window_matches:
        same_source = True
        basis = "reported_paths_and_window"
    else:
        same_source = False
        basis = "insufficient_or_mismatched_evidence"

    return {
        "same_source_and_lyrics": same_source,
        "identity_fields_match": fields_match,
        "reported_window_matches": window_matches,
        "input_audio_hashes_available": hashes_available,
        "input_audio_hashes_match": hashes_match if hashes_available else None,
        "verification_basis": basis,
    }


def _phase_row(
    phase_item: dict[str, Any] | None,
    lyrics_item: dict[str, Any] | None,
) -> dict[str, Any]:
    report = phase_item.get("report") if phase_item else None
    quality = report.get("quality") if isinstance(report, dict) else None
    if not isinstance(quality, dict):
        quality = {}
    lyrics = lyrics_item or {}
    row: dict[str, Any] = {
        "status": report.get("status") if report else (
            phase_item.get("manifest_status") if phase_item else None
        ),
        "quality_accepted": quality.get("accepted"),
        "quality": {key: quality.get(key) for key in QUALITY_METRICS},
        "lyrics": {
            key: (lyrics.get(key, {}).get("cer")
                  if isinstance(lyrics.get(key), dict) else None)
            for key in LYRICS_METRICS
        },
    }
    return row


def compare_phase_reports(
    matrix_root: Path,
    baseline_phase: str = "baseline",
    candidate_phase: str = "candidate",
) -> dict[str, Any]:
    """汇总同一运行根目录下两轮报告的安全数值差异。"""
    if baseline_phase == candidate_phase:
        raise ValueError("baseline and candidate phase must differ")
    root = matrix_root.resolve(strict=True)
    baseline_items = _manifest_items(root, baseline_phase)
    candidate_items = _manifest_items(root, candidate_phase)
    baseline_lyrics = _lyrics_rows(root, baseline_phase)
    candidate_lyrics = _lyrics_rows(root, candidate_phase)
    keys = sorted(set(baseline_items) | set(candidate_items))
    rows = []
    comparable_count = 0
    incomparable_count = 0
    for key in keys:
        profile, separator, language = key.partition(":")
        if not separator:
            profile, language = "unknown", "unknown"
        before_entry = baseline_items.get(key)
        after_entry = candidate_items.get(key)
        before_report = before_entry.get("report") if before_entry else None
        after_report = after_entry.get("report") if after_entry else None
        source_evidence = _source_evidence(before_report, after_report, root)
        same_source = source_evidence["same_source_and_lyrics"]
        before_lyrics = baseline_lyrics.get((profile, language))
        after_lyrics = candidate_lyrics.get((profile, language))
        before = _phase_row(before_entry, before_lyrics)
        after = _phase_row(after_entry, after_lyrics)
        voice_pitch_before = (
            before_report.get("voice_pitch_shift_semitones") if before_report else None
        )
        voice_pitch_after = (
            after_report.get("voice_pitch_shift_semitones") if after_report else None
        )
        accompaniment_pitch_before = (
            before_report.get("accompaniment_pitch_shift_semitones") if before_report else None
        )
        accompaniment_pitch_after = (
            after_report.get("accompaniment_pitch_shift_semitones") if after_report else None
        )
        same_pitch_shift = (
            voice_pitch_before == voice_pitch_after
            and accompaniment_pitch_before == accompaniment_pitch_after
        )
        comparable = (
            same_source
            and same_pitch_shift
            and before.get("status") == "passed"
            and after.get("status") == "passed"
        )
        incomparable_reasons = []
        if before_report is None or after_report is None:
            incomparable_reasons.append("missing_phase_report")
        if not source_evidence["identity_fields_match"]:
            incomparable_reasons.append("song_or_source_path_mismatch")
        if not source_evidence["input_audio_hashes_available"]:
            if not source_evidence["reported_window_matches"]:
                incomparable_reasons.append("source_window_unverified_or_mismatched")
        elif not source_evidence["input_audio_hashes_match"]:
            incomparable_reasons.append("input_audio_hash_mismatch")
            if not source_evidence["reported_window_matches"]:
                incomparable_reasons.append("source_window_mismatch")
        if not same_pitch_shift:
            incomparable_reasons.append("pitch_shift_mismatch")
        if before.get("status") != "passed" or after.get("status") != "passed":
            incomparable_reasons.append("phase_not_passed")
        if comparable:
            comparable_count += 1
        else:
            incomparable_count += 1
        deltas = {}
        for metric in QUALITY_METRICS:
            deltas[metric] = _metric_pair(
                before["quality"].get(metric), after["quality"].get(metric)
            )
        for metric in LYRICS_METRICS:
            deltas[f"{metric}_cer"] = _metric_pair(
                before["lyrics"].get(metric), after["lyrics"].get(metric)
            )
        rows.append({
            "profile_id": profile,
            "language": language,
            "song_id": (after_report or before_report or {}).get("song_id"),
            "comparable": comparable,
            "same_source_and_lyrics": same_source,
            "source_evidence": source_evidence,
            "incomparable_reasons": incomparable_reasons,
            "same_pitch_shift": same_pitch_shift,
            "voice_pitch_shift_semitones": {
                "baseline": voice_pitch_before,
                "candidate": voice_pitch_after,
                "same": voice_pitch_before == voice_pitch_after,
            },
            "accompaniment_pitch_shift_semitones": {
                "baseline": accompaniment_pitch_before,
                "candidate": accompaniment_pitch_after,
                "same": accompaniment_pitch_before == accompaniment_pitch_after,
            },
            "baseline": before,
            "candidate": after,
            "deltas": deltas,
        })

    return {
        "schema_version": 1,
        "baseline_phase": baseline_phase,
        "candidate_phase": candidate_phase,
        "diagnostic_only": True,
        "automatic_acceptance": False,
        "selection_rule": (
            "只比较同歌、同歌词、同移调且两轮均通过的样本；"
            "候选须守住音质门槛、三语咬字不退步并经人工试听。训练 loss 不作替换依据。"
        ),
        "summary": {
            "items": len(rows),
            "comparable": comparable_count,
            "incomparable": incomparable_count,
            "baseline_lyrics_qa_present": bool(baseline_lyrics),
            "candidate_lyrics_qa_present": bool(candidate_lyrics),
        },
        "items": rows,
    }


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="只读比较唱歌 baseline 和 candidate")
    parser.add_argument("--matrix-root", type=Path, required=True)
    parser.add_argument("--baseline-phase", default="baseline")
    parser.add_argument("--candidate-phase", default="candidate")
    parser.add_argument("--output", type=Path)
    return parser.parse_args(argv)


def _write_json(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        report = compare_phase_reports(args.matrix_root, args.baseline_phase, args.candidate_phase)
        output = args.output or args.matrix_root / "phase_comparison.json"
        _write_json(output, report)
    except Exception as exc:  # noqa: BLE001 - 终端只显示异常类型。
        print(f"phase 比较失败：{type(exc).__name__}", file=sys.stderr)
        return 2
    print(json.dumps({
        "report": str(output),
        "items": report["summary"]["items"],
        "comparable": report["summary"]["comparable"],
        "automatic_acceptance": False,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
