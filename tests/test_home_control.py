from types import SimpleNamespace

import pytest

from app.astrbot_home_control import HomeControl
from app.services.home_devices import DeviceError, HomeDevices, device_action


def event(mid):
    raw = {"message_type":"private", "self_id":"bot", "user_id":"owner", "message_id":mid}
    return SimpleNamespace(get_platform_id=lambda:"test", message_obj=SimpleNamespace(raw_message=raw))


@pytest.mark.asyncio
async def test_pair_bind_drive_read_back_and_dedup_after_restart(tmp_path, monkeypatch):
    home = HomeDevices(tmp_path)
    state = {"entity_id":"light.bedroom", "state":"off", "attributes":{"friendly_name":"小灯"}}
    posted = []
    async def api(self, base, secret, method, path, **kwargs):
        assert secret == "private-test"
        if path == "": return {"message":"API running."}
        if path == "states": return [state, {"entity_id":"sensor.light"}]
        if method == "POST":
            posted.append((path, kwargs["json"]))
            state.update(state="on", attributes={"brightness":kwargs["json"]["brightness"]})
            return []
        return dict(state)
    monkeypatch.setattr(HomeDevices, "api", api)
    await home.connect({"base":"http://127.0.0.1:8123", "token":"private-test"})
    assert [d["entity"] for d in await home.available()] == ["light.bedroom"]
    await home.bind("卧室灯", "light.bedroom")
    assert "private-test" not in str(home.status())
    assert "50%" in await home.execute(event("first"), "把卧室灯调到50%")
    assert await HomeDevices(tmp_path).execute(event("first"), "把卧室灯调到50%") == ""
    assert len(posted) == 1 and posted[0][0] == "services/light/turn_on"
    assert "开启" in await home.execute(event("next"), "卧室灯状态")
    with pytest.raises(DeviceError):
        await home.bind("假灯", "light.unknown")
    assert not home.disconnect()["devices"]
    with pytest.raises(DeviceError):
        home.connection()


@pytest.mark.parametrize(("entity","text","service"), [
    ("climate.study","空调调到26度","climate/set_temperature"),
    ("media_player.room","音箱音量设为20%","media_player/volume_set"),
    ("scene.read","打开阅读场景","scene/turn_on"), ("button.pc","按一下电脑开机","button/press"),
    ("script.phone","执行手机响铃","script/turn_on"), ("switch.plug","关闭插座","switch/turn_off"),
])
def test_routes_by_actual_domain(entity, text, service):
    assert device_action(entity, text, {"attributes":{}})[0] == service
    assert device_action(entity, text+"状态", {"attributes":{}}) is None


def test_negative_and_hypothetical_not_device_actions():
    tool = HomeControl()
    for text in ["不要打开台灯","如果打开卧室灯会怎么样","听说他打开空调","刚才打开过空调","之前有人关闭卧室灯"]:
        assert not tool.interested(("qq","bot","","owner"), text, True)


@pytest.mark.asyncio
async def test_no_private_actions_from_group_or_another_sender():
    tool = HomeControl()
    assert not await tool.handle(None, event("g"), ("a","b","group","x"), "打开卧室灯")
    assert not await tool.handle(None, event("p"), ("a","b","","stranger"), "打开卧室灯")


@pytest.mark.asyncio
async def test_fresh_wake_button_and_assumed_climate_temperature(tmp_path,monkeypatch):
    home = HomeDevices(tmp_path)
    states = {"button.computer":{"entity_id":"button.computer","state":"unknown","attributes":{}},
              "climate.ac":{"entity_id":"climate.ac","state":"cool","attributes":{
                "temperature":70,"min_temp":60,"max_temp":85,"target_temp_step":1,"assumed_state":True}}}
    actions = []
    async def api(base,secret,method,path,**kwargs):
        if not path: return {"message":"API running."}
        if path=="states": return list(states.values())
        if path=="config": return {"unit_system":{"temperature":"°F"}}
        if method=="POST":
            actions.append((path,kwargs["json"]))
            if path.endswith("/set_temperature"): states["climate.ac"]["attributes"]["temperature"] = kwargs["json"]["temperature"]
            return []
        return states[path.split("/",1)[1]]
    monkeypatch.setattr(home,"api",api)
    await home.connect({"base":"http://localhost:8123","token":"demo"})
    await home.bind("电脑开机","button.computer")
    await home.bind("空调","climate.ac")
    assert "已接收" in await home.execute(event("b1"),"按一下电脑开机")
    assert "控制器估计" in await home.execute(event("b2"),"把空调调到26度")
    assert actions[-1][1]["temperature"]==79 and "79℉" in await home.execute(event("b3"),"空调状态")
