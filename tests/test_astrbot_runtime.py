import asyncio
import importlib
import sys
from pathlib import Path
from types import ModuleType

import pytest

from app.config import Settings
from app.services.onebot_routing import (
    ASTRBOT_API_BASE,
    current_onebot_self_id,
    onebot_client,
    onebot_route,
    register_action_client,
    set_current_onebot_self_id,
    track_replies,
    unregister_action_client,
)


def _fake_core(settings=None):
    core = ModuleType("app.main")
    core.settings = settings or Settings(_env_file=None)
    core.app = object()
    core.initialized = []
    core.closed = []

    async def initialize_runtime(app):
        core.initialized.append(app)

    async def close_runtime(app):
        core.closed.append(app)

    async def dispatch_onebot_event(event, request, *, allow_chat=True):
        return {"ok": True}

    core.initialize_runtime = initialize_runtime
    core.close_runtime = close_runtime
    core.dispatch_onebot_event = dispatch_onebot_event
    return core


@pytest.fixture
def runtime_module(monkeypatch):
    module = importlib.import_module("app.astrbot_runtime")
    original_import_module = importlib.import_module
    core_holder = {}

    def fake_import_module(name, *args, **kwargs):
        if name == "app.main":
            return core_holder["core"]
        return original_import_module(name, *args, **kwargs)

    monkeypatch.setattr(module.importlib, "import_module", fake_import_module)
    module._test_core_holder = core_holder
    yield module
    if hasattr(module, "_test_core_holder"):
        del module._test_core_holder


@pytest.fixture(autouse=True)
def clear_routing_state():
    set_current_onebot_self_id("")
    for self_id in ("runtime-one", "runtime-two", "runtime-cancel", "outside-runtime"):
        unregister_action_client(self_id)
    yield
    set_current_onebot_self_id("")
    for self_id in ("runtime-one", "runtime-two", "runtime-cancel", "outside-runtime"):
        unregister_action_client(self_id)


def _install_core(runtime_module, monkeypatch, core):
    runtime_module._test_core_holder["core"] = core
    monkeypatch.setitem(sys.modules, "app.main", core)


def _group_event(self_id, **updates):
    event = {
        "self_id": self_id,
        "post_type": "message",
        "message_type": "group",
        "group_id": "42",
        "user_id": "7",
        "message": [{"type": "text", "data": {"text": "hello"}}],
    }
    event.update(updates)
    return event


@pytest.mark.asyncio
async def test_initialize_uses_root_env_resolves_paths_and_restores_core_settings(
    runtime_module, monkeypatch, tmp_path
):
    env_path = tmp_path / ".env"
    env_path.write_text(
        "DATABASE_PATH=./state/chat.sqlite3\n"
        "PERSONA_PROMPT_FILE=./prompts/robot.txt\n"
        "SINGING_PYTHON=./runtime/python.exe\n"
        "SINGING_SEED_ROOT=./runtime/seed-vc\n",
        encoding="utf-8",
    )
    for name in (
        "DATABASE_PATH",
        "PERSONA_PROMPT_FILE",
        "SINGING_PYTHON",
        "SINGING_SEED_ROOT",
    ):
        monkeypatch.delenv(name, raising=False)

    original_settings = Settings(_env_file=None)
    core = _fake_core(original_settings)
    _install_core(runtime_module, monkeypatch, core)
    previous_cwd = Path.cwd()
    runtime = runtime_module.ChatRuntime(project_root=tmp_path, legacy_chat=False)

    await runtime.initialize()

    assert Path.cwd() == previous_cwd
    assert core.initialized == [core.app]
    assert Path(core.settings.database_path) == (tmp_path / "state/chat.sqlite3").resolve()
    assert Path(core.settings.persona_prompt_file) == (tmp_path / "prompts/robot.txt").resolve()
    assert Path(core.settings.singing_python) == (tmp_path / "runtime/python.exe").resolve()
    assert Path(core.settings.singing_seed_root) == (tmp_path / "runtime/seed-vc").resolve()

    await runtime.close()

    assert core.closed == [core.app]
    assert core.settings is original_settings


@pytest.mark.asyncio
async def test_handle_event_uses_reply_tracking_and_legacy_chat_setting(
    runtime_module, monkeypatch, tmp_path
):
    core = _fake_core()
    received = []

    async def dispatch(event, request, *, allow_chat=True):
        received.append((event, request.app, allow_chat))
        set_current_onebot_self_id(str(event["self_id"]))
        route = onebot_route(core.settings)
        async with onebot_client(core.settings) as client:
            response = await client.post(
                f"{route.api_base}/send_group_msg",
                json={"group_id": event["group_id"], "message": "answer"},
            )
        assert response.json()["data"] == {"sent_by": event["self_id"]}
        return {"ok": True, "source": "legacy"}

    async def event_action(action, **params):
        return {"sent_by": "runtime-one"}

    core.dispatch_onebot_event = dispatch
    _install_core(runtime_module, monkeypatch, core)
    runtime = runtime_module.ChatRuntime(project_root=tmp_path, legacy_chat=False)
    await runtime.initialize()

    result = await runtime.handle_event(_group_event("runtime-one"), event_action)

    assert result["handled"] is True
    assert result["source"] == "legacy"
    assert current_onebot_self_id() == ""
    assert received == [(_group_event("runtime-one"), core.app, False)]
    await runtime.close()


@pytest.mark.asyncio
async def test_parallel_events_keep_self_ids_and_reply_counters_isolated(
    runtime_module, monkeypatch, tmp_path
):
    core = _fake_core()
    entered = 0
    both_entered = asyncio.Event()
    routed = []

    async def dispatch(event, request, *, allow_chat=True):
        nonlocal entered
        self_id = str(event["self_id"])
        set_current_onebot_self_id(self_id)
        entered += 1
        if entered == 2:
            both_entered.set()
        await both_entered.wait()
        route = onebot_route(core.settings)
        async with onebot_client(core.settings) as client:
            response = await client.post(
                f"{route.api_base}/send_private_msg",
                json={"user_id": self_id, "message": "reply"},
            )
        routed.append(response.json()["data"]["sender"])
        return {"ok": True}

    def action_for(self_id):
        async def action_call(action, **params):
            await asyncio.sleep(0)
            return {"sender": self_id}

        return action_call

    core.dispatch_onebot_event = dispatch
    _install_core(runtime_module, monkeypatch, core)
    runtime = runtime_module.ChatRuntime(project_root=tmp_path)
    await runtime.initialize()

    first, second = await asyncio.gather(
        runtime.handle_event(_group_event("runtime-one"), action_for("runtime-one")),
        runtime.handle_event(_group_event("runtime-two"), action_for("runtime-two")),
    )

    assert first["handled"] is True
    assert second["handled"] is True
    assert sorted(routed) == ["runtime-one", "runtime-two"]
    await runtime.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "reason",
    ["blocked", "user_blocked", "group_disabled", "plugin_disabled", "duplicate_event"],
)
async def test_known_ignored_reasons_are_handled(runtime_module, monkeypatch, tmp_path, reason):
    core = _fake_core()

    async def dispatch(event, request, *, allow_chat=True):
        return {"ok": True, "ignored": True, "reason": reason}

    core.dispatch_onebot_event = dispatch
    _install_core(runtime_module, monkeypatch, core)
    runtime = runtime_module.ChatRuntime(project_root=tmp_path)
    await runtime.initialize()

    result = await runtime.handle_event(_group_event("runtime-one"), lambda *args, **kwargs: None)

    assert result["handled"] is True
    await runtime.close()


@pytest.mark.asyncio
async def test_unhandled_response_and_existing_handled_result(runtime_module, monkeypatch, tmp_path):
    core = _fake_core()
    core.dispatch_onebot_event = _returning({"ok": True, "ignored": True})
    _install_core(runtime_module, monkeypatch, core)
    runtime = runtime_module.ChatRuntime(project_root=tmp_path, legacy_chat=False)
    await runtime.initialize()

    quiet = await runtime.handle_event(_group_event("runtime-one"), _unused_action)
    core.dispatch_onebot_event = _returning({"ok": True, "handled": True})
    already_handled = await runtime.handle_event(_group_event("runtime-two"), _unused_action)

    assert quiet["handled"] is False
    assert already_handled["handled"] is True
    await runtime.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "event",
    [
        {"self_id": "runtime-one", "post_type": "notice", "message_type": "group"},
        {"self_id": "runtime-one", "post_type": "message", "message_type": "private"},
    ],
)
async def test_non_group_or_non_message_is_ignored_without_dispatch(
    runtime_module, monkeypatch, tmp_path, event
):
    core = _fake_core()
    calls = []

    async def dispatch(*args, **kwargs):
        calls.append((args, kwargs))
        return {"ok": True}

    core.dispatch_onebot_event = dispatch
    _install_core(runtime_module, monkeypatch, core)
    runtime = runtime_module.ChatRuntime(project_root=tmp_path)
    await runtime.initialize()

    result = await runtime.handle_event(event, _unused_action)

    assert result["ignored"] is True
    assert result["handled"] is False
    assert calls == []
    await runtime.close()


@pytest.mark.asyncio
async def test_handle_event_requires_initialization(runtime_module, tmp_path):
    runtime = runtime_module.ChatRuntime(project_root=tmp_path)

    with pytest.raises(RuntimeError):
        await runtime.handle_event(_group_event("runtime-one"), _unused_action)


@pytest.mark.asyncio
async def test_cancelled_event_does_not_leak_reply_tracking_and_close_unregisters_ids(
    runtime_module, monkeypatch, tmp_path
):
    core = _fake_core()
    dispatch_started = asyncio.Event()

    async def dispatch(event, request, *, allow_chat=True):
        set_current_onebot_self_id(str(event["self_id"]))
        dispatch_started.set()
        await asyncio.Event().wait()

    core.dispatch_onebot_event = dispatch
    _install_core(runtime_module, monkeypatch, core)
    runtime = runtime_module.ChatRuntime(project_root=tmp_path)
    await runtime.initialize()
    task = asyncio.create_task(runtime.handle_event(_group_event("runtime-cancel"), _unused_action))
    await dispatch_started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    with track_replies() as counts:
        assert (counts.attempted, counts.succeeded) == (0, 0)
    await runtime.close()

    assert core.settings is not None
    assert onebot_route(core.settings, "runtime-cancel").api_base != ASTRBOT_API_BASE


@pytest.mark.asyncio
async def test_close_unregisters_only_this_runtime_action_clients(runtime_module, monkeypatch, tmp_path):
    async def external_action(action, **params):
        return {"external": True}

    register_action_client("outside-runtime", external_action)
    core = _fake_core()
    _install_core(runtime_module, monkeypatch, core)
    runtime = runtime_module.ChatRuntime(project_root=tmp_path)
    await runtime.initialize()
    await runtime.handle_event(_group_event("runtime-one"), _unused_action)

    assert onebot_route(core.settings, "runtime-one").api_base == ASTRBOT_API_BASE
    await runtime.close()

    assert onebot_route(core.settings, "runtime-one").api_base != ASTRBOT_API_BASE
    assert onebot_route(core.settings, "outside-runtime").api_base == ASTRBOT_API_BASE


async def _unused_action(action, **params):
    return None


def _returning(value):
    async def dispatch(event, request, *, allow_chat=True):
        return value

    return dispatch
