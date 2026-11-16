from unittest.mock import AsyncMock

import pytest

from app import astrbot_steam_library as steam
from tests.activity_fixtures import host_at, turn


@pytest.mark.asyncio
async def test_shared_libraries_require_consent_and_verify_actual_group_size(tmp_path, monkeypatch):
    tool, host = steam.SteamLibrary(), host_at(tmp_path)
    alice = ("qq","99","100","11")
    bob = (*alice[:3],"12")
    names = {"10":"海岸","20":"迷雾","30":"拼图"}
    lookup = AsyncMock(side_effect=lambda q:(q,{"title":names[q]}))
    monkeypatch.setattr(steam,"lookup",lookup)
    async def details(appid):
        return {"categories":[{"id":38}],"short_description":{"10":"支持2至4人合作","20":"合作冒险","30":"仅限2-2人"}[appid]}
    monkeypatch.setattr(steam,"details",details)
    monkeypatch.setattr(steam,"owned",AsyncMock(return_value=None))
    assert "没读到" in await turn(tool,host,alice,"0","同步我的Steam库 76561198000000001")
    assert "共享" in await turn(tool,host,alice,"1","共享我的游戏：10、20、30")
    assert "自愿共享" in await turn(tool,host,bob,"2","我们两个人能一起玩哪些游戏？")
    assert "共享" in await turn(tool,host,bob,"3","共享我的游戏：10、20")
    reply = await turn(steam.SteamLibrary(),host,alice,"4","我们两个人能一起玩哪款游戏？")
    assert "2～4人" in reply and "在线联机" in reply and "需核实人数：迷雾" in reply and "拼图" not in reply
    assert "自愿共享" in await turn(tool,host,bob,"5","我们三个人能一起玩哪些游戏？")
    assert "自愿共享" in await turn(tool,host,("qq","99","101","11"),"6","共同游戏库")
    assert "撤回" in await turn(tool,host,bob,"7","撤回我的Steam库共享")
    assert "自愿共享" in await turn(tool,host,alice,"8","共同游戏库")
    assert not tool.interested(("qq","99","","11"),"共同游戏库",True)
