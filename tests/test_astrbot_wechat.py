from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.astrbot_wechat import WeChatTransport, normalize_wechat_event


@dataclass
class FakeMessageChain:
    chain: list


@dataclass
class FakePlain:
    text: str


@dataclass
class FakeImage:
    file: str

    @classmethod
    def fromURL(cls, url):
        return cls(url)

    @classmethod
    def fromFileSystem(cls, path):
        return cls(path)

    @classmethod
    def fromBase64(cls, data):
        return cls("base64://" + data)


@dataclass
class FakeFile:
    name: str
    file: str = ""
    url: str = ""


@dataclass
class FakeVideo:
    file: str


COMPONENTS = SimpleNamespace(
    MessageChain=FakeMessageChain,
    Plain=FakePlain,
    Image=FakeImage,
    File=FakeFile,
    Video=FakeVideo,
)


class FakeContext:
    def __init__(self):
        self.sent = []

    async def send_message(self, umo, chain):
        await asyncio.sleep(0)
        self.sent.append((umo, chain))


def fake_event(
    *,
    platform_id="wx-one",
    platform_name="weixin_oc",
    account_id="acct-one",
    kind="FriendMessage",
    session_id="peer-one",
    sender_id="peer-one",
    group_id="",
    message_id="m1",
    message_str="你好",
    components=None,
):
    obj = SimpleNamespace(
        type=kind,
        sender=SimpleNamespace(user_id=sender_id, nickname="用户甲"),
        group_id=group_id,
        message_id=message_id,
        timestamp=123,
        message=list(components or []),
    )
    platform = SimpleNamespace(account_id=account_id)
    return SimpleNamespace(
        platform=platform,
        platform_meta=SimpleNamespace(name=platform_name, id=platform_id),
        message_obj=obj,
        unified_msg_origin=f"{platform_id}:{kind}:{session_id}",
        get_platform_name=lambda: platform_name,
        get_platform_id=lambda: platform_id,
        get_self_id=lambda: platform_id,
        get_message_type=lambda: kind,
        get_session_id=lambda: session_id,
        get_sender_id=lambda: sender_id,
        get_sender_name=lambda: "用户甲",
        get_group_id=lambda: group_id,
        get_message_str=lambda: message_str,
        get_messages=lambda: obj.message,
    )


def test_private_event_is_namespaced_and_includes_synthetic_mention():
    event = fake_event()

    normalized = normalize_wechat_event(event)

    assert normalized is not None
    assert normalized["message_type"] == "group"
    assert normalized["wechat_private"] is True
    assert normalized["group_id"].startswith("wechat:group:wx-one:acct-one:private:")
    assert normalized["user_id"].startswith("wechat:user:wx-one:acct-one:member:")
    assert normalized["self_id"] in normalized["group_id"] or normalized["self_id"].startswith(
        "wechat:self:wx-one:acct-one:bot:"
    )
    assert normalized["message"][0] == {"type": "at", "data": {"qq": normalized["self_id"]}}
    assert normalized["message"][1] == {"type": "text", "data": {"text": "你好"}}


def test_ids_separate_platforms_accounts_and_users():
    first = normalize_wechat_event(fake_event())
    other_platform = normalize_wechat_event(fake_event(platform_id="wx-two"))
    other_account = normalize_wechat_event(fake_event(account_id="acct-two"))
    other_user = normalize_wechat_event(fake_event(sender_id="peer-two", session_id="peer-two"))

    assert first and other_platform and other_account and other_user
    assert len({first["group_id"], other_platform["group_id"], other_account["group_id"]}) == 3
    assert first["user_id"] != other_user["user_id"]


def test_group_event_keeps_platform_group_namespace_and_media():
    image_type = type("Image", (), {"file": "https://cdn.invalid/pic.png", "url": ""})
    image = image_type()
    event = fake_event(
        kind="GroupMessage",
        session_id="room-1",
        sender_id="peer-2",
        group_id="room-1",
        components=[image],
        message_str="[图片]",
    )

    normalized = normalize_wechat_event(event)

    assert normalized is not None
    assert normalized["wechat_private"] is False
    assert normalized["group_id"].startswith("wechat:group:wx-one:acct-one:group:")
    assert not any(segment["type"] == "at" for segment in normalized["message"])
    assert any(segment["type"] == "image" for segment in normalized["message"])
    assert normalized["sender"]["role"] == "member"


@pytest.mark.parametrize(
    "text",
    [
        "/bot off", "/blacklist add 123", "/群记忆", "/今日奥特曼",
        "/随机夺舍", "/随机二次元角色", "群里记住：大家喜欢动画",
        "删除群记忆：测试内容", "删除记忆 测试内容",
    ],
)
def test_private_group_only_commands_are_ignored(text):
    assert normalize_wechat_event(fake_event(message_str=text)) is None


def test_non_wechat_platform_is_ignored():
    assert normalize_wechat_event(fake_event(platform_name="aiocqhttp")) is None


def test_group_event_without_sender_is_ignored():
    event = fake_event(kind="GroupMessage", session_id="room", sender_id="", group_id="room")

    assert normalize_wechat_event(event) is None


def test_untranscribed_voice_placeholder_does_not_become_chat_text():
    record_type = type("Record", (), {"file": "local-audio.bin", "url": ""})
    event = fake_event(components=[record_type()], message_str="[语音]")

    normalized = normalize_wechat_event(event)

    assert normalized is not None
    assert not any(segment["type"] == "text" for segment in normalized["message"])
    assert any(segment["type"] == "record" for segment in normalized["message"])


def test_voice_placeholder_is_removed_but_other_text_is_kept():
    plain_type = type("Plain", (), {"text": "帮我听这句"})
    record_type = type("Record", (), {"file": "local-audio.bin", "url": ""})
    event = fake_event(
        components=[plain_type(), record_type()],
        message_str="帮我听这句\n[语音]",
    )

    normalized = normalize_wechat_event(event)

    assert normalized is not None
    text_segments = [segment for segment in normalized["message"] if segment["type"] == "text"]
    assert text_segments == [{"type": "text", "data": {"text": "帮我听这句"}}]


@pytest.mark.asyncio
async def test_stable_transport_routes_parallel_sessions_and_background_replies():
    context = FakeContext()
    bridge = WeChatTransport(
        context, platform_id="wx-one", account_id="acct-one", component_types=COMPONENTS
    )
    assert bridge.action_call is bridge.action_call
    event_a = fake_event(session_id="peer-a", sender_id="peer-a", message_id="a")
    event_b = fake_event(session_id="peer-b", sender_id="peer-b", message_id="b")
    normalized_a = bridge.bind_event(event_a)
    normalized_b = bridge.bind_event(event_b)
    assert normalized_a and normalized_b

    await asyncio.gather(
        bridge.action_call(
            "send_group_msg", group_id=normalized_a["group_id"], message="后台 A"
        ),
        bridge.action_call(
            "send_group_msg", group_id=normalized_b["group_id"], message="当前 B"
        ),
    )
    later_a = await bridge.action_call(
        "send_group_msg", group_id=normalized_a["group_id"], message="后台 A 后续段"
    )

    assert later_a["status"] == "ok"
    assert len(context.sent) == 3
    sent = {chain.chain[0].text: umo for umo, chain in context.sent}
    assert sent["后台 A"] == event_a.unified_msg_origin
    assert sent["当前 B"] == event_b.unified_msg_origin
    assert sent["后台 A 后续段"] == event_a.unified_msg_origin
    assert bridge.action_call is not None


@pytest.mark.asyncio
async def test_route_is_account_scoped_and_never_falls_back_to_latest_event():
    first_context = FakeContext()
    second_context = FakeContext()
    first = WeChatTransport(
        first_context, platform_id="wx-one", account_id="acct-one", component_types=COMPONENTS
    )
    second = WeChatTransport(
        second_context, platform_id="wx-two", account_id="acct-two", component_types=COMPONENTS
    )
    first_event = fake_event()
    second_event = fake_event(platform_id="wx-two", account_id="acct-two")
    first_normalized = first.bind_event(first_event)
    second_normalized = second.bind_event(second_event)
    assert first_normalized and second_normalized

    forged = await first.action_call(
        "send_group_msg", group_id=second_normalized["group_id"], message="不应发送"
    )
    private_user = await first.action_call(
        "send_private_msg", user_id=second_normalized["user_id"], message="不应发送"
    )
    valid = await second.action_call(
        "send_private_msg", user_id=second_normalized["user_id"], message="私聊"
    )

    assert forged["status"] == "failed"
    assert private_user["status"] == "failed"
    assert first_context.sent == []
    assert valid["status"] == "ok"
    assert second_context.sent[0][0] == second_event.unified_msg_origin


@pytest.mark.asyncio
async def test_route_table_is_bounded_and_evicted_sessions_fail_closed():
    context = FakeContext()
    bridge = WeChatTransport(
        context,
        platform_id="wx-one",
        account_id="acct-one",
        max_routes=1,
        component_types=COMPONENTS,
    )
    old = bridge.bind_event(fake_event(session_id="old", sender_id="old"))
    new = bridge.bind_event(fake_event(session_id="new", sender_id="new"))
    assert old and new

    result = await bridge.action_call(
        "send_group_msg", group_id=old["group_id"], message="过期"
    )

    assert result["status"] == "failed"
    assert len(bridge._routes) == 1
    assert context.sent == []


@pytest.mark.asyncio
async def test_group_lookup_actions_are_rejected_without_failing_safe_send_route():
    context = FakeContext()
    bridge = WeChatTransport(
        context, platform_id="wx-one", account_id="acct-one", component_types=COMPONENTS
    )
    normalized = bridge.bind_event(fake_event())
    assert normalized

    member = await bridge.action_call(
        "get_group_member_info",
        group_id=normalized["group_id"],
        user_id=normalized["user_id"],
    )
    history = await bridge.action_call(
        "get_group_msg_history", group_id=normalized["group_id"], count=20
    )
    admin = await bridge.action_call(
        "set_group_ban", group_id=normalized["group_id"], user_id=normalized["user_id"]
    )
    notice = await bridge.action_call(
        "send_group_notice", group_id=normalized["group_id"], content="不应发布公告"
    )
    reply = await bridge.action_call(
        "send_group_msg", group_id=normalized["group_id"], message="普通回复仍可发"
    )

    assert {member["status"], history["status"], admin["status"], notice["status"]} == {"failed"}
    assert reply["status"] == "ok"
    assert len(context.sent) == 1


@pytest.mark.asyncio
async def test_record_uses_file_attachment_without_exposing_missing_path(tmp_path: Path):
    context = FakeContext()
    bridge = WeChatTransport(
        context, platform_id="wx-one", account_id="acct-one", component_types=COMPONENTS
    )
    normalized = bridge.bind_event(fake_event())
    assert normalized
    audio = tmp_path / "take.wav"
    audio.write_bytes(b"audio")

    sent = await bridge.action_call(
        "send_group_msg",
        group_id=normalized["group_id"],
        message=[
            {"type": "record", "data": {"file": str(audio)}},
            {"type": "record", "data": {"file": str(tmp_path / "missing.wav")}},
        ],
    )

    assert sent["status"] == "ok"
    chain = context.sent[0][1].chain
    assert any(isinstance(component, FakeFile) and component.file == str(audio) for component in chain)
    assert any(isinstance(component, FakePlain) and "不支持直接发送语音" in component.text for component in chain)
    assert str(tmp_path / "missing.wav") not in repr(chain)


@pytest.mark.asyncio
async def test_image_urls_base64_and_forward_messages_become_message_chain():
    context = FakeContext()
    bridge = WeChatTransport(
        context, platform_id="wx-one", account_id="acct-one", component_types=COMPONENTS
    )
    normalized = bridge.bind_event(fake_event())
    assert normalized

    result = await bridge.action_call(
        "send_group_forward_msg",
        group_id=normalized["group_id"],
        messages=[
            {"type": "node", "data": {"content": [
                {"type": "text", "data": {"text": "图"}},
                {"type": "image", "data": {"file": "https://cdn.invalid/a.png"}},
                {"type": "image", "data": {"file": "base64://YWJj"}},
            ]}},
        ],
    )

    assert result["status"] == "ok"
    assert [type(value) for value in context.sent[0][1].chain] == [FakePlain, FakeImage, FakeImage]
    assert context.sent[0][1].chain[1].file == "https://cdn.invalid/a.png"
    assert context.sent[0][1].chain[2].file == "base64://YWJj"


@pytest.mark.asyncio
async def test_installed_astrbot_4282_event_and_components_route_in_isolation(tmp_path, monkeypatch):
    monkeypatch.setenv("ASTRBOT_ROOT", str(tmp_path / "isolated-astrbot"))
    monkeypatch.chdir(tmp_path)
    repo_root = Path(__file__).resolve().parents[1]
    site_packages = repo_root / "data" / "astrbot" / "runtime" / "tool-envs" / "astrbot" / "Lib" / "site-packages"
    if not site_packages.is_dir():
        pytest.skip("本机 AstrBot runtime 未附在此 checkout")
    monkeypatch.syspath_prepend(str(site_packages))
    try:
        import astrbot
        from astrbot.api.event import MessageChain
        from astrbot.api.message_components import File, Image, Plain, Record, Video
        from astrbot.core.platform.astrbot_message import AstrBotMessage, MessageMember
        from astrbot.core.platform.message_type import MessageType
        from astrbot.core.platform.platform_metadata import PlatformMetadata
        from astrbot.core.platform.sources.weixin_oc.weixin_oc_event import WeixinOCMessageEvent
    except (ImportError, SyntaxError) as exc:
        pytest.skip(f"当前 Python 不能载入本机 AstrBot runtime：{type(exc).__name__}")

    assert astrbot.__version__ == "4.28.2"
    message = AstrBotMessage()
    message.type = MessageType.FRIEND_MESSAGE
    message.self_id = "wx-adapter"
    message.session_id = "peer-1"
    message.message_id = "local-test-message"
    message.sender = MessageMember(user_id="peer-1", nickname="用户甲")
    message.message = [Plain("组件文本"), Record.fromBase64("YWJj"), Image.fromURL("https://cdn.invalid/in.png")]
    message.timestamp = 123
    message.raw_message = {}
    metadata = PlatformMetadata(name="weixin_oc", description="test", id="wx-adapter")
    adapter = SimpleNamespace(account_id="wx-account")
    event = WeixinOCMessageEvent(
        message_str="微信语音转写",
        message_obj=message,
        platform_meta=metadata,
        session_id="peer-1",
        platform=adapter,
    )
    context = FakeContext()
    bridge = WeChatTransport(context, platform_id="wx-adapter", component_types=SimpleNamespace(
        MessageChain=MessageChain, Plain=Plain, Image=Image, File=File, Video=Video
    ))

    raw_event = bridge.bind_event(event)
    assert raw_event is not None
    assert raw_event["message"][1] == {"type": "text", "data": {"text": "微信语音转写"}}
    assert any(segment["type"] == "record" for segment in raw_event["message"])
    assert any(segment["type"] == "image" for segment in raw_event["message"])

    audio = tmp_path / "answer.wav"
    audio.write_bytes(b"isolated fixture")
    result = await bridge.action_call(
        "send_group_msg",
        group_id=raw_event["group_id"],
        message=[
            {"type": "text", "data": {"text": "文件附件"}},
            {"type": "image", "data": {"file": "https://cdn.invalid/out.png"}},
            {"type": "record", "data": {"file": str(audio)}},
        ],
    )

    assert result["status"] == "ok"
    assert isinstance(context.sent[0][1], MessageChain)
    chain = context.sent[0][1].chain
    assert isinstance(chain[0], Plain)
    assert isinstance(chain[1], Image)
    assert isinstance(chain[2], File)
    assert not any(isinstance(value, Record) for value in chain)
    assert chain[2].file == str(audio)
