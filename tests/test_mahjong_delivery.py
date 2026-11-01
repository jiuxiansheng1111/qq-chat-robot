"""Progress, terminal outcomes, cancellation, and failed image delivery."""

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app import astrbot_mahjong as adapter
from app.services.mahjong import cards


def fake_event():
    return SimpleNamespace(
        send=AsyncMock(),
        make_result=lambda: SimpleNamespace(message=lambda text: text),
        message_obj=SimpleNamespace(raw_message={"self_id": "bot", "user_id": "human"}),
        chain_result=lambda chain: chain,
    )


def runtime():
    return SimpleNamespace(_core=SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace())))


async def test_timeout_sends_terminal_reply_and_releases_review_lock(monkeypatch):
    lock = asyncio.Lock()
    cancelled = asyncio.Event()

    async def stuck(*args):
        async with lock:
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

    monkeypatch.setattr(adapter, "handle_native_review", stuck)
    monkeypatch.setattr(adapter, "REVIEW_TIMEOUT", .03)
    monkeypatch.setattr(adapter, "PROGRESS_INTERVAL", .01)
    event = fake_event()
    await adapter.deliver_native_review(runtime(), event, "链接=synthetic")
    replies = [call.args[0] for call in event.send.call_args_list]
    assert "正在读取" in replies[0] and any("仍在处理中" in text for text in replies)
    assert "已超时" in replies[-1] and "任务已结束" in replies[-1]
    assert cancelled.is_set() and not lock.locked()


async def test_unexpected_failure_is_reported_without_error_details(monkeypatch):
    monkeypatch.setattr(adapter, "handle_native_review", AsyncMock(side_effect=RuntimeError("private-error-token")))
    event = fake_event()
    await adapter.deliver_native_review(runtime(), event, "链接=synthetic")
    replies = [call.args[0] for call in event.send.call_args_list]
    assert "处理失败" in replies[-1] and "private-error-token" not in "".join(replies)


async def test_render_timeout_falls_back_to_finished_report(monkeypatch):
    async def stuck(*args):
        await asyncio.Event().wait()

    text = "完整复盘建议\n" * 500
    monkeypatch.setattr(adapter, "handle_native_review", AsyncMock(return_value=text))
    monkeypatch.setattr(cards, "report_messages", stuck)
    monkeypatch.setattr(adapter, "RENDER_TIMEOUT", .01)
    event = fake_event()
    await adapter.deliver_native_review(runtime(), event, "详情 synthetic 1")
    assert "".join(call.args[0] for call in event.send.call_args_list) == text


async def test_image_send_failure_falls_back_to_every_text_chunk(monkeypatch):
    import sys
    from types import ModuleType

    image_api = ModuleType("astrbot.api.message_components")
    image_api.Image = SimpleNamespace(fromFileSystem=lambda path: ("image", path))
    image_api.Plain = lambda text: ("text", text)
    monkeypatch.setitem(sys.modules, image_api.__name__, image_api)
    text = "完整复盘建议\n" * 500
    monkeypatch.setattr(adapter, "handle_native_review", AsyncMock(return_value=text))
    monkeypatch.setattr(cards, "report_messages", AsyncMock(return_value=(
        cards.ReportMessage("图片", Path("sample.jpg")),)))
    event = fake_event()
    sent = []

    async def send(part):
        if isinstance(part, list):
            raise RuntimeError("transport rejected image")  # noqa: TRY004 -- simulated platform rejection.
        sent.append(part)

    event.send = send
    await adapter.deliver_native_review(runtime(), event, "详情 synthetic 1")
    assert "补发文字" in sent[0] and "".join(sent[1:]) == text


async def test_cancellation_does_not_leave_background_analysis(monkeypatch):
    started, cancelled = asyncio.Event(), asyncio.Event()

    async def stuck(*args):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    monkeypatch.setattr(adapter, "handle_native_review", stuck)
    task = asyncio.create_task(adapter.deliver_native_review(runtime(), fake_event(), "链接=synthetic"))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert cancelled.is_set()
