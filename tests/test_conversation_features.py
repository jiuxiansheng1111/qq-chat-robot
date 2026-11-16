"""QQ entry-point regressions for the four independent conversation plugins."""

import asyncio
import io
import json
from itertools import count
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from PIL import Image as Raster
from test_astrbot_companion_turns import Image, Plain, Record, say
from test_astrbot_group_chat import Event

from app import astrbot_card_lookup as cards
from app import astrbot_card_quiz as quiz
from app import astrbot_watchlist as watch
from app.astrbot_group_chat import _COMMAND
from app.astrbot_turn_features import get_feature, release, remember_lookup

# Keep the real entry point even though the older companion fixture isolates card lookup.
card_handler = cards.handle


@pytest.fixture
def chat(room, tmp_path):
    plugin = room.plugin
    plugin.runtime.project_root = tmp_path
    plugin.context.get_config = lambda *_: {}
    serial = count(4000)

    def make(text, *, user="8", group="1000000001", at=None, image=None, audio=None, mid=None):
        segments = [{"type": "text", "data": {"text": text}}] if text else []
        chain = []
        if at:
            segments.append({"type": "at", "data": {"qq": at}})
        if image:
            segments.append({"type": "image", "data": {"url": image}})
            chain.append(Image(image))
        if audio:
            segments.append({"type": "record", "data": {"file": audio}})
            chain.append(Record(audio))
        ev = Event(text, user=user, group=group or "", self_id="99", message_id=str(mid or next(serial)),
                   segments=segments, components=chain)
        if not group:
            raw = ev.message_obj.raw_message
            raw.update(message_type="private")
            raw.pop("group_id", None)
        ev.unified_msg_origin = f"qq:{group or 'private'}:{user}"
        ev.plain_result = lambda t: [Plain(t)]
        ev.chain_result = lambda v: v
        ev.send = AsyncMock()
        return ev

    async def post(text, **options):
        ev = make(text, **options)
        await plugin.conversation_features(ev)
        return ev

    return SimpleNamespace(plugin=plugin, make=make, post=post, model=plugin.context.get_using_provider_async.return_value)


def reply(event):
    return say(event.send.await_args.args[0]) if event.send.await_count else ""


@pytest.mark.asyncio
async def test_combined_life_features_use_the_platform_entry_and_keep_scopes(chat, monkeypatch):
    from app import astrbot_fridge
    from app.astrbot_study import Study
    from app.astrbot_time_capsule import TimeCapsule

    monkeypatch.setattr(Study, "start", lambda self, host: None)
    monkeypatch.setattr(TimeCapsule, "start", lambda self, host: None)
    monkeypatch.setattr(astrbot_fridge, "_model", AsyncMock(return_value="两枚鸡蛋可以蒸蛋羹。"))
    cases = [
        ("记下钥匙放在玄关抽屉", "玄关"),
        ("保存项目 小鲸鱼：尾巴还要缝", "小鲸鱼"),
        ("陪我自习10分钟", "10分钟"),
        ("冰箱有鸡蛋能做什么", "蒸蛋羹"),
        ("聚餐AA 小林、小张、我", "小张"),
        ("建个群时间胶囊 明天20点", "20:00"),
        ("继续上次的项目", "尾巴还要缝"),
        ("钥匙放哪了", "玄关抽屉"),
        ("查看自习状态", "分钟"),
    ]
    for number, (text, expected) in enumerate(cases, 6000):
        result = await chat.post(text, at="99", mid=number)
        assert result.stopped and expected in reply(result), (text, reply(result))
        assert not reply(await chat.post(text, at="99", mid=number))
    assert "没有" in reply(await chat.post("钥匙放哪了", user="other", at="99"))
    assert not (await chat.post("晚上见", at="99")).stopped
    await release(chat.plugin)


@pytest.mark.asyncio
async def test_listen_multiple_media_once_and_separate_speakers(chat, monkeypatch):
    vision = AsyncMock(return_value="草图里的两扇窗")
    hearing = AsyncMock(return_value=("靠右边那扇窗", ""))
    monkeypatch.setattr("app.astrbot_listen_first.observe_image", vision)
    monkeypatch.setattr("app.astrbot_listen_first.hear", hearing)
    chat.model.text_chat.return_value = SimpleNamespace(completion_text="右边那扇窗可以留给书桌")
    assert "等你" in reply(await chat.post("先听我说完"))
    assert not (await chat.post("等我说完", at="18")).stopped
    assert not (await chat.post("好了", group="1000000002")).stopped
    assert not (await chat.post("好了", user="9")).stopped
    for text, options in [("我在摆书桌", {}), ("", {"image": "layout"}), ("", {"audio": "real-clip"}), ("/点歌 晴天", {})]:
        piece = await chat.post(text, **options)
        assert piece.stopped and not piece.send.await_count
    chat.model.text_chat.assert_not_awaited()
    answer = await chat.post("好了，你怎么看", mid=999)
    assert reply(answer) == "右边那扇窗可以留给书桌" and answer.send.await_count == 1
    rows = json.loads(chat.model.text_chat.await_args.kwargs["prompt"])
    assert rows == [{"文字": "我在摆书桌"}, {"图片": "草图里的两扇窗"}, {"语音": ["靠右边那扇窗"]},
                    {"文字": "/点歌 晴天"}, {"文字": "你怎么看"}]
    assert vision.await_args.args[1] == ["layout"] and hearing.await_args.args[2].file == "real-clip"
    duplicate = await chat.post("好了，你怎么看", mid=999)
    assert duplicate.stopped and not duplicate.send.await_count and chat.model.text_chat.await_count == 1


@pytest.mark.asyncio
async def test_listen_cancel_permission_and_unreadable_attachments(chat, monkeypatch):
    chat.plugin._legacy_event_allowed.return_value = False
    assert not (await chat.post("我分几条发", group=None)).stopped
    chat.plugin._legacy_event_allowed.return_value = True
    await chat.post("我分几条发", group=None)
    await chat.post("这个先不讨论", group=None)
    assert "放下" in reply(await chat.post("这段算了", group=None))
    assert not (await chat.post("我说完了", group=None)).stopped
    await chat.post("等我发完", group=None)
    monkeypatch.setattr("app.astrbot_listen_first.observe_image", AsyncMock(side_effect=ValueError))
    monkeypatch.setattr("app.astrbot_listen_first.hear", AsyncMock(side_effect=ValueError))
    chat.model.text_chat.return_value = SimpleNamespace(completion_text="你可以把图里那句贴出来吗？")
    await chat.post("", group=None, image="bad", audio="bad")
    await chat.post("说完了", group=None)
    assert json.loads(chat.model.text_chat.await_args.kwargs["prompt"]) == [{"图片": "本条图片未能读取", "语音": ["本条语音未能辨认"]}]


@pytest.mark.asyncio
async def test_concrete_card_correction_keeps_preview_and_player_scope(chat, monkeypatch):
    monkeypatch.setattr(cards, "handle", card_handler)
    base = {"name": "测试卡", "mana": 2, "effect": "抽一张牌", "wild": True}
    lookup = AsyncMock(return_value={"status": "ok", "cards": [
        dict(base, dbf_id=1, standard=True, set="核心系列"), dict(base, dbf_id=2, standard=False, set="旧版系列")]})
    monkeypatch.setattr(cards, "query", lookup)
    first = chat.make("查卡 测试卡", group=None)
    assert await cards.handle(chat.plugin, first, command=True)
    assert "核心系列" in reply(first) and "旧版系列" in reply(first)
    assert not (await chat.post("我说的是狂野那张", user="16", group=None)).stopped
    assert "卡名" in reply(await chat.post("不是这个", group=None)) and lookup.await_count == 1
    corrected = await chat.post("我说的是狂野那张", group=None)
    assert "旧版系列" in reply(corrected) and "核心系列" not in reply(corrected) and "确认" in reply(corrected)
    assert lookup.await_args.kwargs == {"action": "card", "mode": "wild", "query": "测试卡"}
    monkeypatch.setattr("app.astrbot_hearthstone.query", lookup)
    await chat.plugin.qqchat_hearthstone(chat.make("能查另一张的效果吗", group=None), action="card", query="另一张")
    await chat.post("我说的是标准那张", group=None)
    assert lookup.await_args.kwargs == {"action": "card", "mode": "standard", "query": "另一张"}


@pytest.mark.asyncio
async def test_music_weather_corrections_use_actual_subject_or_ask(chat, monkeypatch):
    commands = []

    async def receive(ev):
        commands.append(ev.get_extra(_COMMAND))

    monkeypatch.setattr(chat.plugin, "on_onebot_message", receive)
    loose = await chat.post("放个晴天", group=None)
    assert not loose.stopped
    ev = await chat.post("周杰伦那首", group=None)
    assert ev.stopped and commands == ["点歌 晴天 周杰伦"] and ev.get_extra(_COMMAND) is None
    retry = AsyncMock(return_value=True)
    remember_lookup(chat.plugin, chat.make("查深圳天气", group=None), "weather", "深圳", retry=retry)
    assert (await chat.post("不是深圳，是苏州", group=None)).stopped and retry.await_args.args[-1] == "苏州"
    assert "哪首" in reply(await chat.post("周杰伦那首", group=None)) and len(commands) == 1
    await chat.post("《七里香》", group=None)
    assert commands[-1] == "点歌 七里香 周杰伦"
    await chat.post("换个话题", group=None)
    assert not (await chat.post("不是这个", group=None)).stopped


@pytest.mark.asyncio
async def test_watch_consent_isolation_restart_and_forget(chat):
    assert "哪部" in reply(await chat.post("帮我记一下，我看到第六集了"))
    assert "第 6 集" in reply(await chat.post("《星际牛仔》"))
    await release(chat.plugin, "watchlist")
    assert "第 6 集" in reply(await chat.post("我的追番进度"))
    assert "星际牛仔" not in reply(await chat.post("我的追番进度", user="12"))
    assert "第几集" in reply(await chat.post("别剧透", user="12"))
    assert "星际牛仔" not in reply(await chat.post("我的追番进度", group=None))
    informal = await chat.post("《星际牛仔》我看到第七集了", at="99")
    assert "要帮你记住" in reply(informal)
    await chat.post("先别记")
    assert not (await chat.post("帮我记一下，我还没看到第八集")).stopped
    assert "第 6 集" in reply(await chat.post("我的追番进度"))
    assert "第 7 集" in reply(await chat.post("帮我记一下《星际牛仔》看到第7集了"))
    assert "删掉" in reply(await chat.post("忘掉《星际牛仔》的进度"))
    assert "还没记过" in reply(await chat.post("我的追番进度"))


@pytest.mark.asyncio
async def test_watch_only_verified_episodes_and_observed_screenshot(chat, monkeypatch):
    await chat.post("帮我记一下《星际牛仔》看到第3集了")
    facts = AsyncMock(return_value={1: "在车站见到了阿葵。", 2: "第十集会揭晓真相。", 4: "未来的秘密。"})
    vision = AsyncMock(return_value="站台边的红帽子")
    monkeypatch.setattr(watch, "episode_evidence", facts)
    monkeypatch.setattr(watch, "observe_image", vision)
    chat.model.text_chat.return_value = SimpleNamespace(completion_text='{"episode":1,"quote":"在车站见到了阿葵"}')
    answer = await chat.post("这个人之前出现过吗", image="screenshot")
    assert "第 1 集" in reply(answer) and "阿葵" in reply(answer)
    payload = json.loads(chat.model.text_chat.await_args.kwargs["prompt"])
    assert payload["已看分集资料"] == {"1": "在车站见到了阿葵。"} and payload["截图中可见"] == "站台边的红帽子"
    assert vision.await_args.args[1] == ["screenshot"]
    chat.model.text_chat.return_value = SimpleNamespace(completion_text='{"episode":1,"quote":"编造的身份"}')
    assert "没法确认" in reply(await chat.post("这个人之前出现过吗"))
    calls = facts.await_count
    assert "留给你" in reply(await chat.post("《星际牛仔》男主最后死了吗"))
    assert facts.await_count == calls


@pytest.mark.asyncio
async def test_one_turn_watch_discussion_and_literal_group_song(chat, monkeypatch):
    source = AsyncMock(return_value={2: "两人在钟楼重逢。"})
    monkeypatch.setattr(watch, "episode_evidence", source)
    chat.model.text_chat.return_value = SimpleNamespace(completion_text='{"episode":2,"quote":"两人在钟楼重逢"}')
    result = await chat.post("我在看星际牛仔，刚看到第2集，这个人之前出现过吗")
    assert "钟楼" in reply(result) and source.await_args.args == ("星际牛仔", 2)
    assert not (chat.plugin.runtime.project_root / "data/watch-progress.sqlite3").exists()
    assert "星际牛仔" in reply(await chat.post("帮我记一下星际牛仔我看到第三集了"))
    assert not (await chat.post("后面会下雨吗")).stopped
    assert not (await chat.post("放个晴天")).stopped
    queries = []
    async def dispatch(ev):
        queries.append(ev.get_extra(_COMMAND))
    monkeypatch.setattr(chat.plugin, "on_onebot_message", dispatch)
    assert (await chat.post("周杰伦那首")).stopped and queries == ["点歌 晴天 周杰伦"]


@pytest.mark.asyncio
async def test_episode_source_rejects_ambiguous_titles_and_foreign_or_later_episodes(monkeypatch):
    subjects = {"data": [{"id": 9, "type": 2, "name_cn": "星际牛仔", "summary": "不能传入的整部剧情"}]}
    rows = [{"subject_id": sid, "type": typ, "sort": n, "desc": f"第{n}集的资料"} for sid, typ, n in
            [(9, 0, 1), (9, 0, 4), (10, 0, 2), (9, 1, 2)]]
    calls = AsyncMock(side_effect=[subjects, {"data": rows, "total": 4}])
    monkeypatch.setattr(watch, "request_json", calls)
    assert await watch.episode_evidence("星际牛仔", 3) == {1: "第1集的资料"}
    assert calls.await_args.kwargs["params"]["limit"] == 3 and calls.await_args.kwargs["params"]["type"] == 0
    calls.side_effect = [{"data": subjects["data"] + [{"id": 10, "type": 2, "name_cn": "星际牛仔"}]}]
    assert await watch.episode_evidence("星际牛仔", 2) == {} and calls.await_count == 3


QUIZ_CARD = {"name": "洛欧塞布", "english_name": "Loatheb", "mana": 5, "class": "NEUTRAL",
             "effect": "洛欧塞布使敌方法术的法力值消耗增加。", "set": "纳克萨玛斯的诅咒"}


@pytest.mark.asyncio
async def test_quiz_group_alias_clues_and_one_winner(chat, monkeypatch):
    game = get_feature(chat.plugin, "card_quiz")
    draw = AsyncMock(return_value=(QUIZ_CARD, b"full", b"art"))
    monkeypatch.setattr(game, "draw", draw)
    assert not (await chat.post("来局猜卡", group=None)).stopped
    first = await chat.post("来局猜卡 狂野")
    assert first.send.await_args.args[0][1].file == b"art" and "洛欧塞布" not in reply(first)
    assert draw.await_args.args[-1] == "wild"
    assert not (await chat.post("塞布", group="1000000002")).stopped
    assert not (await chat.post("是不是明天下雨")).stopped
    assert "没猜中" in reply(await chat.post("我猜是火球术", user="12"))
    for expected in ("5 费", "中立", "使敌方法术"):
        hint = reply(await chat.post("给个提示", user="12"))
        assert expected in hint and "洛欧塞布" not in hint
    guesses = await asyncio.gather(chat.post("塞布", user="12"), chat.post("Loatheb", user="14"))
    results = [ev for ev in guesses if ev.send.await_count]
    assert len(results) == 1 and "猜中了" in reply(results[0]) and results[0].send.await_args.args[0][1].file == b"full"
    assert not game.rooms


@pytest.mark.asyncio
async def test_quiz_timer_reveals_or_respects_disabled_group(chat, monkeypatch):
    game = get_feature(chat.plugin, "card_quiz")
    game.interval = .005
    monkeypatch.setattr(game, "draw", AsyncMock(return_value=(QUIZ_CARD, b"full", b"art")))
    ev = await chat.post("开始猜炉石卡")
    timer = next(iter(game.rooms.values())).timer
    await asyncio.wait_for(timer, 2)
    assert [say(a.args[0]).split("，")[0] for a in ev.send.await_args_list][1:3] == ["先看费用", "职业是中立"]
    assert ev.send.await_count == 5 and ev.send.await_args.args[0][1].file == b"full" and not game.rooms
    ev = await chat.post("来局猜卡")
    chat.plugin.context.get_config = lambda _: {"plugin_set": ["some_other_plugin"]}
    await asyncio.wait_for(next(iter(game.rooms.values())).timer, 2)
    assert ev.send.await_count == 1 and not game.rooms


@pytest.mark.asyncio
async def test_game_library_and_radio_share_group_entry_without_consuming_normal_messages(chat):
    assert "自愿共享" in reply(await chat.post("我们两个人能一起玩哪些游戏？"))
    assert "第1期" in reply(await chat.post("开个群电台 夏夜"))
    assert "还没有" in reply(await chat.post("电台节目单",group="1000000002"))
    assert "筹备中" in reply(await chat.post("电台节目单"))
    assert not (await chat.post("听见路边电台了吗")).stopped


def test_quiz_crop_strips_full_frame_nameplate_and_metadata():
    im = Raster.new("RGB", (430, 585), "red")
    im.paste("blue", (135, 109, 300, 235))
    im.paste("white", (170, 145, 225, 190))
    buf = io.BytesIO()
    im.save(buf, "PNG")
    with Raster.open(io.BytesIO(quiz.art_crop(buf.getvalue()))) as crop:
        assert crop.size == (159, 117) and not crop.info
        assert set(crop.getdata()) == {(0, 0, 255), (255, 255, 255)}
