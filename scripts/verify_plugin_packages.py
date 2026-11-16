"""Cold-load the published ZIP files in an isolated AstrBot 4.28.2 process.

Run this script with the AstrBot Python environment. No bot account is needed.
"""
import argparse
import asyncio
import hashlib
import importlib
import itertools
import os
import sys
import tempfile
import tomllib
import zipfile
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch


SOURCE = Path(__file__).resolve().parents[1] / "plugin_packages"
PREFIX = "astrbot_plugin_qqchat_"
NUMBERS = itertools.count(100)


class Context:
    def __init__(self):
        self.stars = {}

    def get_config(self, *_):
        return {}

    def get_all_stars(self):
        return list(self.stars.values())

    def get_registered_star(self, name):
        return self.stars.get(name)


def unpack(directory, destination):
    catalog = tomllib.loads((SOURCE / "catalog.toml").read_text(encoding="utf-8"))
    version = catalog.pop("version")
    expected = {PREFIX + slug + ".zip" for slug in catalog}
    sums = dict(line.split(None, 1)[::-1] for line in (directory / "SHA256SUMS").read_text().splitlines())
    assert set(sums) == expected == {p.name for p in directory.glob("*.zip")}
    for name, digest in sums.items():
        path = directory / name
        assert hashlib.sha256(path.read_bytes()).hexdigest() == digest, name
        with zipfile.ZipFile(path) as archive:
            assert archive.testzip() is None, name
            for member in archive.namelist():
                parts = Path(member).parts
                assert parts[0] == path.stem and not {"..", ".env", "node_modules", "__pycache__"}.intersection(parts), member
                assert parts[1:3] != ("runtime", "data"), member
            archive.extractall(destination)
    return list(catalog), version


def event(text, *, group=False):
    from astrbot.api.message_components import Plain
    from astrbot.core.platform.astrbot_message import AstrBotMessage, MessageMember
    from astrbot.core.platform.message_type import MessageType
    from astrbot.core.platform.platform_metadata import PlatformMetadata
    from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import AiocqhttpMessageEvent

    message = AstrBotMessage()
    message.type = MessageType.GROUP_MESSAGE if group else MessageType.FRIEND_MESSAGE
    message.self_id, message.session_id = "99999", "12345"
    message.message_id = str(next(NUMBERS))
    message.sender = MessageMember(user_id="12345", nickname="测试")
    message.message, message.group_id = [Plain(text)], ("12345" if group else "")
    message.raw_message = {
        "post_type": "message", "message_type": "group" if group else "private", "self_id": message.self_id,
        "user_id": message.session_id, "message_id": message.message_id,
        "message": [{"type": "text", "data": {"text": text}}],
    }
    if group:
        message.raw_message["group_id"] = message.group_id
    ev = AiocqhttpMessageEvent(
        message_str=text, message_obj=message,
        platform_meta=PlatformMetadata(name="aiocqhttp", description="离线验证", id="qq-test"),
        session_id=message.session_id, bot=SimpleNamespace(api=AsyncMock()),
    )
    ev.send = AsyncMock()
    return ev


async def verify(directory, temp):
    from astrbot.api.star import StarTools
    from astrbot.core.star.star import star_map
    from astrbot.core.star.star_manager import PluginManager

    plugins = temp / "plugins"
    slugs, version = unpack(directory, plugins)
    sys.path.insert(0, str(plugins))
    ctx = Context()

    def load(slug, *, activate=True):
        name = PREFIX + slug
        module = importlib.import_module(name + ".main")
        meta = PluginManager._load_plugin_metadata(str(plugins / name))
        assert meta.name == name and meta.version == "v" + version
        cls = star_map[module.__name__].star_cls_type
        assert cls.__module__ == module.__name__
        if activate:
            meta.star_cls, meta.activated = cls(ctx), True
            ctx.stars[name] = meta
        return meta.star_cls

    weather = load("weather")
    await weather.initialize()
    assert weather.host() is None
    core = load("core")
    with patch.object(StarTools, "get_data_dir", return_value=temp / "state"):
        await core.initialize()
    try:
        await weather.initialize()
        assert weather._attached is core and set(core._attached_parts) == {"weather"}
        from app.services.weather import WeatherReport
        report = WeatherReport("深圳", "Asia/Shanghai", 25.0, 26.0, 70.0, 8.0, 0.0, "多云", ())
        ev = event("大肥鱼深圳今天天气怎么样")
        with patch(PREFIX + "weather.logic.fetch_weather", AsyncMock(return_value=report)) as fetch:
            await core.on_natural_message(ev)
        assert ev.is_stopped() and ev.call_llm is False and ev.send.await_count == 1
        assert "25°C" in ev.send.await_args.args[0].chain[0].text and fetch.await_args.args[0] == "深圳"
        game_text = "大肥鱼，一起玩 Steam"
        ev = event(game_text)
        await core.on_natural_message(ev)
        ev.send.assert_not_awaited()

        steam = load("steam")
        await steam.initialize()
        mahjong = load("mahjong")
        await mahjong.initialize()
        ev = event(game_text)
        await core.on_natural_message(ev)
        assert ev.send.await_count == 1 and "Steam" in ev.send.await_args.args[0].chain[0].text
        ev = event(game_text)
        ev.plugins_name = [PREFIX + "weather"]
        await core.on_natural_message(ev)
        ev.send.assert_not_awaited()

        await mahjong.terminate()
        ctx.stars.pop(PREFIX + "mahjong")
        await steam.terminate()
        ctx.stars.pop(PREFIX + "steam")
        assert set(core._attached_parts) == {"weather"}
        ev = event(game_text)
        await core.on_natural_message(ev)
        ev.send.assert_not_awaited()
        ev = event("天气")
        await weather.weather_command(ev)
        assert "城市" in ev.send.await_args.args[0].chain[0].text
        for slug in slugs:
            if slug in {"core", "weather"}:
                continue
            part = load(slug)
            await core.install_files(part)
            with ExitStack() as stack:
                if slug == "accounts":
                    from app.services.local_listener import LocalListener
                    original_init = LocalListener.__init__
                    stack.enter_context(patch.object(LocalListener, "__init__", lambda listener, port: original_init(listener, 0)))
                elif slug == "minecraft":
                    stack.enter_context(patch("app.minecraft_bridge.PORT", 0))
                await part.initialize()
            assert part.host() is core and set(core._attached_parts) == {"weather", slug}, slug
            if slug == "accounts":
                import httpx
                assert part.panel.server.started and part.panel.socket.getsockname()[1] > 0
                transport = httpx.ASGITransport(app=part.panel.server.config.app)
                async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:6190") as client:
                    page = await client.get("/accounts")
                assert page.status_code == 200 and "Minecraft" in page.text
            elif slug == "minecraft":
                assert part.bridge.status == "listening"
            elif slug == "translation":
                with patch.object(core.runtime, "handle_event", AsyncMock(return_value={"handled": True})) as dispatch:
                    await core.feature_command(event("/翻译 你好", group=True))
                    await core.feature_command(event("帮我翻译一下 hello"))
                    assert [args.args[0]["message_type"] for args in dispatch.await_args_list] == ["group", "private"]
                    await core.feature_command(event("/点歌 测试", group=True))
                    await core.feature_command(event("点歌 测试"))
                    assert dispatch.await_count == 2
            elif slug == "music":
                ev = event("点歌")
                ev.bot.api.call_action.return_value = {"message_id": 1}
                await core.feature_command(ev)
                action = ev.bot.api.call_action.await_args
                assert action.args == ("send_private_msg",) and action.kwargs["user_id"] == ev.get_sender_id()
                assert "歌名" in str(action.kwargs["message"])
            elif slug in {"listen_first", "corrections", "watchlist", "card_quiz"}:
                from app.astrbot_turn_features import get_feature
                from astrbot_plugin_qqchat_core.main import ConversationScope

                group = slug == "card_quiz"
                core._get_group_router().config["group_chat_groups"] = ["12345"]
                text = {"listen_first": "等我说完", "corrections": "放个晴天",
                        "watchlist": "帮我记一下《星际牛仔》看到第3集了", "card_quiz": "来局猜卡"}[slug]
                hidden = event(text, group=group)
                hidden.plugins_name = [PREFIX + "weather"]
                assert not ConversationScope().filter(hidden, {})
                await core.conversation_features(hidden)
                hidden.send.assert_not_awaited()
                ev = event(text, group=group)
                assert ConversationScope().filter(ev, {})
                if slug == "card_quiz":
                    card = {"name": "洛欧塞布", "mana": 5, "class": "Neutral", "effect": "法术费用增加"}
                    with patch.object(get_feature(core, slug), "draw", AsyncMock(return_value=(card, b"full", b"art"))):
                        await core.conversation_features(ev)
                    assert len(ev.send.await_args.args[0].chain) == 2
                    last = event("我猜塞布", group=True)
                else:
                    await core.conversation_features(ev)
                    if slug == "corrections":
                        assert not ev.is_stopped()
                    else:
                        assert ev.is_stopped() and ev.send.await_count == 1
                    last = event({"listen_first": "这段算了", "corrections": "周杰伦那首", "watchlist": "我的追番进度"}[slug])
                await core.conversation_features(last)
                assert last.is_stopped() and last.send.await_count == 1
                expected = {"listen_first": "放下", "corrections": "还没启用点歌", "watchlist": "第 3 集", "card_quiz": "猜中了"}
                assert expected[slug] in last.send.await_args.args[0].chain[0].text
                if slug == "corrections":
                    music = load("music")
                    await music.initialize()
                    await core.conversation_features(event("放个晴天", group=True))
                    receive = AsyncMock(side_effect=lambda raw, send: {"handled": True, "music_query": raw["raw_message"].removeprefix("/点歌 ")})
                    with patch.object(core.runtime, "handle_event", receive):
                        for singer in ("周杰伦", "王菲"):
                            correction = event(singer + "那首", group=True)
                            await core.conversation_features(correction)
                            assert correction.is_stopped() and receive.await_args.args[0]["raw_message"] == "/点歌 晴天 " + singer
                    await music.terminate()
                    ctx.stars.pop(PREFIX + "music")
                    with patch(PREFIX + "weather.logic.fetch_weather", AsyncMock(return_value=report)) as fetch:
                        await core.on_natural_message(event("深圳今天天气怎么样"))
                        corrected = event("不是深圳，是苏州")
                        await core.conversation_features(corrected)
                        assert corrected.is_stopped() and [c.args[0] for c in fetch.await_args_list] == ["深圳", "苏州"]
                    card_part, deck_part = load("cards"), load("hearthstone")
                    await card_part.initialize()
                    await deck_part.initialize()
                    seen = AsyncMock(return_value={"status": "ok", "cards": [
                        {"name": "测试卡", "mana": 2, "effect": "抽一张牌", "set": "旧版系列", "wild": True, "standard": False}]})
                    with patch("app.astrbot_hearthstone.query", seen), patch(PREFIX + "cards.logic.query", seen):
                        await deck_part.qqchat_hearthstone(event("测试卡的效果呢"), action="card", query="测试卡")
                        corrected = event("我说的是狂野那张")
                        await core.conversation_features(corrected)
                        assert "旧版系列" in corrected.send.await_args.args[0].chain[0].text
                        assert seen.await_args.kwargs == {"action": "card", "mode": "wild", "query": "测试卡"}
                    for extra in (card_part, deck_part):
                        await extra.terminate()
                        ctx.stars.pop(PREFIX + extra.slug)
            elif slug in {"meetups", "followups"}:
                core._get_group_router().config["group_chat_groups"] = ["12345"]
                text = "周六打雀魂，差一个" if slug == "meetups" else "我明天下午面试"
                ev = event(text, group=slug == "meetups")
                ev.plugins_name = [PREFIX + "weather"]
                await core.on_natural_message(ev)
                ev.send.assert_not_awaited()
                ev = event(text, group=slug == "meetups")
                await core.on_natural_message(ev)
                assert ev.is_stopped() and ev.send.await_count == 1
                assert ("还差 1 人" if slug == "meetups" else "要我") in ev.send.await_args.args[0].chain[0].text
                if slug == "followups":
                    decline = event("不用了")
                    await core.on_natural_message(decline)
                    assert "不再问" in decline.send.await_args.args[0].chain[0].text
            await part.terminate()
            ctx.stars.pop(PREFIX + slug)
            assert set(core._attached_parts) == {"weather"} and part.host() is None, slug
    finally:
        await core.terminate()
    print(f"Verified {len(slugs)} archives, SDK registrations, startup/shutdown, weather, game routing, scope and local services.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, default=SOURCE / "downloads")
    directory = parser.parse_args().directory.resolve()
    with tempfile.TemporaryDirectory(prefix="qqchat-package-") as tmp:
        temp, previous = Path(tmp), Path.cwd()
        os.environ.update(ASTRBOT_ROOT=str(temp), WEB_PROXY_AUTO_DETECT="false")
        try:
            os.chdir(temp)
            asyncio.run(verify(directory, temp))
        finally:
            os.chdir(previous)
