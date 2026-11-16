from unittest.mock import AsyncMock

from activity_fixtures import host_at, turn

from app import astrbot_dinner_split as aa


async def test_dinner_exact_amounts_separate_items_replay_amend_and_ocr(tmp_path,monkeypatch):
    host = host_at(tmp_path)
    key = ("qq","bot","g","小王")
    tool = aa.DinnerSplit()
    await turn(tool,host,key,1,"聚餐AA 小王、小林、小南")
    assert "第1笔" in await turn(tool,host,key,2,"记一笔 我付100元 晚饭")
    assert "小王、小林分" in await turn(tool,host,key,3,"记一笔 小林付30.01元 饮料，不含小南")
    assert not await turn(tool,host,key,3,"记一笔 小林付30.01元 饮料，不含小南")
    result = await turn(aa.DinnerSplit(),host,key,4,"AA结算")
    assert "130.01元" in result and "小林 给 小王 18.32元" in result and "小南 给 小王 33.33元" in result
    assert "没有找到" in await turn(tool,host,("qq","bot","g","外人"),5,"撤销AA第1笔")
    assert "已撤销" in await turn(tool,host,key,6,"撤销AA第2笔")
    monkeypatch.setattr(aa,"image_refs",lambda e:([],["pic"]))
    monkeypatch.setattr(aa,"observe_image",AsyncMock(return_value='{"items":[{"name":"汤","amount":"1.23"}],"total":"1.23"}'))
    assert "确认小票" in await turn(tool,host,key,7,"把这张小票记入AA")
    assert "100.00元" in await turn(tool,host,key,8,"AA结算")
    assert "已记入" in await turn(tool,host,key,9,"确认小票")
    assert "101.23元" in await turn(tool,host,key,10,"AA结算")
    assert "没有待确认" in await turn(tool,host,key,11,"确认小票")
    assert "还没开账" in await turn(tool,host,("qq","bot","g2","小王"),12,"AA结算")


def test_settlement_conserves_every_cent():
    import random
    rng = random.Random(7)
    people = ["甲","乙","丙","丁"]
    for _ in range(200):
        payer = rng.choice(people)
        members = rng.sample(people,rng.randint(1,4))
        amount = rng.randint(1,30000)
        final = dict.fromkeys(people,0)
        final[payer] = amount
        for debtor,creditor,value in aa.settle(people,[(payer,amount,members)]):
            final[debtor] += value
            final[creditor] -= value
        assert sum(final.values()) == amount
        assert max(final[p] for p in members)-min(final[p] for p in members) <= 1
        assert all(final[p]==0 for p in set(people)-set(members))
