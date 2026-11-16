import asyncio
import json
from types import SimpleNamespace

import pytest

from app.services.chat_brain import LUNA, PROFILE, ChatBrain


@pytest.mark.asyncio
async def test_switch_reaches_native_qq_selector_and_the_next_http_request(tmp_path, monkeypatch):
    from test_astrbot_native_onebot import _find_project_root, _load_astrbot_types
    root = _find_project_root()
    monkeypatch.setenv("ASTRBOT_ROOT", str(tmp_path / "native"))
    monkeypatch.chdir(tmp_path)
    _load_astrbot_types(root, monkeypatch)
    from aiohttp import web
    from astrbot.core.agent.runners.tool_loop_agent_runner import ToolLoopAgentRunner
    from astrbot.core.astr_main_agent import _select_provider
    from astrbot.core.provider.entities import ProviderType
    from astrbot.core.provider.manager import ProviderManager

    calls = []
    async def respond(request):
        data = await request.json()
        calls.append((request.path, data["model"], data["messages"][-1]["content"]))
        return web.json_response({"id":"test","object":"chat.completion","model":data["model"],"created":1,
                                  "choices":[{"index":0,"finish_reason":"stop","message":{"role":"assistant","content":"来自新大脑"}}]})
    app = web.Application()
    app.router.add_post("/v1/chat/completions", respond)
    async def models_in_memory(request):
        return web.json_response({"models":[{"name":"qwen-test:latest","context_length":8192}]})
    app.router.add_get("/api/ps",models_in_memory)
    async def model_card(request):
        assert (await request.json())["model"] == "qwen-test"
        return web.json_response({"capabilities":["completion"]})
    app.router.add_post("/api/show", model_card)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner,"127.0.0.1",0)
    await site.start()
    url = "http://127.0.0.1:" + str(next(iter(runner.addresses))[1]) + "/v1"

    class Config(dict):
        async def save_config_async(self, data):
            self.update(data)
            (tmp_path / "qq.json").write_text(json.dumps(self))
        def save_config(self):
            (tmp_path / "base.json").write_text(json.dumps(self))

    qq = Config(agent_runner={"runner_type":"local","config":{"model":{"provider_id":LUNA}}})
    base = Config(provider=[], agent_runner={"runner_type":"local","config":{"model":{"provider_id":"bilibili-original"}}})
    acm = SimpleNamespace(confs={PROFILE:qq}, default_conf=base, get_conf=lambda umo: qq if umo.startswith("qq:") else base)
    manager = object.__new__(ProviderManager)
    manager.acm = acm
    manager.provider_sources_config, manager.providers_config, manager.provider_settings = [], [], {}
    manager.provider_insts, manager.stt_provider_insts, manager.tts_provider_insts, manager.inst_map = [], [], [], {}
    manager.curr_provider_inst = manager.curr_stt_provider_inst = manager.curr_tts_provider_inst = None
    manager.default_chat_provider_id = LUNA
    manager.resource_lock = asyncio.Lock()
    import astrbot.core.provider.manager as native_manager
    monkeypatch.setattr(native_manager,"astrbot_config",base)
    async def using(umo):
        return manager._resolve_using_provider(ProviderType.CHAT_COMPLETION, umo, None)
    context = SimpleNamespace(astrbot_config_mgr=acm, provider_manager=manager, get_config=lambda:base,
                              get_provider_by_id=manager.inst_map.get, get_using_provider_async=using)
    try:
        brain = ChatBrain(context)
        applied = await brain.change({"preset":"ollama","base":url,"model":"qwen-test"}, apply=True)
        assert applied["model"] == "qwen-test"
        assert applied["context"]==8192
        event = SimpleNamespace(unified_msg_origin="qq:FriendMessage:fixture", get_extra=lambda k:None)
        selected = await _select_provider(event, context)
        assert selected.provider_config["modalities"] == ["text"]
        agent = SimpleNamespace(req=SimpleNamespace(func_tool=object()), provider=selected)
        assert ToolLoopAgentRunner._func_tool_for_provider(agent) is None
        assert (await selected.text_chat(prompt="下一轮普通 QQ 对话")).completion_text == "来自新大脑"
        assert [item[:2] for item in calls] == [("/v1/chat/completions","qwen-test")]*2
        assert "普通 QQ" in str(calls[-1][2])
        assert json.loads((tmp_path/"qq.json").read_text())["agent_runner"]["config"]["model"]["provider_id"] == brain.status()["active"]
        assert base["agent_runner"]["config"]["model"]["provider_id"] == "bilibili-original"
        assert json.loads((tmp_path/"base.json").read_text())["provider"][0]["model"] == "qwen-test"
        assert json.loads((tmp_path/"base.json").read_text())["provider"][0]["max_context_tokens"] == 8192
    finally:
        for pid in list(manager.inst_map):
            await manager.terminate_provider(pid)
        await runner.cleanup()
