"""验证原生插件聊天不会丢失角色隔离、音色选择或失败后的完整文字。"""

import sys
from types import ModuleType, SimpleNamespace

import pytest

from app.astrbot_chat import decorate_native_voice, prepare_native_chat
from app.config import Settings


class Database:
    selected = "murasame"
    voice_enabled = True
    possession = None

    async def voice_profile(self, *_):
        return self.selected

    async def daily_possession(self, *_):
        return self.possession

    async def possession_style_profile(self, *_):
        return "说话简短直接。"

    async def long_term_memories(self, *_):
        return ["喜欢清淡的食物"]

    async def group_memories(self, *_):
        return []

    async def voice_mode(self, *_):
        return self.voice_enabled


class Plain:
    def __init__(self, text):
        self.text = text


class Record:
    def __init__(self, file, text=""):
        self.file = file
        self.text = text


class Event:
    def __init__(self, prompt="今天吃什么"):
        self.prompt = prompt
        self.extras = {}
        self.message_obj = SimpleNamespace(raw_message={
            "message_type": "group", "group_id": 123, "user_id": 456, "self_id": 789,
        })
        self.result = SimpleNamespace(chain=[Plain("可以吃碗面。")], is_llm_result=lambda: True)

    def get_message_str(self):
        return self.prompt

    def set_extra(self, key, value):
        self.extras[key] = value

    def get_extra(self, key, default=None):
        return self.extras.get(key, default)

    def get_result(self):
        return self.result


@pytest.fixture
def runtime(monkeypatch):
    components = ModuleType("astrbot.api.message_components")
    components.Plain = Plain
    components.Record = Record
    monkeypatch.setitem(sys.modules, components.__name__, components)
    settings = Settings(
        _env_file=None, voice_enabled=True,
        voice_profiles_json='{"murasame":{"label":"小丛雨"},"mako":{"label":"茉子"}}',
    )
    core = SimpleNamespace(
        settings=settings, app=SimpleNamespace(state=SimpleNamespace(db=Database())),
        possession_identity_prompt=lambda name, mode: f"当前身份：{name}，模式：{mode}",
        group_memory_prompt=lambda memories, identity: str(memories),
    )
    return SimpleNamespace(_core=core)


@pytest.mark.asyncio
async def test_native_chat_preserves_plugin_context_and_only_introductions_get_role_tone(runtime):
    event = Event()
    request = SimpleNamespace(system_prompt="陪伴和表情插件的已有上下文", func_tool=object())
    tools = request.func_tool
    await prepare_native_chat(runtime, event, request)
    assert request.system_prompt.startswith("陪伴和表情插件的已有上下文")
    assert "当前不是自我介绍" in request.system_prompt
    assert "喜欢清淡的食物" in request.system_prompt
    assert request.func_tool is tools

    event.prompt = "介绍一下你自己"
    request.system_prompt = "角色背景"
    await prepare_native_chat(runtime, event, request)
    assert "可以用一句简短角色口吻" in request.system_prompt


@pytest.mark.asyncio
async def test_other_selected_roles_keep_their_own_persona(runtime):
    runtime._core.app.state.db.selected = "mako"
    request = SimpleNamespace(system_prompt="默认人格")
    await prepare_native_chat(runtime, Event(), request)
    assert "常陆茉子" in request.system_prompt
    assert "当前不是自我介绍" not in request.system_prompt


@pytest.mark.asyncio
async def test_possession_identity_takes_precedence_over_default_murasame(runtime):
    runtime._core.app.state.db.possession = ("member", "测试群友", "targeted")
    request = SimpleNamespace(system_prompt="默认人格")
    await prepare_native_chat(runtime, Event(), request)
    assert "当前身份：测试群友" in request.system_prompt
    assert "说话简短直接" in request.system_prompt
    assert "当前不是自我介绍" not in request.system_prompt


@pytest.mark.asyncio
async def test_voice_uses_request_snapshot_keeps_memes_and_has_original_text_fallback(runtime, monkeypatch):
    event = Event()
    runtime._core.app.state.db.selected = "mako"
    await prepare_native_chat(runtime, event, SimpleNamespace(system_prompt=""))
    runtime._core.app.state.db.selected = "murasame"
    image = object()
    event.result.chain.append(image)
    calls = []

    async def synthesize(text, settings, profile, **kwargs):
        calls.append((text, profile, kwargs))
        return "base64://audio"

    monkeypatch.setattr("app.astrbot_chat.synthesize_voice", synthesize)
    await decorate_native_voice(runtime, event)
    assert calls == [("可以吃碗面。", "mako", {"target_language": "zh", "bot_self_id": "789"})]
    assert isinstance(event.result.chain[0], Record)
    assert event.result.chain[0].text == "可以吃碗面。"
    assert event.result.chain[1] is image


@pytest.mark.asyncio
async def test_voice_memory_guard_failure_keeps_complete_original_result(runtime, monkeypatch):
    event = Event()
    await prepare_native_chat(runtime, event, SimpleNamespace(system_prompt=""))
    original = event.result.chain

    async def busy(*args, **kwargs):
        raise RuntimeError("正在翻唱")

    monkeypatch.setattr("app.astrbot_chat.synthesize_voice", busy)
    await decorate_native_voice(runtime, event)
    assert event.result.chain is original


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["text", "command", "already_voice"])
async def test_text_mode_commands_and_existing_audio_do_not_generate_another_voice(runtime, monkeypatch, mode):
    event = Event()
    await prepare_native_chat(runtime, event, SimpleNamespace(system_prompt=""))
    if mode == "text":
        runtime._core.app.state.db.voice_enabled = False
    elif mode == "command":
        event.result.is_llm_result = lambda: False
    else:
        event.result.chain.append(Record("old"))

    async def forbidden(*args, **kwargs):
        pytest.fail("不应生成第二份或未开启的语音")

    monkeypatch.setattr("app.astrbot_chat.synthesize_voice", forbidden)
    await decorate_native_voice(runtime, event)
