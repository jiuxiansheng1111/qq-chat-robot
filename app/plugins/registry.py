import importlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

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
                return self._plugins[name], text[len(command):].strip()
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
        lines = [spec.description for spec in self._plugins.values()]
        return "可用指令：\n" + "\n".join(lines)


registry = PluginRegistry()
registry.register(PluginSpec("help", ("/help", "help"), "/help —— 查看帮助"))
registry.register(PluginSpec("ai", ("/ai", "/AI"), "/ai <问题> —— AI 对话"))
registry.register(PluginSpec("memory", ("/记忆开启", "/记忆关闭", "/记忆删除", "/记忆状态"), "/记忆开启|关闭|删除|状态 —— 管理你的短期记忆"))
registry.register(PluginSpec("cat", ("/猫", "/cat", "猫图", "随机猫", "随机猫咪", "随机猫图"), "/猫 或 @机器人 随机猫咪 —— 随机猫图"))
registry.register(PluginSpec("pig", ("/小猪", "/pig", "猪图", "随机猪", "随机猪猪", "随机小猪"), "/小猪 或 @机器人 随机猪猪 —— 随机小猪图片"))
registry.register(PluginSpec("group_admin", ("/bot", "/blacklist"), "/bot on|off、/blacklist add|remove QQ号 —— 群管理", admin_only=True))
