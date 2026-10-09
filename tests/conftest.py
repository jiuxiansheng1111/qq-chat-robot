import pytest


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
