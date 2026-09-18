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
