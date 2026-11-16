import json

import pytest

from scripts.configure_mobile_qq import receiver


def test_mobile_pairing_uses_private_address_counts_and_preserves_credentials(tmp_path):
    cfg = tmp_path/"cmd_config.json"
    cfg.write_text(json.dumps({"dashboard":{"port":6185},"platform":[{"id":"other","type":"test"}]}))
    uri, secret = receiver(cfg,"192.168.0.103")
    assert uri=="ws://192.168.0.103:6199/ws" and len(secret)>=40
    assert receiver(cfg)==(uri,secret)
    assert receiver(cfg,"100.64.5.10")==("ws://100.64.5.10:6199/ws",secret)
    parsed = json.loads(cfg.read_text(encoding="utf-8-sig"))
    assert parsed["platform"][0]=={"id":"other","type":"test"} and parsed["dashboard"]=={"port":6185}
    assert parsed["platform"][1]["enable"] is True and parsed["platform"][1]["type"]=="aiocqhttp"
    with pytest.raises(ValueError): receiver(cfg,"0.0.0.0")
    assert receiver(cfg)[1]==secret
    parsed["platform"].append({"id":"other-onebot","enable":True,"ws_reverse_port":6199})
    cfg.write_text(json.dumps(parsed))
    with pytest.raises(ValueError,match="6199"): receiver(cfg,"127.0.0.1")
