from app.services.character_catalog import (
    ANIME_FAVORITE_ALIASES,
    CHARACTER_CATALOG_COMMANDS,
    ULTRAMAN_FAVORITE_ALIASES,
    character_catalog_menu,
    extract_catalog_lookup,
)


def test_character_catalog_has_unified_hub_and_separate_favorites():
    assert "图鉴" in CHARACTER_CATALOG_COMMANDS
    assert "本命奥特曼" in ULTRAMAN_FAVORITE_ALIASES
    assert "本命二次元角色" in ANIME_FAVORITE_ALIASES
    text = character_catalog_menu(123, 300)
    assert "日漫与特摄角色图鉴" in text
    assert "本命奥特曼" in text
    assert "本命二次元角色" in text
    assert "两类收藏独立统计" in text


def test_character_catalog_lookup_prefixes():
    assert extract_catalog_lookup("查询奥特曼 迪迦") == ("ultraman", "迪迦")
    assert extract_catalog_lookup("查询二次元角色 雷姆") == ("anime", "雷姆")
    assert extract_catalog_lookup("查询奥特曼") == ("ultraman", "")
    assert extract_catalog_lookup("普通聊天") is None
