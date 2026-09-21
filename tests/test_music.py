from app.main import resolve_music_identity
from app.services.web_search import SearchResult

from app.services.music import (
    choose_netease_track,
    music_query_suffixes,
    netease_track_matches_query,
    parse_deezer_tracks,
    parse_music_identity,
    parse_netease_tracks,
)


def test_parse_deezer_tracks_accepts_safe_preview_and_deduplicates():
    item = {
        "title_short": "TAIDADA",
        "link": "https://www.deezer.com/track/123",
        "preview": "https://cdnt-preview.dzcdn.net/api/1/1.mp3",
        "duration": 240,
        "explicit_lyrics": False,
        "artist": {"name": "ZUTOMAYO"},
        "album": {
            "title": "TAIDADA",
            "cover_big": "https://e-cdns-images.dzcdn.net/images/cover/a.jpg",
        },
    }
    tracks = parse_deezer_tracks({"data": [item, item]})
    assert len(tracks) == 1
    assert tracks[0].artist == "ZUTOMAYO"
    assert tracks[0].title == "TAIDADA"
    assert tracks[0].preview_url.endswith("1.mp3")


def test_parse_deezer_tracks_rejects_explicit_or_untrusted_urls():
    assert parse_deezer_tracks(
        {
            "data": [
                {
                    "title": "Unsafe",
                    "link": "https://evil.example/track",
                    "preview": "https://evil.example/file.mp3",
                    "artist": {"name": "Someone"},
                },
                {
                    "title": "Explicit",
                    "link": "https://www.deezer.com/track/2",
                    "preview": "https://cdnt-preview.dzcdn.net/2.mp3",
                    "explicit_lyrics": True,
                    "artist": {"name": "Someone"},
                },
            ]
        }
    ) == []


def test_netease_prefers_original_artist_and_rejects_cover_versions():
    tracks = parse_netease_tracks(
        {
            "result": {
                "songs": [
                    {
                        "id": 30,
                        "name": "春泥棒 Cover",
                        "artists": [{"name": "Someone"}],
                        "album": {"name": "Cover", "picUrl": ""},
                        "duration": 100000,
                    },
                    {
                        "id": 20,
                        "name": "春泥棒",
                        "artists": [{"name": "其他歌手"}],
                        "album": {"name": "同名曲", "picUrl": ""},
                        "duration": 200000,
                    },
                    {
                        "id": 10,
                        "name": "春泥棒",
                        "artists": [{"name": "ヨルシカ"}],
                        "album": {"name": "創作", "picUrl": ""},
                        "duration": 290000,
                    },
                ]
            }
        }
    )
    selected = choose_netease_track(
        "ヨルシカ 春泥棒", tracks, expected_artist="ヨルシカ", expected_title="春泥棒"
    )
    assert selected is not None
    assert selected.song_id == "10"
    assert netease_track_matches_query("ヨルシカ 春泥棒", selected)


def test_music_query_suffixes_support_artist_alias_plus_title():
    assert music_query_suffixes("夜鹿 春泥棒") == ["春泥棒"]
    assert music_query_suffixes("yorushika Spring Thief") == [
        "Spring Thief",
        "Thief",
    ]
    assert music_query_suffixes("春泥棒") == []


def test_expected_title_requires_an_exact_match():
    tracks = parse_netease_tracks(
        {
            "result": {
                "songs": [
                    {
                        "id": 1,
                        "name": "春泥棒 (Live)",
                        "artists": [{"name": "ヨルシカ"}],
                        "album": {"name": "Live"},
                    }
                ]
            }
        }
    )
    assert choose_netease_track("春泥棒", tracks, expected_title="春泥棒") is None


def test_music_identity_parser_accepts_json_only():
    identity = parse_music_identity(
        '```json\n{"title":"春泥棒","artist":"ヨルシカ",'
        '"search_query":"ヨルシカ 春泥棒"}\n```'
    )
    assert identity is not None
    assert identity.artist == "ヨルシカ"
    assert parse_music_identity("我觉得可能是春泥棒") is None


async def test_music_identity_resolver_uses_translation_aliases(monkeypatch):
    captured: dict[str, object] = {}

    async def fake_search_web(query: str, limit: int = 5):
        captured["query"] = query
        captured["limit"] = limit
        return [
            SearchResult(
                title="Stellar Stellar - 星街すいせい",
                url="https://example.com/song",
                snippet="星街すいせい演唱的 Stellar Stellar",
            )
        ]

    class FakeLLM:
        async def ask(self, messages):
            captured["messages"] = messages
            return (
                '{"title":"Stellar Stellar","artist":"星街すいせい",'
                '"search_query":"星街すいせい Stellar Stellar"}'
            )

    monkeypatch.setattr("app.main.search_web", fake_search_web)
    identity, results = await resolve_music_identity(
        "星街すいせい Stellar Stellar",
        FakeLLM(),
        ["Hoshimachi Suisei Stellar Stellar", "星街彗星 Stellar Stellar"],
    )

    assert identity is not None
    assert identity.artist == "星街すいせい"
    assert len(results) == 1
    assert "Hoshimachi Suisei Stellar Stellar" in str(captured["query"])
    user_prompt = captured["messages"][1]["content"]
    assert "星街彗星 Stellar Stellar" in user_prompt
