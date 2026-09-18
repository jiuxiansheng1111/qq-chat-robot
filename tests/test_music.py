from app.services.music import parse_deezer_tracks


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
