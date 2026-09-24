from __future__ import annotations

import re

CHARACTER_CATALOG_COMMANDS = frozenset(
    {
        "/图鉴", "图鉴", "/角色图鉴", "角色图鉴", "/图鉴菜单", "图鉴菜单",
        "/图鉴总览", "图鉴总览",
        "/日漫与特摄角色图鉴", "日漫与特摄角色图鉴",
    }
)
ULTRAMAN_CATALOG_ALIASES = frozenset(
    {"/特摄角色图鉴", "特摄角色图鉴", "/特摄图鉴", "特摄图鉴"}
)
ANIME_CATALOG_ALIASES = frozenset(
    {"/日漫角色图鉴", "日漫角色图鉴", "/日漫图鉴", "日漫图鉴"}
)
ULTRAMAN_FAVORITE_ALIASES = frozenset(
    {"/本命奥特曼", "本命奥特曼", "/查看本命奥特曼", "查看本命奥特曼"}
)
ANIME_FAVORITE_ALIASES = frozenset(
    {"/本命二次元角色", "本命二次元角色", "/查看本命二次元角色", "查看本命二次元角色"}
)


def character_catalog_menu(ultraman_count: int, anime_count: int) -> str:
    return (
        "┏ 日漫与特摄角色图鉴 ┓\n"
        f"│  共收录：特摄 {ultraman_count} 位/形态｜日漫 {anime_count} 位角色\n"
        "│\n"
        "│  ⚡ 特摄 / 奥特曼\n"
        "│  @我 今日奥特曼\n"
        "│  @我 本命奥特曼\n"
        "│  @我 奥特曼图鉴 / 特摄角色图鉴\n"
        "│  @我 查询奥特曼 迪迦\n"
        "│\n"
        "│  🌸 日漫 / 二次元角色\n"
        "│  @我 随机二次元角色\n"
        "│  @我 本命二次元角色\n"
        "│  @我 二次元角色图鉴 / 日漫角色图鉴\n"
        "│  @我 查询二次元角色 雷姆\n"
        "│\n"
        "│  两类收藏独立统计，本命不会混在一起。\n"
        "┗ 直接 @我 + 角色名 也可以查看资料 ┛"
    )


def extract_catalog_lookup(text: str) -> tuple[str, str] | None:
    cleaned = re.sub(r"\s+", " ", str(text or "")).strip()
    lowered = cleaned.casefold()
    prefixes = (
        ("all", ("角色图鉴", "查询角色图鉴", "查角色图鉴", "/角色图鉴", "/查询角色图鉴")),
        ("ultraman", ("查询奥特曼", "查奥特曼", "/查询奥特曼", "/查奥特曼")),
        ("anime", (
            "查询二次元角色", "查二次元角色", "查询日漫角色", "查日漫角色",
            "/查询二次元角色", "/查询日漫角色",
        )),
    )
    for kind, values in prefixes:
        for prefix in values:
            if lowered == prefix.casefold():
                return kind, ""
            if lowered.startswith(prefix.casefold() + " "):
                return kind, cleaned[len(prefix):].strip()[:100]
    return None
