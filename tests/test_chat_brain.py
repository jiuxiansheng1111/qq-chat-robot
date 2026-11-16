import copy
import json
from types import SimpleNamespace

import pytest

from app.services.chat_brain import LUNA, PROFILE, BrainError, ChatBrain


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


@pytest.fixture
def brain():
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
