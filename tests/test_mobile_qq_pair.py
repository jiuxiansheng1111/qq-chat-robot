import copy
import json

import httpx
import pytest

from app.services import mobile_qq_pair as phone

PAIR = {"url": "ws://192.168.1.5:6199/ws", "token": "a" * 40}


@pytest.mark.parametrize("url", ["ws://127.0.0.1:6199/ws", "ws://1.1.1.1:6199/ws", "ws://192.168.1.1:6199/ws?x=1",
                                "ws://username@192.168.1.1:6199/ws", "ws://192.168.1.1:22/ws", "https://example.com"])
def test_phone_pair_only_accepts_the_generated_private_endpoint(url):
    with pytest.raises(phone.napcat.ConnectError):
        phone.pairing_info(json.dumps({**PAIR, "url": url}))


def test_repeated_or_existing_phone_connection_does_not_change_another_client():
    desktop = {"name": "desktop", "url": phone.napcat.ASTRBOT_WS_URL, "token": "desktop-token"}
    cfg = {"network": {"websocketClients": [desktop], "httpClients": [{"enable": True}]}}
    linked = phone.phone_config(cfg, PAIR)
    assert linked["network"]["websocketClients"][0] == desktop
    assert phone.phone_config(linked, PAIR) == linked
    assert phone.phone_config(linked, None) == cfg
    assert cfg["network"]["websocketClients"] == [desktop]
    with pytest.raises(phone.napcat.ConnectError):
        phone.phone_config({"network": {"websocketClients": [{"name":"another app", **PAIR}]}}, PAIR)


def test_live_and_desktop_pairing_preserve_other_account_and_roll_back(tmp_path, monkeypatch):
    n = phone.napcat
    config_dir = tmp_path / "NapCatQQ Desktop/components/NapCatQQ/config"
    desktop = config_dir.parents[2] / "config/bot.json"
    config_dir.mkdir(parents=True)
    desktop.parent.mkdir(parents=True)
    (config_dir / "webui.json").write_text(json.dumps({"host":"127.0.0.1","port":6099,"token":"ui"}))
    original, _ = n.build_napcat_config({"network": {k: [] for k in n.DESKTOP_CONNECT_KEYS}}, "desktop-token")
    live = copy.deepcopy(original)
    account_config = config_dir / "onebot11_12345.json"
    account_config.write_text(json.dumps(original))
    other = {"bot":{"QQID":"67890"}, "connect": {"preserve":"another account's independent settings"}}
    desktop.write_text(json.dumps({"bots":[{"bot":{"QQID":"12345"},"connect":copy.deepcopy(original["network"])},other]}))
    (tmp_path / ".env").write_text("ONEBOT_API_BASE=http://127.0.0.1:3000\nONEBOT_ACCESS_TOKEN=local\nONEBOT_SELF_ID=12345\n")
    monkeypatch.setattr(n, "load_astrbot_ws_token", lambda root: "desktop-token")
    monkeypatch.setattr(n, "NAPCAT_CONFIG_DEFAULT", config_dir)
    real_preflight = n.preflight
    monkeypatch.setattr(n,"preflight",lambda *a,**k:real_preflight(*a,port_probe=lambda h,p:p==6199,**k))
    writes=[]
    def rpc(req):
        nonlocal live
        path=req.url.path
        if path == "/get_login_info":
            assert req.headers["Authorization"] == "Bearer local"
            data={"user_id":12345}
        elif path == n.WEBUI_LOGIN_PATH:
            data={"Credential":"scoped-fixture"}
        else:
            assert req.headers["Authorization"] == "Bearer scoped-fixture"
            if path == n.SET_CONFIG_PATH:
                live=json.loads(json.loads(req.content)["config"])
                writes.append(copy.deepcopy(live))
                account_config.write_text(json.dumps(live))
            else:
                assert path == n.GET_CONFIG_PATH
            data=live
        return httpx.Response(200,json={"code":0,"data":data})
    def change(remove=False):
        return phone.change(tmp_path,json.dumps(PAIR),remove=remove,
                            client_factory=lambda **kw:httpx.Client(transport=httpx.MockTransport(rpc),**kw))
    assert phone.status(tmp_path)=={"state":"unpaired"}
    assert change()=={"state":"paired"} and len(writes)==1
    assert change()=={"state":"paired"} and len(writes)==1
    assert phone.status(tmp_path)=={"state":"paired"}
    saved=json.loads(desktop.read_text())["bots"]
    assert saved[0]["connect"]==live["network"] and saved[1]==other
    assert len(saved[0]["connect"]["websocketClients"])==2
    assert change(True)=={"state":"unpaired"}
    assert live==original and json.loads(desktop.read_text())["bots"][1]==other
    real_apply=n._apply_desktop_config
    def reject(*args):
        raise n.ConnectError("concurrent desktop edit")
    monkeypatch.setattr(n,"_apply_desktop_config",reject)
    with pytest.raises(n.ConnectError,match="未完成"):
        change()
    assert live==original and phone.status(tmp_path)=={"state":"unpaired"}
    applies=0
    def interrupted(plan, desktop_plan):
        nonlocal applies
        applies+=1
        real_apply(plan,desktop_plan)
        if applies==1:
            raise n.ConnectError("interrupted after desktop write")
    monkeypatch.setattr(n,"_apply_desktop_config",interrupted)
    before=desktop.read_bytes()
    with pytest.raises(n.ConnectError,match="未完成"):
        change()
    assert applies==2 and json.loads(desktop.read_bytes())==json.loads(before) and live==original
