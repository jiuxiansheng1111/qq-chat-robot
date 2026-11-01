"""Small, pinned Liqi descriptor subset; protobuf handles wire validation and packed fields."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from google.protobuf import descriptor_pb2, descriptor_pool, message_factory
from google.protobuf.message import DecodeError

from app.services.mahjong.records import RecordError

MAX_BINARY = 8 * 1024 * 1024


@lru_cache(maxsize=1)
def _pool():
    schema = json.loads(Path(__file__).with_name("liqi_record_schema.json").read_text())
    file = descriptor_pb2.FileDescriptorProto(name="qqchat_liqi.proto", package="lq", syntax="proto3")
    names = set()

    def inventory(items, parent="lq"):
        for name, item in items.items():
            names.add(parent + "." + name)
            inventory(item.get("nested", {}), parent + "." + name)

    inventory(schema)
    scalar = {name.lower().removeprefix("type_"): value
              for name, value in descriptor_pb2.FieldDescriptorProto.Type.items()}

    def build(items, destination, parent="lq"):
        for name, item in items.items():
            message = destination.add(name=name)
            scope = parent + "." + name
            for key, spec in item["fields"].items():
                field = message.field.add(name=key, number=spec["id"])
                field.label = 3 if spec.get("rule") == "repeated" else 1
                if spec["type"] in scalar:
                    field.type = scalar[spec["type"]]
                else:
                    candidates = [scope + "." + spec["type"], parent + "." + spec["type"], "lq." + spec["type"]]
                    field.type_name = "." + next(candidate for candidate in candidates if candidate in names)
                    field.type = descriptor_pb2.FieldDescriptorProto.TYPE_MESSAGE
            build(item.get("nested", {}), message.nested_type, scope)

    build(schema, file.message_type)
    pool = descriptor_pool.DescriptorPool()
    pool.Add(file)
    return pool


def _message(name):
    name = name.removeprefix(".")
    if not name.startswith("lq."):
        name = "lq." + name
    try:
        return message_factory.GetMessageClass(_pool().FindMessageTypeByName(name))()
    except KeyError as exc:
        raise RecordError("牌谱含尚未支持的雀魂事件，已停止，未省略事件假装完整分析") from exc


def encode(name: str, values: dict) -> bytes:
    message = _message(name)

    def fill(target, data):
        for key, value in data.items():
            field = target.DESCRIPTOR.fields_by_name[key]
            if field.is_repeated:
                if field.message_type:
                    for item in value:
                        fill(getattr(target, key).add(), item)
                else:
                    getattr(target, key).extend(value)
            elif field.message_type:
                fill(getattr(target, key), value)
            else:
                setattr(target, key, value)

    fill(message, values)
    return message.SerializeToString()


def decode(name: str, payload: bytes) -> dict:
    if not isinstance(payload, bytes) or len(payload) > MAX_BINARY:
        raise RecordError("雀魂牌谱数据大小或类型不正确")
    message = _message(name)
    try:
        message.ParseFromString(payload)
    except DecodeError as exc:
        raise RecordError("雀魂原始牌谱解码失败，不能据此分析整场") from exc

    def plain(item):
        result = {}
        for field in item.DESCRIPTOR.fields:
            value = getattr(item, field.name)
            if field.is_repeated:
                result[field.name] = [plain(v) for v in value] if field.message_type else list(value)
            elif field.message_type:
                if item.HasField(field.name):
                    result[field.name] = plain(value)
            else:
                result[field.name] = value
        return result

    return plain(message)


def record_actions(payload: bytes) -> list[dict]:
    outer = decode("Wrapper", payload)
    if outer["name"] != ".lq.GameDetailRecords":
        raise RecordError("服务器返回的不是完整对局牌谱")
    detail = decode("GameDetailRecords", outer["data"])
    wrapped = ([a["result"] for a in detail["actions"] if a["type"] == 1 and a["result"]]
               if detail["version"] >= 210715 else detail["records"])
    if not wrapped or len(wrapped) > 8192:
        raise RecordError("雀魂牌谱为空或事件过多")
    records = []
    for data in wrapped:
        item = decode("Wrapper", data)
        if not item["name"].startswith(".lq.Record"):
            raise RecordError("牌谱事件不是赛后Record记录")
        records.append({"name": item["name"].removeprefix(".lq."), "data": decode(item["name"], item["data"])})
    return records
