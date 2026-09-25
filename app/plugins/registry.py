import importlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from app.services.anime_character import ANIME_CHARACTER_ROSTER
from app.services.character_catalog import character_catalog_menu
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
            "│  @我 角色图鉴 捷德 / 角色图鉴 雷姆（统一搜索）\n"
            "│  @我 今日奥特曼 / 本命奥特曼\n"
            "│  @我 随机二次元角色 / 本命二次元角色\n"
            f"│  特摄 {roster_count} 位/形态｜日漫 {anime_character_count} 位角色\n"
            "│\n"
            "│  🌦️ 实时天气｜@我 天气 新加坡 / @我 北京天气\n"
            "│  主天气源异常时自动联网搜索并交给 LLM 整理\n"
            "│\n"
            "│  🖼️ 随机图片｜@我 随机猫咪 / @我 随机猪猪 / @我 随机奶龙\n"
            "│  🌐 翻译与联网｜@我 翻译 外语内容 / @我 搜索 关键词\n"
            "│  🎵 点歌｜@我 点歌 歌名\n"
            "│  📺 B站｜播放视频 关键词\n"
            "│  🔊 语音｜@我 启动语音 / @我 开启语音 / @我 关闭语音 / @我 语音状态\n"
            "│  🎙️ 音色｜@我 音色列表 / 选择音色 音色ID\n"
            "│  🗣️ 丛雨语音｜@我 丛雨语音（发送授权语音素材）\n"
            "│  🎨 生图｜@我 生成图片 描述\n"
            "│  🎭 夺舍｜夺舍 @群成员 / 随机夺舍 / 退出\n"
            "│  ♡ 恋爱模式｜开启恋爱模式 / 关闭恋爱模式\n"
            "│  🧠 记忆｜记住：内容 / 删除记忆 关键词\n"
            "│\n"
            "┗ @我 帮助 或 /help ┛\n\n"
            + character_catalog_menu(roster_count, anime_character_count)
        )


registry = PluginRegistry()
registry.register(PluginSpec("help", ("/help", "/帮助", "help", "帮助"), "/help —— 查看帮助"))
registry.register(
    PluginSpec(
        "romance_mode",
        (
            "/恋爱模式", "恋爱模式", "/开启恋爱模式", "开启恋爱模式",
            "/关闭恋爱模式", "关闭恋爱模式", "/恋爱模式状态", "恋爱模式状态",
        ),
        "按用户开关恋爱模式；严重辱骂/性骚扰/暴力威胁过量时当天禁止开启",
    )
)
registry.register(PluginSpec("ai", ("/ai", "/AI"), "/ai <问题> —— AI 对话"))
registry.register(
    PluginSpec(
        "translation",
        ("/翻译", "/translate"),
        "/翻译 <内容> —— 翻译成简体中文并给出转写/搜索别名",
    )
)
registry.register(
    PluginSpec(
        "music",
        ("/点歌", "/music"),
        "/点歌 <歌手/歌名> —— 网易云原唱音乐卡片",
    )
)
registry.register(
    PluginSpec(
        "bilibili_video",
        ("/视频", "/bili", "/bilibili"),
        "/视频 <关键词> 或 @机器人 播放视频 <关键词> —— B站高相关高播放视频卡片",
    )
)
registry.register(
    PluginSpec(
        "memory",
        ("/记忆开启", "/记忆关闭", "/记忆删除", "/记忆状态"),
        "/记忆开启|关闭|删除|状态 —— 管理你的短期记忆",
    )
)
registry.register(
    PluginSpec(
        "cat",
        ("/猫", "/cat", "猫图", "随机猫", "随机猫咪", "随机猫图"),
        "/猫 或 @机器人 随机猫咪 —— 随机猫图",
    )
)
registry.register(
    PluginSpec(
        "pig",
        ("/小猪", "/pig", "猪图", "随机猪", "随机猪猪", "随机小猪"),
        "/小猪 或 @机器人 随机猪猪 —— 随机真实小猪照片",
    )
)
registry.register(
    PluginSpec(
        "nailong",
        ("/奶龙", "奶龙", "随机奶龙", "来只奶龙", "龙来"),
        "/奶龙 或 @机器人 随机奶龙 —— 随机奶龙表情包",
    )
)
registry.register(
    PluginSpec(
        "ultraman",
        (
            "/今日奥特曼",
            "今日奥特曼",
            "/我的奥特曼",
            "我的奥特曼",
            "/本命奥特曼",
            "本命奥特曼",
            "/查看本命奥特曼",
            "查看本命奥特曼",
            "/奥特曼图鉴",
            "奥特曼图鉴",
            "/特摄角色图鉴",
            "特摄角色图鉴",
            "/特摄图鉴",
            "特摄图鉴",
        ),
        "每日抽取、收藏统计、本命奥特曼、完整图鉴与角色资料",
    )
)
registry.register(
    PluginSpec(
        "anime_character",
        (
            "/随机二次元角色",
            "随机二次元角色",
            "/今日二次元角色",
            "今日二次元角色",
            "/我的二次元角色",
            "我的二次元角色",
            "/查看本命二次元角色",
            "查看本命二次元角色",
            "/本命二次元角色",
            "本命二次元角色",
            "/二次元角色图鉴",
            "二次元角色图鉴",
            "/日漫角色图鉴",
            "日漫角色图鉴",
            "/日漫图鉴",
            "日漫图鉴",
        ),
        "随机抽取、收藏统计、本命角色、角色图鉴与角色资料",
    )
)
registry.register(
    PluginSpec(
        "character_catalog",
        (
            "/图鉴",
            "图鉴",
            "/角色图鉴",
            "角色图鉴",
            "/图鉴菜单",
            "图鉴菜单",
            "/图鉴总览",
            "图鉴总览",
            "/日漫与特摄角色图鉴",
            "日漫与特摄角色图鉴",
        ),
        "日漫与特摄角色统一主入口；两类收藏与本命统计保持独立",
    )
)
registry.register(
    PluginSpec(
        "weather",
        ("/天气", "/weather"),
        "/天气 <城市> —— 实时天气；主接口失败后自动联网 + LLM 兜底",
    )
)
registry.register(
    PluginSpec(
        "group_admin",
        ("/bot", "/blacklist"),
        "/bot on|off、/blacklist add|remove QQ号 —— 群管理",
        admin_only=True,
    )
)
