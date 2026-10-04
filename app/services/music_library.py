"""候选、歌词和公开歌单；账号登录仍由本机会员桥管理。"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, replace
from urllib.parse import urlparse

import httpx

from app.config import Settings
from app.services.bounded_http import request_bytes, request_json
from app.services.music import (
    NeteaseTrack,
    netease_track_matches_query,
    parse_netease_tracks,
    rank_netease_tracks,
    search_netease_music,
)

_HEADERS = {"User-Agent": "Mozilla/5.0", "Referer": "https://music.163.com/"}
_ID = re.compile(r"[0-9]{1,20}")


@dataclass(frozen=True)
class MusicPlaylist:
    playlist_id: str
    title: str
    tracks: tuple[NeteaseTrack, ...]
    total: int


async def search_music_candidates(query: str, settings: Settings) -> list[NeteaseTrack]:
    tracks = await search_netease_music(query, settings)
    ranked = rank_netease_tracks(query, tracks)
    matched = [track for track in ranked if netease_track_matches_query(query, track)]
    # 没有精确命中时仍给候选，由用户确认，不把第一首当作原唱。
    return await _fill_missing_covers((matched or ranked)[:5], settings)


async def _fill_missing_covers(
    tracks: list[NeteaseTrack], settings: Settings,
) -> list[NeteaseTrack]:
    missing = [track.song_id for track in tracks if not track.cover_url]
    if not missing:
        return tracks
    try:
        async with httpx.AsyncClient(trust_env=False) as client:
            payload = await request_json(
                client, "GET", "https://music.163.com/api/song/detail",
                params={"ids": json.dumps([int(song_id) for song_id in missing])},
                headers=_HEADERS, max_bytes=512 * 1024,
                timeout_seconds=min(6, max(1, settings.music_timeout_seconds)),
            )
        songs = payload.get("songs") if isinstance(payload, dict) else None
        details = parse_netease_tracks({"result": {"songs": songs}})
    except (httpx.HTTPError, TimeoutError, ValueError):
        return tracks
    by_id = {track.song_id: track for track in details}
    # 只补封面和缺失时长，歌名、歌手及别名仍取本次搜索结果。
    return [
        replace(
            track, cover_url=by_id[track.song_id].cover_url,
            duration_seconds=track.duration_seconds or by_id[track.song_id].duration_seconds,
        ) if not track.cover_url and track.song_id in by_id else track
        for track in tracks
    ]


async def fetch_music_lyrics(track: NeteaseTrack, settings: Settings) -> str:
    if not _ID.fullmatch(track.song_id):
        raise ValueError("歌曲编号无效")
    async with httpx.AsyncClient(trust_env=False) as client:
        payload = await request_json(
            client, "GET", "https://music.163.com/api/song/lyric",
            params={"id": track.song_id, "lv": "-1", "kv": "1", "tv": "1"},
            headers=_HEADERS, max_bytes=256 * 1024,
            timeout_seconds=max(1, settings.music_timeout_seconds),
        )
    if not isinstance(payload, dict) or payload.get("code") not in (None, 200):
        raise ValueError("暂时没有查到这首歌的歌词")
    section = payload.get("lrc")
    lyrics = section.get("lyric") if isinstance(section, dict) else None
    if not isinstance(lyrics, str) or not lyrics.strip():
        raise ValueError("这首歌暂无歌词，可能是纯音乐")
    if len(lyrics) > 12_000:
        raise ValueError("歌词太长，暂时无法生成歌词图")
    return lyrics


async def fetch_music_playlist(playlist_id: str, settings: Settings) -> MusicPlaylist:
    if not _ID.fullmatch(playlist_id):
        raise ValueError("请提供网易云歌单编号或歌单链接")
    async with httpx.AsyncClient(trust_env=False) as client:
        payload = await request_json(
            client, "GET", "https://music.163.com/api/playlist/detail",
            params={"id": playlist_id, "n": "5"}, headers=_HEADERS,
            max_bytes=1024 * 1024, timeout_seconds=max(1, settings.music_timeout_seconds),
        )
    if not isinstance(payload, dict) or payload.get("code") not in (None, 200):
        raise ValueError("歌单暂时无法读取，请确认它是公开歌单")
    section = payload.get("playlist") or payload.get("result")
    if not isinstance(section, dict) or str(section.get("id")) != playlist_id:
        raise ValueError("没有查到对应的公开歌单")
    raw_tracks = section.get("tracks")
    tracks = parse_netease_tracks({"result": {"songs": raw_tracks[:5]}}) if isinstance(
        raw_tracks, list,
    ) else []
    if not tracks:
        raise ValueError("歌单中没有可展示的歌曲")
    try:
        total = max(len(tracks), int(section.get("trackCount") or len(tracks)))
    except (TypeError, ValueError):
        total = len(tracks)
    return MusicPlaylist(
        playlist_id, str(section.get("name") or "网易云歌单")[:120], tuple(tracks), total,
    )


def _cover_url(value: str) -> str:
    try:
        parsed = urlparse(value)
        host = (parsed.hostname or "").lower()
        allowed = host == "music.126.net" or host.endswith(".music.126.net")
        if (
            len(value) > 2048 or parsed.scheme != "https" or not allowed
            or parsed.port not in (None, 443) or parsed.username or parsed.password
            or parsed.fragment
        ):
            return ""
    except ValueError:
        return ""
    return value


async def fetch_music_covers(
    tracks: list[NeteaseTrack], settings: Settings,
) -> dict[str, bytes]:
    gate = asyncio.Semaphore(2)
    async with httpx.AsyncClient(
        trust_env=False, limits=httpx.Limits(max_connections=2, max_keepalive_connections=2),
    ) as client:
        async def fetch(track: NeteaseTrack) -> tuple[str, bytes] | None:
            url = _cover_url(track.cover_url)
            if not url:
                return None
            async with gate:
                try:
                    data = await request_bytes(
                        client, "GET", url, max_bytes=4 * 1024 * 1024,
                        timeout_seconds=min(6, max(1, settings.music_timeout_seconds)),
                    )
                    return url, data
                except (httpx.HTTPError, TimeoutError, ValueError):
                    return None
        results = await asyncio.gather(*(fetch(track) for track in tracks[:5]))
    return dict(result for result in results if result is not None)
