"""Explicit Mahjong review routing in QQ private chats and already-enabled groups."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Mapping
from contextlib import suppress
from types import SimpleNamespace

from app.services.mahjong.intent import normalize_intent, prepare_event_text

_LOGGER = logging.getLogger(__name__)
REVIEW_TIMEOUT = 720.0
PROGRESS_INTERVAL = 60.0
SEND_TIMEOUT = 30.0
RENDER_TIMEOUT = 30.0


def request_args(event, state=None):
    platform = str(event.get_platform_name()).casefold()
    raw = getattr(getattr(event, "message_obj", None), "raw_message", None)
    if platform not in {"aiocqhttp", "onebot", "onebot_v11"} or not isinstance(raw, Mapping):
        return None
    if (raw.get("message_type") not in {"group", "private"} or raw.get("post_type", "message") != "message"
            or str(raw.get("user_id", "")) == str(raw.get("self_id", ""))
            or (raw.get("sender") or {}).get("is_bot") is True):
        return None
    original = event.get_extra("astrbot_original_message_str", "") or event.get_message_str()
    text = prepare_event_text(dict(raw), original, state) if state is not None else original
    normalized = normalize_intent(text)
    if normalized is None:
        return None
    tail = normalized.removeprefix("/牌谱分析").strip()
    # Functional discussion must stay in chat; arbitrary free text is not a command.
    if tail and not (tail in {"帮助", "help"} or tail.startswith(("链接=", "文件=", "JSON=", "座位=", "赛制=", "AI=", "详情 ", "导出 "))):
        return None
    return tail


async def handle_native_review(runtime, event, args):
    from app.plugins.mahjong import handle_mahjong

    raw = dict(event.message_obj.raw_message)
    state = runtime._core.app.state
    group = str(raw.get("group_id") or "")
    if group and not await state.db.plugin_enabled(group, "mahjong_review"):
        return "本群尚未开启牌谱分析。"
    context = SimpleNamespace(args=args, event=raw, group_id=group, user_id=str(raw.get("user_id", "")),
                              request=SimpleNamespace(app=runtime._core.app))
    return await handle_mahjong(context)


async def _send(event, message) -> bool:
    try:
        await asyncio.wait_for(event.send(message), timeout=SEND_TIMEOUT)
        return True
    except Exception as exc:  # noqa: BLE001 -- platform failures must not orphan the task.
        _LOGGER.warning("mahjong delivery failed: %s", type(exc).__name__)
        return False


async def _text(event, text) -> bool:
    return await _send(event, event.make_result().message(text))


async def _wait_for_review(runtime, event, args):
    task = asyncio.create_task(handle_native_review(runtime, event, args), name="mahjong-private-or-group-review")
    started = time.monotonic()
    try:
        async with asyncio.timeout(REVIEW_TIMEOUT):
            while True:
                done, _ = await asyncio.wait({task}, timeout=PROGRESS_INTERVAL)
                if done:
                    return task.result()
                elapsed = int((time.monotonic() - started) // 60)
                await _text(event, f"牌谱复盘仍在处理中，已用约{elapsed}分钟；完成后会继续发送图文。"
                                  f"整个分析最多等待{int(REVIEW_TIMEOUT // 60)}分钟，超时会明确告诉你。")
    finally:
        if not task.done():
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task


async def deliver_native_review(runtime, event, args):
    """Send all parts directly while the event stays claimed, with a bounded terminal reply."""
    from app.services.mahjong.cards import ReportMessage, report_messages
    from app.services.mahjong.highlights import split_report

    long_request = args.startswith(("链接=", "文件=", "JSON=", "座位=", "赛制=", "AI="))
    if long_request and not await _text(event, "正在读取整场牌谱并生成逐局图文，分析可能需要几分钟；处理中会更新进度。"):
        return
    _LOGGER.info("mahjong review started")
    try:
        text = await _wait_for_review(runtime, event, args)
    except TimeoutError:
        _LOGGER.warning("mahjong review timed out")
        await _text(event, "这次牌谱分析已超时，任务已结束，没有继续排队。请重新发送牌谱链接；"
                           "也可在消息末尾加 AI=关闭，先看本地逐局牌效复盘。")
        return
    except Exception as exc:  # noqa: BLE001 -- every accepted request needs a terminal outcome.
        _LOGGER.warning("mahjong review failed: %s", type(exc).__name__)
        await _text(event, "这次牌谱复盘处理失败，任务已结束，没有继续排队。请重新发送牌谱链接。")
        return
    _LOGGER.info("mahjong review analysis completed; preparing delivery")
    raw = event.message_obj.raw_message
    owner = (str(raw.get("self_id", "")), str(raw.get("group_id") or ""), str(raw.get("user_id", "")))
    try:
        parts = await asyncio.wait_for(report_messages(runtime._core.app.state, text, owner), timeout=RENDER_TIMEOUT)
    except Exception as exc:  # noqa: BLE001 -- keep the finished analysis even when rendering fails.
        _LOGGER.warning("mahjong report rendering failed: %s", type(exc).__name__)
        parts = tuple(ReportMessage(chunk) for chunk in split_report(text))
    for part in parts:
        if part.image:
            from astrbot.api.message_components import Image, Plain

            try:
                sent = await _send(event, event.chain_result([Plain(part.text), Image.fromFileSystem(str(part.image))]))
            except Exception as exc:  # noqa: BLE001 -- image preparation can fail before transport.
                _LOGGER.warning("mahjong image preparation failed: %s", type(exc).__name__)
                sent = False
            if not sent:
                await _text(event, "复盘已完成，但图片发送未确认成功，下面补发文字版。")
                for chunk in split_report(text):
                    if not await _text(event, chunk):
                        return
                break
        elif not await _text(event, part.text):
            return
    _LOGGER.info("mahjong review delivery completed")
