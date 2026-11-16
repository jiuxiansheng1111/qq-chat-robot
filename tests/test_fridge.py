from unittest.mock import AsyncMock

from activity_fixtures import host_at, turn

from app import astrbot_fridge as fridge


async def test_photo_evidence_priorities_and_unreadable_recovery(tmp_path,monkeypatch):
    tool,host,key = fridge.Fridge(),host_at(tmp_path),("qq","bot","g","A")
    model = AsyncMock(return_value="先做番茄炒蛋。")
    monkeypatch.setattr(fridge,"_model", model)
    monkeypatch.setattr(fridge,"image_refs",lambda e:([],["true-attachment"]))
    monkeypatch.setattr(fridge,"observe_image",AsyncMock(return_value='{"ingredients":["番茄","鸡蛋"]}'))
    assert "鸡蛋" in await turn(tool,host,key,1,"这些食材能做什么？番茄我想先吃，只有十分钟")
    facts = model.await_args.args[-1]
    assert facts["能从照片确认的食材"] == ["番茄","鸡蛋"] and "十分钟" in facts["用户的要求"]
    monkeypatch.setattr(fridge,"observe_image",AsyncMock(return_value="无法辨认"))
    assert "没辨清" in await turn(tool,host,key,2,"冰箱里能做什么")
    assert model.await_count == 1 and not tool.interested(("qq","bot","g","B"),"有鸡蛋",False)
    monkeypatch.setattr(fridge,"image_refs",lambda e:([],[]))
    assert "炒蛋" in await turn(tool,host,key,3,"有番茄和鸡蛋")
    assert not tool.waiting
