"""Use actual AstrBot content parts and installed Companion XML wire format."""
import ast
import importlib.util
import sys
from types import SimpleNamespace

import pytest


def test_installed_companion_persona_cleanup(monkeypatch, tmp_path):
    from test_astrbot_native_onebot import _find_project_root, _load_astrbot_types
    root = _find_project_root()
    monkeypatch.setenv("ASTRBOT_ROOT", str(tmp_path / "isolated-native"))
    _load_astrbot_types(root, monkeypatch)
    from astrbot.core.agent.message import TextPart
    from astrbot.core.provider.entities import ProviderRequest

    from app.astrbot_qq_persona import apply_qq_persona

    path = root / "data/astrbot/instance/data/plugins/astrbot_plugin_private_companion/conversation_prompt_section.py"
    if not path.is_file():
        pytest.skip("需要本机已安装的 Private Companion 插件，用于验证真实提示词格式")
    spec = importlib.util.spec_from_file_location("isolated_companion_renderer", path)
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    section = module.prompt_section
    # Exercise the installed plugin's real body, which was not covered by the
    # older reply.style-only fixture. No plugin instance or live store is opened.
    source = ast.parse((path.parent / "main.py").read_text(encoding="utf-8-sig"))
    plugin_class = next(node for node in source.body if isinstance(node, ast.ClassDef) and node.name == "PrivateCompanionPlugin")
    method = next(node for node in plugin_class.body if isinstance(node, ast.FunctionDef) and node.name == "_format_group_persona_denoise_body")
    namespace = {"runtime_persona_setting": lambda owner, key, default: getattr(owner, key, default)}
    exec("from __future__ import annotations\n" + ast.unparse(method), namespace)  # noqa: S102 -- trusted installed plugin source
    denoise = namespace[method.name](SimpleNamespace(enable_group_persona_denoise=True))
    assert "宁可短一点" in denoise
    rendered = module.render_prompt_sections([
        section(key="reply.style", title="回复风格约束", source="reply_style", content="旧龙女仆20字限制"),
        section(key="group.persona_denoise", title="群聊人格降噪", source="group", content=denoise),
        section(key="group.privacy", title="群聊隐私", source="group", content="不公开其他人的私聊"),
        section(key="evidence", title="牌谱数据", source="tool", content="东1局第8巡，推荐切9万"),
    ])
    part = TextPart(text=rendered)
    part._private_companion_turn_fragments = True
    request = ProviderRequest(prompt="分析整场", system_prompt="", extra_user_content_parts=[part])
    apply_qq_persona(SimpleNamespace(), SimpleNamespace(get_platform_name=lambda: "aiocqhttp"), request)
    assert "旧龙女仆" not in request.extra_user_content_parts[0].text
    assert "宁可短一点" not in request.extra_user_content_parts[0].text
    assert "不公开其他人的私聊" in request.extra_user_content_parts[0].text
    assert "推荐切9万" in request.extra_user_content_parts[0].text
    assert "大肥鱼" in request.system_prompt and request.prompt == "分析整场"
