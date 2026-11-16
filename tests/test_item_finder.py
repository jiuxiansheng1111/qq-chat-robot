from unittest.mock import AsyncMock

from activity_fixtures import host_at, turn

from app import astrbot_item_finder
from app.services import activity_notes


async def test_save_retrieve_own_original_picture_forget_without_crossing_groups(tmp_path, monkeypatch):
    host, key = host_at(tmp_path), ("qq","bot","g","我")
    photo = b"original-image-fixture"
    monkeypatch.setattr(activity_notes,"attached_photo",AsyncMock(return_value=photo))
    delivered = []
    async def send(event,text,image=None):
        delivered.append((text,image))
        await event.send(text)
    monkeypatch.setattr(activity_notes,"send_photo_note",send)
    tool = astrbot_item_finder.ItemFinder()
    assert "抽屉" in await turn(tool,host,key,"1","记下备用钥匙放在第二层抽屉")
    assert not await turn(tool,host,key,"1","记下备用钥匙放在别处")
    assert "第二层" in await turn(astrbot_item_finder.ItemFinder(),host,key,"2","备用钥匙放哪了")
    assert delivered[-1][1] == photo
    assert "没有" in await turn(tool,host,("qq","bot","g","另一个人"),"3","备用钥匙放哪了")
    assert "没有" in await turn(tool,host,("qq","bot","另一群","我"),"3","备用钥匙放哪了")
    assert "删掉" in await turn(tool,host,key,"4","忘掉备用钥匙的位置")
    assert "没有" in await turn(tool,host,key,"5","备用钥匙放哪了")
