import hashlib
import hmac
import logging
import secrets
from contextlib import asynccontextmanager
from dataclasses import dataclass

import httpx
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from redis.exceptions import RedisError

from app.api.admin import router as admin_router
from app.api.auth import router as auth_router
from app.config import get_settings
from app.core.auth import AuthService
from app.core.idempotency import EventDeduplicator, RedisEventDeduplicator
from app.core.rate_limit import LocalRateLimiter, RedisRateLimiter
from app.db.database import Database
from app.llm.manager import LLMManager
from app.llm.memory import ConversationMemory
from app.llm.providers import LLMError
from app.plugins.media import (
    NAILONG_SOURCE_URL,
    random_image,
    random_nailong_image,
    random_real_pig_image,
)
from app.plugins.registry import registry

settings = get_settings()
logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("qqchat")

CAT_IMAGE_COMMANDS = frozenset({"/猫", "/cat", "猫图", "随机猫", "随机猫咪", "随机猫图"})
PIG_IMAGE_COMMANDS = frozenset({"/小猪", "/pig", "猪图", "随机猪", "随机猪猪", "随机小猪"})
NAILONG_IMAGE_COMMANDS = frozenset({"/奶龙", "奶龙", "随机奶龙", "来只奶龙", "龙来"})


@dataclass
class PluginContext:
    request: Request
    event: dict
    group_id: str
    user_id: str
    text: str
    is_admin: bool
    args: str = ""


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings.validate_security()
    db = Database(settings)
    await db.init()
    auth = AuthService(settings, db)
    await auth.ensure_admin()
    app.state.db = db
    app.state.auth = auth
    app.state.llm = LLMManager(settings)
    app.state.memory = ConversationMemory(settings.max_context_messages)
    app.state.deduplicator = EventDeduplicator(settings.event_dedupe_ttl_seconds)
    registry.load_modules(settings.plugin_modules)
    app.state.limiter = LocalRateLimiter(
        limit=settings.user_rate_limit_per_minute, window_seconds=60
    )
    app.state.group_limiter = LocalRateLimiter(
        limit=settings.group_rate_limit_per_minute, window_seconds=60
    )
    if settings.redis_url:
        try:
            redis_limiter = RedisRateLimiter(
                settings.redis_url, limit=settings.user_rate_limit_per_minute, window_seconds=60
            )
            await redis_limiter.connect()
            app.state.limiter = redis_limiter
            redis_group_limiter = RedisRateLimiter(
                settings.redis_url, limit=settings.group_rate_limit_per_minute, window_seconds=60
            )
            await redis_group_limiter.connect()
            app.state.group_limiter = redis_group_limiter
            redis_deduplicator = RedisEventDeduplicator(
                settings.redis_url, settings.event_dedupe_ttl_seconds
            )
            await redis_deduplicator.connect()
            app.state.deduplicator = redis_deduplicator
            logger.info("Redis rate limiter enabled")
        except (ImportError, OSError, RuntimeError, RedisError) as exc:
            logger.warning("Redis unavailable, using local limiter: %s", exc)
    try:
        yield
    finally:
        await app.state.llm.aclose()


app = FastAPI(title="qqchat robot", version="0.1.0", lifespan=lifespan)
app.include_router(auth_router)
app.include_router(admin_router)


@app.get("/health/live")
async def live():
    return {"status": "ok"}


@app.get("/health/ready")
async def ready(request: Request):
    if not getattr(request.app.state, "llm", None):
        return JSONResponse({"status": "not_ready"}, status_code=503)
    return {"status": "ready"}


@app.get("/health/llm")
async def llm_health(request: Request):
    return {"status": "ok", "providers": request.app.state.llm.snapshot()}


def message_text(event: dict) -> str:
    message = event.get("message", "")
    if isinstance(message, str):
        return message.strip()
    parts = []
    for segment in message or []:
        if segment.get("type") == "text":
            parts.append(segment.get("data", {}).get("text", ""))
    return "".join(parts).strip()


def bot_mentioned(event: dict) -> bool:
    if not settings.onebot_self_id or not isinstance(event.get("message"), list):
        return False
    return any(
        segment.get("type") == "at"
        and str(segment.get("data", {}).get("qq", "")) == settings.onebot_self_id
        for segment in event["message"]
    )


def mentioned_image_command(event: dict, commands: frozenset[str]) -> bool:
    """Return whether a QQ @mention contains one of the image commands."""
    return bot_mentioned(event) and message_text(event) in commands


def webhook_token_valid(
    configured_token: str,
    x_onebot_token: str | None,
    authorization: str | None,
    x_signature: str | None = None,
    raw_body: bytes = b"",
) -> bool:
    """Accept NapCat HMAC signatures plus the legacy token header styles."""
    if not configured_token:
        return True

    if x_signature and raw_body:
        expected = "sha1=" + hmac.new(
            configured_token.encode(), raw_body, hashlib.sha1
        ).hexdigest()
        if secrets.compare_digest(x_signature, expected):
            return True

    bearer_token = ""
    if authorization and authorization.lower().startswith("bearer "):
        bearer_token = authorization[7:].strip()
    provided_token = x_onebot_token or bearer_token
    return bool(provided_token) and secrets.compare_digest(provided_token, configured_token)


async def send_group_message(group_id: str, message: str) -> None:
    if not settings.onebot_api_base:
        logger.info("[dry-run] group=%s message=%s", group_id, message)
        return
    headers = {"Authorization": f"Bearer {settings.onebot_access_token}"} if settings.onebot_access_token else {}
    async with httpx.AsyncClient(timeout=10) as client:
        await client.post(f"{settings.onebot_api_base.rstrip('/')}/send_group_msg", headers=headers, json={"group_id": group_id, "message": message})


async def send_group_image(group_id: str, image_file: str, source_url: str = "") -> None:
    if not settings.onebot_api_base:
        logger.info("[dry-run] group=%s image=%s", group_id, image_file[:80])
        return
    headers = {"Authorization": f"Bearer {settings.onebot_access_token}"} if settings.onebot_access_token else {}
    message = [{"type": "image", "data": {"file": image_file}}]
    if source_url:
        message.append(
            {
                "type": "text",
                "data": {"text": f"\n真实照片来源：Wikimedia Commons\n{source_url}"},
            }
        )
    async with httpx.AsyncClient(timeout=15) as client:
        await client.post(f"{settings.onebot_api_base.rstrip('/')}/send_group_msg", headers=headers, json={"group_id": group_id, "message": message})


@app.post("/onebot/webhook")
async def onebot_webhook(
    request: Request,
    x_onebot_token: str | None = Header(default=None),
    authorization: str | None = Header(default=None),
    x_signature: str | None = Header(default=None),
):
    raw_body = await request.body()
    if not webhook_token_valid(
        settings.onebot_webhook_token,
        x_onebot_token,
        authorization,
        x_signature,
        raw_body,
    ):
        raise HTTPException(status_code=401, detail="invalid webhook token")
    event = await request.json()
    event_id = str(event.get("message_id") or event.get("event_id") or "")
    if event_id and not await request.app.state.deduplicator.first_seen(event_id):
        return {"ok": True, "ignored": True, "reason": "duplicate_event"}
    if event.get("post_type") != "message" or event.get("message_type") != "group":
        return {"ok": True, "ignored": True}

    text = message_text(event)
    group_id = str(event.get("group_id", ""))
    user_id = str(event.get("user_id", event.get("sender", {}).get("user_id", "")))
    sender_role = event.get("sender", {}).get("role", "member")
    is_admin = sender_role in {"admin", "owner"}
    if not group_id or not text:
        return {"ok": True, "ignored": True}
    group_enabled = await request.app.state.db.group_enabled(group_id)
    can_reenable = text in {"/bot on", "/机器人开启"} and is_admin
    if not group_enabled and not can_reenable:
        return {"ok": True, "ignored": True, "reason": "group_disabled"}
    if await request.app.state.db.is_blocked(group_id, user_id):
        return {"ok": True, "ignored": True, "reason": "user_blocked"}
    if not await request.app.state.limiter.allow(f"user:{user_id}"):
        return {"ok": True, "ignored": True, "reason": "user_rate_limited"}
    if not await request.app.state.group_limiter.allow(f"group:{group_id}"):
        return {"ok": True, "ignored": True, "reason": "group_rate_limited"}

    plugin_spec, _ = registry.resolve(text)
    if plugin_spec and plugin_spec.name != "group_admin" and not await request.app.state.db.plugin_enabled(group_id, plugin_spec.name):
        return {"ok": True, "ignored": True, "reason": "plugin_disabled"}

    plugin_result = await registry.dispatch(
        text,
        PluginContext(request, event, group_id, user_id, text, is_admin),
    )
    if plugin_result:
        plugin_name, reply = plugin_result
        if reply:
            await send_group_message(group_id, reply)
        return {"ok": True, "plugin": plugin_name}

    if text in {"/bot off", "/机器人关闭"} and is_admin:
        await request.app.state.db.set_group_enabled(group_id, False)
        await send_group_message(group_id, "本群机器人已关闭。")
    elif text in {"/bot on", "/机器人开启"} and is_admin:
        await request.app.state.db.set_group_enabled(group_id, True)
        await send_group_message(group_id, "本群机器人已开启。")
    elif text.startswith("/blacklist ") and is_admin:
        parts = text.split()
        if len(parts) == 3 and parts[1] in {"add", "remove"}:
            await request.app.state.db.set_blocked(group_id, parts[2], parts[1] == "add")
            await send_group_message(group_id, "黑名单已更新。")
        else:
            await send_group_message(group_id, "用法：/blacklist add QQ号 或 /blacklist remove QQ号")
    elif text in {"/记忆开启", "/memory on"}:
        await request.app.state.db.set_memory_enabled(group_id, user_id, True)
        await send_group_message(group_id, "已开启你的短期对话记忆。输入 /记忆关闭 可停止，输入 /记忆删除 可清除当前记忆。")
    elif text in {"/记忆关闭", "/memory off"}:
        await request.app.state.db.set_memory_enabled(group_id, user_id, False)
        request.app.state.memory.clear(group_id, user_id)
        await send_group_message(group_id, "已关闭并清除你的短期对话记忆。")
    elif text in {"/记忆删除", "/memory clear"}:
        request.app.state.memory.clear(group_id, user_id)
        await send_group_message(group_id, "你的短期对话记忆已清除。")
    elif text in {"/记忆状态", "/memory status"}:
        enabled = await request.app.state.db.memory_enabled(group_id, user_id)
        await send_group_message(group_id, "你的短期对话记忆：已开启" if enabled else "你的短期对话记忆：未开启")
    elif text in {"/help", "help"}:
        await send_group_message(group_id, registry.help_text())
    elif text in CAT_IMAGE_COMMANDS or mentioned_image_command(event, CAT_IMAGE_COMMANDS):
        try:
            image = await random_image(settings.cat_api_url, "", settings)
            await send_group_image(group_id, image)
        except (RuntimeError, httpx.HTTPError) as exc:
            logger.warning("cat image failed: %s", exc)
            await send_group_message(group_id, "猫图服务暂时不可用，请稍后再试。")
    elif text in PIG_IMAGE_COMMANDS or mentioned_image_command(event, PIG_IMAGE_COMMANDS):
        try:
            image = await random_real_pig_image(settings.pig_api_url, settings)
            await send_group_image(group_id, image.url, image.source_url)
        except (RuntimeError, httpx.HTTPError) as exc:
            logger.warning("pig image failed: %s", exc)
            await send_group_message(group_id, "小猪图片服务暂时不可用，请稍后再试。")
    elif text in NAILONG_IMAGE_COMMANDS or mentioned_image_command(event, NAILONG_IMAGE_COMMANDS):
        try:
            image = await random_nailong_image(settings)
            await send_group_image(group_id, image, NAILONG_SOURCE_URL)
        except (RuntimeError, httpx.HTTPError) as exc:
            logger.warning("nailong image failed: %s", exc)
            await send_group_message(group_id, "奶龙图库暂时不可用，请稍后再试。")
    elif text.startswith(("/ai ", "/AI ")) or (bot_mentioned(event) and text):
        prompt = text.split(" ", 1)[1].strip() if text.startswith(("/ai ", "/AI ")) else text
        memory_enabled = await request.app.state.db.memory_enabled(group_id, user_id)
        messages = request.app.state.memory.messages(
            group_id, user_id, settings.persona_prompt(), prompt, memory_enabled
        )
        try:
            answer = await request.app.state.llm.ask(messages)
            request.app.state.memory.append(group_id, user_id, prompt, answer, memory_enabled)
            await send_group_message(group_id, answer[:2000])
        except (LLMError, httpx.HTTPError) as exc:
            logger.warning("LLM request failed: %s", exc)
            await send_group_message(group_id, "我现在有点忙，稍后再试一下吧。")
    return {"ok": True}
