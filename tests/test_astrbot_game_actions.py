"""End-to-end natural QQ routes, with no sends to actual QQ or game accounts."""

import asyncio
import json
import shutil
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from test_astrbot_companion_turns import Record, Reply, say

from app import astrbot_chat_actions as chat
from app import astrbot_game_companion as games
from app.services import game_clients


@pytest.mark.asyncio
async def test_reminder_from_image_confirm_and_cancel_scoped(room, monkeypatch):
    time = (datetime.now(chat.CHINA) + timedelta(days=1)).replace(hour=16, minute=0, second=0, microsecond=0)
    model = AsyncMock(return_value=json.dumps({"title": "交作业", "when": time.isoformat(), "evidence": "明天下午四点"}))
    monkeypatch.setattr(chat, "_model", model)
    vision = SimpleNamespace(text_chat=AsyncMock(return_value=SimpleNamespace(completion_text="明天下午四点交作业")))
    room.plugin.context.get_provider_by_id = lambda _: vision
    jobs = {}
    async def add(**kw):
        job = SimpleNamespace(job_id="generated", enabled=True, name=kw["name"],
                              payload={**kw["payload"], "run_at": kw["run_at"].isoformat()})
        jobs[job.job_id] = job
        return job
    async def list_jobs(_):
        return list(jobs.values())
    async def remove(key):
        jobs.pop(key)
    manager = room.plugin.context.cron_manager = SimpleNamespace(add_active_job=AsyncMock(side_effect=add),
        list_jobs=list_jobs, delete_job=remove, get_next_run_time=lambda k: time if k in jobs else None)
    assert not await room.post("", images=["unrelated-user"], user="another")
    assert not await room.post("", images=["actual-homework"])
    assert "16:00" in say(await room.post("这张通知提醒我一下"))
    assert vision.text_chat.await_args.kwargs["image_urls"] == ["actual-homework"]
    assert "明天下午四点交作业" in model.await_args.args[-1]["材料"]
    assert not jobs
    assert not await room.post("好的", user="another")
    assert not await room.post("可以", group="1000000002")
    assert "记好了" in say(await room.post("可以", mid="set1"))
    assert not await room.post("可以", mid="set1")
    assert manager.add_active_job.await_count == 1
    assert jobs["generated"].payload["sender_id"] == "8"
    assert not await room.post("我交了", user="another")
    assert "没找到" in say(await room.post("取消提醒", group="1000000002"))
    room.plugin._companion_turns.sessions.clear()
    assert "收掉" in say(await room.post("我交了"))
    assert not jobs


@pytest.mark.asyncio
async def test_voice_reminder_needs_real_time_and_quote_draft_is_copyable(room, monkeypatch):
    planner = AsyncMock(return_value=json.dumps({"title": "交稿", "when": "2099-03-04T10:00:00+08:00",
                                                "evidence": "上午十点", "question": "想几点提醒？"}))
    monkeypatch.setattr(chat, "_model", planner)
    monkeypatch.setattr("app.astrbot_companion_turns.hear", AsyncMock(return_value=("提醒我交稿", "")))
    assert "几点" in say(await room.post("", record=Record("actual-record")))
    assert "提醒我交稿" in planner.await_args.args[-1]["材料"]
    assert "放下" in say(await room.post("不用了"))
    quoted = Reply(201, sender_id="17")
    quoted.message_str = "周五晚上要不要来聚餐？"
    planner.side_effect = [json.dumps({"question": "你想去，还是想婉拒？"}),
                           json.dumps({"draft": "周五我有安排啦，你们玩得开心！"}),
                           json.dumps({"draft": "周五有约啦，下次见！"})]
    assert "婉拒" in say(await room.post("这句怎么回？", reply=quoted))
    assert planner.await_args.args[-1]["对方原话"] == quoted.message_str
    assert not await room.post("婉拒", user="stranger")
    assert "周五" in say(await room.post("我想婉拒"))
    assert "下次见" in say(await room.post("再短一点"))
    assert planner.await_args.args[-1]["对方原话"] == quoted.message_str
    assert not room.api.call_action.await_count
    assert not await room.post("这句怎么回", reply=quoted, at="other")
    planner.side_effect = None
    planner.return_value = json.dumps({"draft": "周五我可以来"})
    assert "可以来" in say(await room.post("这句怎么回？", reply=quoted, at="17"))


@pytest.mark.asyncio
async def test_reminding_about_a_game_is_not_joining_it(room, monkeypatch):
    planner = AsyncMock(return_value=json.dumps({"title": "玩炉石", "evidence": "", "when": "", "question": "明天几点？"}))
    monkeypatch.setattr(chat, "_model", planner)
    assert "几点" in say(await room.post("提醒我明天跟朋友一起打炉石"))
    assert not await room.post("我也来", user="other")


@pytest.mark.asyncio
async def test_quote_id_alone_is_read_from_the_same_chat(room, monkeypatch):
    planner = AsyncMock(return_value=json.dumps({"draft": "改到周二可以吗？"}))
    monkeypatch.setattr(chat, "_model", planner)
    room.api.call_action.return_value = {"data": {"group_id": "1000000001", "message": [
        {"type": "text", "data": {"text": "明天一起出门吗？"}}]}}
    assert "周二" in say(await room.post("这条该怎么回？", reply=Reply(380, sender_id=11)))
    assert planner.await_args.args[-1]["对方原话"] == "明天一起出门吗？"
    room.api.call_action.assert_awaited_once_with("get_msg", message_id=380)
    assert "原话" in say(await room.post("这条怎么回？", reply=Reply(380, sender_id=11), group="1000000002"))
    assert planner.await_count == 1


@pytest.mark.asyncio
async def test_mahjong_shared_invite_and_real_local_practice(room, monkeypatch):
    assert "电脑" in say(await room.post("一起打雀魂，房号 12345"))
    assert "2 人" in say(await room.post("我也来", user="2"))
    assert not await room.post("我也来", group="1000000002", user="2")
    assert "12345" in say(await room.post("雀魂报名名单", user="3"))
    assert "1 人" in say(await room.post("取消报名", user="2"))
    assert "14 张" in say(await room.post("雀魂何切 123m456p"))
    assert "和牌牌形" in say(await room.post("雀魂练何切 123456m345p678s11z"))
    assert "1m" in say(await room.post("雀魂练何切 123457m345p678s11z"))
    assert not await room.post("切 1z", user="4")
    assert "未见" in say(await room.post("切 1z"))
    assert "没有" in say(await room.post("切 7z"))
    replay = AsyncMock()
    monkeypatch.setattr("app.astrbot_mahjong.deliver_native_review", replay)
    link = "https://game.maj-soul.com/1/?paipu=240101-abcdef"
    assert not await room.post(link)
    assert replay.await_args.args[-1] == "链接=" + link
    room.clock += 3601
    assert not await room.post("我也来")


@pytest.mark.asyncio
async def test_hearthstone_deck_mode_reply_reuses_real_ingress(room, monkeypatch):
    assert "战网" in say(await room.post("肥鱼，陪我打炉石"))
    query = AsyncMock(return_value={"status": "ok", "mode": "wild", "items": ["a test deck"]})
    monkeypatch.setattr("app.astrbot_hearthstone.query", query)
    model = AsyncMock(return_value="我们看看这套狂野20牧")
    monkeypatch.setattr(chat, "_model", model)
    assert "狂野20牧" in say(await room.post("狂野20牧"))
    assert query.await_args.kwargs["action"] == "recommend"
    assert query.await_args.kwargs["mode"] == "wild"
    assert model.await_args.args[-1]["查到的资料"]["items"] == ["a test deck"]
    assert not await room.post("狂野20牧", user="4")
    assert "战网昵称" in say(await room.post("Fish#12345"))
    assert "2 人" in say(await room.post("炉石算我一个", user="19"))
    await room.post("雀魂房号 12345", user="other")
    assert "哪一场" in say(await room.post("我也来", user="new"))
    assert "2 人" in say(await room.post("雀魂", user="new"))
    assert not await room.post("他说要一起玩炉石", group="1000000002")


@pytest.mark.asyncio
async def test_steam_confirm_identity_and_reject_other_protocols(room, monkeypatch):
    actual_join = game_clients.steam_join
    join = AsyncMock(return_value="submitted")
    monkeypatch.setattr(game_clients, "steam_join", join)
    url = "steam://joinlobby/620/109775243012345678/76561198000000000"
    assert "大厅" in say(await room.post(url))
    assert not join.await_count
    assert not await room.post("好", user="another")
    assert not await room.post("好", group="1000000002")
    assert "交给 Steam" in say(await room.post("好", mid="confirm-1"))
    assert not await room.post("好", mid="confirm-1")
    assert join.await_args.args == ("620", "109775243012345678", "76561198000000000")
    check = AsyncMock(return_value="in_game")
    monkeypatch.setattr(game_clients, "steam_status", check)
    assert "还没确认" in say(await room.post("进房了吗"))
    check.return_value = "in_lobby"
    assert "已经在" in say(await room.post("进房了吗"))
    assert not await room.post(url + "/malformed", user="new")
    assert not await room.post("steam://run/10", user="new")
    assert not await room.post("https://elsewhere.invalid/joinlobby/1/2/3", user="new")
    monkeypatch.setattr(game_clients, "settings", dict)
    assert await actual_join("620", "109775243012345678", "76561198000000000") == "no_account"
    launch = SimpleNamespace(returncode=0)
    from unittest.mock import Mock

    recorder = Mock(return_value=launch)
    monkeypatch.setattr(game_clients, "steam_client", lambda _: ("Steam.exe", "ready"))
    monkeypatch.setattr(game_clients.subprocess, "run", recorder)
    assert await actual_join("620", "109775243012345678", "76561198000000000") == "submitted"
    assert recorder.call_args.args[0] == ["Steam.exe", "--", url]


@pytest.mark.asyncio
async def test_minecraft_no_server_and_owner_binding(room, monkeypatch, tmp_path):
    monkeypatch.setattr(game_clients, "CONFIG", tmp_path / "game.json")
    assert "地址" in say(await room.post("大肥鱼，进我的 Minecraft 服务器"))
    assert "主人" in say(await room.post("play.example.org:25566"))
    assert not game_clients.CONFIG.exists()
    monkeypatch.setattr(games, "is_owner_event", lambda ev: True)
    mc = room.plugin._companion_turns.games.mc
    mc.command = AsyncMock(side_effect=[{"status": "offline"}, {"status": "refused"}])
    assert "没让我进" in say(await room.post("play.example.org:25566"))
    assert game_clients.settings() == {"minecraft": {"host": "play.example.org", "port": 25566}}
    assert game_clients.server_address("[::1]:25570") == ("::1", 25570)
    assert game_clients.server_address("login@host:25565/secret") is None


@asynccontextmanager
async def java_world(room, monkeypatch, tmp_path, *flags):
    node = shutil.which("node")
    if not node or not (game_clients.PLAYER.parent / "node_modules/mineflayer").exists():
        pytest.skip("requires the Minecraft player npm dependencies")
    world = await asyncio.create_subprocess_exec(node, str(game_clients.ROOT / "tests/fixtures/minecraft_java_world.cjs"), *flags,
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        port = int(await asyncio.wait_for(world.stdout.readline(), 10))
        monkeypatch.setattr(game_clients, "CONFIG", tmp_path / "local.json")
        game_clients.CONFIG.write_text(json.dumps({"minecraft": {"host": "127.0.0.1", "port": port, "auth": "offline",
                                                   "username": "TestFish", "version": "1.20.1"}}))
        yield world
    finally:
        await room.plugin._companion_turns.games.mc.close()
        if world.returncode is None:
            world.kill()
        await world.communicate()


@pytest.mark.asyncio
async def test_java_player_really_spawns_moves_and_leaves_through_qq(room, monkeypatch, tmp_path):
    async with java_world(room, monkeypatch, tmp_path) as world:
        assert "已经进到" in say(await room.post("大肥鱼，一起玩 Minecraft"))
        assert "另一位" in say(await room.post("一起玩 Minecraft", user="other"))
        assert "看不到" in say(await room.post("跟随 Missing"))
        assert "游戏名" in say(await room.post("跟着我"))
        assert "TestGuide" in say(await room.post("TestGuide"))
        movement = await asyncio.wait_for(world.stdout.readline(), 12)
        assert b"moved toward TestGuide" in movement
        assert "原地" in say(await room.post("别跟了"))
        assert "退出" in say(await room.post("退服"))
        assert "没在" in say(await room.post("你进服了吗"))


@pytest.mark.asyncio
async def test_java_player_mines_rests_and_resumes_without_qq_prompt(room, monkeypatch, tmp_path):
    async with java_world(room, monkeypatch, tmp_path, "--survival") as world:
        assert "已经进到" in say(await room.post("大肥鱼，一起玩 Minecraft"))
        assert b"picked up oak_log 1" in await asyncio.wait_for(world.stdout.readline(), 22)
        assert "歇一会儿" in say(await room.post("先停一下"))
        assert "橡木原木 ×1" in say(await room.post("背包里有什么"))
        assert "原地歇" in say(await room.post("MC 你在干嘛"))
        assert "另一位" in say(await room.post("MC 背包里有什么", user="other"))
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(world.stdout.readline(), 2)
        # The next pickup must happen without a resume command, even if chat context expires.
        room.clock += 2400
        assert b"picked up oak_log 2" in await asyncio.wait_for(world.stdout.readline(), 62)
        assert "橡木原木 ×2" in say(await room.post("背包里有什么"))
        assert "退出" in say(await room.post("退服"))
