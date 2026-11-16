"""The cancelled expression mode has no command or prompt entry."""
from types import SimpleNamespace as NS

import pytest

from app.astrbot_group_chat import normalize_natural_command
from app.astrbot_qq_persona import apply_qq_persona
from app.services.qq_feature_menu import core_groups, requested_topic


@pytest.mark.parametrize("text", ["开启恶魔模式", "关闭恶魔模式", "恶魔模式状态", "恶魔模式帮助"])
def test_removed_mode_does_not_wake_or_appear_in_menus(text):
    assert normalize_natural_command(text) is None
    assert not requested_topic(text)
    assert "恶魔" not in str(core_groups())

def test_normal_persona_keeps_history_and_tools():
    history, tools = [{"role": "user", "content": "之前说过恶魔模式"}], object()
    req = NS(system_prompt="原有事实规则", prompt="当前问题", contexts=history, func_tool=tools)
    assert apply_qq_persona(NS(), NS(get_platform_name=lambda: "aiocqhttp"), req)
    assert "恶魔模式" not in req.system_prompt
    assert req.contexts is history and req.func_tool is tools
    assert req.prompt == "当前问题" and "原有事实规则" in req.system_prompt
