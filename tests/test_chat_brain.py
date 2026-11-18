import copy
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services.chat_brain import (
    LUNA,
    MOBILE_ONEBOT_ID,
    PROFILE,
    WEB_SESSION,
    BrainError,
    ChatBrain,
)


class Profile(dict):
    async def save_config_async(self, data):
        self.update(data)


class Manager:
    def __init__(self):
        self.saved = []
        self.instances = {}
        self.calls = []

    async def load_provider(self, cfg):
        async def chat(**kwargs):
            self.calls.append((cfg["id"], kwargs["prompt"]))
            if cfg["model"] == "rejected":
                raise ValueError("test-private-secret")
            return SimpleNamespace(completion_text="你好")
        self.instances[cfg["id"]] = SimpleNamespace(text_chat=chat, get_model=lambda: cfg["model"])

    async def create_provider(self, cfg):
        self.saved.append(copy.deepcopy(cfg))
        await self.load_provider(cfg)

    async def terminate_provider(self, pid):
        self.instances.pop(pid, None)

    async def delete_provider(self,provider_id):
        await self.terminate_provider(provider_id)
        self.saved[:] = [p for p in self.saved if p["id"]!=provider_id]


@pytest.fixture
def brain(monkeypatch):
    monkeypatch.setattr("app.services.chat_brain.ollama_window",AsyncMock(return_value=4096))
    monkeypatch.setattr("app.services.chat_brain.ollama_modalities", AsyncMock(return_value=["text", "tool_use"]))
    manager = Manager()
    model = {"provider_id": LUNA}
    profile = Profile(agent_runner={"runner_type": "local", "config": {"model": model, "compression": model.copy()}})
    context = SimpleNamespace(provider_manager=manager, astrbot_config_mgr=SimpleNamespace(confs={PROFILE: profile}),
                              get_provider_by_id=manager.instances.get, get_config=lambda: {"provider": manager.saved})
    return ChatBrain(context)


@pytest.mark.asyncio
@pytest.mark.parametrize(("preset","protocol"), [("ollama","openai_chat_completion"), ("deepseek","openai_chat_completion"),
    ("zhipu","openai_chat_completion"), ("openai","openai_responses"), ("claude","anthropic_chat_completion"),
    ("gemini","openai_chat_completion"), ("custom","anthropic_chat_completion")])
async def test_switch_probe_and_reopen(brain, preset, protocol):
    data = {"preset": preset, "model": "demo", "key": "" if preset == "ollama" else "test-private-secret"}
    if preset == "custom":
        data.update(base="https://models.example.org/api", protocol="messages")
    assert not (await brain.change(data, apply=False))["applied"]
    manager = brain.context.provider_manager
    assert not manager.saved and not manager.instances and brain.status()["active"] == LUNA
    assert (await brain.change(data, apply=True))["reply"] == "你好"
    reopened = ChatBrain(brain.context)
    snapshot = reopened.status()
    assert snapshot["active"] == manager.saved[0]["id"]
    assert manager.saved[0]["type"] == protocol and snapshot["active"] in manager.instances
    assert reopened.profile()["agent_runner"]["config"]["compression"]["provider_id"] == snapshot["active"]
    assert "test-private-secret" not in json.dumps(snapshot)
    await brain.change(dict(data, key="", saved=snapshot["active"]), apply=True)
    assert len(manager.saved) == 1


@pytest.mark.asyncio
async def test_model_selection_survives_native_provider_source_split(brain):
    await brain.change({"preset":"zhipu","model":"example","key":"test-private-secret"},apply=True)
    provider = copy.deepcopy(brain.context.provider_manager.saved[0])
    provider_id = provider["id"]
    source = {k:v for k,v in provider.items() if k not in {"id","enable","model","modalities"}}
    source["id"] = provider_id + "_source"
    entry = {k:provider[k] for k in ("id","enable","model","modalities")}
    entry["provider_source_id"] = source["id"]
    brain.context.get_config = lambda: {"provider":[entry],"provider_sources":[source]}
    reopened = ChatBrain(brain.context)
    state = reopened.status()
    assert state["profiles"][0]["preset"]=="zhipu" and state["profiles"][0]["id"]==provider_id
    assert state["active"]==provider_id and "test-private-secret" not in json.dumps(state)
    assert (await reopened.change({"preset":"zhipu","model":"example","saved":provider_id,"key":""},apply=True))["applied"]
    assert len(brain.context.provider_manager.saved)==1


@pytest.mark.asyncio
async def test_failure_and_changed_host_preserve_current(brain):
    data = {"preset":"custom", "model":"good", "base":"https://first.example/v1", "key":"test-private-secret"}
    await brain.change(data, apply=True)
    current = brain.status()["active"]
    with pytest.raises(BrainError, match="密钥"):
        await brain.change(dict(data, saved=current, key="", base="https://second.example/v1"), apply=True)
    with pytest.raises(BrainError) as error:
        await brain.change(dict(data, model="rejected"), apply=True)
    assert "test-private-secret" not in str(error.value)
    assert brain.status()["active"] == current and set(brain.context.provider_manager.instances) == {current}


@pytest.mark.asyncio
async def test_model_window_and_failure_after_native_provider_save(brain,monkeypatch):
    response = await brain.change({"preset":"ollama","model":"example"},apply=True)
    assert response["context"]==4096 and brain.context.provider_manager.saved[0]["max_context_tokens"]==4096
    initial = brain.status()["active"]
    profile = brain.profile()
    monkeypatch.setattr(profile,"save_config_async",AsyncMock(side_effect=OSError("disk error")))
    with pytest.raises(BrainError):
        await brain.change({"preset":"custom","base":"http://localhost:9000/v1","model":"new","context":2048},apply=True)
    assert [p["id"] for p in brain.context.provider_manager.saved]==[initial]
    assert set(brain.context.provider_manager.instances)=={initial} and brain.status()["active"]==initial
    with pytest.raises(BrainError,match="上下文"):
        await brain.change({"preset":"ollama","model":"example","context":-3},apply=True)


@pytest.mark.asyncio
async def test_web_only_phone_can_select_model_before_pairing_and_recovers_from_failed_save(brain, monkeypatch):
    monkeypatch.setenv("QQCHAT_TERMUX_BIN","/data/data/com.termux/files/usr/bin")
    acm = brain.context.astrbot_config_mgr
    original = acm.confs.pop(PROFILE)
    base = {"agent_runner": original["agent_runner"], "provider": brain.context.provider_manager.saved, "platform": []}
    brain.context.get_config = lambda: base
    routes = {"other::": "other-profile"}
    acm.ucr = SimpleNamespace(umop_to_conf_id=routes)
    async def create(config, name):
        acm.confs["new"] = Profile(copy.deepcopy(config))
        return "new"
    async def remove(pid):
        del acm.confs[pid]
    async def route(new_routes):
        acm.ucr.umop_to_conf_id = new_routes
    acm.create_conf, acm.delete_conf = create, remove
    acm.ucr.update_routing_data = AsyncMock(side_effect=[OSError("disk full"), None])
    data = {"preset":"custom","base":"http://localhost:45325/v1","model":"text-only","mode":"text"}
    with pytest.raises(BrainError):
        await brain.change(data, apply=True)
    assert not acm.confs and not base["provider"] and acm.ucr.umop_to_conf_id == routes
    acm.ucr.update_routing_data = route
    await brain.change(data, apply=True)
    assert brain.status()["active"] == base["provider"][0]["id"]
    assert base["provider"][0]["modalities"] == ["text"]
    assert acm.ucr.umop_to_conf_id == {WEB_SESSION:"new", MOBILE_ONEBOT_ID + "::":"new", "webchat::":"new", **routes}
    assert base["agent_runner"]["config"]["model"]["provider_id"] == LUNA
    del acm.ucr.umop_to_conf_id["webchat::"]  # Upgrade an already configured web-assistant-only phone.
    await brain.change(dict(data, saved=brain.status()["active"], mode="image_tools"), apply=True)
    assert acm.ucr.umop_to_conf_id["webchat::"] == "new"
    assert base["provider"][1]["modalities"] == ["text", "image", "tool_use"]
    assert brain.status()["profiles"][1]["mode"] == "image_tools"


@pytest.mark.asyncio
async def test_ollama_catalogue_keeps_unknown_and_identifies_missing_local_models(brain):
    from aiohttp import web
    models = ["loaded", "missing", "unsure"]
    app = web.Application()
    async def catalogue(request):
        return web.json_response({"data": [{"id":name} for name in models]})
    async def show(request):
        name = (await request.json())["model"]
        code = {"loaded":200, "missing":404, "unsure":503}[name]
        return web.json_response({"capabilities":["completion"]}, status=code)
    app.router.add_get("/v1/models", catalogue)
    app.router.add_post("/api/show", show)
    app.router.add_get("/compat/v1/models", catalogue)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    base = "http://127.0.0.1:" + str(next(iter(runner.addresses))[1])
    try:
        assert await brain.models({"base":base + "/v1"}) == {"models":["loaded","unsure"],"unavailable":["missing"]}
        assert await brain.models({"base":base + "/compat/v1"}) == {"models":models,"unavailable":[]}
    finally:
        await runner.cleanup()
