from app.services.singing_excerpt import _clean_lyrics, select_singing_excerpt
from app.services.singing_sources import LyricLine


def test_song_metadata_is_removed_without_dropping_a_sung_title():
    title = "朋友的酒（DJ版）"
    artists = ("泽亦轩",)
    lines = (
        LyricLine(0.0, "作词：泽亦轩"),
        LyricLine(0.35, "作曲：泽亦轩"),
        LyricLine(0.7, "编曲：泽亦轩"),
        LyricLine(1.05, "朋友的酒-泽亦轩 remix"),
        LyricLine(1.74, "改编词、曲：泽亦轩"),
        LyricLine(60.06, "昨日一去不复回哦耶"),
        LyricLine(67.47, "开心比什么都贵"),
        LyricLine(74.88, "覆水不能再收回哦耶"),
        LyricLine(81.0, "朋友的酒最珍贵"),
    )
    cleaned = _clean_lyrics(
        lines,
        180,
        ignore_texts=(title, *artists),
        song_title=title,
        song_artists=artists,
    )
    assert cleaned == lines[5:]

    selection = select_singing_excerpt(
        lines,
        180,
        20,
        115,
        ignore_texts=(title, *artists),
        song_title=title,
        song_artists=artists,
    )
    assert 59.9 <= selection.start_seconds <= 60.0
    assert 15 <= selection.duration_seconds <= 25
