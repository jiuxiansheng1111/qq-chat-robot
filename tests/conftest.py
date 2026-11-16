import pytest


@pytest.fixture(scope="session", autouse=True)
def disable_astrbot_usage_uploads():
    # Native OneBot sends schedule SDK usage counters on the current event loop.
    # Keep optional telemetry out of the tests and their per-test database lifetimes.
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("ASTRBOT_DISABLE_METRICS", "1")
        yield


@pytest.fixture
def room(monkeypatch):
    from test_astrbot_companion_turns import make_room

    return make_room(monkeypatch)


@pytest.fixture(autouse=True)
def isolate_owner_archive_and_usage(tmp_path,monkeypatch,request):
    """Offline fixtures must never write to real owner records or token usage."""
    if request.node.get_closest_marker("live"):
        yield
        return
    from app import astrbot_listening
    from app.services import owner_memory, owner_policy, qq_context_policy, usage_ledger
    monkeypatch.setattr(owner_policy,"POLICY_PATH",tmp_path/"owner-policy.json")
    monkeypatch.setattr(owner_memory,"ARCHIVE_PATH",tmp_path/"owner-memory.sqlite3")
    monkeypatch.setattr(usage_ledger,"LEDGER_PATH",tmp_path/"usage.sqlite3")
    monkeypatch.setattr(qq_context_policy,"POLICY_PATH",tmp_path/"context-policy.json")
    monkeypatch.setattr(astrbot_listening,"OPERATIONS_PATH",tmp_path/"listening-operations.json")
    context_token=qq_context_policy._QQ_CONTEXT.set(False)
    usage_token=usage_ledger._CONTEXT.set(None)
    yield
    qq_context_policy._QQ_CONTEXT.reset(context_token)
    usage_ledger._CONTEXT.reset(usage_token)


@pytest.fixture(autouse=True)
def isolate_qq_voice_pack(tmp_path,monkeypatch,request):
    """Unit fixtures must not inherit an installed live QQ default voice pack."""
    if request.node.get_closest_marker("live"):
        return
    from app.services import qq_voice
    monkeypatch.setattr(qq_voice,"PROFILE_PATH",tmp_path/"no-live-qq-voice.json")


@pytest.fixture(autouse=True)
def isolate_default_cat_history(tmp_path, monkeypatch, request):
    """Offline tests must never reserve images in the user's live history."""
    if request.node.get_closest_marker("live"):
        return
    from pathlib import Path

    from app.services.cat_history import CatImageHistory

    default = (Path(__file__).resolve().parents[1] / "data/cat_image_history.sqlite3").resolve()
    original = CatImageHistory.from_settings.__func__

    def isolated(cls, settings):
        value = getattr(settings, "cat_history_path", "./data/cat_image_history.sqlite3")
        if Path(value).resolve() == default:
            return cls(tmp_path / "cat_history.sqlite3")
        return original(cls, settings)

    monkeypatch.setattr(CatImageHistory, "from_settings", classmethod(isolated))


def pytest_addoption(parser):
    parser.addoption(
        "--live",
        action="store_true",
        default=False,
        help="run real external API and OneBot health checks",
    )


def pytest_collection_modifyitems(config, items):
    if config.getoption("--live"):
        return
    skip_live = pytest.mark.skip(reason="需要显式添加 --live 才会调用真实外部服务")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip_live)


@pytest.fixture
def owner_enabled():
    import json

    from app.services import owner_policy

    owner_policy.POLICY_PATH.write_text(json.dumps({"enabled":True,"owner_id":"12345","owner_name":"久",
        "archive_owner_private_and_group":True,"music_profile_enabled":True}),encoding="utf-8")
