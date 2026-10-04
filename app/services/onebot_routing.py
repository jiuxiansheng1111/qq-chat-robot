from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass
from typing import Any

import httpx

ASTRBOT_API_BASE = "http://astrbot.onebot.local"
ActionCall = Callable[..., Awaitable[Any]]


@dataclass(frozen=True)
class OneBotRoute:
    self_id: str
    api_base: str
    access_token: str
    webhook_token: str


@dataclass
class ReplyCounter:
    attempted: int = 0
    succeeded: int = 0


_current_self_id: ContextVar[str] = ContextVar("onebot_self_id", default="")
_reply_counter: ContextVar[ReplyCounter | None] = ContextVar("onebot_reply_counter", default=None)
_action_clients: dict[str, ActionCall] = {}


def set_current_onebot_self_id(self_id: str) -> None:
    _current_self_id.set(str(self_id or "").strip())


def current_onebot_self_id() -> str:
    return _current_self_id.get().strip()


def register_action_client(self_id: str, action_call: ActionCall) -> None:
    """按机器人账号绑定 AstrBot 协议接口。"""
    selected = str(self_id or "").strip()
    if not selected:
        raise ValueError("self_id must not be empty")
    if not callable(action_call):
        raise TypeError("action_call must be callable")
    _action_clients[selected] = action_call


def unregister_action_client(self_id: str) -> bool:
    """移除账号的接口，返回是否找到。"""
    selected = str(self_id or "").strip()
    return _action_clients.pop(selected, None) is not None


@contextmanager
def track_replies() -> Iterator[ReplyCounter]:
    """记录本次处理尝试和成功发送了几条回复。"""
    counter = ReplyCounter()
    token: Token[ReplyCounter | None] = _reply_counter.set(counter)
    try:
        yield counter
    finally:
        _reply_counter.reset(token)


def _registered_route(settings: Any, self_id: str) -> OneBotRoute:
    secondary_id = str(getattr(settings, "onebot_self_id_2", "") or "").strip()
    if secondary_id and self_id == secondary_id:
        webhook_token = (
            getattr(settings, "onebot_webhook_token_2", "")
            or getattr(settings, "onebot_webhook_token", "")
            or ""
        )
    else:
        webhook_token = getattr(settings, "onebot_webhook_token", "") or ""
    return OneBotRoute(
        self_id=self_id,
        api_base=ASTRBOT_API_BASE,
        access_token="",
        webhook_token=str(webhook_token),
    )


def onebot_route(settings, self_id: str | None = None) -> OneBotRoute:
    primary_id = str(getattr(settings, "onebot_self_id", "") or "").strip()
    selected = str(self_id or current_onebot_self_id() or primary_id or "").strip()
    if selected and selected in _action_clients:
        return _registered_route(settings, selected)

    secondary_id = str(getattr(settings, "onebot_self_id_2", "") or "").strip()
    if secondary_id and selected == secondary_id:
        return OneBotRoute(
            self_id=secondary_id,
            api_base=str(
                getattr(settings, "onebot_api_base_2", "")
                or getattr(settings, "onebot_api_base", "")
                or ""
            ).strip(),
            access_token=str(getattr(settings, "onebot_access_token_2", "") or "").strip(),
            webhook_token=str(
                getattr(settings, "onebot_webhook_token_2", "")
                or getattr(settings, "onebot_webhook_token", "")
                or ""
            ).strip(),
        )

    return OneBotRoute(
        self_id=str(primary_id or selected or "").strip(),
        api_base=str(getattr(settings, "onebot_api_base", "") or "").strip(),
        access_token=str(getattr(settings, "onebot_access_token", "") or "").strip(),
        webhook_token=str(getattr(settings, "onebot_webhook_token", "") or "").strip(),
    )


def _action_sends_reply(action: str) -> bool:
    action = action.casefold()
    return (
        action == "send_msg"
        or action.startswith(("send_private_", "send_group_"))
        or "forward" in action.split("_")
    )


def _success_payload(payload: Any) -> tuple[dict[str, Any], bool]:
    if isinstance(payload, dict) and {"status", "retcode", "data"}.issubset(payload):
        succeeded = payload.get("status") == "ok" and payload.get("retcode") in (0, "0")
        return payload, succeeded
    return {"status": "ok", "retcode": 0, "data": payload}, True


def _callback_timeout(request: httpx.Request) -> float | None:
    timeout = request.extensions.get("timeout")
    if not isinstance(timeout, dict):
        return 5.0
    value = timeout.get("read")
    if value is None:
        return None
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        return 5.0


def _action_from_request(request: httpx.Request) -> str:
    action = request.url.path.rstrip("/").rsplit("/", 1)[-1]
    if not action:
        raise httpx.RequestError("OneBot action path is empty", request=request)
    return action


def onebot_client(settings, *, self_id: str | None = None, **httpx_client_kwargs) -> httpx.AsyncClient:
    """已绑定的账号走 AstrBot，其余账号沿用 HTTP 接口。"""
    route = onebot_route(settings, self_id)
    action_call = _action_clients.get(route.self_id)
    if route.api_base != ASTRBOT_API_BASE or action_call is None:
        return httpx.AsyncClient(**httpx_client_kwargs)

    async def dispatch(request: httpx.Request) -> httpx.Response:
        action = _action_from_request(request)
        counter = _reply_counter.get()
        tracks_reply = counter is not None and _action_sends_reply(action)
        if tracks_reply:
            counter.attempted += 1
        try:
            try:
                params = json.loads(request.content) if request.content else {}
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise httpx.RequestError("OneBot request body is not valid JSON", request=request) from exc
            if not isinstance(params, dict):
                raise httpx.RequestError("OneBot request body must be a JSON object", request=request)

            timeout_seconds = _callback_timeout(request)
            if timeout_seconds is None:
                payload = await action_call(action, **params)
            else:
                async with asyncio.timeout(timeout_seconds):
                    payload = await action_call(action, **params)
            body, succeeded = _success_payload(payload)
            response = httpx.Response(200, json=body, request=request)
            if tracks_reply and succeeded:
                counter.succeeded += 1
            return response
        except httpx.HTTPError:
            raise
        except TimeoutError as exc:
            raise httpx.TimeoutException("AstrBot OneBot action timed out", request=request) from exc
        except Exception as exc:
            if _action_sends_reply(action) and "timeout: ntevent" in str(exc).casefold():
                raise httpx.TimeoutException(
                    "QQ message delivery confirmation timed out", request=request
                ) from exc
            raise httpx.RequestError("AstrBot OneBot action failed", request=request) from exc

    kwargs = dict(httpx_client_kwargs)
    kwargs["transport"] = httpx.MockTransport(dispatch)
    return httpx.AsyncClient(**kwargs)
