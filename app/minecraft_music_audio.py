"""Public full-song source validation and bounded OGG assets for the game bridge."""
from __future__ import annotations

import asyncio
import json
import logging
import math
import re
import tempfile
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urljoin

from app.config import Settings
from app.minecraft_voice_runtime import run_worker
from app.services.audio_chunks import locate_audio_tool
from app.services.bounded_http import request_json
from app.services.http_routing import outbound_http_client
from app.services.music import NeteaseTrack, parse_netease_tracks
from app.services.singing_sources import _cdn_url, _validated_source_payload

MAX_SECONDS = 12 * 60
MAX_ASSET_BYTES = 16 * 1024 * 1024
MAX_CACHE_BYTES = 256 * 1024 * 1024
ASSET_TTL_SECONDS = 6 * 60 * 60
MAX_SOURCE_BYTES = 100 * 1024 * 1024
_ASSET_ID = re.compile(r"[0-9a-f]{32}")
_HEADERS = {"User-Agent": "Mozilla/5.0 qq-chatrobot-minecraft/1", "Referer": "https://music.163.com/"}
_LOG = logging.getLogger(__name__)


class MusicError(RuntimeError):
    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code, self.status = code, status


def track_json(track: NeteaseTrack) -> dict:
    return {"id": f"netease:{track.song_id}", "title": track.title[:120], "artist": track.artist[:100],
            "duration_seconds": track.duration_seconds, "page_url": track.page_url}


def _refuse_paid_marker(row: dict) -> None:
    # Be conservative when the platform explicitly marks membership or purchase.
    # A URL accompanying that marker is not permission to work around the restriction.
    if str(row.get("fee")) in {"1", "4"}:
        raise MusicError("public_audio_unavailable", "此曲目被平台标为会员或付费音源，游戏桥不获取此音频。", 409)


async def detail_track(song_id: str, settings: Settings) -> NeteaseTrack:
    if not re.fullmatch(r"[0-9]{1,20}", song_id):
        raise MusicError("invalid_track", "歌曲编号无效。")
    async with outbound_http_client(settings=settings, follow_redirects=False) as client:
        payload = await request_json(client, "GET", "https://music.163.com/api/song/detail",
                                     params={"ids": json.dumps([int(song_id)])}, headers=_HEADERS,
                                     max_bytes=512 * 1024, timeout_seconds=max(1, settings.music_timeout_seconds))
    songs = payload.get("songs") if isinstance(payload, dict) else None
    for row in songs if isinstance(songs, list) else []:
        if isinstance(row, dict) and str(row.get("id")) == song_id:
            _refuse_paid_marker(row)
    tracks = parse_netease_tracks({"result": {"songs": songs}})
    track = next((item for item in tracks if item.song_id == song_id), None)
    if track is None:
        raise MusicError("track_unavailable", "没有查到所选歌曲的公开资料。", 404)
    if not 0 < track.duration_seconds <= MAX_SECONDS:
        raise MusicError("duration_limit", "仅支持有明确时长、且不超过12分钟的歌曲。", 409)
    return track


async def download_public_source(track: NeteaseTrack, settings: Settings, destination: Path) -> None:
    """Only the anonymous official player route; never call the member bridge."""
    async with outbound_http_client(settings=settings, follow_redirects=False) as client:
        payload = await request_json(client, "GET", "https://music.163.com/api/song/enhance/player/url",
                                     params={"id": track.song_id, "ids": f"[{track.song_id}]", "br": "128000"},
                                     headers=_HEADERS, max_bytes=256 * 1024,
                                     timeout_seconds=max(1, settings.music_timeout_seconds))
        entries = payload.get("data") if isinstance(payload, dict) else None
        for entry in entries if isinstance(entries, list) else []:
            if isinstance(entry, dict) and str(entry.get("id")) == track.song_id:
                _refuse_paid_marker(entry)
                if entry.get("code") not in (None, 200):
                    raise MusicError("public_audio_unavailable", "平台拒绝提供此曲目的完整公开音源。", 409)
        try:
            # Reuse the existing strict ID/CDN/trial/full-length checks, with its public branch only.
            url, expected_size = _validated_source_payload(payload, track, settings, member=False)
        except RuntimeError as exc:
            raise MusicError("public_audio_unavailable", "平台未提供完整公开音源，可能需要授权或仅有试听；不能在游戏内播放。", 409) from exc
        limit = min(MAX_SOURCE_BYTES, max(1, int(settings.singing_max_source_bytes)))
        if expected_size > limit:
            raise MusicError("source_too_large", "原曲音频超过本次大小限制。", 409)
        async with asyncio.timeout(70):
            for _ in range(5):
                async with client.stream("GET", url, headers=_HEADERS, follow_redirects=False,
                                         timeout=max(10, settings.music_timeout_seconds)) as response:
                    if response.status_code in (301, 302, 303, 307, 308):
                        url = _cdn_url(urljoin(url, response.headers.get("location", "")))
                        if not url:
                            raise MusicError("unsafe_redirect", "音源跳转地址不受支持。", 409)
                        continue
                    if response.status_code != 200 or "content-range" in response.headers:
                        raise MusicError("public_audio_unavailable", "平台未返回完整公开音频。", 409)
                    content_type = response.headers.get("content-type", "").split(";", 1)[0].casefold()
                    if not (content_type.startswith("audio/") or content_type in {"application/octet-stream", "binary/octet-stream"}):
                        raise MusicError("invalid_audio", "平台返回的内容不是音频。", 409)
                    try:
                        declared = int(response.headers.get("content-length", "0"))
                    except ValueError:
                        declared = 0
                    if declared > limit:
                        raise MusicError("source_too_large", "原曲音频超过本次大小限制。", 409)
                    count = 0
                    with destination.open("xb") as output:
                        async for chunk in response.aiter_bytes(64 * 1024):
                            count += len(chunk)
                            if count > limit:
                                raise MusicError("source_too_large", "原曲音频超过本次大小限制。", 409)
                            output.write(chunk)
                    if (count <= 0 or declared and count != declared or count < expected_size * 0.9
                            or count < track.duration_seconds * 5000):
                        raise MusicError("incomplete_audio", "下载结果不足以覆盖完整歌曲。", 409)
                    return
            raise MusicError("redirect_limit", "音源跳转次数过多。", 409)


@dataclass(frozen=True)
class MusicAsset:
    asset_id: str
    path: Path
    track: dict
    duration_seconds: float
    size_bytes: int
    created_at: float
    expires_at: float

    def response(self) -> dict:
        return {"protocol": 1, "asset_id": self.asset_id, "title": self.track["title"],
                "artist": self.track["artist"], "duration_seconds": self.duration_seconds,
                "format": "ogg_vorbis", "size_bytes": self.size_bytes,
                "audio_path": f"/minecraft/bridge/music/audio/{self.asset_id}"}


class MusicAssetCache:
    def __init__(self, project_root: Path):
        self.root = (project_root / "data" / "minecraft_music" / "assets").resolve()
        if not self.root.is_relative_to(project_root.resolve()):
            raise MusicError("unsafe_cache", "音乐缓存路径不在项目目录内。", 503)
        self.root.mkdir(parents=True, exist_ok=True)
        self.leases: dict[str, int] = {}

    def _path(self, asset_id: str, suffix: str) -> Path:
        if not _ASSET_ID.fullmatch(asset_id):
            raise MusicError("invalid_asset", "音频资产编号无效。", 404)
        path = self.root / f"{asset_id}{suffix}"
        if path.is_symlink() or path.resolve().parent != self.root:
            raise MusicError("invalid_asset", "音频资产路径无效。", 404)
        return path

    def get(self, asset_id: str) -> MusicAsset:
        path, metadata = self._path(asset_id, ".ogg"), self._path(asset_id, ".json")
        try:
            if not metadata.is_file() or metadata.stat().st_size > 4096:
                raise ValueError("metadata missing")
            data = json.loads(metadata.read_text(encoding="utf-8"))
            duration, expires = float(data["duration_seconds"]), float(data["expires_at"])
            size = path.stat().st_size
            if (data["asset_id"] != asset_id or not 0 < duration <= MAX_SECONDS
                    or not 0 < size <= MAX_ASSET_BYTES or size != data["size_bytes"]
                    or not math.isfinite(expires) or expires <= time.time()
                    or not isinstance(data["track"], dict)
                    or not re.fullmatch(r"netease:[0-9]{1,20}", data["track"].get("id", ""))):
                raise ValueError("asset invalid/expired")
            return MusicAsset(asset_id, path, data["track"], duration, size, float(data["created_at"]), expires)
        except (OSError, ValueError, TypeError, KeyError) as exc:
            self.remove(asset_id)
            raise MusicError("asset_expired", "歌曲缓存已过期，请重新准备播放。", 404) from exc

    def remove(self, asset_id: str) -> bool:
        if self.leases.get(asset_id, 0):
            return False
        try:
            self._path(asset_id, ".ogg").unlink(missing_ok=True)
            self._path(asset_id, ".json").unlink(missing_ok=True)
            return True
        except (OSError, MusicError):
            return False

    def entries(self) -> list[MusicAsset]:
        result = []
        for metadata in self.root.glob("*.json"):
            if not _ASSET_ID.fullmatch(metadata.stem):
                continue
            try:
                result.append(self.get(metadata.stem))
            except MusicError:
                pass
        return result

    def find_track(self, track_id: str) -> MusicAsset | None:
        return next((asset for asset in self.entries() if asset.track["id"] == track_id), None)

    def publish(self, source: Path, track: NeteaseTrack, duration: float) -> MusicAsset:
        size = source.stat().st_size
        if not 0 < size <= MAX_ASSET_BYTES:
            raise MusicError("asset_too_large", "编码后的歌曲超过16MiB上限。", 409)
        asset_id, now = uuid.uuid4().hex, time.time()
        destination = self._path(asset_id, ".ogg")
        metadata = self._path(asset_id, ".json")
        asset = MusicAsset(asset_id, destination, track_json(track), duration, size, now, now + ASSET_TTL_SECONDS)
        serialized = json.dumps({"asset_id": asset_id, "track": asset.track,
                                 "duration_seconds": duration, "size_bytes": size,
                                 "created_at": now, "expires_at": asset.expires_at}, ensure_ascii=False).encode("utf-8")
        added_bytes = size + len(serialized)
        entries = sorted(self.entries(), key=lambda asset: asset.created_at)
        # Count actual generated files too, including a leased/expired asset.
        files = [path for path in self.root.glob("*.ogg") if _ASSET_ID.fullmatch(path.stem)
                 and not path.is_symlink() and path.resolve().parent == self.root]
        for orphan in files:
            if not self._path(orphan.stem, ".json").exists() and not self.leases.get(orphan.stem, 0):
                self.remove(orphan.stem)
        # Include small metadata files in the disk-cache limit too.
        files.extend(path for path in self.root.glob("*.json") if _ASSET_ID.fullmatch(path.stem)
                     and not path.is_symlink() and path.resolve().parent == self.root)
        used = sum(path.stat().st_size for path in files if path.exists())
        for old in entries:
            if used + added_bytes <= MAX_CACHE_BYTES:
                break
            old_metadata = self._path(old.asset_id, ".json")
            old_bytes = old.size_bytes + (old_metadata.stat().st_size if old_metadata.exists() else 0)
            if self.remove(old.asset_id):
                used -= old_bytes
        if used + added_bytes > MAX_CACHE_BYTES:
            raise MusicError("cache_busy", "音乐缓存正在使用，暂时无法腾出空间。", 503)
        try:
            source.replace(destination)
            metadata.write_bytes(serialized)
        except OSError:
            destination.unlink(missing_ok=True)
            metadata.unlink(missing_ok=True)
            raise
        return asset


async def prepare_audio(track: NeteaseTrack, settings: Settings, project_root: Path,
                        cache: MusicAssetCache) -> MusicAsset:
    existing = cache.find_track(f"netease:{track.song_id}")
    if existing:
        return existing
    ffmpeg = locate_audio_tool("ffmpeg", settings.singing_ffmpeg_path, project_root)
    ffprobe = locate_audio_tool("ffprobe", settings.singing_ffprobe_path, project_root)
    audit: dict = {}
    # Cache and staging share a filesystem so publishing is atomic.
    with tempfile.TemporaryDirectory(prefix="prepare-", dir=cache.root.parent, ignore_cleanup_errors=True) as temporary:
        work = Path(temporary)
        source, encoded = work / "original.audio", work / "encoded.ogg"
        await download_public_source(track, settings, source)
        probe = await run_worker([
            ffprobe, "-v", "error", "-select_streams", "a:0", "-show_entries",
            "stream=codec_name,sample_rate,channels:format=duration", "-of", "json", source,
        ], cwd=work, project_root=project_root, stage="minecraft_music_probe", timeout_seconds=15,
            audit=audit, model_loading=False)
        information = json.loads(probe)
        duration = float(information.get("format", {}).get("duration", 0))
        if not math.isfinite(duration) or not 0 < duration <= MAX_SECONDS:
            raise MusicError("duration_limit", "音频实际时长无效或超过12分钟。", 409)
        if duration + 5 < track.duration_seconds * 0.95:
            raise MusicError("incomplete_audio", "音频实际时长不足以覆盖完整歌曲。", 409)
        await run_worker([
            ffmpeg, "-v", "error", "-nostdin", "-y", "-i", source, "-map", "0:a:0", "-vn",
            "-c:a", "libvorbis", "-b:a", "96k", "-ar", "44100", "-ac", "2",
            "-t", str(MAX_SECONDS), "-f", "ogg", encoded,
        ], cwd=work, project_root=project_root, stage="minecraft_music_encode", timeout_seconds=65,
            audit=audit, model_loading=False)
        with encoded.open("rb") as audio:
            magic = audio.read(4)
        if magic != b"OggS":
            raise MusicError("invalid_audio", "未能生成有效OGG音频。", 503)
        encoded_probe = await run_worker([
            ffprobe, "-v", "error", "-select_streams", "a:0", "-show_entries",
            "stream=codec_name,sample_rate,channels:format=duration", "-of", "json", encoded,
        ], cwd=work, project_root=project_root, stage="minecraft_music_verify", timeout_seconds=15,
            audit=audit, model_loading=False)
        encoded_information = json.loads(encoded_probe)
        streams = encoded_information.get("streams", [])
        encoded_duration = float(encoded_information.get("format", {}).get("duration", 0))
        if (len(streams) != 1 or streams[0].get("codec_name") != "vorbis"
                or str(streams[0].get("sample_rate")) != "44100" or streams[0].get("channels") not in (1, 2)
                or not math.isfinite(encoded_duration) or not 0 < encoded_duration <= MAX_SECONDS + 0.1
                or encoded_duration + 1 < duration):
            raise MusicError("invalid_audio", "OGG音频格式或时长验证未通过。", 503)
        asset = cache.publish(encoded, track, min(duration, encoded_duration))
        _LOG.info("Minecraft music prepared track=%s bytes=%s duration=%.2f", track.song_id, asset.size_bytes, duration)
        return asset
