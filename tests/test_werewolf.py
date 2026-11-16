import json
from random import Random
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app import astrbot_werewolf as game_plugin
from app.services import werewolf as game
from tests.activity_fixtures import ActivityEvent, host_at


def fixed(*roles):
    class Deal:
        def shuffle(self, deck):
            assert sorted(deck) == sorted(roles)
            deck[:] = roles
    return Deal()


def game_of_eight():
    initial = game.lobby("11","小蓝")
    game.start(initial,practice=True,rng=fixed(game.SEER,game.WOLF,game.WOLF,game.WITCH,game.HUNTER,
                                            game.VILLAGER,game.VILLAGER,game.VILLAGER))
    for p in initial["seats"]: p["auto"] = False
    return initial


@pytest.mark.parametrize("role",[game.WOLF,game.SEER,game.WITCH,game.HUNTER,game.VILLAGER])
def test_fish_plays_a_complete_table_using_only_its_own_role(role):
    for seed in range(200):
        rng = Random(seed)
        table = game.lobby("11","用户")
        game.start(table,practice=True,rng=rng)
        if game.player(table,2)["role"] == role: break
    else: pytest.fail("could not generate requested role")
    table["seats"][0]["auto"] = True
    seen = game.view_for(table,2)
    assert all(set(p)=={"号","昵称","存活"} for p in seen["玩家"])
    assert ("狼队座位" in seen)==(role==game.WOLF)
    assert ("自己的查验" in seen)==(role==game.SEER)
    assert ("自己的药" in seen)==(role==game.WITCH)
    assert "当晚遇袭" not in seen and game.seat_speech(seen)
    for _ in range(35):
        game.advance(table,rng)
        if table["phase"]=="over": break
        assert table["phase"]=="day"
        game.begin_vote(table)
    assert table["phase"]=="over"
    live = game.living(table)
    wolves = sum(game.player(table,n)["role"]==game.WOLF for n in live)
    assert (table["winner"]=="好人" and wolves==0) or (table["winner"]=="狼人" and wolves>=len(live)-wolves)


def test_witch_limits_hunter_and_tie_resolution():
    g = game_of_eight()
    game.act(g,2,"袭击",4)
    game.act(g,3,"袭击",4)
    game.advance(g)
    assert g["phase"]=="seer"
    assert "狼人" in game.act(g,1,"查验",2)
    assert "自己的查验" not in game.view_for(g,6)
    game.advance(g)
    assert game.view_for(g,4)["当晚遇袭"]==4 and ("救人",4) not in game.choices(g,4)
    game.act(g,4,"用毒",5)
    with pytest.raises(ValueError): game.act(g,4,"跳过",0)
    game.advance(g)
    assert g["phase"]=="day" and not game.player(g,4)["alive"] and not game.player(g,5)["alive"]
    assert g["potions"] == {"save":True,"poison":False}
    game.begin_vote(g)
    for _ in range(2):
        for i,n in enumerate(game.living(g)):
            game.act(g,n,"投票",2 if i%2 else 3)
        game.advance(g)
        if g["phase"]=="tie":
            with pytest.raises(ValueError): game.act(g,1,"投票",8)
    assert g["phase"]=="wolves" and len(game.living(g))==6
    g = game_of_eight()
    g["phase"] = "day"
    game.begin_vote(g)
    for n in game.living(g): game.act(g,n,"投票",5)
    game.advance(g)
    assert g["phase"]=="hunter" and game.eligible(g)==[5]
    game.act(g,5,"开枪",2)
    game.advance(g)
    assert not game.player(g,2)["alive"] and g["phase"]=="wolves"


@pytest.mark.asyncio
async def test_private_night_group_delivery_recovery_and_role_limited_narration(tmp_path,monkeypatch):
    host,tool = host_at(tmp_path),game_plugin.Werewolf()
    actual_start = game.start
    def arranged(table,*,practice):
        actual_start(table,practice=practice,rng=fixed(game.SEER,game.WOLF,game.WOLF,game.WITCH,game.HUNTER,
                                                     game.VILLAGER,game.VILLAGER,game.VILLAGER))
    monkeypatch.setattr(game,"start",arranged)
    provider = SimpleNamespace(text_chat=AsyncMock(return_value=SimpleNamespace(completion_text="先听三号的发言。")))
    host.context.get_using_provider_async = AsyncMock(return_value=provider)
    api = SimpleNamespace(call_action=AsyncMock(return_value={"message_id":123}))
    async def post(text,group,mid,uid="11",plugin=tool):
        key = ("qq","99",group,uid)
        ev = ActivityEvent(key,mid)
        ev.bot = SimpleNamespace(api=api)
        assert plugin.interested(key,text,True)
        assert await plugin.handle(host,ev,key,text)
        return [c.args[0] for c in ev.send.await_args_list]
    public = await post("狼人杀练习","101","1")
    assert "预言家" not in "".join(public) and "狼队座位" not in "".join(public)
    private = [c for c in api.call_action.await_args_list if c.args==("send_private_msg",)]
    assert len(private)==1 and private[0].kwargs["user_id"]=="11" and "预言家" in private[0].kwargs["message"][0]["data"]["text"]
    assert not await post("狼人杀练习","101","1",plugin=game_plugin.Werewolf())
    assert "私聊" in "".join(await post("查验2号","101","2"))
    assert "预言家" in "".join(await post("我的狼人杀身份","","3"))
    assert "没有参加" in "".join(await post("我的狼人杀身份","","4",uid="88"))
    await post("狼人杀练习","202","5")
    assert "多桌" in "".join(await post("我的狼人杀身份","","6"))
    reply = await post("狼人杀 群101 查验2号","","7",plugin=game_plugin.Werewolf())
    assert "狼人阵营" in "".join(reply)
    outgoing = [c for c in api.call_action.await_args_list if c.args==("send_group_msg",)]
    assert outgoing and all(c.kwargs["group_id"]=="101" and "你的查验结果" not in str(c.kwargs) for c in outgoing)
    prompt = json.loads(provider.text_chat.await_args.kwargs["prompt"])
    assert prompt["身份"]==game.WOLF and "自己的查验" not in prompt and "当晚遇袭" not in prompt
    assert "contexts" in provider.text_chat.await_args.kwargs and provider.text_chat.await_args.kwargs["contexts"]==[]
    assert not any("先听三号" in s for s in await post("狼人杀 群202 我的身份","","8"))
