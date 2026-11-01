"""在真实 AstrBot WakingCheck 管线验证早期归一化仍受原生命令权限控制。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from test_astrbot_native_onebot import _find_project_root, _load_astrbot_types, _make_group_event


@pytest.mark.parametrize(("text", "admin", "expected"), [
    ("群分析", False, "denied"),
    ("帮我总结一下最近3天的群聊", False, "denied"),
    ("群分析", True, "allowed"),
    ("请帮我总结一下最近3天的群聊", True, "allowed"),
    ("不要群分析", True, "ordinary"),
    ("群分析怎么用", True, "ordinary"),
])
async def test_real_waking_stage_normalizes_before_command_and_permission_filters(
    monkeypatch, tmp_path, text, admin, expected,
):
    root = _find_project_root()
    monkeypatch.setenv("ASTRBOT_ROOT", str(tmp_path / "isolated-astrbot"))
    monkeypatch.chdir(tmp_path)
    types = _load_astrbot_types(root, monkeypatch)
    monkeypatch.syspath_prepend(str(root))
    import importlib.util
    import sys

    from astrbot.api.event import filter
    from astrbot.api.star import Star
    from astrbot.core.pipeline.waking_check.stage import WakingCheckStage
    from astrbot.core.star.session_plugin_manager import SessionPluginManager
    from astrbot.core.star.star import star_map
    from astrbot.core.star.star_handler import star_handlers_registry

    monkeypatch.setattr(star_handlers_registry, "_handlers", [])
    monkeypatch.setattr(star_handlers_registry, "star_handlers_map", {})
    spec = importlib.util.spec_from_file_location("_group_pipeline_test_main", root / "main.py")
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    plugin = module.QQChatPlugin(SimpleNamespace(), {
        "legacy_chat": False, "group_chat_groups": ["12345"], "natural_commands_enabled": True,
    })
    plugin.runtime = object()
    star_map[spec.name].name = "isolated-qqchat"

    class SecuredPlugin(Star):
        @filter.command("群分析")
        @filter.permission_type(filter.PermissionType.ADMIN)
        async def secured_analysis(self, event, days: int = 1):
            raise AssertionError("测试只运行唤醒检查，不应执行功能或发出网络请求")

    star_map[SecuredPlugin.__module__].name = "isolated-secured-plugin"
    handlers = [h for h in star_handlers_registry if h.handler_module_path in {
        spec.name, SecuredPlugin.__module__,
    }]
    monkeypatch.setattr(star_handlers_registry, "_handlers", handlers)

    async def allow_session(_event, handlers):
        return handlers

    monkeypatch.setattr(SessionPluginManager, "filter_handlers_by_session", allow_session)
    cfg = {
        "platform_settings": {"no_permission_reply": True, "ignore_bot_self_message": True},
        "admins_id": ["12345"] if admin else [], "wake_prefix": ["/"], "plugin_set": ["*"],
    }
    stage = WakingCheckStage()
    await stage.initialize(SimpleNamespace(astrbot_config=cfg, db_helper=None, astrbot_config_id="test"))
    event, api, raw = _make_group_event(types)
    event.message_str = text
    event.message_obj.message = [types.Plain(text)]
    raw["message"] = [{"type": "text", "data": {"text": text}}]
    sent = []

    async def record_send(chain):
        sent.append(chain)

    monkeypatch.setattr(event, "send", record_send)
    await stage.process(event)
    activated = event.get_extra("activated_handlers", [])
    secured = [h for h in activated if h.handler_name == "secured_analysis"]
    if expected == "denied":
        assert event.is_stopped() and sent and not secured
    elif expected == "allowed":
        assert not event.is_stopped() and not sent and len(secured) == 1
        assert event.is_at_or_wake_command
        assert event.get_message_str() == ("群分析 3" if "3天" in text else "群分析")
        assert event.get_extra("handlers_parsed_params")[secured[0].handler_full_name]["days"] == (
            3 if "3天" in text else 1
        )
    else:
        assert not secured and not sent
        assert event.get_message_str() == text
    assert api.calls == []
