"""Lightweight, model-independent quality measurements for singing conversion."""

from collections.abc import Mapping

import numpy as np


def cosine_similarity(first: np.ndarray, second: np.ndarray) -> float | None:
    """Cosine of two mean speaker embeddings, or None for unusable vectors."""
    a = np.asarray(first, dtype=np.float64).reshape(-1)
    b = np.asarray(second, dtype=np.float64).reshape(-1)
    if a.shape != b.shape or not a.size or not np.all(np.isfinite(a)) or not np.all(np.isfinite(b)):
        return None
    norm = float(np.linalg.norm(a) * np.linalg.norm(b))
    return float(np.clip(np.dot(a, b) / norm, -1, 1)) if norm > 1e-12 else None


def _aligned_pitch_metrics(
    source_f0: np.ndarray,
    converted_f0: np.ndarray,
    f0_hop_seconds: float,
    max_delay_seconds: float,
) -> tuple[float | None, float, float, int]:
    source = np.asarray(source_f0, dtype=np.float64).reshape(-1)
    converted = np.asarray(converted_f0, dtype=np.float64).reshape(-1)
    if f0_hop_seconds <= 0 or max_delay_seconds < 0:
        raise ValueError("F0 frame hop and alignment window must be positive")
    source_voiced = np.isfinite(source) & (source > 0)
    converted_voiced = np.isfinite(converted) & (converted > 0)
    source_voiced_frames = int(np.count_nonzero(source_voiced))
    if source_voiced_frames == 0:
        return None, 0.0, 0.0, 0

    max_delay_frames = round(max_delay_seconds / f0_hop_seconds)
    source_positions = np.arange(len(source))
    best_score: tuple[float, float, float, int] | None = None
    best_metrics: tuple[float | None, float, float, int] | None = None
    for shift in range(-max_delay_frames, max_delay_frames + 1):
        converted_positions = source_positions + shift
        in_bounds = (converted_positions >= 0) & (converted_positions < len(converted))
        source_indices = source_positions[source_voiced & in_bounds]
        target_indices = converted_positions[source_indices]
        matched = converted_voiced[target_indices]
        source_indices = source_indices[matched]
        target_indices = target_indices[matched]
        matched_count = len(source_indices)
        recall = matched_count / source_voiced_frames
        if matched_count:
            cents = np.abs(1200 * np.log2(converted[target_indices] / source[source_indices]))
            median_cents = float(np.median(cents))
            within_ratio = float(np.count_nonzero(cents <= 100) / matched_count)
        else:
            median_cents = None
            within_ratio = 0.0
        # Reward preserved pitch and voiced coverage together. Prefer a smaller
        # delay only after those measurements are equal.
        score = (
            within_ratio * recall,
            recall,
            -(median_cents if median_cents is not None else float("inf")),
            -abs(shift),
        )
        if best_score is None or score > best_score:
            best_score = score
            best_metrics = (median_cents, within_ratio, recall, shift)
    assert best_metrics is not None
    return best_metrics


def compute_quality_report(
    source_f0: np.ndarray,
    converted_f0: np.ndarray,
    converted_audio: np.ndarray,
    source_duration_seconds: float,
    converted_duration_seconds: float,
    reference_embedding: np.ndarray,
    converted_embedding: np.ndarray,
    *,
    f0_hop_seconds: float = 0.01,
    max_delay_seconds: float = 0.1,
) -> dict[str, float | int | None]:
    """Compute measurable pitch, voice, length, and waveform checks.

    A missing pitch or speaker estimate is represented by ``None`` and fails
    the default acceptance check. This report does not measure naturalness.
    """
    audio = np.asarray(converted_audio, dtype=np.float64).reshape(-1)
    if source_duration_seconds <= 0 or converted_duration_seconds <= 0 or not audio.size:
        raise ValueError("Audio and both durations must be nonempty and positive")
    if not np.all(np.isfinite(audio)):
        raise ValueError("Converted audio contains non-finite samples")
    pitch_median, within_ratio, recall, delay_frames = _aligned_pitch_metrics(
        source_f0, converted_f0, f0_hop_seconds, max_delay_seconds
    )
    source_voiced_frames = int(
        np.count_nonzero(np.isfinite(source_f0) & (np.asarray(source_f0) > 0))
    )
    return {
        "pitch_median_cents": pitch_median,
        "pitch_within_semitone_ratio": within_ratio,
        "voiced_recall": recall,
        "voice_similarity": cosine_similarity(reference_embedding, converted_embedding),
        "duration_ratio": float(converted_duration_seconds / source_duration_seconds),
        "source_voiced_frames": source_voiced_frames,
        "converted_rms": float(np.sqrt(np.mean(np.square(audio)))),
        "clipping_ratio": float(np.count_nonzero(np.abs(audio) >= 0.999) / audio.size),
        "alignment_delay_ms": round(delay_frames * f0_hop_seconds * 1000),
    }


def quality_failures(
    report: Mapping[str, float | int | None],
    *,
    max_pitch_median_cents: float = 100,
    min_pitch_within_semitone_ratio: float = 0.70,
    min_voiced_recall: float = 0.70,
    max_duration_error_ratio: float = 0.03,
    min_converted_rms: float = 1e-4,
    max_clipping_ratio: float = 0.01,
    min_voice_similarity: float = 0.35,
    min_source_voiced_frames: int = 100,
) -> list[str]:
    """List failed acceptance checks. Callers may tune thresholds explicitly."""
    failures: list[str] = []

    def number(key: str) -> float | None:
        value = report.get(key)
        if isinstance(value, (int, float, np.number)) and np.isfinite(value):
            return float(value)
        return None

    voiced = number("source_voiced_frames")
    pitch = number("pitch_median_cents")
    within = number("pitch_within_semitone_ratio")
    recall = number("voiced_recall")
    similarity = number("voice_similarity")
    duration = number("duration_ratio")
    rms = number("converted_rms")
    clipping = number("clipping_ratio")

    if voiced is None or voiced < min_source_voiced_frames:
        failures.append("原始人声的有声帧不足")
    if pitch is None or pitch >= max_pitch_median_cents:
        failures.append("转换后音高偏差过大")
    if within is None or within < min_pitch_within_semitone_ratio:
        failures.append("转换后保留的旋律音高比例不足")
    if recall is None or recall < min_voiced_recall:
        failures.append("转换后保留的有声帧比例不足")
    if duration is None or abs(duration - 1) > max_duration_error_ratio:
        failures.append("转换前后时长偏差过大")
    if rms is None or rms <= min_converted_rms:
        failures.append("转换后人声音量过低")
    if clipping is None or clipping >= max_clipping_ratio:
        failures.append("转换后人声削波过多")
    if similarity is None or similarity < min_voice_similarity:
        failures.append("转换后与目标音色相似度不足")
    return failures
