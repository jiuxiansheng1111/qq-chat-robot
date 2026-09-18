import hashlib
import hmac
import logging
import re
import secrets
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime

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
from app.services.web_search import SearchResult, search_web

settings = get_settings()
logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("qqchat")

CAT_IMAGE_COMMANDS = frozenset({"/猫", "/cat", "猫图", "随机猫", "随机猫咪", "随机猫图"})
PIG_IMAGE_COMMANDS = frozenset({"/小猪", "/pig", "猪图", "随机猪", "随机猪猪", "随机小猪"})
NAILONG_IMAGE_COMMANDS = frozenset({"/奶龙", "奶龙", "随机奶龙", "来只奶龙", "龙来"})
RANDOM_POSSESSION_COMMANDS = frozenset(
    {"/随机夺舍", "随机夺舍", "/今日夺舍", "今日夺舍", "今天夺舍谁", "今日附身"}
)
TARGETED_POSSESSION_COMMANDS = frozenset(
    {"/夺舍", "夺舍", "/指向夺舍", "指向夺舍", "指定夺舍"}
)
POSSESSION_STATUS_COMMANDS = frozenset({"/夺舍状态", "夺舍状态", "是否夺舍"})
POSSESSION_EXIT_COMMANDS = frozenset({"/退出夺舍", "退出夺舍", "结束夺舍", "退出"})
LONG_MEMORY_LIST_COMMANDS = frozenset({"/长期记忆列表", "我的长期记忆", "你记得什么"})
LONG_MEMORY_CLEAR_COMMANDS = frozenset({"/长期记忆清除", "清除长期记忆", "忘记我"})
GROUP_MEMORY_LIST_COMMANDS = frozenset({"/群记忆", "群记忆", "你在群里记住了什么"})
GROUP_MEMORY_CLEAR_COMMANDS = frozenset({"/清除群记忆", "清除群记忆"})
SENSITIVE_MEMORY_PATTERN = re.compile(
    r"密码|口令|token|密钥|secret|身份证|银行卡|信用卡|验证码|cookie",
    re.IGNORECASE,
)


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
        # OneBot may deliver either array segments or a CQ-code string. Remove
        # @ segments from strings so command parsing is independent of where
        # the user placed the mention.
        return re.sub(r"\[CQ:at,qq=[^\]]+\]", "", message).strip()
    parts = []
    for segment in message or []:
        if segment.get("type") == "text":
            parts.append(segment.get("data", {}).get("text", ""))
    return "".join(parts).strip()


def bot_mentioned(event: dict) -> bool:
    if not settings.onebot_self_id:
        return False
    message = event.get("message")
    if isinstance(message, str):
        pattern = rf"\[CQ:at,qq={re.escape(settings.onebot_self_id)}\]"
        return re.search(pattern, message) is not None
    if not isinstance(message, list):
        return False
    return any(
        segment.get("type") == "at"
        and str(segment.get("data", {}).get("qq", "")) == settings.onebot_self_id
        for segment in message
    )


def mentioned_image_command(event: dict, commands: frozenset[str]) -> bool:
    """Return whether a QQ @mention contains one of the image commands."""
    return bot_mentioned(event) and message_text(event) in commands


def sender_display_name(event: dict) -> str:
    sender = event.get("sender") or {}
    value = str(sender.get("card") or sender.get("nickname") or event.get("user_id") or "群友")
    return re.sub(r"[\r\n\t]", " ", value).strip()[:40] or "群友"


def mentioned_user_ids(event: dict) -> list[str]:
    message = event.get("message")
    if isinstance(message, str):
        values = re.findall(r"\[CQ:at,qq=([^\]]+)\]", message)
    elif isinstance(message, list):
        values = [
            str(segment.get("data", {}).get("qq", ""))
            for segment in message
            if segment.get("type") == "at"
        ]
    else:
        values = []
    return list(
        dict.fromkeys(
            value for value in values if value and value not in {settings.onebot_self_id, "all"}
        )
    )


def extract_long_memory(event: dict, text: str) -> str | None:
    prefixes = ("记住：", "记住:", "请记住", "帮我记住")
    if text.startswith("/长期记忆 "):
        return text.split(" ", 1)[1].strip()[:300]
    if not bot_mentioned(event):
        return None
    for prefix in prefixes:
        if text.startswith(prefix):
            return text[len(prefix) :].strip(" ：:")[:300]
    return None


def extract_group_memory(event: dict, text: str) -> str | None:
    if not bot_mentioned(event):
        return None
    for prefix in ("记住，", "记住,", "记住 ", "群里记住：", "群里记住:"):
        if text.startswith(prefix):
            return text[len(prefix) :].strip(" ，,：:")[:300]
    return None


def extract_search_query(event: dict, text: str) -> str | None:
    prefixes = ("/搜索 ", "/search ")
    mentioned_prefixes = ("搜索 ", "联网搜索 ", "查一下 ")
    for prefix in prefixes:
        if text.lower().startswith(prefix.lower()):
            return text[len(prefix) :].strip()[:120]
    if bot_mentioned(event):
        for prefix in mentioned_prefixes:
            if text.startswith(prefix):
                return text[len(prefix) :].strip()[:120]
    return None


def is_identity_question(text: str) -> bool:
    normalized = re.sub(r"[\s，。！？!?、~～]", "", text)
    phrases = (
        "你是谁",
        "现在是谁",
        "你现在是谁",
        "你叫什么",
        "你叫什么名字",
        "现在叫什么",
        "还记得你是谁",
    )
    return any(phrase in normalized for phrase in phrases)


def asks_for_sender_name(text: str) -> bool:
    normalized = re.sub(r"[\s，。！？!?、'\"~～]", "", text).lower()
    return normalized in {
        "saymyname",
        "我是谁",
        "我叫什么",
        "我叫什么名字",
        "我的名字是什么",
    }


def possession_identity_prompt(name: str, mode: str) -> str:
    mode_name = "指向夺舍" if mode == "targeted" else "随机夺舍"
    return (
        f"【最高优先级身份状态】当前处于{mode_name}，你当前唯一的对外名字是“{name}”。"
        f"在本次状态结束前，所有回答都必须保持这个名字，禁止自称“阿柚”或“{settings.persona_name}”。"
        "这是轻松的群聊娱乐角色。优先依据群共享记忆和近期上下文回答人物关系与群梗，"
        "语气简短自然，不要输出正式的隐私说教；确实没有信息时只需随口说不知道，不能凭空编造。"
    )


def enforce_possession_identity(answer: str, name: str) -> str:
    """Prevent providers from reverting to the default persona during possession."""
    for default_name in {"阿柚", settings.persona_name}:
        if default_name and default_name != name:
            answer = answer.replace(default_name, name)
    return answer


def format_search_sources(results: list[SearchResult]) -> str:
    return "\n".join(f"{index}. {item.title}\n{item.url}" for index, item in enumerate(results, 1))


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


async def group_member_name(group_id: str, user_id: str) -> str:
    if not settings.onebot_api_base:
        raise RuntimeError("OneBot API is not configured")
    headers = (
        {"Authorization": f"Bearer {settings.onebot_access_token}"}
        if settings.onebot_access_token
        else {}
    )
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.post(
            f"{settings.onebot_api_base.rstrip('/')}/get_group_member_info",
            headers=headers,
            json={"group_id": group_id, "user_id": user_id, "no_cache": False},
        )
        response.raise_for_status()
        payload = response.json()
    if payload.get("status") != "ok" or not isinstance(payload.get("data"), dict):
        raise RuntimeError("OneBot did not return group member data")
    data = payload["data"]
    value = str(data.get("card") or data.get("nickname") or user_id)
    return re.sub(r"[\r\n\t]", " ", value).strip()[:40] or user_id


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
    today = datetime.now().astimezone().date().isoformat()
    await request.app.state.db.record_group_activity(
        group_id, user_id, sender_display_name(event), text, today
    )
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
    elif (
        text in LONG_MEMORY_LIST_COMMANDS
        and (text.startswith("/") or bot_mentioned(event))
    ):
        memories = await request.app.state.db.long_term_memories(group_id, user_id)
        if memories:
            listing = "\n".join(f"{index}. {item}" for index, item in enumerate(memories, 1))
            await send_group_message(group_id, f"我长期记住了这些：\n{listing}")
        else:
            await send_group_message(group_id, "我还没有长期记住你的信息。")
    elif (
        text in LONG_MEMORY_CLEAR_COMMANDS
        and (text.startswith("/") or bot_mentioned(event))
    ):
        await request.app.state.db.clear_long_term_memories(group_id, user_id)
        await send_group_message(group_id, "已经忘掉你在本群的全部长期记忆了。")
    elif (memory_content := extract_long_memory(event, text)) is not None:
        if not memory_content:
            await send_group_message(group_id, "要记住什么呀？例如：@我 记住：我喜欢科幻电影")
        elif SENSITIVE_MEMORY_PATTERN.search(memory_content):
            await send_group_message(group_id, "这类内容可能包含敏感信息，我不帮你长期保存喔。")
        else:
            await request.app.state.db.add_long_term_memory(group_id, user_id, memory_content)
            await send_group_message(group_id, "好，我长期记住了。需要删除时对我说“忘记我”。")
    elif (group_memory := extract_group_memory(event, text)) is not None:
        if not group_memory:
            await send_group_message(group_id, "要记住什么？例如：@我 记住，hzh 是 Cat#")
        elif SENSITIVE_MEMORY_PATTERN.search(group_memory):
            await send_group_message(group_id, "这段像是敏感信息，我就不存啦。")
        else:
            await request.app.state.db.add_group_memory(group_id, group_memory)
            await send_group_message(group_id, "记住了 (｀・ω・´)")
    elif text in GROUP_MEMORY_LIST_COMMANDS and (text.startswith("/") or bot_mentioned(event)):
        memories = await request.app.state.db.group_memories(group_id)
        if memories:
            await send_group_message(group_id, "群记忆：\n" + "\n".join(f"- {item}" for item in memories))
        else:
            await send_group_message(group_id, "本群还没有共享记忆。")
    elif text in GROUP_MEMORY_CLEAR_COMMANDS and (text.startswith("/") or bot_mentioned(event)):
        if is_admin:
            await request.app.state.db.clear_group_memories(group_id)
            await send_group_message(group_id, "本群共享记忆已清空。")
        else:
            await send_group_message(group_id, "只有群管理员可以清除群记忆。")
    elif text in POSSESSION_EXIT_COMMANDS and (text.startswith("/") or bot_mentioned(event)):
        possession = await request.app.state.db.daily_possession(group_id, today)
        if not possession:
            if await request.app.state.db.possession_exited(group_id, today):
                await send_group_message(group_id, "今天已经退出夺舍了，明天会自动恢复抽取。")
            else:
                await send_group_message(group_id, "今天还没有开始夺舍。")
        elif user_id == possession[0] or is_admin:
            await request.app.state.db.exit_daily_possession(group_id, today, user_id)
            await send_group_message(group_id, "已退出今天的夺舍状态，明天会自动恢复 ( ´▽｀)")
        else:
            await send_group_message(group_id, "只有今天被抽中的群友或群管理员可以退出夺舍。")
    elif text in POSSESSION_STATUS_COMMANDS and (text.startswith("/") or bot_mentioned(event)):
        if await request.app.state.db.possession_exited(group_id, today):
            await send_group_message(group_id, "今日夺舍状态：已退出，明天自动恢复。")
        else:
            possession = await request.app.state.db.daily_possession(group_id, today)
            if possession:
                mode_name = "指向" if possession[2] == "targeted" else "随机"
                await send_group_message(
                    group_id,
                    f"现在是“{possession[1]}”，{mode_name}中 (｀・ω・´)",
                )
            else:
                await send_group_message(group_id, "今日夺舍状态：尚未抽取。")
    elif bot_mentioned(event) and asks_for_sender_name(text):
        await send_group_message(group_id, f"你的名字是“{sender_display_name(event)}”。")
    elif bot_mentioned(event) and is_identity_question(text):
        if await request.app.state.db.possession_exited(group_id, today):
            await send_group_message(
                group_id,
                f"今天的夺舍已经退出啦，现在我是机器人“{settings.persona_name}”。",
            )
        else:
            possession = await request.app.state.db.daily_possession(group_id, today)
            if possession:
                _, name, mode = possession
                mode_name = "指向夺舍" if mode == "targeted" else "随机夺舍"
                await send_group_message(
                    group_id,
                    f"我现在是“{name}”，{mode_name}中 (｀・ω・´)",
                )
            else:
                await send_group_message(
                    group_id,
                    f"我现在是机器人“{settings.persona_name}”，今天还没有进入夺舍状态。",
                )
    elif text in TARGETED_POSSESSION_COMMANDS and (text.startswith("/") or bot_mentioned(event)):
        if await request.app.state.db.possession_exited(group_id, today):
            await send_group_message(group_id, "今天已经退出夺舍了，明天会自动恢复。")
        else:
            targets = mentioned_user_ids(event)
            if len(targets) != 1:
                await send_group_message(group_id, "用法：@我 指向夺舍 @一名群成员")
            else:
                target_id = targets[0]
                try:
                    name = await group_member_name(group_id, target_id)
                except (RuntimeError, ValueError, httpx.HTTPError) as exc:
                    logger.warning("group member lookup failed: %s", exc)
                    name = await request.app.state.db.member_display_name(group_id, target_id)
                if not name:
                    await send_group_message(group_id, "没有查到这名群成员，请确认对方仍在群里。")
                else:
                    await request.app.state.db.set_targeted_possession(
                        group_id, today, target_id, name
                    )
                    await send_group_message(
                        group_id,
                        f"夺舍成功，我现在是“{name}” (｀・ω・´)\n"
                        f"{name}本人或管理员可发送“@我 退出”。",
                    )
    elif text in RANDOM_POSSESSION_COMMANDS and (text.startswith("/") or bot_mentioned(event)):
        if await request.app.state.db.possession_exited(group_id, today):
            await send_group_message(group_id, "今天已经退出夺舍了，明天会自动恢复抽取。")
            return {"ok": True}
        possession = await request.app.state.db.get_or_create_daily_possession(group_id, today)
        if possession:
            _, name, _ = possession
            await send_group_message(
                group_id,
                f"本群今日乐子：{name}！我现在是“{name}” (｀・ω・´)\n"
                f"{name}本人或管理员可发送“@我 退出”。",
            )
        else:
            await send_group_message(group_id, "今天还没有候选人：群友当天发言达到 15 条才会加入随机抽取。")
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
    elif (search_query := extract_search_query(event, text)) is not None:
        if not search_query:
            await send_group_message(group_id, "想搜什么？例如：@我 搜索 Python 3.13 新特性")
        else:
            try:
                results = await search_web(search_query)
                if not results:
                    await send_group_message(group_id, "这次没有搜到可靠结果，换个关键词试试吧。")
                else:
                    evidence = "\n\n".join(
                        f"标题：{item.title}\n摘要：{item.snippet}\n网址：{item.url}"
                        for item in results
                    )
                    messages = [
                        {
                            "role": "system",
                            "content": settings.persona_prompt()
                            + "\n你正在根据联网搜索结果回答。只使用给定结果，无法确认的内容要说明；"
                            "回答简洁，不要编造网址。",
                        },
                        {
                            "role": "user",
                            "content": f"问题：{search_query}\n\n搜索结果：\n{evidence}",
                        },
                    ]
                    try:
                        summary = await request.app.state.llm.ask(messages)
                        reply = f"{summary[:1300]}\n\n来源：\n{format_search_sources(results)}"
                    except (LLMError, httpx.HTTPError):
                        reply = "搜到这些结果：\n" + format_search_sources(results)
                    await send_group_message(group_id, reply[:2000])
            except (ValueError, RuntimeError, httpx.HTTPError) as exc:
                logger.warning("web search failed: %s", exc)
                await send_group_message(group_id, "联网搜索暂时不可用，稍后再试一下吧。")
    elif text.startswith(("/ai ", "/AI ")) or (bot_mentioned(event) and text):
        prompt = text.split(" ", 1)[1].strip() if text.startswith(("/ai ", "/AI ")) else text
        memory_enabled = await request.app.state.db.memory_enabled(group_id, user_id)
        long_memories = await request.app.state.db.long_term_memories(group_id, user_id)
        group_memories = await request.app.state.db.group_memories(group_id)
        style_hint = await request.app.state.db.group_style_hint(group_id)
        possession = await request.app.state.db.daily_possession(group_id, today)
        persona = settings.persona_prompt()
        possession_name = ""
        if long_memories:
            persona += "\n\n用户明确要求长期记住的信息：\n" + "\n".join(
                f"- {item}" for item in long_memories
            )
        if group_memories:
            persona += "\n\n本群成员明确要求记住的共享信息：\n" + "\n".join(
                f"- {item}" for item in group_memories
            )
        if style_hint:
            persona += (
                "\n\n本群匿名聚合出的表达风格：" + style_hint
                + "。自然参考即可，不要照搬某个成员，也不要强行使用网络用语。"
            )
        if possession:
            _, name, mode = possession
            possession_name = name
            identity_prompt = possession_identity_prompt(name, mode)
            persona = identity_prompt + "\n\n" + persona + "\n\n" + identity_prompt
        messages = request.app.state.memory.messages(
            group_id, user_id, persona, prompt, memory_enabled
        )
        if possession:
            shared_context = await request.app.state.db.possession_context_messages(
                group_id, today
            )
            messages[1:1] = shared_context
        try:
            answer = await request.app.state.llm.ask(messages)
            if possession_name:
                answer = enforce_possession_identity(answer, possession_name)
                await request.app.state.db.append_possession_exchange(
                    group_id, today, prompt, answer
                )
            request.app.state.memory.append(group_id, user_id, prompt, answer, memory_enabled)
            await send_group_message(group_id, answer[:2000])
        except (LLMError, httpx.HTTPError) as exc:
            logger.warning("LLM request failed: %s", exc)
            await send_group_message(group_id, "我现在有点忙，稍后再试一下吧。")
    return {"ok": True}
