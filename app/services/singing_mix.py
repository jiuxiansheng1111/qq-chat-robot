"""翻唱音量分析与混音滤镜。"""

from __future__ import annotations

import math
import wave
from pathlib import Path

import numpy as np


def stabilize_vocal_envelope(
    source: np.ndarray, converted: np.ndarray, sample_rate: int,
) -> tuple[np.ndarray, dict]:
    """参考原唱的轻重补弱字，最多两倍；空白处不补声音。"""
    source = np.asarray(source, dtype=np.float32).reshape(-1)
    converted = np.asarray(converted, dtype=np.float32).reshape(-1)
    if (
        type(sample_rate) is not int or sample_rate <= 0
        or not source.size or not converted.size
        or not np.all(np.isfinite(source)) or not np.all(np.isfinite(converted))
        or abs(source.size - converted.size) > sample_rate * 0.03
    ):
        raise ValueError("人声音量整理的时间轴或样本无效")
    window, hop = max(1, round(sample_rate * 0.1)), max(1, round(sample_rate * 0.05))
    centers, source_levels, converted_levels, peaks = [], [], [], []
    for first in range(0, converted.size, hop):
        last = min(converted.size, first + window)
        original = source[first:min(source.size, last)]
        target = converted[first:last]
        centers.append((first + last) * 0.5)
        source_levels.append(float(np.sqrt(np.mean(np.square(original, dtype=np.float64)))) if original.size else 0)
        converted_levels.append(float(np.sqrt(np.mean(np.square(target, dtype=np.float64)))))
        peaks.append(float(np.max(np.abs(target))))
    original = np.asarray(source_levels)
    target = np.asarray(converted_levels)

    def active_level(levels):
        active = levels[levels > 0]
        if not active.size:
            return 0.0
        count = max(1, int(np.ceil(active.size * 0.2)))
        return float(np.median(np.partition(active, active.size - count)[-count:]))

    source_level, target_level = active_level(original), active_level(target)
    gain = np.ones(target.size)
    if source_level > 0 and target_level > 0:
        active = (original >= source_level * 0.05) & (target >= target_level * 0.0025)
        desired = (original[active] / source_level) / (target[active] / target_level)
        gain[active] = np.clip(np.sqrt(desired), 0.5, 2.0)
    # 先按窗限制峰值，再平滑增益；不整体压低伴奏。
    gain = np.minimum(gain, 0.90 / np.maximum(peaks, 1e-8))
    envelope = np.interp(np.arange(converted.size, dtype=np.float32), centers, gain).astype(np.float32)
    adjusted = converted * envelope
    if not np.all(np.isfinite(adjusted)):
        raise ValueError("人声音量整理产生了无效样本")
    return adjusted, {
        "boosted_windows": int(np.count_nonzero(gain > 1.05)),
        "attenuated_windows": int(np.count_nonzero(gain < 0.95)),
        "maximum_gain": float(np.max(gain)), "minimum_gain": float(np.min(gain)),
        "peak_before": float(np.max(np.abs(converted))),
        "peak_after": float(np.max(np.abs(adjusted))),
        "source_samples": int(source.size), "converted_samples": int(converted.size),
    }


def wav_rms(path: Path) -> float:
    """分块读取 PCM WAV，返回归一化 RMS。"""
    total_square = 0.0
    total_samples = 0
    with wave.open(str(path), "rb") as audio:
        if audio.getcomptype() != "NONE":
            raise ValueError("只支持未压缩 PCM WAV")
        sample_width = audio.getsampwidth()
        if sample_width not in (2, 4):
            raise ValueError("只支持 16 位或 32 位整数 PCM WAV")
        if audio.getnchannels() < 1 or audio.getframerate() < 1:
            raise ValueError("WAV 声道数或采样率无效")
        dtype = np.dtype("<i2" if sample_width == 2 else "<i4")
        scale = float(1 << (sample_width * 8 - 1))
        while True:
            payload = audio.readframes(65536)
            if not payload:
                break
            if len(payload) % sample_width:
                raise ValueError("PCM WAV 数据长度异常")
            samples = np.frombuffer(payload, dtype=dtype).astype(np.float64)
            if not np.all(np.isfinite(samples)):
                raise ValueError("PCM WAV 包含非有限样本")
            samples /= scale
            total_square += float(np.dot(samples, samples))
            total_samples += int(samples.size)
    if total_samples == 0:
        raise ValueError("PCM WAV 没有音频样本")
    result = math.sqrt(total_square / total_samples)
    if not math.isfinite(result):
        raise ValueError("WAV RMS 计算结果无效")
    return result


def accompaniment_mix_gain(
    accompaniment_rms: float,
    target_vocal_rms: float,
    ceiling: float = 0.35,
    gap_db: float = 9,
) -> float:
    """将伴奏 RMS 控制在人声目标以下，并限制最大增益。"""
    values = (accompaniment_rms, target_vocal_rms, ceiling, gap_db)
    if any(isinstance(value, bool) or not math.isfinite(value) for value in values):
        raise ValueError("混音增益参数必须是有限数值")
    if accompaniment_rms <= 0 or target_vocal_rms <= 0 or ceiling <= 0 or gap_db < 0:
        raise ValueError("混音增益参数超出有效范围")
    gain = min(ceiling, target_vocal_rms * (10 ** (-gap_db / 20)) / accompaniment_rms)
    if not math.isfinite(gain) or gain < 0:
        raise ValueError("伴奏增益计算结果无效")
    return gain


def vocal_clarity_filter() -> str:
    """先整理人声，再测电平和补音量。"""
    return (
        "highpass=f=60,equalizer=f=2400:t=q:w=0.8:g=2,"
        "acompressor=threshold=0.16:ratio=1.5:attack=15:release=120:makeup=1,"
        # 写入 PCM 前就限制尖峰，后面的混音限幅无法补救已经削波的样本。
        "alimiter=limit=0.90:attack=5:release=80:latency=1:level=false"
    )


def vocal_forward_mix_filter(
    vocal_gain: float,
    background_gain: float,
    master_gain: float,
    duration_seconds: float,
    fade_seconds: float = 0,
    *,
    prepared_vocal: bool = False,
) -> str:
    """保留伴奏，只提高人声，并限制峰值。"""
    values = (vocal_gain, background_gain, master_gain, duration_seconds, fade_seconds)
    if any(isinstance(value, bool) or not math.isfinite(value) for value in values):
        raise ValueError("混音滤镜参数必须是有限数值")
    if vocal_gain <= 0 or background_gain < 0 or master_gain <= 0:
        raise ValueError("混音增益超出有效范围")
    if duration_seconds <= 0 or fade_seconds < 0 or fade_seconds * 2 > duration_seconds:
        raise ValueError("混音时长或淡化时长无效")

    if type(prepared_vocal) is not bool:
        raise ValueError("人声处理标志必须是布尔值")
    vocal_filter = "" if prepared_vocal else vocal_clarity_filter() + ","
    # 已整理的人声是单声道；直接复制，避免自动上混削弱 3 dB。
    channels = "pan=stereo|c0=c0|c1=c0" if prepared_vocal else "aformat=channel_layouts=stereo"
    filters = (
        f"[0:a]{channels},{vocal_filter}"
        f"volume={vocal_gain:.10f}[vocal];"
        f"[1:a]aformat=channel_layouts=stereo,volume={background_gain:.10f}[background];"
        f"[vocal][background]amix=inputs=2:duration=longest:normalize=0,"
        f"volume={master_gain:.10f},alimiter=limit=0.95:latency=1:level=false"
    )
    if fade_seconds > 0:
        fade_start = duration_seconds - fade_seconds
        filters += (
            f",afade=t=in:st=0:d={fade_seconds:.10f}"
            f",afade=t=out:st={fade_start:.10f}:d={fade_seconds:.10f}"
        )
    return filters
