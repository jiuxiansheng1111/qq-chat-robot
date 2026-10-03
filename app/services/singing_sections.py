"""用原唱人声的停顿来分段，连续覆盖原曲。"""

import math
import wave
from collections.abc import Sequence
from pathlib import Path

import numpy as np

from app.services.singing_excerpt import SingingExcerpt
from app.services.singing_sources import LyricLine


def plan_paused_sections(
    vocal_pcm: Path, lines: Sequence[LyricLine], duration_seconds: float,
    max_seconds: float = 115,
) -> tuple[SingingExcerpt, ...]:
    if not math.isfinite(duration_seconds) or duration_seconds <= 0:
        raise ValueError("歌曲时长无效")
    if not math.isfinite(max_seconds) or not 5 <= max_seconds <= 115:
        raise ValueError("语音单段时长必须在 5 到 115 秒之间")
    if duration_seconds <= max_seconds:
        return (SingingExcerpt(0, duration_seconds, "歌曲结尾"),)

    levels: list[float] = []
    with wave.open(str(vocal_pcm), "rb") as audio:
        rate = audio.getframerate()
        if audio.getnchannels() != 1 or audio.getsampwidth() != 2 or rate <= 0:
            raise ValueError("停顿分析需要单声道 16 位 PCM")
        frames = max(1, round(rate * 0.05))
        step = frames / rate
        while payload := audio.readframes(frames):
            values = np.frombuffer(payload, dtype="<i2").astype(np.float64) / 32768
            levels.append(float(np.sqrt(np.mean(values * values))))
    if not levels:
        raise ValueError("没有可分析的人声")
    threshold = max(0.0003, float(np.percentile(levels, 70)) * 0.04)
    pauses: list[tuple[float, float]] = []
    run_start: int | None = None
    for index, level in enumerate([*levels, float("inf")]):
        if level <= threshold:
            if run_start is None:
                run_start = index
        elif run_start is not None:
            if (index - run_start) * step >= 0.2:
                pauses.append((run_start * step + 0.05, index * step - 0.05))
            run_start = None
    lyric_times = sorted({
        line.time_seconds for line in lines
        if line.text.strip() and math.isfinite(line.time_seconds)
        and 0 < line.time_seconds < duration_seconds
    })
    sections: list[SingingExcerpt] = []
    start = 0.0
    while duration_seconds - start > max_seconds:
        # 给歌词换句兜底预留 0.2 秒，不占用两分钟余量。
        hard_end = start + max_seconds - 0.2
        target = hard_end - min(15, max_seconds * 0.15)
        minimum = start + min(5, max_seconds * 0.4)
        choices: list[tuple[float, float]] = []
        for left, right in pauses:
            left, right = max(left, minimum), min(right, hard_end)
            if left > right:
                continue
            at = min(max(target, left), right)
            nearby = [time for time in lyric_times if left <= time <= right]
            if nearby:
                at = min(nearby, key=lambda time: abs(time - target))
            sentence_distance = min((abs(at - time) for time in lyric_times), default=0)
            score = abs(at - target) + min(5, sentence_distance)
            choices.append((score, at))
        if not choices:
            boundaries = [time for time in lyric_times if minimum <= time <= hard_end]
            if not boundaries:
                raise ValueError("这段没有人声停顿或逐句歌词，暂时无法保证完整收句，请先用翻唱片段")
            # LRC 的下一句起点作为上一句的收尾点，QQ 段尾补一个短停顿。
            end = boundaries[-1]
            reason, pause = "歌词换句位置，段尾补短停顿", 0.18
        else:
            _, end = min(choices)
            reason, pause = "歌词附近的人声停顿", 0
        if not start < end <= hard_end:
            raise ValueError("人声分段位置无效")
        sections.append(SingingExcerpt(start, end, reason, pause))
        start = end
    sections.append(SingingExcerpt(start, duration_seconds, "歌曲结尾"))
    return tuple(sections)
