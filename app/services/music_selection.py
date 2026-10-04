"""点歌候选的短期状态与明确命令解析。"""

from __future__ import annotations

import re
import time
import unicodedata
from collections import OrderedDict
from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING
from urllib.parse import parse_qs, urlparse

if TYPE_CHECKING:
    from app.services.music import NeteaseTrack


MUSIC_SELECTION_TTL_SECONDS = 120
MUSIC_SELECTION_MAX_ENTRIES = 128
MUSIC_SELECTION_MAX_CANDIDATES = 5


@dataclass(frozen=True)
class MusicSelectionKey:
    self_id: str
    conversation_id: str
    user_id: str


@dataclass(frozen=True)
class MusicSelectionEntry:
    query: str
    tracks: tuple[NeteaseTrack, ...]
    token: int
    expires_at: float
    # 序号从 1 开始，与用户看到的候选编号一致。
    selected_index: int | None = None


@dataclass(frozen=True)
class MusicAction:
    action: str
    query: str = ""
    index: int | None = None


class MusicSelectionStore:
    """按账号、会话和用户隔离候选；方法不做网络或持久化操作。"""

    def __init__(self, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._entries: OrderedDict[MusicSelectionKey, MusicSelectionEntry] = OrderedDict()
        # 全局递增可避免取消后旧请求的回包撞上新请求代次。
        self._next_token = 0

    def _purge_expired(self, now: float) -> None:
        expired = [key for key, entry in self._entries.items() if entry.expires_at <= now]
        for key in expired:
            self._entries.pop(key, None)

    def _get_active(self, key: MusicSelectionKey) -> MusicSelectionEntry | None:
        now = self._clock()
        self._purge_expired(now)
        entry = self._entries.get(key)
        if entry is not None:
            self._entries.move_to_end(key)
        return entry

    def begin_search(self, key: MusicSelectionKey, query: str) -> int:
        """立即替换该用户旧候选，并返回本次搜索的代次。"""
        now = self._clock()
        self._purge_expired(now)
        self._next_token += 1
        entry = MusicSelectionEntry(
            query=query[:120],
            tracks=(),
            token=self._next_token,
            expires_at=now + MUSIC_SELECTION_TTL_SECONDS,
        )
        self._entries[key] = entry
        self._entries.move_to_end(key)
        while len(self._entries) > MUSIC_SELECTION_MAX_ENTRIES:
            self._entries.popitem(last=False)
        return entry.token

    def complete(
        self,
        key: MusicSelectionKey,
        token: int,
        tracks: Iterable[NeteaseTrack],
    ) -> bool:
        """只接收当前且未过期的搜索结果，并按歌曲 ID 去重。"""
        now = self._clock()
        self._purge_expired(now)
        entry = self._entries.get(key)
        if entry is None or entry.token != token or entry.expires_at <= now:
            return False

        unique_tracks: list[NeteaseTrack] = []
        seen_ids: set[str] = set()
        for track in tracks:
            raw_song_id = track.song_id
            if not isinstance(raw_song_id, str) or len(raw_song_id) > 20:
                continue
            if not re.fullmatch(r"[0-9]{1,20}", raw_song_id) or raw_song_id in seen_ids:
                continue
            seen_ids.add(raw_song_id)
            unique_tracks.append(track)
            if len(unique_tracks) >= MUSIC_SELECTION_MAX_CANDIDATES:
                break

        self._entries[key] = replace(entry, tracks=tuple(unique_tracks))
        self._entries.move_to_end(key)
        return True

    def get(self, key: MusicSelectionKey) -> MusicSelectionEntry | None:
        return self._get_active(key)

    def get_candidate(
        self, key: MusicSelectionKey, index: int
    ) -> NeteaseTrack | None:
        entry = self._get_active(key)
        if entry is None or index < 1 or index > len(entry.tracks):
            return None
        return entry.tracks[index - 1]

    def select(self, key: MusicSelectionKey, index: int) -> NeteaseTrack | None:
        """记录用户选中的 1 起始序号，并返回对应曲目。"""
        entry = self._get_active(key)
        if entry is None or index < 1 or index > len(entry.tracks):
            return None
        self._entries[key] = replace(entry, selected_index=index)
        self._entries.move_to_end(key)
        return entry.tracks[index - 1]

    def cancel(self, key: MusicSelectionKey) -> bool:
        self._purge_expired(self._clock())
        return self._entries.pop(key, None) is not None

    def clear(self) -> None:
        self._entries.clear()


_POLITE_PREFIX_RE = re.compile(
    r"^(?:你\s*)?(?:(?:能不能|可以|能)\s*)?"
    r"(?:(?:请|麻烦)\s*)?(?:(?:帮我|给我)\s*)*"
)
_DENIED_COMMAND_RE = re.compile(
    r"^(?:不要|别|不用|不必|无需)\s*(?:再\s*)?(?:(?:帮我|给我)\s*)*"
    r"(?:选歌|查歌词|取消选歌|歌单|点歌|来首|播放)(?:\s*\d+)?$"
)
_SELECTION_RE = re.compile(r"^选歌\s*([0-9]+)$")
_LYRICS_PREFIX = r"(?:查歌词|查看歌词)"
_LYRICS_INDEX_RE = re.compile(rf"^{_LYRICS_PREFIX}\s*([0-9]+)$")
_LYRICS_HELP_RE = re.compile(
    rf"^{_LYRICS_PREFIX}\s*(?:怎么用|如何用|如何使用|用法|帮助|"
    r"是什么|是什么意思|干什么|干嘛|啥意思)$",
    re.IGNORECASE,
)
_LYRICS_EMPTY_RE = re.compile(rf"^{_LYRICS_PREFIX}$")
_LYRICS_QUERY_RE = re.compile(rf"^{_LYRICS_PREFIX}\s+(.+)$", re.DOTALL)
_LYRICS_FUSED_QUERY_RE = re.compile(rf"^{_LYRICS_PREFIX}(.+)$", re.DOTALL)
_CANCEL_RE = re.compile(r"^取消\s*选歌$")
_PLAYLIST_RE = re.compile(r"^歌单\s*(.*?)$", re.DOTALL)
_SEARCH_RE = re.compile(r"^(点歌|来首|播放)\s*(.+)$", re.DOTALL)
_VIDEO_QUERY_RE = re.compile(
    r"^(?:(?:(?:一个|一段|这段|那段|这个|那个)?视频)|b站(?:视频)?|"
    r"哔哩哔哩(?:视频)?|bilibili(?:视频)?|"
    r"BV[0-9A-Za-z]+|https?://|www\.)",
    re.IGNORECASE,
)
_PLAYLIST_ID_RE = re.compile(r"[0-9]{1,20}")


def _clean_query(value: str) -> str:
    return " ".join(value.strip(" ：:").split())[:120]


def _playlist_id(value: str) -> str | None:
    """只接受纯 ID 或 music.163.com 上的 HTTPS 歌单链接。"""
    candidate = value.strip()
    if _PLAYLIST_ID_RE.fullmatch(candidate):
        return candidate
    if len(candidate) > 2048 or any(char.isspace() for char in candidate):
        return None

    try:
        parsed = urlparse(candidate)
    except ValueError:
        return None
    if (
        parsed.scheme.lower() != "https"
        or parsed.netloc.casefold() != "music.163.com"
        or parsed.hostname != "music.163.com"
        or parsed.username is not None
        or parsed.password is not None
    ):
        return None

    query = parsed.query
    is_playlist = parsed.path.rstrip("/") == "/playlist"
    if parsed.path in {"", "/"} and parsed.fragment:
        fragment_path, separator, fragment_query = parsed.fragment.partition("?")
        if fragment_path.rstrip("/") == "/playlist":
            is_playlist = True
            if separator:
                query = fragment_query
    if not is_playlist:
        return None

    values = parse_qs(query, keep_blank_values=True).get("id", [])
    if len(values) != 1 or not _PLAYLIST_ID_RE.fullmatch(values[0]):
        return None
    return values[0]


def parse_music_action(text: str) -> MusicAction | None:
    """识别音乐指令，裸数字和否定句不触发。"""
    if not isinstance(text, str) or not text or len(text) > 2048:
        return None
    value = unicodedata.normalize("NFKC", text).strip()
    if not value or _DENIED_COMMAND_RE.fullmatch(value):
        return None

    slash_command = value.startswith("/")
    if slash_command:
        value = value[1:].lstrip()

    if slash_command:
        music_match = re.fullmatch(r"music(?:\s+(.*))?", value, re.IGNORECASE | re.DOTALL)
        if music_match:
            query = _clean_query(music_match.group(1) or "")
            return MusicAction("search", query=query)

    value = _POLITE_PREFIX_RE.sub("", value, count=1).strip()
    if not value:
        return None

    if _CANCEL_RE.fullmatch(value):
        return MusicAction("cancel")

    if value in {"点歌", "来首", "播放"}:
        return MusicAction("search")

    match = _SELECTION_RE.fullmatch(value)
    if match:
        return MusicAction("select", index=int(match.group(1)))

    if _LYRICS_EMPTY_RE.fullmatch(value) or _LYRICS_HELP_RE.fullmatch(value):
        return MusicAction("lyrics_query")

    match = _LYRICS_INDEX_RE.fullmatch(value)
    if match:
        return MusicAction("lyrics_candidate", index=int(match.group(1)))

    match = _LYRICS_QUERY_RE.fullmatch(value)
    if match:
        query = _clean_query(match.group(1))
        return MusicAction("lyrics_query", query=query) if query else None

    match = _LYRICS_FUSED_QUERY_RE.fullmatch(value)
    if match:
        query = _clean_query(match.group(1))
        return MusicAction("lyrics_query", query=query) if query else None

    match = _PLAYLIST_RE.fullmatch(value)
    if match:
        playlist_id = _playlist_id(match.group(1))
        return MusicAction("playlist", query=playlist_id or "")

    match = _SEARCH_RE.fullmatch(value)
    if match:
        command, raw_query = match.groups()
        query = _clean_query(raw_query)
        if not query:
            return None
        if command == "播放" and _VIDEO_QUERY_RE.match(query):
            return None
        return MusicAction("search", query=query)

    return None
