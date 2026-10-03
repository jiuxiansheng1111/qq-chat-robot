"""翻唱音量分析与混音滤镜。"""

from __future__ import annotations

import math
import wave
from pathlib import Path

import numpy as np


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


def vocal_forward_mix_filter(
    vocal_gain: float,
    background_gain: float,
    master_gain: float,
    duration_seconds: float,
    fade_seconds: float = 0,
) -> str:
    """保留伴奏，只提高人声，并限制峰值。"""
    values = (vocal_gain, background_gain, master_gain, duration_seconds, fade_seconds)
    if any(isinstance(value, bool) or not math.isfinite(value) for value in values):
        raise ValueError("混音滤镜参数必须是有限数值")
    if vocal_gain <= 0 or background_gain < 0 or master_gain <= 0:
        raise ValueError("混音增益超出有效范围")
    if duration_seconds <= 0 or fade_seconds < 0 or fade_seconds * 2 > duration_seconds:
        raise ValueError("混音时长或淡化时长无效")

    filters = (
        f"[0:a]aformat=channel_layouts=stereo,highpass=f=60,"
        f"volume={vocal_gain:.10f},"
        "acompressor=threshold=0.32:ratio=1.5:attack=20:release=120:makeup=1[vocal];"
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
