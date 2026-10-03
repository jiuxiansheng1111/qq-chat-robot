"""Resolve songs, timed lyrics, and explicitly available audio for AI singing.

Local originals belong in ``data/singing/songs.json`` as
``{"12345": "songs/example.mp3"}`` (or ``{"12345": {"path": "..."}}``).
All manifest paths are confined to ``data/singing``. Remote audio is accepted
only when NetEase's anonymous player API returns a full, public CDN source.
"""

import json
import os
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urljoin, urlparse

import httpx

from app.config import Settings
from app.services.music import (
    NeteaseTrack,
    choose_netease_track,
    music_query_suffixes,
    netease_track_matches_query,
    normalize_music_text,
    parse_netease_tracks,
    search_netease_music,
)

SINGING_DATA_ROOT = Path(__file__).resolve().parents[2] / "data" / "singing"
_LYRIC_URL = "https://music.163.com/api/song/lyric"
_DETAIL_URL = "https://music.163.com/api/song/detail"
_PLAYER_URL = "https://music.163.com/api/song/enhance/player/url"
_HEADERS = {"User-Agent": "Mozilla/5.0 qq-chatrobot/0.1", "Referer": "https://music.163.com/"}
_TIME_TAG = re.compile(r"\[(\d{1,3}):(\d{2})(?:\.(\d{1,3}))?\]")
_OFFSET_TAG = re.compile(r"\[offset:([+-]?\d+)\]", re.IGNORECASE)
_AUDIO_EXTENSIONS = frozenset({".mp3", ".m4a", ".flac", ".wav", ".ogg"})
_MAX_LYRICS_BYTES = 256 * 1024
_MAX_MANIFEST_BYTES = 1024 * 1024
_DEFAULT_MAX_SOURCE_BYTES = 100 * 1024 * 1024


class SingingSourceError(RuntimeError):
    """A full, usable singing source could not be resolved."""


@dataclass(frozen=True)
class LyricLine:
    time_seconds: float
    text: str


@dataclass(frozen=True)
class SingingSong:
    track: NeteaseTrack
    lyrics_text: str
    lyric_lines: tuple[LyricLine, ...]
    source_path: Path | None
    source_url: str | None
    source_size_bytes: int | None = None


def parse_lrc(value: str) -> tuple[LyricLine, ...]:
    """Parse ordinary LRC, including repeated timestamps and millisecond offset."""
    if len(value.encode("utf-8")) > _MAX_LYRICS_BYTES:
        raise SingingSourceError("歌词文件过大")
    offset_tag = _OFFSET_TAG.search(value)
    offset_ms = int(offset_tag.group(1)) if offset_tag else 0
    lines: list[LyricLine] = []
    for raw in value.splitlines():
        tags = list(_TIME_TAG.finditer(raw))
        if not tags:
            continue
        lyric = _TIME_TAG.sub("", raw).strip()
        if not lyric:
            continue
        for tag in tags:
            minute, second = int(tag.group(1)), int(tag.group(2))
            if second >= 60:
                continue
            fraction = tag.group(3) or "0"
            millisecond = int(fraction.ljust(3, "0"))
            timestamp_ms = max(0, minute * 60_000 + second * 1000 + millisecond + offset_ms)
            lines.append(LyricLine(timestamp_ms / 1000, lyric))
    return tuple(sorted(lines, key=lambda line: line.time_seconds))


def _max_source_bytes(settings: Settings) -> int:
    value = getattr(settings, "singing_max_source_bytes", _DEFAULT_MAX_SOURCE_BYTES)
    try:
        return max(1, min(int(value), 500 * 1024 * 1024))
    except (ValueError, TypeError):
        return _DEFAULT_MAX_SOURCE_BYTES


def _confined_path(value: str | Path) -> Path:
    root = SINGING_DATA_ROOT.resolve()
    candidate = Path(value)
    if not candidate.is_absolute():
        candidate = root / candidate
    resolved = candidate.resolve()
    if not resolved.is_relative_to(root):
        raise SingingSourceError("本地歌曲路径必须位于 data/singing 内")
    return resolved


def _local_source(song_id: str, settings: Settings) -> tuple[Path | None, Path | None]:
    manifest = SINGING_DATA_ROOT / "songs.json"
    if not manifest.exists():
        return None, None
    if manifest.stat().st_size > _MAX_MANIFEST_BYTES:
        raise SingingSourceError("本地歌曲清单过大")
    try:
        mapping = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SingingSourceError("本地歌曲清单无法读取") from exc
    if not isinstance(mapping, dict):
        raise SingingSourceError("本地歌曲清单应为按歌曲 ID 索引的对象")
    item = mapping.get(song_id)
    if item is None:
        return None, None
    if isinstance(item, str):
        source_value, lyric_value = item, None
    elif isinstance(item, dict):
        source_value, lyric_value = item.get("path"), item.get("lyrics_path")
    else:
        raise SingingSourceError("本地歌曲清单中的歌曲路径无效")
    if not isinstance(source_value, str) or not source_value.strip():
        raise SingingSourceError("本地歌曲清单中的歌曲路径无效")
    source = _confined_path(source_value)
    if source.suffix.lower() not in _AUDIO_EXTENSIONS or not source.is_file():
        raise SingingSourceError("本地原曲文件不存在或格式不受支持")
    if not 0 < source.stat().st_size <= _max_source_bytes(settings):
        raise SingingSourceError("本地原曲文件大小无效")
    lyric_path = None
    if lyric_value is not None:
        if not isinstance(lyric_value, str):
            raise SingingSourceError("本地歌词路径无效")
        lyric_path = _confined_path(lyric_value)
        if lyric_path.suffix.lower() != ".lrc" or not lyric_path.is_file():
            raise SingingSourceError("本地歌词文件不存在或格式不受支持")
    return source, lyric_path


def _cdn_url(value: object, *, upgrade_http: bool = False) -> str:
    url = str(value or "").strip()
    if not url or any(ord(char) < 32 or char in " \\" for char in url):
        return ""
    try:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        port = parsed.port
    except ValueError:
        return ""
    if (
        parsed.scheme not in ({"https", "http"} if upgrade_http else {"https"})
        or not (host == "music.126.net" or host.endswith(".music.126.net"))
        or port not in (None, 443)
        or parsed.username is not None
        or parsed.password is not None
        or not parsed.path
        or parsed.fragment
    ):
        return ""
    # The anonymous player API currently emits HTTP CDN links. Rewrite only
    # a validated official host; every actual download still uses HTTPS.
    return parsed._replace(scheme="https").geturl() if parsed.scheme == "http" else url


def _is_trial(entry: dict) -> bool:
    if entry.get("freeTrialInfo"):
        return True
    privilege = entry.get("freeTrialPrivilege")
    if not isinstance(privilege, dict):
        return False
    return any(
        privilege.get(key) not in (None, False, 0, "")
        for key in (
            "resConsumable",
            "userConsumable",
            "listenType",
            "cannotListenReason",
            "freeLimitTagType",
        )
    )


def _select_track(query: str, tracks: list[NeteaseTrack]) -> NeteaseTrack | None:
    normalized = normalize_music_text(query)
    named = [
        track
        for track in tracks
        if normalize_music_text(track.title) in normalized
        and any(normalize_music_text(artist) in normalized for artist in track.artists)
    ]
    if named:
        named.sort(key=lambda track: max(len(normalize_music_text(a)) for a in track.artists), reverse=True)
        for track in named:
            named_artist = next(
                artist for artist in track.artists if normalize_music_text(artist) in normalized
            )
            selected = choose_netease_track(
                query, tracks, expected_artist=named_artist, expected_title=track.title
            )
            if selected:
                return selected
    selected = choose_netease_track(query, tracks)
    return selected if selected and netease_track_matches_query(query, selected) else None


def _version_tags(value: str) -> set[str]:
    normalized = normalize_music_text(value)
    tags = set()
    if "dj版" in normalized or "djmix" in normalized or "dj混音" in normalized or re.search(
        r"[（(]\s*dj\s*[）)]", value, re.IGNORECASE
    ):
        tags.add("dj")
    if "remix" in normalized or "混音版" in normalized:
        tags.add("remix")
    if "live版" in normalized or "现场版" in normalized or re.search(
        r"[（(]\s*live\s*[）)]", value, re.IGNORECASE
    ):
        tags.add("live")
    return tags


async def _search_track(query: str, settings: Settings) -> NeteaseTrack:
    explicit = re.split(r"\s*[/／]\s*|\s+[-–—]\s+", query, maxsplit=1)
    title_artist = (explicit[0].strip(), explicit[1].strip()) if len(explicit) == 2 else None
    if title_artist and all(title_artist):
        title, artist = title_artist
        candidates = [query, f"{artist} {title}", title]
    else:
        title_artist = None
        candidates = [query, *music_query_suffixes(query)]
    requested_versions = _version_tags(query)
    for candidate in dict.fromkeys(candidates):
        tracks = [
            track
            for track in await search_netease_music(candidate, settings)
            if _version_tags(track.title) == requested_versions
        ]
        if title_artist:
            selected = choose_netease_track(
                query, tracks, expected_title=title_artist[0], expected_artist=title_artist[1]
            )
            if selected:
                return selected
            continue
        selected = _select_track(query, tracks)
        if selected:
            return selected
        selected = choose_netease_track(candidate, tracks, expected_title=candidate)
        if selected and candidate != query:
            return selected
    raise SingingSourceError("未找到匹配的网易云歌曲，请提供歌名和歌手名")


async def _detail_track(client: httpx.AsyncClient, track: NeteaseTrack) -> NeteaseTrack:
    if track.duration_seconds > 0:
        return track
    response = await client.get(_DETAIL_URL, params={"ids": f"[{track.song_id}]"}, headers=_HEADERS)
    response.raise_for_status()
    payload = response.json()
    songs = payload.get("songs", []) if isinstance(payload, dict) else []
    parsed = parse_netease_tracks({"result": {"songs": songs}})
    return next((item for item in parsed if item.song_id == track.song_id), track)


async def _lyrics(client: httpx.AsyncClient, song_id: str, local: Path | None) -> str:
    if local is not None:
        if local.stat().st_size > _MAX_LYRICS_BYTES:
            raise SingingSourceError("本地歌词文件过大")
        try:
            return local.read_text(encoding="utf-8-sig")
        except (OSError, UnicodeError) as exc:
            raise SingingSourceError("本地歌词文件无法读取") from exc
    response = await client.get(
        _LYRIC_URL, params={"id": song_id, "lv": "-1", "kv": "1", "tv": "1"}, headers=_HEADERS
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict) or payload.get("code") not in (None, 200):
        raise SingingSourceError("网易云歌词接口未返回可用歌词")
    lrc = payload.get("lrc")
    value = lrc.get("lyric") if isinstance(lrc, dict) else None
    return value if isinstance(value, str) else ""


async def _public_source(
    client: httpx.AsyncClient, track: NeteaseTrack, settings: Settings
) -> tuple[str, int]:
    response = await client.get(
        _PLAYER_URL,
        params={"id": track.song_id, "ids": f"[{track.song_id}]", "br": "128000"},
        headers=_HEADERS,
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict) or payload.get("code") != 200:
        raise SingingSourceError("网易云未提供公开可播放的原曲")
    entries = payload.get("data")
    if not isinstance(entries, list):
        raise SingingSourceError("网易云未提供公开可播放的原曲")
    entry = next(
        (item for item in entries if isinstance(item, dict) and str(item.get("id")) == track.song_id),
        None,
    )
    if not entry or _is_trial(entry):
        raise SingingSourceError("原曲仅提供试听片段或需要授权")
    url = _cdn_url(entry.get("url"), upgrade_http=True)
    try:
        size = int(entry.get("size") or 0)
    except (TypeError, ValueError):
        size = 0
    if not url or not 0 < size <= _max_source_bytes(settings):
        raise SingingSourceError("网易云未提供安全且完整的公开音源")
    if track.duration_seconds <= 0 or size < track.duration_seconds * 5000:
        raise SingingSourceError("音源大小不足以覆盖整首歌曲")
    return url, size


async def resolve_singing_song(query: str, settings: Settings) -> SingingSong:
    """Find a song and its timed lyrics, then a local or public full audio source."""
    query = " ".join(query.split()).strip()[:120]
    if not query:
        raise SingingSourceError("请提供歌名，建议同时提供歌手名")
    track = await _search_track(query, settings)
    source_path, local_lyrics = _local_source(track.song_id, settings)
    timeout = getattr(settings, "music_timeout_seconds", 10)
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
            if source_path is None:
                track = await _detail_track(client, track)
            lyrics_text = await _lyrics(client, track.song_id, local_lyrics)
            lyric_lines = parse_lrc(lyrics_text)
            if not lyric_lines:
                raise SingingSourceError("这首歌没有可用于分段的时间轴歌词")
            if source_path is not None:
                return SingingSong(track, lyrics_text, lyric_lines, source_path, None)
            source_url, source_size = await _public_source(client, track, settings)
            return SingingSong(track, lyrics_text, lyric_lines, None, source_url, source_size)
    except (httpx.HTTPError, ValueError, json.JSONDecodeError) as exc:
        raise SingingSourceError("歌曲信息或音源暂时无法读取") from exc


async def download_singing_source(song: SingingSong, job_dir: Path, settings: Settings) -> Path:
    """Stream a validated CDN file into a job directory under data/singing."""
    if song.source_path is not None:
        source = _confined_path(song.source_path)
        if not source.is_file():
            raise SingingSourceError("本地原曲文件不存在")
        if not 0 < source.stat().st_size <= _max_source_bytes(settings):
            raise SingingSourceError("本地原曲文件大小无效")
        return source
    url = _cdn_url(song.source_url)
    if not url:
        raise SingingSourceError("没有可下载的完整原曲")
    directory = _confined_path(job_dir)
    directory.mkdir(parents=True, exist_ok=True)
    extension = Path(urlparse(url).path).suffix.lower()
    if extension not in _AUDIO_EXTENSIONS:
        extension = ".mp3"
    destination = directory / f"original{extension}"
    partial = directory / f".{uuid.uuid4().hex}.partial"
    max_bytes = _max_source_bytes(settings)
    timeout = getattr(settings, "music_timeout_seconds", 10)
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
            for _ in range(5):
                async with client.stream("GET", url, headers=_HEADERS, follow_redirects=False) as response:
                    if response.status_code in (301, 302, 303, 307, 308):
                        redirected = _cdn_url(urljoin(url, response.headers.get("location", "")))
                        if not redirected:
                            raise SingingSourceError("音源跳转到了未获准的地址")
                        url = redirected
                        continue
                    response.raise_for_status()
                    content_type = response.headers.get("content-type", "").split(";", 1)[0].lower()
                    if not (
                        content_type.startswith("audio/")
                        or content_type in {"application/octet-stream", "binary/octet-stream"}
                    ):
                        raise SingingSourceError("音源服务器没有返回音频文件")
                    try:
                        declared_size = int(response.headers.get("content-length", "0"))
                    except ValueError:
                        declared_size = 0
                    if declared_size > max_bytes:
                        raise SingingSourceError("音源文件超过大小限制")
                    byte_count = 0
                    with partial.open("xb") as output:
                        async for chunk in response.aiter_bytes(64 * 1024):
                            byte_count += len(chunk)
                            if byte_count > max_bytes:
                                raise SingingSourceError("音源文件超过大小限制")
                            output.write(chunk)
                    if not byte_count or (declared_size and byte_count != declared_size):
                        raise SingingSourceError("音源下载不完整")
                    if song.source_size_bytes and byte_count < song.source_size_bytes * 0.9:
                        raise SingingSourceError("音源下载结果短于完整歌曲")
                    if song.track.duration_seconds and byte_count < song.track.duration_seconds * 5000:
                        raise SingingSourceError("音源下载结果短于完整歌曲")
                    os.replace(partial, destination)
                    return destination
            raise SingingSourceError("音源跳转次数过多")
    except httpx.HTTPError as exc:
        raise SingingSourceError("音源下载失败或需要授权") from exc
    finally:
        partial.unlink(missing_ok=True)
