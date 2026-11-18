"""Pair a second reverse OneBot connection with the phone from the owner's local account panel."""
from __future__ import annotations

import copy
import ipaddress
import json
import re
from dataclasses import replace
from pathlib import Path
from urllib.parse import urlsplit

from scripts import connect_astrbot_qq as napcat

NAME = "dafeiyu-android"
PRIVATE = tuple(ipaddress.ip_network(cidr) for cidr in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "100.64.0.0/10"))


def pairing_info(raw):
    try:
        pair = json.loads(raw) if isinstance(raw, str) and len(raw) <= 1000 else {}
        url, token = pair["url"], pair["token"]
        parsed = urlsplit(url)
        host = ipaddress.IPv4Address(parsed.hostname)
        if (not any(host in subnet for subnet in PRIVATE) or parsed.port != 6199
                or url != f"ws://{host}:6199/ws" or not re.fullmatch(r"[A-Za-z0-9_-]{40}", token)):
            raise ValueError
    except (AttributeError, ValueError, KeyError, TypeError):
        raise napcat.ConnectError("请从手机 QQ 收件箱复制同一私网的配对信息。") from None
    return {"url": url, "token": token}


def phone_config(original, pair):
    config = copy.deepcopy(original)
    clients = config.get("network", {}).get("websocketClients")
    if not isinstance(clients, list) or any(not isinstance(item, dict) for item in clients):
        raise napcat.ConnectError("当前账号的 QQ 连接配置无法识别。")
    marked = [item for item in clients if item.get("name") == NAME]
    if len(marked) > 1:
        raise napcat.ConnectError("当前账号有多条同名手机连接，请先在 NapCat 核对。")
    if pair is None:
        if marked:
            clients.remove(marked[0])
        return config
    if any(c.get("url") == pair["url"] and c.get("name") != NAME for c in clients):
        raise napcat.ConnectError("该手机地址已经有单独的连接配置。")
    entry = marked[0] if marked else {}
    if not marked:
        clients.append(entry)
    entry.update(name=NAME, enable=True, messagePostFormat="array", reportSelfMessage=False,
                 heartInterval=15000, reconnectInterval=3000, debug=False, **pair)
    return config


def status(root: Path):
    try:
        account = napcat.load_onebot_accounts(root / ".env", "primary")[0]
        self_id = account.expected_self_id
        if not self_id:
            with napcat._new_http_client() as client:
                self_id = napcat._get_onebot_self_id(client, account)
        cfg = napcat._read_account_config(napcat.NAPCAT_CONFIG_DEFAULT, self_id)
        clients = cfg["network"].get("websocketClients", [])
        paired = any(c.get("name") == NAME and c.get("enable") is True for c in clients if isinstance(c, dict))
        return {"state": "paired" if paired else "unpaired"}
    except (napcat.ConnectError, OSError):
        return {"state": "unavailable"}


def connection_identity(config):
    # NapCat's WebUI fills in or omits a debug flag when serializing each connection.
    network = copy.deepcopy(config.get("network"))
    if isinstance(network, dict):
        for key in napcat.DESKTOP_CONNECT_KEYS:
            values = network.get(key)
            if isinstance(values, list) and key != "plugins":
                for item in values:
                    if isinstance(item, dict):
                        item.pop("debug", None)
    return network


def change(root: Path, raw, *, remove=False, config_dir=None, client_factory=None):
    pair = None if remove else pairing_info(raw)
    plan = napcat.preflight(root, config_dir or napcat.NAPCAT_CONFIG_DEFAULT, account="primary", client_factory=client_factory)
    old = plan.old_config
    disk = napcat._read_account_config(plan.napcat_config_dir, plan.self_id)
    if not napcat._same_json(connection_identity(disk), connection_identity(old)):
        raise napcat.ConnectError("当前登录账号和 NapCat 管理页的连接记录不一致，请核对正在使用的账号。")
    updated = phone_config(old, pair)
    desktop = napcat._plan_desktop_config(plan, plan.napcat_config_dir.parents[2] / "config/bot.json",
                                          transform=lambda cfg: phone_config(cfg, pair))
    with napcat._new_http_client(client_factory) as client:
        def read():
            return napcat._webui_get_config(client, plan.webui, plan.credential)

        def write(config):
            napcat._webui_set_config(client, plan.webui, plan.credential, config)

        if not napcat._same_json(read(), old):
            raise napcat.ConnectError("QQ 设置刚刚发生变化，请重新配对。")
        changed = not napcat._same_json(old, updated)
        if changed:
            backup = napcat._new_backup_dir(plan.project_root) / "onebot-config.json"
            napcat._write_json_private(backup, old)
        try:
            if changed:
                write(updated)
                if not napcat._same_json(connection_identity(read()), connection_identity(updated)):
                    raise napcat.ConnectError("NapCat 未确认手机连接设置。")
            napcat._apply_desktop_config(plan, desktop)
        except napcat.ConnectError:
            desktop_restored = False
            try:
                current_bytes, current_desktop = napcat._read_json_bytes(desktop.path, "NapCat Desktop bot.json")
                if desktop.changed and napcat._same_json(current_desktop, desktop.updated):
                    rollback = replace(desktop, source_bytes=current_bytes,
                                       source_sha256=napcat.hashlib.sha256(current_bytes).hexdigest(),
                                       original=current_desktop, updated=desktop.original, changed=True)
                    napcat._apply_desktop_config(plan, rollback)
                    current_desktop = desktop.original
                desktop_restored = napcat._same_json(current_desktop, desktop.original)
            except napcat.ConnectError:
                pass
            if changed:
                current = read()
                if napcat._same_json(connection_identity(current), connection_identity(updated)):
                    write(old)
                    current = read()
                if not napcat._same_json(connection_identity(current), connection_identity(old)):
                    raise napcat.ConnectError("QQ 配置有并发变化，请在 NapCat 核对手机连接。私有备份已保存。") from None
            if not desktop_restored:
                raise napcat.ConnectError("桌面 QQ 配置有并发变化，请检查 NapCat 记录。私有备份已保存。") from None
            raise napcat.ConnectError("未完成手机配对，请重新检查电脑配置后再试。") from None
    return {"state": "unpaired" if remove else "paired"}
