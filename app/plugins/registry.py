import importlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from app.services.anime_character import ANIME_CHARACTER_ROSTER
from app.services.ultraman import ULTRAMAN_ROSTER

PluginHandler = Callable[[Any], Awaitable[str | None]]


@dataclass(frozen=True)
class PluginSpec:
    name: str
    commands: tuple[str, ...]
    description: str
    admin_only: bool = False
    handler: PluginHandler | None = None


class PluginRegistry:
    def __init__(self):
        self._plugins: dict[str, PluginSpec] = {}
        self._aliases: dict[str, str] = {}

    def register(self, spec: PluginSpec) -> None:
        self._plugins[spec.name] = spec
        for command in spec.commands:
            self._aliases[command] = spec.name

    def resolve(self, text: str) -> tuple[PluginSpec | None, str]:
        exact = self.find(text)
        if exact:
            return exact, ""
        for command, name in self._aliases.items():
            if text.startswith(command + " "):
                return self._plugins[name], text[len(command) :].strip()
        return None, ""

    async def dispatch(self, text: str, context: Any) -> tuple[str, str | None] | None:
        spec, args = self.resolve(text)
        if not spec or not spec.handler:
            return None
        if spec.admin_only and not context.is_admin:
            return spec.name, "需要管理员权限。"
        context.args = args
        return spec.name, await spec.handler(context)

    @staticmethod
    def load_modules(module_names: str) -> None:
        for module_name in (name.strip() for name in module_names.split(",")):
            if module_name:
                importlib.import_module(module_name)

    def find(self, command: str) -> PluginSpec | None:
        name = self._aliases.get(command)
        return self._plugins.get(name) if name else None

    def help_text(self) -> str:
        roster_count = len(ULTRAMAN_ROSTER)
        anime_character_count = len(ANIME_CHARACTER_ROSTER)
        return (
            "┏ 小丛雨 · 功能菜单 ┓\n"
            "│\n"
            "│  💬 聊天｜@我 + 想说的话\n"
            "│\n"
            "│  📚 日漫与特摄角色图鉴\n"
            "│  @我 图鉴 / 角色图鉴\n"
            "│  @我 今日奥特曼 / 本命奥特曼\n"
            "│  @我 随机二次元角色 / 本命二次元角色\n"
            f"│  特摄 {roster_count} 位/形态｜日漫 {anime_character_count} 位角色\n"
            "│\n"
            "│  🌦️ 实时天气｜@我 天气 新加坡 / @我 北京天气\n"
            "│  主天气源异常时自动联网搜索并交给 LLM 整理\n"
            "│\n"
            "│  🖼️ 随机图片｜随机猫咪 / 随机猪猪 / 随机奶龙\n"
            "│  🌐 翻译与联网｜翻译 外语内容 / 搜索 关键词\n"
            "│  🎵 点歌｜点歌 歌名\n"
            "│  📺 B站｜播放视频 关键词\n"
            "│  🎭 夺舍｜夺舍 @群成员 / 随机夺舍 / 退出\n"
            "│  ♡ 好感度｜好感度 / 好感度记录\n"
            "│  🧠 记忆｜记住：内容 / 删除记忆 关键词\n"
            "│\n"
            "┗ @我 帮助 或 /help ┛\n\n"
            + character_catalog_menu(roster_count, anime_character_count)
        )
registry.register(
    PluginSpec(
        "character_catalog",
        (
            "/图鉴", "图鉴", "/角色图鉴", "角色图鉴",
            "/日漫与特摄角色图鉴", "日漫与特摄角色图鉴",
            "/今日奥特曼", "今日奥特曼", "/我的奥特曼", "我的奥特曼",
            "/本命奥特曼", "本命奥特曼", "/奥特曼图鉴", "奥特曼图鉴",
            "/特摄角色图鉴", "特摄角色图鉴",
            "/随机二次元角色", "随机二次元角色",
            "/今日二次元角色", "今日二次元角色",
            "/我的二次元角色", "我的二次元角色",
            "/本命二次元角色", "本命二次元角色",
            "/二次元角色图鉴", "二次元角色图鉴",
            "/日漫角色图鉴", "日漫角色图鉴",
        ),
        "日漫与特摄角色统一入口；两类收藏与本命统计保持独立",
    )
)
registry.register(
    PluginSpec(
        "weather",
        ("/天气", "/weather"),
        "/天气 <城市> —— 实时天气；主接口失败后自动联网 + LLM 兜底",
    )
)

