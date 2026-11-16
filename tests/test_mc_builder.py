import asyncio

import pytest

from app import astrbot_mc_builder as build
from app.services import game_clients
from tests.activity_fixtures import host_at, turn
from tests.test_astrbot_game_actions import java_world


@pytest.mark.asyncio
async def test_blueprint_is_scoped_and_rechecks_the_real_chest_after_transfer(room,monkeypatch,tmp_path):
    host, tool = host_at(tmp_path), build.McBuilder()
    host._get_companion_turns = room.plugin._get_companion_turns
    key = ("qq","99","100","11")
    async def disabled(*args): return False
    monkeypatch.setattr(build,"scheduled_allowed",disabled)
    try:
        async with java_world(room,monkeypatch,tmp_path,"--construction") as world:
            assert "5×5" in await turn(tool,host,key,"1","MC工程 河岸小屋")
            assert "先说" in await turn(tool,host,(*key[:3],"12"),"2","工程进度")
            assert "4 65 5" in await turn(tool,host,key,"3","工程材料箱 4 65 5")
            assert "核对" in await turn(tool,host,key,"4","开始工程备料")
            observations = []
            async with asyncio.timeout(24):
                while not observations or observations[-1] != b"verified chest wood 32":
                    observations.append((await world.stdout.readline()).strip())
            assert b"verified chest wood 0" in observations
            async with asyncio.timeout(8):
                while "已齐" not in (progress := await turn(tool,host,key,str(len(observations)+10),"工程进度")):
                    observations.append(b"poll")
                    await asyncio.sleep(.2)
            assert "原木 32/32" in progress and "石料 25/25" in progress
            assert await turn(tool,host,key,"1","MC工程 河岸小屋") == ""
            restored = build.McBuilder()
            try:
                assert "原木 32/32" in await turn(restored,host,key,"100","工程进度")
                monkeypatch.setattr(game_clients,"settings",lambda:{"minecraft":{"host":"different.test"}})
                assert "服务器已经换了" in await turn(restored,host,key,"101","开始工程备料")
            finally: await restored.close()
    finally: await tool.close()
