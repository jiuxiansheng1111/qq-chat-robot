import json
from types import SimpleNamespace

import httpx
import pytest

from app.services import singing_sources as sources
from app.services.music import NeteaseTrack

TRACK = NeteaseTrack("123", "春泥棒", ("ヨルシカ",), "創作", "", 200)
SETTINGS = SimpleNamespace(music_timeout_seconds=10, music_search_limit=8)
CDN_URL = "https://m10.music.126.net/sample.mp3"


def _mock_client(monkeypatch, handler):
    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        sources.httpx,
        "AsyncClient",
        lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs),
    )


def test_lrc_multiple_timestamps_fraction_and_offset():
    lines = sources.parse_lrc("[offset:+250]\n[00:01.2][00:03.045]你好\n[00:05]世界\n[ar:歌手]")
    assert [(line.time_seconds, line.text) for line in lines] == [
        (1.45, "你好"),
        (3.295, "你好"),
        (5.25, "世界"),
    ]
    assert sources.parse_lrc("[offset:-500]\n[00:00.20]开头")[0].time_seconds == 0


async def test_explicit_title_artist_selects_original_and_retries_title(monkeypatch):
    other = NeteaseTrack("1", "春泥棒", ("其他歌手",), "同名曲", "", 200)
    queries = []

    async def search(query, settings):
        queries.append(query)
        return [other, TRACK] if query == "春泥棒" else []

    monkeypatch.setattr(sources, "search_netease_music", search)
    selected = await sources._search_track("春泥棒 / ヨルシカ", SETTINGS)
    assert selected == TRACK
    assert queries == ["春泥棒 / ヨルシカ", "ヨルシカ 春泥棒", "春泥棒"]


async def test_dj_request_requires_dj_title_and_plain_request_rejects_it(monkeypatch):
    plain = NeteaseTrack("1", "朋友的酒", ("李晓杰",), "", "", 230)
    dj = NeteaseTrack("2", "朋友的酒（DJ版）", ("李晓杰",), "", "", 230)

    async def search(query, settings):
        return [plain, dj]

    monkeypatch.setattr(sources, "search_netease_music", search)
    assert await sources._search_track("朋友的酒 DJ版 李晓杰", SETTINGS) == dj
    assert await sources._search_track("朋友的酒 李晓杰", SETTINGS) == plain

    async def only_plain(query, settings):
        return [plain]

    monkeypatch.setattr(sources, "search_netease_music", only_plain)
    with pytest.raises(sources.SingingSourceError):
        await sources._search_track("朋友的酒 DJ版 李晓杰", SETTINGS)


async def test_resolve_uses_local_manifest_and_lyrics(monkeypatch, tmp_path):
    monkeypatch.setattr(sources, "SINGING_DATA_ROOT", tmp_path)
    (tmp_path / "song.mp3").write_bytes(b"music")
    (tmp_path / "song.lrc").write_text("[00:01.00]你好", encoding="utf-8")
    (tmp_path / "songs.json").write_text(
        json.dumps({"123": {"path": "song.mp3", "lyrics_path": "song.lrc"}}), encoding="utf-8"
    )

    async def search(query, settings):
        return [TRACK]

    monkeypatch.setattr(sources, "search_netease_music", search)
    song = await sources.resolve_singing_song("ヨルシカ 春泥棒", SETTINGS)
    assert song.track == TRACK
    assert song.source_path == tmp_path / "song.mp3"
    assert song.source_url is None
    assert song.lyric_lines[0].time_seconds == 1
    assert await sources.download_singing_source(song, tmp_path / "jobs" / "1", SETTINGS) == song.source_path


async def test_resolve_public_source_and_download(monkeypatch, tmp_path):
    monkeypatch.setattr(sources, "SINGING_DATA_ROOT", tmp_path)

    async def search(query, settings):
        return [TRACK]

    monkeypatch.setattr(sources, "search_netease_music", search)
    audio = b"a" * 1_100_000

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/lyric"):
            return httpx.Response(200, json={"code": 200, "lrc": {"lyric": "[00:02]你好"}})
        if request.url.path.endswith("/player/url"):
            return httpx.Response(
                200,
                json={
                    "code": 200,
                    "data": [{
                        "id": 123,
                        "url": CDN_URL.replace("https://", "http://"),
                        "size": len(audio),
                        "freeTrialInfo": None,
                        "freeTrialPrivilege": {"resConsumable": False, "userConsumable": False},
                    }],
                },
            )
        if str(request.url) == CDN_URL:
            return httpx.Response(200, headers={"content-type": "audio/mpeg"}, content=audio)
        raise AssertionError(request.url)

    _mock_client(monkeypatch, handler)
    song = await sources.resolve_singing_song("ヨルシカ 春泥棒", SETTINGS)
    assert song.source_url == CDN_URL
    path = await sources.download_singing_source(song, tmp_path / "jobs" / "1", SETTINGS)
    assert path.read_bytes() == audio


@pytest.mark.parametrize(
    "url,trial",
    [
        ("https://evil.example/song.mp3", None),
        ("http://evil.example/song.mp3", None),
        ("https://m10.music.126.net.evil.example/song.mp3", None),
        (CDN_URL, {"start": 0, "end": 30}),
        (None, None),
    ],
)
async def test_resolve_rejects_unsafe_and_trial_urls(monkeypatch, tmp_path, url, trial):
    monkeypatch.setattr(sources, "SINGING_DATA_ROOT", tmp_path)

    async def search(query, settings):
        return [TRACK]

    monkeypatch.setattr(sources, "search_netease_music", search)

    def handler(request):
        if request.url.path.endswith("/lyric"):
            return httpx.Response(200, json={"code": 200, "lrc": {"lyric": "[00:01]你好"}})
        return httpx.Response(
            200,
            json={
                "code": 200,
                "data": [{"id": 123, "url": url, "size": 1_100_000, "freeTrialInfo": trial}],
            },
        )

    _mock_client(monkeypatch, handler)
    with pytest.raises(sources.SingingSourceError):
        await sources.resolve_singing_song("春泥棒", SETTINGS)


async def test_resolve_reports_unauthorized_lyrics(monkeypatch, tmp_path):
    monkeypatch.setattr(sources, "SINGING_DATA_ROOT", tmp_path)

    async def search(query, settings):
        return [TRACK]

    monkeypatch.setattr(sources, "search_netease_music", search)
    _mock_client(monkeypatch, lambda request: httpx.Response(401))
    with pytest.raises(sources.SingingSourceError, match="暂时无法读取"):
        await sources.resolve_singing_song("春泥棒", SETTINGS)


async def test_download_rejects_redirect_and_removes_partial(monkeypatch, tmp_path):
    monkeypatch.setattr(sources, "SINGING_DATA_ROOT", tmp_path)
    song = sources.SingingSong(TRACK, "[00:01]你好", (), None, CDN_URL, 1_100_000)

    def handler(request):
        return httpx.Response(302, headers={"location": "https://evil.example/song.mp3"})

    _mock_client(monkeypatch, handler)
    directory = tmp_path / "jobs" / "1"
    with pytest.raises(sources.SingingSourceError, match="未获准"):
        await sources.download_singing_source(song, directory, SETTINGS)
    assert list(directory.iterdir()) == []


async def test_download_size_limit_removes_partial(monkeypatch, tmp_path):
    monkeypatch.setattr(sources, "SINGING_DATA_ROOT", tmp_path)
    song = sources.SingingSong(TRACK, "[00:01]你好", (), None, CDN_URL, None)
    settings = SimpleNamespace(music_timeout_seconds=10, singing_max_source_bytes=10)
    _mock_client(
        monkeypatch,
        lambda request: httpx.Response(
            200, headers={"content-type": "audio/mpeg"}, content=b"a" * 20
        ),
    )
    directory = tmp_path / "jobs" / "1"
    with pytest.raises(sources.SingingSourceError, match="大小限制"):
        await sources.download_singing_source(song, directory, settings)
    assert list(directory.iterdir()) == []


def test_local_manifest_rejects_path_traversal(monkeypatch, tmp_path):
    root = tmp_path / "singing"
    root.mkdir()
    monkeypatch.setattr(sources, "SINGING_DATA_ROOT", root)
    outside = tmp_path / "other.mp3"
    outside.write_bytes(b"music")
    (root / "songs.json").write_text(json.dumps({"123": "../other.mp3"}), encoding="utf-8")
    with pytest.raises(sources.SingingSourceError, match="data/singing"):
        sources._local_source("123", SETTINGS)
