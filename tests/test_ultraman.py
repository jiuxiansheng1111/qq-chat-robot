from app.services.ultraman import (
    ULTRAMAN_DEBUT_YEARS,
    ULTRAMAN_PROFILES,
    ULTRAMAN_ROSTER,
    ultraman_profile_text,
)


def test_every_ultraman_has_complete_collection_card_content():
    assert len(ULTRAMAN_ROSTER) >= 30
    for hero in ULTRAMAN_ROSTER:
        profile = ULTRAMAN_PROFILES[hero.name]
        assert profile.quote.strip()
        assert profile.description.strip()
        assert profile.background.strip()
        assert 1966 <= ULTRAMAN_DEBUT_YEARS[hero.name] <= 2026
        text = ultraman_profile_text(hero)
        assert "首次登场" in text
        assert "战士特点" in text
        assert "光之背景" in text
