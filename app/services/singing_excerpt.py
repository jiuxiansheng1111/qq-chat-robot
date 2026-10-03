"""为翻唱选择短片段并整理对应歌词。"""

import math
import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from numbers import Real

from app.services.singing_sources import LyricLine

_HARD_MAX_SECONDS = 115.0
_FALLBACK_START_SECONDS = 30.0
_VOCAL_LEAD_SECONDS = 0.1
_PREFERRED_MIN_SECONDS = 15.0
_PREFERRED_MAX_SECONDS = 25.0

_CREDIT_LINE = re.compile(
    r"^(?:"
    r"作词|作曲|改编词(?:、曲)?|改编曲|词曲|词|曲|编曲|制作人|监制|出品人?|发行人?|混音|母带|录音|演唱|原唱|和声|企划|统筹|制作|专辑|歌名|歌曲名|"
    r"作詞|詞曲|詞|曲|編曲|歌詞|歌手|原曲|音楽|プロデューサー|レーベル|"
    r"lyrics?|words\s+and\s+music|music|written|written\s+and\s+performed|"
    r"composed|composer|songwriter|lyricist|producer|produced|arranged|arranger|arrangement|performed|vocals?|"
    r"original\s+(?:song|track)|song(?:\s+title)?|artist|singer|track|mix(?:ed)?|mastered|"
    r"recorded|recording|publisher|label|album|title|タイトル|"
    r"copyright|all\s+rights\s+reserved|©|℗"
    r")\s*(?::|=|[-–—]|\bby\b)\s*\S.*$",
    re.IGNORECASE,
)
_CREDIT_LABEL = re.compile(
    r"^(?:作词|作曲|改编词(?:、曲)?|改编曲|词曲|编曲|制作人|监制|出品人?|发行人?|混音|母带|录音|演唱|原唱|和声|企划|统筹|制作|专辑|歌名|歌曲名|"
    r"作詞|詞曲|詞|曲|編曲|歌詞|歌手|原曲|音楽|プロデューサー|レーベル|"
    r"lyrics?|words\s+and\s+music|music|written|composer|songwriter|lyricist|producer|"
    r"arranger|arrangement|performed|vocals?|artist|singer|song|track|タイトル|"
    r"copyright|all\s+rights\s+reserved|©|℗)$",
    re.IGNORECASE,
)


def _finite_number(value: object, name: str) -> float:
    """校验有限数值并统一转成浮点数。"""
    if isinstance(value, bool) or not isinstance(value, Real):
        raise TypeError(f"{name} 必须是有限数值")
    try:
        result = float(value)
    except (OverflowError, TypeError, ValueError) as exc:
        raise ValueError(f"{name} 必须是有限数值") from exc
    if not math.isfinite(result):
        raise ValueError(f"{name} 必须是有限数值")
    return result


def _positive_number(value: object, name: str) -> float:
    result = _finite_number(value, name)
    if result <= 0:
        raise ValueError(f"{name} 必须大于零")
    return result


def _normalized_text(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).split()).casefold()


def _is_credit_line(text: str) -> bool:
    candidate = unicodedata.normalize("NFKC", text).strip().strip("[]【】()（） ")
    return bool(_CREDIT_LINE.match(candidate) or _CREDIT_LABEL.fullmatch(candidate))


def _compact_text(value: str) -> str:
    return re.sub(r"[\W_]+", "", unicodedata.normalize("NFKC", value).casefold())


def _is_version_title_line(text: str, song_title: str | None, song_artists: Sequence[str]) -> bool:
    """只删除带版本标记且能匹配当前歌曲/歌手的标题行。"""
    if not song_title:
        return False
    normalized = unicodedata.normalize("NFKC", text).casefold()
    compact = _compact_text(text)
    has_version_marker = bool(
        re.search(r"\b(?:dj|remix|remaster(?:ed)?|mix)\b", normalized)
        or any(marker in compact for marker in ("dj版", "混音版", "remix版"))
    )
    if not has_version_marker:
        return False
    normalized_title = unicodedata.normalize("NFKC", song_title).casefold()
    base_title = re.sub(r"[（(][^）)]*[）)]", "", normalized_title)
    title_candidates = (_compact_text(song_title), _compact_text(base_title))
    title_matches = any(len(value) >= 2 and value in compact for value in title_candidates)
    artist_matches = any(
        len(value := _compact_text(artist)) >= 2 and value in compact
        for artist in song_artists
        if isinstance(artist, str) and artist.strip()
    )
    # 允许完整曲名本身带版本标记；否则要求同一行也带当前歌手名。
    exact_version_title = len(title_candidates[0]) >= 2 and title_candidates[0] in compact
    return title_matches and (artist_matches or exact_version_title)


def _clean_lyrics(
    lines: Sequence[LyricLine],
    duration_seconds: float,
    ignore_texts: Sequence[str] = (),
    *,
    song_title: str | None = None,
    song_artists: Sequence[str] = (),
) -> tuple[LyricLine, ...]:
    ignored = {
        _normalized_text(text)
        for text in ignore_texts
        if isinstance(text, str) and _normalized_text(text)
    }
    clean: list[tuple[int, LyricLine]] = []
    for index, line in enumerate(lines):
        try:
            raw_time = line.time_seconds
            timestamp = _finite_number(raw_time, "歌词时间")
            text = line.text
        except (AttributeError, TypeError, ValueError):
            continue
        if timestamp < 0 or timestamp >= duration_seconds or not isinstance(text, str):
            continue
        text = text.strip()
        normalized = _normalized_text(text)
        if (
            not normalized
            or normalized in ignored
            or _is_credit_line(text)
            or _is_version_title_line(text, song_title, song_artists)
        ):
            continue
        clean.append((index, LyricLine(timestamp, text)))
    clean.sort(key=lambda item: item[1].time_seconds)
    return tuple(line for _, line in clean)


@dataclass(frozen=True)
class SingingExcerpt:
    """原曲中的翻唱片段区间。"""

    start_seconds: float
    end_seconds: float
    reason: str
    pause_seconds: float = 0

    def __post_init__(self) -> None:
        start = _finite_number(self.start_seconds, "片段起点")
        end = _finite_number(self.end_seconds, "片段终点")
        if start < 0 or end <= start:
            raise ValueError("片段时长必须大于零，且起点不能小于零")
        if not isinstance(self.reason, str):
            raise TypeError("片段原因必须是文本")
        pause = _finite_number(self.pause_seconds, "段尾停顿")
        if not 0 <= pause <= 1:
            raise ValueError("段尾停顿必须在零到一秒之间")
        object.__setattr__(self, "start_seconds", start)
        object.__setattr__(self, "end_seconds", end)
        object.__setattr__(self, "pause_seconds", pause)

    @property
    def duration_seconds(self) -> float:
        return self.end_seconds - self.start_seconds


def select_singing_excerpt(
    lines: Sequence[LyricLine],
    duration_seconds: float,
    target_seconds: float = 20,
    max_seconds: float = 115,
    *,
    ignore_texts: Sequence[str] = (),
    song_title: str | None = None,
    song_artists: Sequence[str] = (),
) -> SingingExcerpt:
    """从首句有效歌词处优先选取约二十秒片段。"""
    duration = _positive_number(duration_seconds, "歌曲时长")
    target = _positive_number(target_seconds, "目标片段时长")
    requested_max = _positive_number(max_seconds, "片段时长上限")
    hard_max = min(requested_max, _HARD_MAX_SECONDS)
    target = min(target, hard_max)
    usable = _clean_lyrics(
        lines, duration, ignore_texts,
        song_title=song_title, song_artists=song_artists,
    )

    if not usable:
        length = min(target, hard_max, duration)
        start = min(_FALLBACK_START_SECONDS, max(0.0, duration - length))
        return SingingExcerpt(start, start + length, "无有效歌词，取约三十秒处")

    start = max(0.0, usable[0].time_seconds - _VOCAL_LEAD_SECONDS)
    max_end = min(duration, start + hard_max)
    if max_end <= start:
        raise ValueError("歌曲时长不足以形成有效片段")

    # 歌词起点和歌曲结尾都是可用的自然结束边界。
    endings = {
        line.time_seconds
        for line in usable
        if start < line.time_seconds <= max_end
    }
    if duration <= max_end:
        endings.add(duration)

    candidates: list[tuple[float, int]] = []
    for end in sorted(endings):
        length = end - start
        if length <= 0 or length > hard_max:
            continue
        count = sum(start <= line.time_seconds < end for line in usable)
        if count:
            candidates.append((end, count))

    preferred = [
        (end, count)
        for end, count in candidates
        if count >= 2
        and _PREFERRED_MIN_SECONDS <= end - start <= _PREFERRED_MAX_SECONDS
    ]
    if preferred:
        after_target = [item for item in preferred if item[0] - start >= target]
        if after_target:
            end, _ = min(after_target, key=lambda item: item[0])
        else:
            end, _ = max(preferred, key=lambda item: item[0])
        reason = "按歌词起点对齐，保留起音余量"
        return SingingExcerpt(start, end, reason)

    # 歌词稀疏时只在略长于目标的范围内延长。
    reasonable_max = min(hard_max, max(_PREFERRED_MAX_SECONDS, target + 10.0))
    sparse = [
        (end, count)
        for end, count in candidates
        if count >= 2 and _PREFERRED_MIN_SECONDS <= end - start <= reasonable_max
    ]
    if sparse:
        after_target = [item for item in sparse if item[0] - start >= target]
        if after_target:
            end, _ = min(after_target, key=lambda item: item[0])
        else:
            end, _ = max(sparse, key=lambda item: item[0])
        return SingingExcerpt(start, end, "歌词较稀疏，按可用歌词边界收尾")

    # 只有一句歌词或没有可用结束边界时，按目标长度安全回退。
    fallback_end = min(max_end, start + target)
    if duration <= max_end:
        fallback_end = min(duration, max(fallback_end, start))
    if fallback_end <= start:
        raise ValueError("歌曲时长不足以形成有效片段")
    return SingingExcerpt(start, fallback_end, "歌词行较少，按目标时长截取")


def shift_excerpt_lyrics(
    lines: Sequence[LyricLine], excerpt: SingingExcerpt
) -> tuple[LyricLine, ...]:
    """移除片段外歌词，并把剩余时间改为相对片段起点。"""
    if not isinstance(excerpt, SingingExcerpt):
        raise TypeError("片段参数无效")
    shifted: list[tuple[int, LyricLine]] = []
    for index, line in enumerate(_clean_lyrics(lines, excerpt.end_seconds)):
        timestamp = line.time_seconds
        if timestamp < excerpt.start_seconds:
            continue
        shifted.append(
            (index, LyricLine(timestamp - excerpt.start_seconds, line.text))
        )
    shifted.sort(key=lambda item: item[1].time_seconds)
    return tuple(line for _, line in shifted)


def format_excerpt_lrc(lines: Sequence[LyricLine]) -> str:
    """把片段内的相对歌词时间格式化为 LRC。"""
    formatted: list[tuple[float, int, str]] = []
    for index, line in enumerate(lines):
        try:
            timestamp = _finite_number(line.time_seconds, "歌词时间")
            text = line.text
        except (AttributeError, TypeError, ValueError):
            continue
        if timestamp < 0 or not isinstance(text, str) or not text.strip():
            continue
        formatted.append((timestamp, index, " ".join(text.split())))

    output: list[str] = []
    for timestamp, _, text in sorted(formatted, key=lambda item: (item[0], item[1])):
        total_millis = round(timestamp * 1000)
        minutes, remainder = divmod(total_millis, 60_000)
        seconds, millis = divmod(remainder, 1000)
        output.append(f"[{minutes:02d}:{seconds:02d}.{millis:03d}]{text}")
    return "\n".join(output)


def plan_singing_sections(
    lines: Sequence[LyricLine],
    duration_seconds: float,
    max_seconds: float = 115,
) -> tuple[SingingExcerpt, ...]:
    """连续切分整首歌曲，并尽量在接近时长上限前按歌词起点落刀。"""
    duration = _positive_number(duration_seconds, "歌曲时长")
    requested_max = _positive_number(max_seconds, "片段时长上限")
    hard_max = min(requested_max, _HARD_MAX_SECONDS)
    usable = _clean_lyrics(lines, duration)
    lyric_times = sorted({line.time_seconds for line in usable})

    sections: list[SingingExcerpt] = []
    start = 0.0
    while duration - start > hard_max:
        hard_end = start + hard_max
        margin = min(20.0, hard_max * 0.2)
        target_end = hard_end - margin
        window_start = max(
            start + hard_max * 0.5,
            target_end - max(2.0, margin * 0.5),
        )
        boundaries = [
            timestamp
            for timestamp in lyric_times
            if window_start <= timestamp <= hard_end and timestamp > start
        ]
        after_target = [timestamp for timestamp in boundaries if timestamp >= target_end]
        if after_target:
            end = min(after_target)
            reason = "按下一句歌词起点切分"
        elif boundaries:
            end = max(boundaries)
            reason = "按附近歌词起点切分"
        else:
            end = hard_end
            reason = "needs_silence_boundary"
        if end <= start:
            end = hard_end
            reason = "needs_silence_boundary"
        sections.append(SingingExcerpt(start, end, reason))
        start = end

    if duration > start:
        sections.append(SingingExcerpt(start, duration, "歌曲结尾"))
    return tuple(sections)
