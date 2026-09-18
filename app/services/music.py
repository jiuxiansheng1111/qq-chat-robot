import json
import re
import unicodedata
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx

from app.config import Settings


@dataclass(frozen=True)
class MusicTrack:
    title: str
    artist: str
    album: str
    page_url: str
    preview_url: str
    cover_url: str
    duration_seconds: int


@dataclass(frozen=True)
class NeteaseTrack:
    song_id: str
    title: str
    artists: tuple[str, ...]
    album: str
    cover_url: str
    duration_seconds: int

    @property
    def artist(self) -> str:
        return "/".join(self.artists)

    @property
    def page_url(self) -> str:
        return f"https://music.163.com/#/song?id={self.song_id}"


@dataclass(frozen=True)
class MusicIdentity:
    title: str
    artist: str
    search_query: str


def parse_music_identity(value: str) -> MusicIdentity | None:
    value = value.strip()
    fenced = re.search(r"\{.*\}", value, re.DOTALL)
    if not fenced:
        return None
    try:
        data = json.loads(fenced.group(0))
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    title = str(data.get("title") or "").strip()[:120]
    artist = str(data.get("artist") or "").strip()[:100]
    search_query = str(data.get("search_query") or "").strip()[:120]
    if not title or not artist:
        return None
    return MusicIdentity(title, artist, search_query or f"{artist} {title}")


UNOFFICIAL_VERSION_MARKERS = (
    "cover",
    "remix",
    "karaoke",
    "instrumental",
    "piano",
    "acoustic",
    "翻唱",
    "伴奏",
    "纯音乐",
    "钢琴",
    "吉他",
    "原曲歌手",
    "カラオケ",
)


def normalize_music_text(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).casefold()
    return "".join(character for character in value if character.isalnum())


def music_query_suffixes(query: str) -> list[str]:
    """Return deterministic fallback title candidates from a mixed artist/title query."""
    tokens = query.split()
    if len(tokens) < 2:
        return []
    candidates: list[str] = []
    for start in range(1, len(tokens)):
        candidate = " ".join(tokens[start:]).strip()
        if candidate and candidate not in candidates:
            candidates.append(candidate)
    return candidates


def parse_netease_tracks(payload: dict) -> list[NeteaseTrack]:
    result = payload.get("result") if isinstance(payload, dict) else None
    songs = result.get("songs", []) if isinstance(result, dict) else []
    tracks: list[NeteaseTrack] = []
    for item in songs if isinstance(songs, list) else []:
        if not isinstance(item, dict):
            continue
        artist_items = item.get("artists") or item.get("ar") or []
        artists = tuple(
            str(artist.get("name", "")).strip()
            for artist in artist_items
            if isinstance(artist, dict) and str(artist.get("name", "")).strip()
        )
        album_data = item.get("album") or item.get("al") or {}
        song_id = str(item.get("id") or "").strip()
        title = str(item.get("name") or "").strip()
        if not song_id.isdigit() or not title or not artists:
            continue
        tracks.append(
            NeteaseTrack(
                song_id=song_id,
                title=title[:120],
                artists=artists,
                album=str(album_data.get("name") or "")[:120],
                cover_url=_https_url(album_data.get("picUrl"), ("music.126.net",)),
                duration_seconds=max(
                    0, int(item.get("duration") or item.get("dt") or 0) // 1000
                ),
            )
        )
    return tracks


def choose_netease_track(
    query: str, tracks: list[NeteaseTrack], expected_artist: str = "", expected_title: str = ""
) -> NeteaseTrack | None:
    """Choose deterministically and reject covers unless the user requested that version."""
    query_norm = normalize_music_text(query)
    artist_norm = normalize_music_text(expected_artist)
    title_norm = normalize_music_text(expected_title)
    requested_version = any(marker in query.casefold() for marker in UNOFFICIAL_VERSION_MARKERS)
    ranked: list[tuple[tuple[int, int, int, int], NeteaseTrack]] = []
    for index, track in enumerate(tracks):
        combined = f"{track.title} {track.artist}".casefold()
        unofficial = any(marker in combined for marker in UNOFFICIAL_VERSION_MARKERS)
        if unofficial and not requested_version:
            continue
        track_title = normalize_music_text(track.title)
        track_artists = [normalize_music_text(artist) for artist in track.artists]
        exact_title = bool(title_norm and track_title == title_norm)
        artist_match = bool(
            artist_norm
            and any(
                artist_norm == artist or artist_norm in artist or artist in artist_norm
                for artist in track_artists
            )
        )
        if artist_norm and not artist_match:
            continue
        if title_norm and track_title != title_norm:
            continue
        query_match = bool(track_title and track_title in query_norm)
        try:
            stable_id = int(track.song_id)
        except ValueError:
            stable_id = 10**20 + index
        score = (
            0 if artist_match else 1,
            0 if exact_title else 1,
            0 if query_match else 1,
            stable_id,
        )
        ranked.append((score, track))
    return min(ranked, key=lambda item: item[0])[1] if ranked else None


def netease_track_matches_query(query: str, track: NeteaseTrack) -> bool:
    query_norm = normalize_music_text(query)
    title_norm = normalize_music_text(track.title)
    if not query_norm or not title_norm:
        return False
    if query_norm == title_norm:
        return True
    return title_norm in query_norm and any(
        normalize_music_text(artist) in query_norm for artist in track.artists
    )


def _https_url(value: object, allowed_suffixes: tuple[str, ...]) -> str:
    url = str(value or "").strip()
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or not any(
        host == suffix or host.endswith("." + suffix) for suffix in allowed_suffixes
    ):
        return ""
    return url


def parse_deezer_tracks(payload: dict) -> list[MusicTrack]:
    items = payload.get("data") if isinstance(payload, dict) else None
    tracks: list[MusicTrack] = []
    seen: set[tuple[str, str]] = set()
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict) or item.get("explicit_lyrics") is True:
            continue
        artist_data = item.get("artist") if isinstance(item.get("artist"), dict) else {}
        album_data = item.get("album") if isinstance(item.get("album"), dict) else {}
        title = str(item.get("title_short") or item.get("title") or "").strip()
        artist = str(artist_data.get("name") or "").strip()
        preview = _https_url(item.get("preview"), ("dzcdn.net",))
        page = _https_url(item.get("link"), ("deezer.com",))
        if not title or not artist or not preview or not page:
            continue
        key = (title.casefold(), artist.casefold())
        if key in seen:
            continue
        seen.add(key)
        tracks.append(
            MusicTrack(
                title=title[:120],
                artist=artist[:100],
                album=str(album_data.get("title") or "")[:120],
                page_url=page,
                preview_url=preview,
                cover_url=_https_url(
                    album_data.get("cover_xl")
                    or album_data.get("cover_big")
                    or album_data.get("cover_medium"),
                    ("dzcdn.net",),
                ),
                duration_seconds=max(0, int(item.get("duration") or 0)),
            )
        )
    return tracks


async def search_music(query: str, settings: Settings) -> list[MusicTrack]:
    query = " ".join(query.split()).strip()[:120]
    if not query:
        return []
    async with httpx.AsyncClient(timeout=settings.music_timeout_seconds) as client:
        response = await client.get(
            f"{settings.music_api_url.rstrip('/')}/search",
            params={"q": query, "limit": max(1, min(settings.music_search_limit, 25))},
            headers={"User-Agent": "qq-chatrobot/0.1 (personal music discovery bot)"},
        )
        response.raise_for_status()
        return parse_deezer_tracks(response.json())


async def search_netease_music(query: str, settings: Settings) -> list[NeteaseTrack]:
    query = re.sub(r"\s+", " ", query).strip()[:120]
    if not query:
        return []
    headers = {
        "User-Agent": "Mozilla/5.0 qq-chatrobot/0.1",
        "Referer": "https://music.163.com/",
    }
    data = {
        "s": query,
        "type": "1",
        "limit": str(max(5, min(settings.music_search_limit, 25))),
        "offset": "0",
        "total": "true",
    }
    async with httpx.AsyncClient(timeout=settings.music_timeout_seconds) as client:
        response = await client.post(
            settings.netease_music_api_url, data=data, headers=headers
        )
        response.raise_for_status()
        return parse_netease_tracks(response.json())
