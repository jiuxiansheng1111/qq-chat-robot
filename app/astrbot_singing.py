"""QQ singing uses the installed cover engine and the existing serial queue."""
from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import Mapping
from copy import copy

from app.astrbot_qq_persona import is_qq
from app.services.qq_voice import qq_voice_settings
from app.services.singing import (
    archive_singing_job,
    cleanup_singing_job,
    generate_singing_cover,
    parse_singing_command,
    runtime_paths,
)
from app.services.singing_jobs import QueueError, SingingError
from app.services.voice import resolve_character_profile, voice_profile_authorized, voice_profiles

_LOGGER = logging.getLogger(__name__)
_MARKER = "【当前会话实际声音能力】"
_CAPABILITY_QUESTION = re.compile(
    r"(?:你)?(?:会不会|能不能|可不可以|会|能|可以)(?:唱歌|唱)(?:吗|么|嘛|不)?[啊呀呢？?！!。~～\s]*"
)


async def selected_voice(core, event):
    settings = qq_voice_settings(core.settings)
    raw = getattr(getattr(event, "message_obj", None), "raw_message", {}) or {}
    profile = settings.voice_profile_default
    if raw.get("group_id"):
        profile = await core.app.state.db.voice_profile(str(raw["group_id"]), str(raw.get("user_id", "")), profile)
    return settings, profile, str(raw.get("self_id", ""))


def _addressed_text(event):
    from app.astrbot_group_chat import _segment_data, _segments, _strip_name
    from app.astrbot_listening import ALIASES
    text, named = _strip_name(event.get_message_str().strip(), ALIASES)
    if not event.get_group_id():
        return text, True
    raw = getattr(getattr(event, "message_obj", None), "raw_message", {}) or {}
    self_id = str(raw.get("self_id", ""))
    targets = [str(_segment_data(s).get("qq", "")) for s in _segments(raw) if s.get("type") == "at"]
    replies = [str(getattr(p, "sender_id", "")) for p in getattr(event.message_obj, "message", ())
               if type(p).__name__ == "Reply" and getattr(p, "sender_id", "")]
    at_bot = bool(self_id and (self_id in targets or self_id in replies))
    if (targets or replies) and not at_bot:
        # A native wake flag or nickname must not redirect somebody else's @.
        return "", False
    return text, bool(at_bot or named or getattr(event, "is_at_or_wake_command", False))


def capability_question(event):
    if not is_qq(event):
        return False
    text, addressed = _addressed_text(event)
    return bool(addressed and _CAPABILITY_QUESTION.fullmatch(text))


def command_for(event, settings):
    """Only explicit addressed commands bypass chat; capability questions do not."""
    if not is_qq(event):
        return None
    from app.astrbot_group_chat import normalize_natural_command
    from app.astrbot_listening import ALIASES
    text, addressed = _addressed_text(event)
    if not addressed and not text.startswith("/"):
        return None
    if _CAPABILITY_QUESTION.fullmatch(text):
        return None
    # Accept the natural imperative before looking for discussion words, so
    # a title such as 《不要说话》 is not mistaken for a negative instruction.
    natural = re.fullmatch(
        r"(?:(?:请|麻烦)(?:你)?[，,\s]*)?(?:(?:给我|帮我|帮忙|替我)(?:再)?)?"
        r"(?:唱一段|唱几句|唱一首|唱整首|唱歌片段|翻唱片段|唱歌完整|翻唱完整|唱歌|翻唱)"
        r"\s*[：:]?\s*《(?P<title>[^《》\n]+)》(?:[，,\s]*(?:好不好|好吗|可以吗|行吗|吧|呗))?[？?！!。~～\s]*", text
    )
    if natural:
        from app.services.singing import SingingCommand
        mode = "clip" if re.search(r"唱一段|唱几句|唱歌片段|翻唱片段", text[:natural.start("title")]) else "full"
        return SingingCommand("sing", natural.group("title").strip(), mode=mode)
    # Discussion/quoting is left to the current model, never a song job.
    if re.search(r"不要|别唱|不想|怎么|如何|是什么|会唱歌吗|能唱歌吗|[“”\"`]|^>", text):
        return None
    text = re.sub(r"^(?:(?:请|麻烦)(?:你)?[，,\s]*)?(?:给我|帮我|帮忙|替我)(?:再)?(?=唱|翻唱)", "", text)
    text = normalize_natural_command(text, names=ALIASES) or text
    return parse_singing_command(text, voice_profiles(settings), addressed=addressed)


def _singing_ready(core, settings):
    if not settings.singing_enabled or not getattr(core.app.state, "singing_jobs", None):
        return False
    try:
        runtime_paths(settings)
    except Exception:  # noqa: BLE001 -- Voice pipelines have backend-specific readiness failures.
        return False
    return True


async def prepare_audio_capabilities(core, event, request):
    if not is_qq(event):
        # Registration is global in AstrBot. Do not expose this new QQ-only
        # action to Bilibili/WeChat, or mutate their shared tool manager.
        original = getattr(request, "func_tool", None)
        if callable(getattr(original, "get_tool", None)) and original.get_tool("qqchat_sing") is not None:
            tools = copy(original)
            tools.tools = list(original.tools)
            tools.remove_tool("qqchat_sing")
            request.func_tool = tools
        return
    if core is None or _MARKER in str(getattr(request, "system_prompt", "")):
        return
    settings, profile, self_id = await selected_voice(core, event)
    profiles = voice_profiles(settings)
    allowed = {key: value for key, value in profiles.items() if voice_profile_authorized(settings, key, self_id)}
    tools = getattr(request, "func_tool", None)
    get_tool = getattr(tools, "get_tool", lambda name: None)
    def enabled_tool(name):
        tool = get_tool(name)
        return tool is not None and getattr(tool, "active", True)
    voice_ready = bool(settings.voice_enabled and enabled_tool("qqchat_send_voice") and
                       profile in allowed and allowed[profile].get("tts_enabled") is not False)
    sing_ready = bool(enabled_tool("qqchat_sing") and _singing_ready(core, settings))
    singing_voices = [str(v.get("label") or key) for key, v in allowed.items() if v.get("singing_enabled") is not False]
    state = {"语音可调用": voice_ready, "唱歌可调用": sing_ready and bool(singing_voices),
             "当前音色": str(allowed.get(profile, {}).get("label") or "未选择"),
             "当前音色可唱歌": sing_ready and profile in allowed and allowed[profile].get("singing_enabled") is not False}
    request.system_prompt = str(getattr(request, "system_prompt", "") or "") + "\n" + _MARKER + "\n" + json.dumps(state, ensure_ascii=False) + (
        "\n这是当前QQ真实工具与音色状态，优先于旧历史中对能力的猜测。问会不会唱时按此自然回答，"
        "缺歌名就问想听什么，不为能力询问启动任务。明确点唱且歌名清楚时用qqchat_sing，"
        "朗读或普通语音用qqchat_send_voice，不拿朗读歌词冒充演唱。唱歌生成期间可继续聊天，"
        "以实际结果确认完成。仅某个音色不可用不等于整体不会唱；必要时用工具查可用音色。"
        "这些是内部调用依据，不向聊天对象复述配置、工具名或后台信息。"
    )


async def singing_action(plugin, event, *, action="sing", query="", voice="", mode="full"):
    if not is_qq(event) or plugin.runtime is None:
        return {"status": "unavailable"}
    raw = getattr(getattr(event, "message_obj", None), "raw_message", {})
    if not isinstance(raw, Mapping) or not await plugin._legacy_event_allowed(raw):
        return {"status": "denied"}
    core = plugin.runtime._core
    settings, profile, self_id = await selected_voice(core, event)
    manager = getattr(core.app.state, "singing_jobs", None)
    scope = str(event.unified_msg_origin)
    key = (self_id, scope, str(raw.get("user_id", "")))
    if not all(key) or manager is None:
        return {"status": "unavailable"}
    profiles = {k: v for k, v in voice_profiles(settings).items() if voice_profile_authorized(settings, k, self_id)}
    if action == "voices":
        return {"status": "ok", "voices": [str(v.get("label") or k) for k, v in profiles.items() if v.get("singing_enabled") is not False]}
    if action == "status":
        job = manager.status(key)
        return {"status": "ok", "job": {"song": job.query, "state": job.status} if job else None}
    if action == "cancel":
        return {"status": "cancelled" if await manager.cancel(key) else "no_active_job"}
    if action != "sing" or mode not in {"full", "clip"}:
        return {"status": "invalid_arguments"}
    if not settings.singing_enabled:
        return {"status": "disabled", "message": "这次唱歌没有开启"}
    if not query.strip():
        return {"status": "needs_song", "message": "还没有明确要唱哪首歌，自然问歌名"}
    if len(query) > 160:
        return {"status": "invalid_arguments", "message": "只需要歌名、歌手或歌曲链接"}
    if voice:
        profile = resolve_character_profile(settings, voice, self_id)
    if profile not in profiles or profiles[profile].get("singing_enabled") is False:
        return {"status": "voice_unavailable", "message": "这次选择的音色暂时不能唱，可先查其他可唱音色，不等于没有语音或不会唱歌"}
    # An exact capability question cannot authorize inventing a song to perform.
    if re.fullmatch(r".*?(?:你会唱歌吗|你能唱歌吗|会不会唱歌|能不能唱歌)[？?！!\s]*", event.get_message_str()):
        return {"status": "needs_song", "message": "对方只是询问唱歌能力，尚未点歌，不启动演唱"}
    try:
        runtime_paths(settings)
        job = manager.submit(key, query.strip(), lambda job: _run(plugin, event, job, settings, profile, self_id, mode))
    except QueueError:
        return {"status": "busy", "message": "已有演唱或等待中的请求，这次没有新接单"}
    except Exception as exc:  # noqa: BLE001 -- Translate backend startup failures into a command response.
        _LOGGER.warning("QQ唱歌启动检查未通过 error_type=%s", type(exc).__name__)
        return {"status": "unavailable", "message": "本次演唱未能开始"}
    from app.astrbot_analysis_delivery import defer_analysis_reply
    defer_analysis_reply(event, job.id)
    return {"status": "accepted", "job_id": job.id, "message": "已接收，后台完成后直接发送歌声，当前不发开始或进度回执，不再提交同一任务"}


async def _run(plugin, event, job, settings, profile, self_id, mode):
    from astrbot.api.message_components import Plain, Record

    from app.astrbot_failure_reply import Failure, describe_failure, install_send_guard
    from app.services.owner_memory import record_assistant
    install_send_guard(plugin.context, event)
    delivered = 0
    sending = False
    title = job.query

    async def progress(value):
        job.progress = value

    async def send_chunk(chunk, name, index, total):
        nonlocal delivered, sending, title
        if not await plugin._legacy_event_allowed(getattr(event.message_obj, "raw_message", None)):
            raise SingingError("原会话已关闭，未继续发送")
        title = name
        label = f"《{name}》" + (f" {index}/{total}" if total > 1 else "")
        sending = True
        await event.send(event.chain_result([Plain(label), Record(file=str(chunk.path.resolve()), text=label)]))
        delivered += 1
        sending = False
        job.progress = f"已送达 {delivered}/{total} 段"
        if index < total:
            await asyncio.sleep(settings.singing_segment_pause_seconds)

    async def on_chunk(part):
        await send_chunk(part.chunk, part.song.track.title, part.index, part.total)

    try:
        async with asyncio.timeout(settings.singing_job_timeout_seconds):
            cover = await generate_singing_cover(job.query, profile, job.id, settings, progress,
                bot_self_id=self_id, mode=mode, on_chunk=on_chunk)
            if not delivered:
                for index, chunk in enumerate(cover.chunks, 1):
                    await send_chunk(chunk, cover.song.track.title, index, len(cover.chunks))
            if not delivered:
                raise SingingError("没有可发送的歌声")
        # All ACKs are established before optional memory work.
        try:
            await asyncio.to_thread(record_assistant, event, f"演唱并发送《{title}》{'片段' if mode == 'clip' else ''}", source="singing")
        except Exception as exc:  # noqa: BLE001 -- Archival must not change delivery status.
            _LOGGER.warning("主人演唱记录未归档 error_type=%s", type(exc).__name__)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        _LOGGER.exception("QQ唱歌未完成 job=%s sent_parts=%s", job.id, delivered)
        kind = "uncertain" if sending else "timeout" if isinstance(exc, TimeoutError) else "unavailable"
        feature = "这次演唱" + (f"的后续部分，前{delivered}段已送出" if delivered else "")
        text = await describe_failure(plugin.context, event, Failure(feature, kind))
        if await plugin._legacy_event_allowed(getattr(event.message_obj, "raw_message", None)):
            try:
                await event.send(event.plain_result(text))
            except Exception:  # noqa: BLE001 -- The adapter may already have sent the notice.
                _LOGGER.warning("QQ唱歌最终说明未确认送达 job=%s", job.id)
        raise SingingError(text) from exc
    finally:
        try:
            if delivered:
                await asyncio.to_thread(archive_singing_job, job.id)
        except Exception:  # noqa: BLE001 -- Clean up the job even if the optional archive failed.
            _LOGGER.warning("QQ唱歌文件未归档 job=%s", job.id)
        await asyncio.to_thread(cleanup_singing_job, job.id)


async def handle_command(plugin, event):
    core = getattr(plugin.runtime, "_core", None)
    if not is_qq(event) or core is None or getattr(event, "is_stopped", lambda: False)():
        return False
    if capability_question(event):
        raw = getattr(getattr(event, "message_obj", None), "raw_message", {})
        if not isinstance(raw, Mapping) or not await plugin._legacy_event_allowed(raw):
            return False
        settings, profile, self_id = await selected_voice(core, event)
        profiles = voice_profiles(settings)
        available = _singing_ready(core, settings) and any(
            p.get("singing_enabled") is not False and voice_profile_authorized(settings, name, self_id)
            for name, p in profiles.items()
        )
        current = available and profile in profiles and profiles[profile].get("singing_enabled") is not False and voice_profile_authorized(settings, profile, self_id)
        text = ("会呀，想听本鱼唱哪首？" if current else
                "会唱，不过得换个能唱的音色，你想听哪首？" if available else
                "本鱼这会儿还唱不了，暂时没法把歌声发给你")
        await event.send(event.plain_result(text))
        event.should_call_llm(False)
        event.stop_event()
        _LOGGER.info("QQ唱歌能力询问已按本地状态回答 available=%s current_voice_ready=%s", available, bool(current))
        return True
    command = command_for(event, qq_voice_settings(core.settings))
    if command is None:
        return False
    result = await singing_action(plugin, event, action=command.action, query=command.query,
                                  voice=command.profile_id or "", mode=command.mode)
    if result["status"] != "accepted":
        from app.astrbot_failure_reply import Failure, describe_failure
        state = result["status"]
        if state == "ok" and "voices" in result:
            text = "可以唱的音色有：" + "、".join(result["voices"])
        elif state == "ok":
            job = result["job"]
            states = {"queued": "还在等前一首唱完", "running": "还没唱完，先陪你聊着", "succeeded": "歌声已经发出去了", "failed": "这次没唱成", "cancelled": "已经停下来了"}
            text = states.get(job["state"], "还没有结果") if job else "本鱼现在没有等着唱的歌"
        elif state == "needs_song":
            text = "想听本鱼唱哪首？"
        elif state in {"cancelled", "no_active_job"}:
            text = "好的，我知道了" if state == "cancelled" else "本鱼现在没在唱啦"
        else:
            kind = state if state in {"busy", "disabled", "denied"} else "unavailable"
            text = await describe_failure(plugin.context, event, Failure("这次演唱", kind))
        await event.send(event.plain_result(text))
    event.should_call_llm(False)
    event.stop_event()
    return True
