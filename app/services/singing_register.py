"""与模型无关的八度规划，用于把歌手旋律移到目标音域。"""

from __future__ import annotations

import math

import numpy as np

OCTAVE_SHIFTS = (-12, 0, 12)


def choose_octave_shift(source_f0, target_median_hz: float) -> dict[str, float | int]:
    """选择一个八度偏移，让整首歌的 F0 中位数最接近目标。

    计算有声统计时会排除非有限或非正的 F0 帧，并至少要求一帧有效数据。整体统一移调，保留音程和调性；目标音域距离相同时不移调。
    """
    if isinstance(target_median_hz, (bool, str, bytes)):
        raise TypeError("target_median_hz must be a number from 80 to 1000 Hz")
    try:
        target_hz = float(target_median_hz)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("target_median_hz must be a number from 80 to 1000 Hz") from exc
    if not math.isfinite(target_hz) or not 80 <= target_hz <= 1000:
        raise ValueError("target_median_hz must be from 80 to 1000 Hz")

    try:
        f0 = np.asarray(source_f0, dtype=np.float64)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("source_f0 must be a one-dimensional F0 sequence") from exc
    if f0.ndim != 1 or f0.size == 0:
        raise ValueError("source_f0 must be a nonempty one-dimensional F0 sequence")
    voiced = f0[np.isfinite(f0) & (f0 > 0)]
    if voiced.size == 0:
        raise ValueError("source_f0 contains no finite voiced frames")

    source_median = float(np.median(voiced))
    source_log2 = math.log2(source_median)
    distances = {
        shift: abs(source_log2 + (shift / 12) - math.log2(target_hz))
        for shift in OCTAVE_SHIFTS
    }
    nearest = min(distances.values())
    tied = [shift for shift, distance in distances.items()
            if math.isclose(distance, nearest, rel_tol=1e-12, abs_tol=1e-12)]
    shift = min(tied, key=lambda value: (value != 0, abs(value), value))

    predicted_median = 2 ** (source_log2 + (shift / 12))
    if not math.isfinite(predicted_median) or predicted_median <= 0:
        raise ValueError("source_f0 voiced values are outside the supported range")
    return {
        "source_median_hz": source_median,
        "source_p10_hz": float(np.percentile(voiced, 10)),
        "source_p90_hz": float(np.percentile(voiced, 90)),
        "source_voiced_frames": int(voiced.size),
        "source_total_frames": int(f0.size),
        "source_nonfinite_frames": int(np.count_nonzero(~np.isfinite(f0))),
        "source_unvoiced_frames": int(np.count_nonzero(np.isfinite(f0) & (f0 <= 0))),
        "target_median_hz": target_hz,
        "expected_semitone_shift": int(shift),
        "predicted_median_hz": predicted_median,
        "log2_distance_to_target": float(distances[shift]),
    }
