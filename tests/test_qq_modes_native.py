import importlib.util
import sys
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

from test_astrbot_native_onebot import _find_project_root, _load_astrbot_types, _make_group_event


async def test_native_video_topic_help_does_not_execute_or_send_the_full_menu(monkeypatch, tmp_path):
    root = _find_project_root()
    monkeypatch.setenv("ASTRBOT_ROOT", str(tmp_path / "native"))
    types = _load_astrbot_types(root, monkeypatch)
    from app.astrbot_group_chat import GroupChatRouter
    from app.services.qq_feature_menu import core_groups
    spec = importlib.util.spec_from_file_location("_topic_native_plugin", root / "main.py")
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    plugin = module.QQChatPlugin(NS(), {})
    plugin.runtime = NS(_core=NS(settings=NS()))
    monkeypatch.setattr(plugin, "_legacy_event_allowed", AsyncMock(return_value=True))
    monkeypatch.setattr("app.astrbot_menu.build_unified_menu_groups", lambda *args: core_groups())
    e, api, raw = _make_group_event(types)
    e.message_obj.group_id = "12345"
    e.message_str = "视频帮助"
    e.message_obj.message = [types.Plain(e.message_str)]
    raw["message"] = [{"type": "text", "data": {"text": e.message_str}}]
    router = GroupChatRouter({"group_chat_groups": ["12345"]})
    router.prepare_event(e)
    results = [result async for result in plugin.on_unified_help_menu(e)]
    assert len(results) == 1 and e.is_stopped()
    text = "".join(p.text for p in results[0].chain if isinstance(p, types.Plain))
    assert "30秒" in text and "网易云" not in text and "唱歌" not in text
    assert not api.calls
