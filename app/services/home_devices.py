"""Locally paired Home Assistant entities and verified state changes."""
from __future__ import annotations

import asyncio
import re
from datetime import datetime

import httpx

from app.services.bounded_http import request_json
from app.services.chat_brain import endpoint
from app.services.social_plans import CHINA, PlanStore

DOMAINS = {"light", "switch", "climate", "media_player", "button", "scene", "script", "input_button"}
SCHEMA = """
CREATE TABLE IF NOT EXISTS connection (id INTEGER PRIMARY KEY CHECK(id=1), base TEXT NOT NULL, token TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS devices (name TEXT PRIMARY KEY, entity TEXT NOT NULL UNIQUE);
"""


class DeviceError(ValueError):
    pass


class HomeDevices:
    def __init__(self, root):
        self.store = PlanStore(root / "data/dafeiyu/home-devices.sqlite3", SCHEMA)

    def status(self):
        def read(db):
            connection = db.execute("SELECT base FROM connection WHERE id=1").fetchone()
            bindings = db.execute("SELECT name,entity FROM devices ORDER BY name").fetchall()
            return {"base": connection[0] if connection else "", "devices": [dict(x) for x in bindings]}
        return self.store.transaction(read)

    async def api(self, base, token, method, path, **kwargs):
        async with httpx.AsyncClient(trust_env=False) as client:
            return await request_json(client, method, base + "/api/" + path, max_bytes=8_000_000,
                                      timeout_seconds=10, headers={"Authorization": "Bearer " + token}, **kwargs)

    def connection(self):
        row = self.store.transaction(lambda db: db.execute("SELECT base,token FROM connection WHERE id=1").fetchone())
        if not row:
            raise DeviceError("先在本机账号页连接 Home Assistant，再选你要让我控制的设备。")
        return tuple(row)

    async def connect(self, data):
        base = endpoint(data.get("base", ""))
        token = data.get("token", "")
        if not isinstance(token, str) or not token.strip() or len(token) > 2500 or any(ord(c) < 32 for c in token):
            raise DeviceError("请填写 Home Assistant 的长期访问令牌。")
        response = await self.api(base, token, "GET", "")
        if not isinstance(response, dict) or response.get("message") != "API running.":
            raise DeviceError("这个地址没有返回 Home Assistant API 的状态。")
        def save(db):
            old = db.execute("SELECT base FROM connection").fetchone()
            if old and old[0] != base:
                db.execute("DELETE FROM devices")
            db.execute("INSERT OR REPLACE INTO connection VALUES (1,?,?)", (base, token))
        self.store.transaction(save)
        return self.status()

    async def available(self):
        rows = await self.api(*self.connection(), "GET", "states")
        return [{"entity": row["entity_id"], "name": str(row.get("attributes", {}).get("friendly_name") or row["entity_id"])}
                for row in rows if row.get("entity_id", "").partition(".")[0] in DOMAINS]

    async def bind(self, name, entity):
        if not isinstance(name, str) or not re.fullmatch(r"[\w\- ]{1,32}", name.strip()) or not isinstance(entity, str):
            raise DeviceError("请给设备起一个简短的名字。")
        if entity not in {x["entity"] for x in await self.available()}:
            raise DeviceError("在当前连接里找不到这个设备。")
        def save(db):
            db.execute("DELETE FROM devices WHERE entity=?", (entity,))
            db.execute("INSERT OR REPLACE INTO devices VALUES (?,?)", (name.strip(), entity))
        self.store.transaction(save)
        return self.status()

    def remove(self, name):
        self.store.transaction(lambda db: db.execute("DELETE FROM devices WHERE name=?", (name,)))
        return self.status()

    def disconnect(self):
        def clear(db):
            db.execute("DELETE FROM devices")
            db.execute("DELETE FROM connection")
        self.store.transaction(clear)
        return self.status()

    async def execute(self, event, text):
        bindings = self.status()["devices"]
        matches = [b for b in bindings if b["name"] in text]
        if not matches:
            self.connection()
            return "可用设备有" + "、".join(b["name"] for b in bindings) if bindings else "账号页里还没有选要控制的设备。"
        if len(matches) > 1:
            return "这句话里有多个设备名，请分别指一个。要让灯光和音乐一起变化，可以绑定一个场景。"
        name, entity = matches[0]["name"], matches[0]["entity"]
        connection = self.connection()
        state = await self.api(*connection, "GET", "states/" + entity)
        if state.get("entity_id") != entity or state.get("state") in {None, "unknown", "unavailable"}:
            return name + "现在没有可核实的状态。"
        action = device_action(entity, text, state)
        if action is None:
            return name + "当前" + describe_state(state)
        service, payload, expected = action
        # A repeated QQ event, even after restarting, must not press a button twice.
        claimed = self.store.turn(event, lambda db: "claimed", datetime.now(CHINA))
        if not claimed:
            return ""
        await self.api(*connection, "POST", "services/" + service, json={"entity_id": entity, **payload})
        if expected is None:
            return "Home Assistant 已接收「" + name + "」的动作，设备没有提供完成回报。"
        for attempt in range(3):
            if attempt:
                await asyncio.sleep(.35 * attempt)
            latest = await self.api(*connection, "GET", "states/" + entity)
            if latest.get("entity_id") == entity and expected(latest):
                return name + "已回报" + describe_state(latest)
        return name + "的指令已发出，设备还没有回报目标状态。"


def describe_state(row):
    attrs = row.get("attributes") or {}
    values = {"off":"关闭", "on":"开启", "playing":"播放中", "paused":"已暂停", "cool":"制冷", "heat":"制热"}
    label = values.get(row["state"], str(row["state"]))
    if row["state"] != "off":
        if attrs.get("temperature") is not None:
            label += f"、设定 {attrs['temperature']}℃"
        elif attrs.get("brightness") is not None:
            label += f"、亮度 {round(attrs['brightness'] * 100 / 255)}%"
        elif attrs.get("volume_level") is not None:
            label += f"、音量 {round(attrs['volume_level'] * 100)}%"
    return label


def device_action(entity, text, state):
    domain = entity.partition(".")[0]
    if re.search(r"状态|开着吗|开了吗|关了吗|多少|是否", text):
        return None
    attrs = state.get("attributes") or {}
    num = re.search(r"(-?\d+(?:\.\d+)?)\s*(%|％|度|℃)", text)
    if num:
        value = float(num[1])
        if domain == "climate" and num[2] in {"度","℃"} and attrs.get("min_temp", 7) <= value <= attrs.get("max_temp", 35):
            return "climate/set_temperature", {"temperature":value}, lambda s: s.get("attributes", {}).get("temperature") == value
        if domain in {"light","media_player"} and num[2] in {"%","％"} and 0 <= value <= 100:
            if domain == "light":
                brightness = round(value * 255 / 100)
                return "light/turn_on", {"brightness":brightness}, lambda s: (value == 0 and s["state"] == "off" or
                    s["state"] == "on" and abs(s.get("attributes", {}).get("brightness", -10) - brightness) <= 1)
            return "media_player/volume_set", {"volume_level":value/100}, lambda s: abs(s.get("attributes", {}).get("volume_level", -10) - value/100) < .011
        raise DeviceError("这个数值或单位不在设备支持的范围内。")
    if domain in {"button","input_button","scene","script"} and re.search(r"执行|启动|按一下|按下|打开|开启|切换|运行", text):
        return domain + ("/press" if domain in {"button","input_button"} else "/turn_on"), {}, None
    if domain == "media_player" and re.search(r"播放|暂停", text):
        play = "暂停" not in text
        return "media_player/media_" + ("play" if play else "pause"), {}, lambda s: s["state"] == ("playing" if play else "paused")
    if domain in {"light","climate","switch","media_player"} and re.search(r"开|关|熄灭|点亮", text):
        on = not bool(re.search(r"关|熄灭", text))
        return domain + ("/turn_on" if on else "/turn_off"), {}, lambda s: (
            s.get("state") not in {None,"unknown","unavailable","off","standby"} if on else s.get("state") in {"off","standby"})
    raise DeviceError("想查状态、打开、关闭，还是调到指定的温度、亮度或音量？")
