import pytest


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
