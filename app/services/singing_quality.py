"""用于翻唱转换的轻量、无模型音质指标。"""

from collections.abc import Mapping

import numpy as np


def cosine_similarity(first: np.ndarray, second: np.ndarray) -> float | None:
    """返回两个说话人嵌入均值的余弦相似度；向量不可用时返回 None。"""
    a = np.asarray(first, dtype=np.float64).reshape(-1)
    b = np.asarray(second, dtype=np.float64).reshape(-1)
    if a.shape != b.shape or not a.size or not np.all(np.isfinite(a)) or not np.all(np.isfinite(b)):
        return None
    norm = float(np.linalg.norm(a) * np.linalg.norm(b))
    return float(np.clip(np.dot(a, b) / norm, -1, 1)) if norm > 1e-12 else None


def _voiced_median_f0(f0: np.ndarray) -> float | None:
    values = np.asarray(f0, dtype=np.float64).reshape(-1)
    voiced = values[np.isfinite(values) & (values > 0)]
    return float(np.median(voiced)) if voiced.size else None


def _energy_windows(
    audio: np.ndarray, window_samples: int, hop_samples: int
) -> list[tuple[int, int, float]]:
    """按固定窗计算 RMS，尾部短于半窗的部分不参与。"""
    windows = []
    for start in range(0, audio.size, hop_samples):
        end = min(audio.size, start + window_samples)
        segment = audio[start:end]
        if segment.size < hop_samples:
            continue
        rms = float(np.sqrt(np.mean(np.square(segment, dtype=np.float64))))
        windows.append((start, end, rms))
    return windows


def _active_window_level(windows: list[tuple[int, int, float]]) -> float:
    """用较响的窗口估算各自的活跃电平。"""
    values = np.asarray([rms for _, _, rms in windows if rms > 0], dtype=np.float64)
    if not values.size:
        return 0.0
    top_count = max(1, int(np.ceil(values.size * 0.2)))
    return float(np.median(np.partition(values, values.size - top_count)[-top_count:]))


def compute_energy_coverage(
    source_audio: np.ndarray,
    converted_audio: np.ndarray,
    sample_rate: int = 16000,
    alignment_delay_ms: float = 0,
) -> dict[str, float | None]:
    """比较活跃人声能量覆盖，不判断歌词或辅音是否正确。"""
    if (
        isinstance(sample_rate, bool)
        or not isinstance(sample_rate, (int, np.integer))
        or sample_rate <= 0
    ):
        raise ValueError("采样率必须是正整数")
    if (
        isinstance(alignment_delay_ms, bool)
        or not isinstance(alignment_delay_ms, (int, float, np.number))
        or not np.isfinite(alignment_delay_ms)
    ):
        raise ValueError("对齐延迟必须是有限数值")
    source = np.asarray(source_audio, dtype=np.float64).reshape(-1)
    converted = np.asarray(converted_audio, dtype=np.float64).reshape(-1)
    if not source.size or not converted.size:
        raise ValueError("能量覆盖分析需要非空音频")
    if not np.all(np.isfinite(source)) or not np.all(np.isfinite(converted)):
        raise ValueError("能量覆盖音频包含非有限样本")

    window_samples = max(1, round(sample_rate * 0.1))
    hop_samples = max(1, round(sample_rate * 0.05))
    source_windows = _energy_windows(source, window_samples, hop_samples)
    converted_windows = _energy_windows(converted, window_samples, hop_samples)
    source_level = _active_window_level(source_windows)
    converted_level = _active_window_level(converted_windows)
    if source_level <= 0:
        return {"vocal_energy_recall": None, "max_missing_vocal_seconds": 0.0}

    source_floor = max(1e-8, source_level * 0.05)
    converted_floor = max(1e-8, converted_level * 0.025)
    offset_samples = round(float(alignment_delay_ms) * sample_rate / 1000)
    eligible_count = 0
    present_count = 0
    longest_missing = 0.0
    missing_start: int | None = None
    missing_end = 0

    def finish_missing_run() -> None:
        nonlocal longest_missing, missing_start, missing_end
        if missing_start is not None:
            longest_missing = max(longest_missing, (missing_end - missing_start) / sample_rate)
            missing_start = None

    for start, end, source_rms in source_windows:
        if source_rms < source_floor:
            finish_missing_run()
            continue
        target_start = start + offset_samples
        target_end = end + offset_samples
        clipped_start = max(0, target_start)
        clipped_end = min(converted.size, target_end)
        source_span = end - start
        if clipped_end - clipped_start < max(1, int(np.ceil(source_span * 0.5))):
            # 原唱还在唱而输出已经结束，也要计入缺失。
            eligible_count += 1
            if missing_start is None:
                missing_start = start
            missing_end = end
            continue

        target_segment = converted[clipped_start:clipped_end]
        target_rms = float(np.sqrt(np.mean(np.square(target_segment, dtype=np.float64))))
        eligible_count += 1
        is_present = converted_level > 0 and target_rms >= converted_floor
        if is_present:
            present_count += 1
            finish_missing_run()
        else:
            if missing_start is None:
                missing_start = start
            missing_end = end
    finish_missing_run()

    recall = present_count / eligible_count if eligible_count else None
    return {
        "vocal_energy_recall": float(recall) if recall is not None else None,
        "max_missing_vocal_seconds": float(longest_missing),
    }


def _aligned_pitch_metrics(
    source_f0: np.ndarray,
    converted_f0: np.ndarray,
    f0_hop_seconds: float,
    max_delay_seconds: float,
) -> tuple[float | None, float, float, float | None, int]:
    source = np.asarray(source_f0, dtype=np.float64).reshape(-1)
    converted = np.asarray(converted_f0, dtype=np.float64).reshape(-1)
    if f0_hop_seconds <= 0 or max_delay_seconds < 0:
        raise ValueError("F0 frame hop and alignment window must be positive")
    source_voiced = np.isfinite(source) & (source > 0)
    converted_voiced = np.isfinite(converted) & (converted > 0)
    source_voiced_frames = int(np.count_nonzero(source_voiced))
    converted_voiced_frames = int(np.count_nonzero(converted_voiced))
    if source_voiced_frames == 0:
        precision = 0.0 if converted_voiced_frames else None
        return None, 0.0, 0.0, precision, 0

    max_delay_frames = round(max_delay_seconds / f0_hop_seconds)
    source_positions = np.arange(len(source))
    best_score: tuple[float, float, float, int] | None = None
    best_metrics: tuple[float | None, float, float, float | None, int] | None = None
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
        # 同时奖励音高和有声音段覆盖率；
        # 两项相同时再优先选择延迟更短的结果。
        score = (
            within_ratio * recall,
            recall,
            -(median_cents if median_cents is not None else float("inf")),
            -abs(shift),
        )
        if best_score is None or score > best_score:
            best_score = score
            precision = matched_count / converted_voiced_frames if converted_voiced_frames else None
            best_metrics = (median_cents, within_ratio, recall, precision, shift)
    assert best_metrics is not None
    return best_metrics


def _pitch_residual_diagnostics(
    source_f0: np.ndarray,
    converted_f0: np.ndarray,
    expected_semitone_shift: int,
    alignment_delay_frames: int,
    f0_hop_seconds: float,
) -> dict[str, float | int | None]:
    """比较对齐后的相邻音高变化，源有声区分段计算，不跨静音。"""
    source = np.asarray(source_f0, dtype=np.float64).reshape(-1)
    converted = np.asarray(converted_f0, dtype=np.float64).reshape(-1)
    positions = np.arange(source.size)
    converted_positions = positions + alignment_delay_frames
    in_bounds = (converted_positions >= 0) & (converted_positions < converted.size)
    source_voiced = np.isfinite(source) & (source > 0)
    converted_voiced = np.zeros(source.size, dtype=bool)
    converted_voiced[in_bounds] = (
        np.isfinite(converted[converted_positions[in_bounds]])
        & (converted[converted_positions[in_bounds]] > 0)
    )
    valid = source_voiced & in_bounds & converted_voiced
    source_positions = positions[valid]
    target_positions = converted_positions[valid]
    if not source_positions.size:
        return {
            "source_pitch_step_p95_cents": None,
            "converted_pitch_step_p95_cents": None,
            "pitch_residual_step_median_cents": None,
            "pitch_residual_step_p95_cents": None,
            "pitch_residual_isolated_jump_count": 0,
            "pitch_residual_isolated_jumps_per_minute": None,
            "pitch_residual_adjacent_pair_count": 0,
        }

    expected_source = source[source_positions] * (2 ** (expected_semitone_shift / 12))
    converted_values = converted[target_positions]
    residual_cents = 1200 * np.log2(converted_values / expected_source)
    contiguous_pairs = np.diff(source_positions) == 1
    source_steps = np.abs(1200 * np.log2(
        source[source_positions[1:]] / source[source_positions[:-1]]
    ))[contiguous_pairs]
    converted_steps = np.abs(1200 * np.log2(
        converted[target_positions[1:]] / converted[target_positions[:-1]]
    ))[contiguous_pairs]
    residual_steps = np.abs(np.diff(residual_cents))[contiguous_pairs]
    contiguous_triples = (
        (np.diff(source_positions[:-1]) == 1)
        & (np.diff(source_positions[1:]) == 1)
    )
    jump_count = 0
    triple_centers = np.flatnonzero(contiguous_triples) + 1
    for center in triple_centers:
        left_step = residual_cents[center] - residual_cents[center - 1]
        right_step = residual_cents[center + 1] - residual_cents[center]
        return_step = residual_cents[center + 1] - residual_cents[center - 1]
        if (
            abs(left_step) >= 100
            and abs(right_step) >= 100
            and left_step * right_step < 0
            and abs(return_step) <= 50
        ):
            jump_count += 1

    pair_count = int(np.count_nonzero(contiguous_pairs))
    duration_seconds = pair_count * f0_hop_seconds

    def percentile95(values: np.ndarray) -> float | None:
        return float(np.percentile(values, 95)) if values.size else None

    return {
        "source_pitch_step_p95_cents": percentile95(source_steps),
        "converted_pitch_step_p95_cents": percentile95(converted_steps),
        "pitch_residual_step_median_cents": (
            float(np.median(residual_steps)) if residual_steps.size else None
        ),
        "pitch_residual_step_p95_cents": percentile95(residual_steps),
        "pitch_residual_isolated_jump_count": jump_count,
        "pitch_residual_isolated_jumps_per_minute": (
            float(jump_count * 60 / duration_seconds) if duration_seconds > 0 else None
        ),
        "pitch_residual_adjacent_pair_count": pair_count,
    }


def compute_quality_report(
    source_f0: np.ndarray,
    converted_f0: np.ndarray,
    converted_audio: np.ndarray,
    source_duration_seconds: float,
    converted_duration_seconds: float,
    reference_embedding: np.ndarray,
    converted_embedding: np.ndarray,
    *,
    identity_reference_embedding: np.ndarray | None = None,
    expected_semitone_shift: int = 0,
    f0_hop_seconds: float = 0.01,
    max_delay_seconds: float = 0.1,
) -> dict[str, float | int | None]:
    """计算可测量的音高、人声、时长和波形指标。

    音高或参考说话人估计缺失时以 ``None`` 表示，并无法通过默认验收。独立身份分数仅作可选诊断。说话人余弦相似度只是嵌入向量代理指标，不代表感知到的音色身份或自然度。
    """
    audio = np.asarray(converted_audio, dtype=np.float64).reshape(-1)
    if source_duration_seconds <= 0 or converted_duration_seconds <= 0 or not audio.size:
        raise ValueError("Audio and both durations must be nonempty and positive")
    if not np.all(np.isfinite(audio)):
        raise ValueError("Converted audio contains non-finite samples")
    pitch_source_f0 = np.asarray(source_f0, dtype=np.float64).reshape(-1).copy()
    source_voiced = np.isfinite(pitch_source_f0) & (pitch_source_f0 > 0)
    pitch_source_f0[source_voiced] *= 2 ** (expected_semitone_shift / 12)
    pitch_median, within_ratio, recall, precision, delay_frames = _aligned_pitch_metrics(
        pitch_source_f0, converted_f0, f0_hop_seconds, max_delay_seconds
    )
    pitch_diagnostics = _pitch_residual_diagnostics(
        source_f0,
        converted_f0,
        expected_semitone_shift,
        delay_frames,
        f0_hop_seconds,
    )
    source_voiced_frames = int(
        np.count_nonzero(np.isfinite(source_f0) & (np.asarray(source_f0) > 0))
    )
    return {
        "pitch_median_cents": pitch_median,
        "pitch_within_semitone_ratio": within_ratio,
        "voiced_recall": recall,
        "voiced_precision": precision,
        "source_voiced_median_f0_hz": _voiced_median_f0(source_f0),
        "converted_voiced_median_f0_hz": _voiced_median_f0(converted_f0),
        "expected_semitone_shift": expected_semitone_shift,
        "voice_similarity": cosine_similarity(reference_embedding, converted_embedding),
        "identity_reference_similarity": (
            cosine_similarity(identity_reference_embedding, converted_embedding)
            if identity_reference_embedding is not None else None
        ),
        "duration_ratio": float(converted_duration_seconds / source_duration_seconds),
        "source_voiced_frames": source_voiced_frames,
        "converted_rms": float(np.sqrt(np.mean(np.square(audio)))),
        "clipping_ratio": float(np.count_nonzero(np.abs(audio) >= 0.999) / audio.size),
        "alignment_delay_ms": round(delay_frames * f0_hop_seconds * 1000),
        **pitch_diagnostics,
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
    min_energy_recall: float | None = None,
    max_missing_vocal_seconds: float | None = None,
) -> list[str]:
    """列出未通过的验收项。调用方可显式调整阈值。"""
    failures: list[str] = []

    def number(key: str) -> float | None:
        value = report.get(key)
        if (
            not isinstance(value, bool)
            and isinstance(value, (int, float, np.number))
            and np.isfinite(value)
        ):
            return float(value)
        return None

    if min_energy_recall is not None and (
        not np.isfinite(min_energy_recall) or not 0 <= min_energy_recall <= 1
    ):
        raise ValueError("人声能量覆盖阈值必须在 0 到 1 之间")
    if max_missing_vocal_seconds is not None and (
        not np.isfinite(max_missing_vocal_seconds) or max_missing_vocal_seconds < 0
    ):
        raise ValueError("最大缺失时长阈值必须是非负有限数值")

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
    if min_energy_recall is not None:
        energy_recall = number("vocal_energy_recall")
        if energy_recall is None or energy_recall < min_energy_recall:
            failures.append("转换后人声能量覆盖比例不足")
    if max_missing_vocal_seconds is not None:
        missing_seconds = number("max_missing_vocal_seconds")
        if missing_seconds is None or missing_seconds > max_missing_vocal_seconds:
            failures.append("转换后连续缺失人声过长")
    return failures
