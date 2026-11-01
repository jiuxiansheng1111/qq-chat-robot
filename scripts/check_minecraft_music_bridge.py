"""Targeted local music-bridge check; no QQ messages, user service changes, or model calls.

Uses default settings without reading .env. Platform/LLM fixtures are explicitly
labelled; FFmpeg, aiohttp streaming, cancellation and cache operations are real.
An optional --public-track only permits the anonymous official full-song route.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import shutil
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import httpx
from aiohttp import web

from app.config import Settings
from app.minecraft_bridge import MinecraftBridge
from app.minecraft_music import (
    MAX_MUSIC_BODY,
    MinecraftMusic,
    import_public_playlist,
    manual_track,
    parse_manual_playlist,
)
from app.minecraft_music_audio import MusicAssetCache, MusicError, detail_track
from app.minecraft_voice_runtime import run_worker
from app.services.audio_chunks import locate_audio_tool
from app.services.music import NeteaseTrack


class PrimaryFixture:
    """Never invokes or configures a real LLM."""

    def __init__(self):
        self.messages = []
        self.reply = {"interpretation": "按喜欢的歌单和歌名检索。", "queries": [{"title": "检查曲", "artist": "检查歌手"}],
                      "playlist_ids": [], "suggested_id": "netease:123"}

    async def ask_primary(self, messages):
        self.messages.append(messages)
        return json.dumps(self.reply, ensure_ascii=False)


async def check(output: Path, public_track: str, public_playlist: str) -> dict:
    import app.minecraft_music as music_module
    import app.minecraft_music_audio as audio_module

    output.mkdir(parents=True, exist_ok=True)
    settings = Settings.model_construct()
    ffmpeg = locate_audio_tool("ffmpeg", settings.singing_ffmpeg_path, ROOT)
    ffprobe = locate_audio_tool("ffprobe", settings.singing_ffprobe_path, ROOT)
    settings = settings.model_copy(update={"singing_ffmpeg_path": str(ffmpeg), "singing_ffprobe_path": str(ffprobe)})
    report = {"protocol": 1, "real_llm_called": False, "qq_messages_sent": False,
              "user_services_changed": False, "checks": [], "public_source": None}

    def record(name, **values):
        report["checks"].append({"name": name, "passed": True, **values})

    remote = SimpleNamespace(transport=SimpleNamespace(get_extra_info=lambda _: ("203.0.113.12", 1234)))
    local = SimpleNamespace(transport=SimpleNamespace(get_extra_info=lambda _: ("127.0.0.1", 1234)))
    assert not MinecraftBridge._is_loopback(remote) and MinecraftBridge._is_loopback(local)
    record("tcp_peer_loopback_enforced_without_forwarded_headers")

    playlist = parse_manual_playlist("检查曲 / 检查歌手\n第二首 - 第二歌手\n检查曲 / 检查歌手")
    assert playlist["imported_count"] == 2 and playlist["duplicates_skipped"] == 1
    assert playlist["tracks"][0]["title"] == "检查曲"
    large = parse_manual_playlist("\n".join(f"第{i}首 / 歌手" for i in range(120)))
    assert large["truncated"] and large["imported_count"] == 100 and large["tracks"][-1]["title"] == "第99首"
    json_playlist = parse_manual_playlist(json.dumps({"name": "喜欢的歌", "tracks": [{"title": "检查曲", "artist": "检查歌手"}]}), "json")
    assert json_playlist["tracks"][0] == manual_track("检查曲", "检查歌手")
    for unsafe in ("file:///C:/secret.mp3", "https://example.com/audio.mp3", "C:\\secret.mp3"):
        try:
            parse_manual_playlist(unsafe)
        except MusicError:
            continue
        raise AssertionError("arbitrary URL/path accepted")
    record("manual_text_json_order_dedup_100_limit_and_unsafe_source_rejection")

    with tempfile.TemporaryDirectory(prefix="music-check-", dir=output) as temporary:
        work = Path(temporary)
        fixture = PrimaryFixture()
        bridge = MinecraftBridge(fixture, work, settings=settings)
        service = bridge.music
        track = NeteaseTrack("123", "检查曲", ("检查歌手",), "本地合成音检查", "", 2)
        searches = []

        async def search_fixture(query, _settings):
            searches.append(query)
            return [track]

        async def detail_fixture(song_id, _settings):
            assert song_id == "123"
            return track

        async def source_fixture(_track, _settings, destination):
            # Real PCM generation and subsequent real production OGG encoder.
            await run_worker([ffmpeg, "-v", "error", "-nostdin", "-y", "-f", "lavfi", "-i",
                              "sine=frequency=440:duration=2", "-ar", "44100", "-ac", "2", "-f", "wav", destination],
                             cwd=work, project_root=ROOT, stage="music_check_synthetic_source", timeout_seconds=10,
                             audit={}, model_loading=False)

        application = web.Application(client_max_size=MAX_MUSIC_BODY)
        application.router.add_post("/minecraft/bridge/music/search", bridge._music_search)
        application.router.add_post("/minecraft/bridge/music/playlist/import", bridge._music_import)
        application.router.add_post("/minecraft/bridge/music/prepare", bridge._music_prepare)
        application.router.add_get("/minecraft/bridge/music/audio/{asset_id}", bridge._music_audio)
        application.router.add_post("/minecraft/bridge/chat", bridge._chat)
        runner = web.AppRunner(application, access_log=None, handler_cancellation=True)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        base = f"http://127.0.0.1:{port}/minecraft/bridge"
        try:
            with (patch.object(music_module, "search_music_candidates", search_fixture),
                  patch.object(music_module, "detail_track", detail_fixture),
                  patch.object(audio_module, "download_public_source", source_fixture)):
                async with httpx.AsyncClient(trust_env=False, timeout=30) as client:
                    imported = await client.post(base + "/music/playlist/import", json={"protocol": 1, "value": "检查曲 / 检查歌手"})
                    assert imported.status_code == 200 and not searches and not fixture.messages
                    preferred = imported.json()["tracks"]
                    result = await client.post(base + "/music/search", json={"protocol": 1, "query": "来一首喜欢的歌", "playlist": preferred})
                    assert result.status_code == 200 and result.json()["suggested_id"] == "netease:123"
                    assert json.loads(fixture.messages[-1][1]["content"])["playlist"] == preferred
                    assert searches == ["检查曲 / 检查歌手"]
                    record("real_http_search_import_existing_primary_interface_with_labelled_llm_platform_fixtures")

                    for suffix, payload, expected in (
                        ("search", {"protocol": 1, "query": "x" * 241}, 400),
                        ("search", {"protocol": 1, "query": "x", "playlist": preferred * 101}, 400),
                        ("search", {"protocol": True, "query": "x"}, 400),
                        ("playlist/import", {"protocol": 1, "value": "x", "source": {}}, 400),
                        ("playlist/import", {"protocol": 1, "value": "x" * 16385}, 400),
                        ("prepare", {"protocol": 1, "track": {"id": "../bad", "title": "x"}}, 400),
                    ):
                        rejected = await client.post(base + "/music/" + suffix, json=payload)
                        assert rejected.status_code == expected and rejected.json()["error"]
                    wrong_type = await client.post(base + "/music/search", content="{}", headers={"Content-Type": "text/plain"})
                    assert wrong_type.status_code == 415
                    too_big = await client.post(base + "/music/search", content=b"x" * (MAX_MUSIC_BODY + 1), headers={"Content-Type": "application/json"})
                    assert too_big.status_code == 413 and too_big.json()["error"] == "request_too_large"
                    chat_limit = await client.post(base + "/chat", content=b"x" * 21000, headers={"Content-Type": "application/json"})
                    assert chat_limit.status_code == 413
                    record("real_http_validation_music_128k_and_existing_chat_20k_limits")

                    started = time.monotonic()
                    prepared = await client.post(base + "/music/prepare", json={"protocol": 1, "track": preferred[0]})
                    assert prepared.status_code == 200, prepared.text
                    asset = prepared.json()
                    assert asset["format"] == "ogg_vorbis" and asset["size_bytes"] < 16 * 1024 * 1024
                    downloaded = await client.get(f"http://127.0.0.1:{port}" + asset["audio_path"])
                    assert downloaded.status_code == 200 and downloaded.content[:4] == b"OggS"
                    assert len(downloaded.content) == asset["size_bytes"] and not service.asset_cache().leases
                    range_response = await client.get(f"http://127.0.0.1:{port}" + asset["audio_path"], headers={"Range": "bytes=0-63"})
                    assert range_response.status_code == 206 and len(range_response.content) == 64
                    saved = output / "synthetic-playback.ogg"
                    saved.write_bytes(downloaded.content)
                    probe = await run_worker([ffprobe, "-v", "error", "-show_entries", "stream=codec_name,sample_rate,channels:format=duration",
                                              "-of", "json", saved], cwd=work, project_root=ROOT,
                                             stage="music_check_probe", timeout_seconds=10, audit={}, model_loading=False)
                    info = json.loads(probe)
                    assert info["streams"][0]["codec_name"] == "vorbis"
                    assert info["streams"][0]["sample_rate"] == "44100" and info["streams"][0]["channels"] == 2
                    (output / "synthetic-probe.json").write_text(json.dumps(info, indent=2), encoding="utf-8")
                    record("real_ffmpeg_vorbis_and_http_stream_range_lease_release", seconds=round(time.monotonic() - started, 3),
                           size_bytes=len(downloaded.content), sha256=hashlib.sha256(downloaded.content).hexdigest(), probe=info)
                    cache = service.asset_cache()
                    metadata_path = cache.root / (asset["asset_id"] + ".json")
                    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
                    metadata["expires_at"] = time.time() - 1
                    metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
                    expired = await client.get(f"http://127.0.0.1:{port}" + asset["audio_path"])
                    assert expired.status_code == 404 and not (cache.root / (asset["asset_id"] + ".ogg")).exists()
                    invalid_asset = await client.get(base + "/music/audio/not-generated")
                    assert invalid_asset.status_code == 404
                    record("real_http_asset_expiry_cleanup_and_opaque_id_only")

                    service.prepare_gate = asyncio.Semaphore(0)
                    busy = await client.post(base + "/music/prepare", json={"protocol": 1, "track": preferred[0]})
                    assert busy.status_code == 503 and busy.json()["error"] == "music_busy"
                    service.prepare_gate = asyncio.Semaphore(2)
                    service.search_gate = asyncio.Semaphore(0)
                    busy_search = await client.post(base + "/music/search", json={"protocol": 1, "query": "x"})
                    assert busy_search.status_code == 503 and busy_search.json()["error"] == "music_busy"
                    service.search_gate = asyncio.Semaphore(2)
                    fixture.reply = {"interpretation": "x" * 321}
                    bad_model = await client.post(base + "/music/search", json={"protocol": 1, "query": "x"})
                    assert bad_model.status_code == 502 and bad_model.json()["error"] == "model_response_invalid"
                    record("real_http_explicit_busy_and_invalid_model_result")
                    with patch.object(fixture, "ask_primary", side_effect=TimeoutError):
                        timeout = await client.post(base + "/music/search", json={"protocol": 1, "query": "x"})
                        assert timeout.status_code == 504 and timeout.json()["error"] == "music_timeout"
                    record("real_http_safe_timeout_error_with_injected_provider_timeout")

                    entered, cancelled = asyncio.Event(), asyncio.Event()

                    async def cancellable_source(_track, _settings, _destination):
                        entered.set()
                        try:
                            await asyncio.Future()
                        finally:
                            cancelled.set()

                    with patch.object(audio_module, "download_public_source", cancellable_source):
                        request_task = asyncio.create_task(client.post(base + "/music/prepare", json={"protocol": 1, "track": preferred[0]}))
                        await asyncio.wait_for(entered.wait(), 5)
                        request_task.cancel()
                        await asyncio.gather(request_task, return_exceptions=True)
                        await asyncio.wait_for(cancelled.wait(), 5)
                        await asyncio.sleep(0.05)
                    assert not service.tasks and service.prepare_gate._value == 2
                    assert not list(cache.root.parent.glob("prepare-*"))
                    record("real_http_disconnect_cancels_prepare_releases_gate_and_staging")
        finally:
            await service.close()
            await runner.cleanup()

        # Exercise eviction using a small temporary cap, never filling the user's disk.
        cache = MusicAssetCache(work / "eviction")
        with patch.object(audio_module, "MAX_CACHE_BYTES", 1024 * 1024):
            source = work / "small.ogg"
            source.write_bytes(b"OggS" + b"x" * 96)
            first = cache.publish(source, track, 2)
            source.write_bytes(b"OggS" + b"y" * 96)
            second = cache.publish(source, track, 2)
            cache.leases[first.asset_id] = 1
            source.write_bytes(b"OggS" + b"z" * 96)
            used_bytes = sum(path.stat().st_size for path in cache.root.iterdir())
            with patch.object(audio_module, "MAX_CACHE_BYTES", used_bytes + 50):
                cache.publish(source, track, 2)
            assert first.path.exists() and not second.path.exists()
            cache.leases.clear()
        record("cache_eviction_preserves_streaming_asset_with_scaled_local_limit")

        # Validate public metadata flags without any network or restricted download.
        with patch.object(audio_module, "request_json", return_value={"code": 200, "data": [
            {"id": 123, "url": "https://m7.music.126.net/check.mp3", "size": 50000, "freeTrialInfo": {"start": 0, "end": 1}},
        ]}):
            try:
                await audio_module.download_public_source(track, settings, work / "must-not-download")
            except MusicError as exc:
                assert exc.code == "public_audio_unavailable" and exc.status == 409
            else:
                raise AssertionError("trial audio accepted")
        record("public_source_trial_flag_refused_with_platform_fixture_no_member_bridge")
        with patch.object(audio_module, "request_json", return_value={"code": 200, "data": [
            {"id": 123, "code": 200, "fee": 1, "url": "https://m7.music.126.net/check.mp3", "size": 50000},
        ]}):
            try:
                await audio_module.download_public_source(track, settings, work / "must-not-download-paid")
            except MusicError as exc:
                assert exc.code == "public_audio_unavailable" and exc.status == 409
            else:
                raise AssertionError("explicit paid source accepted")
        record("explicit_paid_marker_refused_even_with_url_platform_fixture")

        # A real child process is cancelled, using the production owned-process cleanup.
        pid_file = work / "owned-pid.txt"
        process_task = asyncio.create_task(run_worker([
            sys.executable, "-c", "import os,pathlib,time; pathlib.Path('owned-pid.txt').write_text(str(os.getpid())); time.sleep(30)",
        ], cwd=work, project_root=ROOT, stage="music_check_owned_cancel", timeout_seconds=10, audit={}, model_loading=False))
        for _ in range(50):
            if pid_file.exists():
                break
            await asyncio.sleep(0.1)
        assert pid_file.exists()
        owned_pid = int(pid_file.read_text())
        process_task.cancel()
        cancelled_result = await asyncio.gather(process_task, return_exceptions=True)
        assert isinstance(cancelled_result[0], asyncio.CancelledError)
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.OpenProcess(0x1000, False, owned_pid)
        if handle:
            exit_code = wintypes.DWORD()
            assert kernel.GetExitCodeProcess(handle, ctypes.byref(exit_code))
            kernel.CloseHandle(handle)
            assert exit_code.value != 259
        record("real_owned_worker_cancel_reaped_only_check_process", pid=owned_pid)

        if public_playlist:
            try:
                imported = await asyncio.wait_for(import_public_playlist(public_playlist, settings), 60)
                report["public_playlist"] = {key: imported[key] for key in ("name", "total", "imported_count", "truncated")}
                report["public_playlist"]["first_track"] = imported["tracks"][0]
            except Exception as exc:  # noqa: BLE001 -- no provider request/config details.
                report["public_playlist"] = {"error": type(exc).__name__}

        if public_track:
            started = time.monotonic()
            try:
                public = await detail_track(public_track, settings)
                isolated = MinecraftMusic(PrimaryFixture(), settings, work / "public")
                asset = await asyncio.wait_for(isolated.prepare({"id": f"netease:{public.song_id}", "title": public.title, "artist": public.artist}), 170)
                cached = isolated.asset_cache().get(asset["asset_id"])
                destination = output / "public-full-song.ogg"
                shutil.copyfile(cached.path, destination)
                report["public_source"] = {"status": "full_public_audio_prepared", "track": {"id": public.song_id, "title": public.title, "artist": public.artist},
                                           "size_bytes": asset["size_bytes"], "duration_seconds": asset["duration_seconds"]}
                await isolated.close()
            except MusicError as exc:
                report["public_source"] = {"status": "explicitly_unavailable", "song_id": public_track, "error": exc.code, "message": str(exc)}
            except Exception as exc:  # noqa: BLE001 -- diagnostic class only, no request/config secrets.
                report["public_source"] = {"status": "network_or_environment_failure", "song_id": public_track, "error": type(exc).__name__}
            report["public_source"]["elapsed_seconds"] = round(time.monotonic() - started, 3)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--public-track", default="")
    parser.add_argument("--public-playlist", default="")
    args = parser.parse_args()
    result = asyncio.run(check(args.output.resolve(), args.public_track, args.public_playlist))
    (args.output / "results.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
