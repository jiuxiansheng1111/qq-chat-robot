"""One person's temporary conversation practice, using the active QQ voice and model."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import wave
from collections import deque
from dataclasses import dataclass, field

from app.astrbot_identity import QQ_PERSONA_PATH
from app.astrbot_silence import is_silence_control_text
from app.services.chat_punctuation import natural_chat_text

_LOG = logging.getLogger(__name__)
PAUSE = re.compile(r"^(?:先)?(?:暂停|停一下|暂停一下|先停停|暂停练习|跳出角色|复盘一下|点评一下|我刚才说得怎么样)(?:一下|陪练|练习)?(?:[，,].*)?[。！？?！吧啊\s]*$")
RESUME = re.compile(r"^(?:那就|可以|我们)?(?:继续|接着来|再来一轮|继续刚才的)(?:练习|练|陪练|面试|说|吧)?[。！？?！啊\s]*$")
EXIT = re.compile(r"^(?:我|我们)?(?:今天)?(?:先)?(?:不(?:想|用)?练了|结束(?:陪练|练习|模拟|面试)|退出(?:陪练|练习|模拟)|聊点别的|练习结束)[吧啦了。！？?！\s]*$")


@dataclass
class Rehearsal:
    scene: str
    spoken: bool = False
    paused: bool = False
    turns: deque = field(default_factory=lambda: deque(maxlen=16))


async def hear(plugin, event, record):
    """Use the session STT if present, otherwise the installed local model."""
    from app.astrbot_soundtry import STATE, analyze_file, attachment_path, collect_attachments

    settings = plugin.runtime._core.settings
    parts = collect_attachments(event)
    index = next(i for i, part in enumerate(parts) if part is record or part == record)
    path = await attachment_path(event, index, settings)
    try:
        with wave.open(str(path)) as source:
            if source.getnframes() / source.getframerate() > 90:
                return "", "这条语音有点长，能把想练的那一段单独发我吗？"
        get_stt = getattr(plugin.context, "get_using_stt_provider_async", None)
        provider = await get_stt(event.unified_msg_origin) if get_stt else None
        if provider:
            try:
                text = await asyncio.wait_for(provider.get_text(audio_url=str(path)), 20)
                if text and text.strip():
                    return text.strip(), ""
            except Exception as exc:  # noqa: BLE001 -- the local recognizer can still read this audio.
                _LOG.info("陪练使用本地语音识别（%s）", type(exc).__name__)
        if (STATE / "models/medium/model.bin").is_file():
            result = await asyncio.wait_for(analyze_file(path, "speech", settings), 100)
            text = "".join(str(c.get("text") or "").strip() + " " for c in result["data"].get("cues", [])).strip()
            if text:
                return text, ""
        return "", "这句我没听清，能再说一遍，或者把原话打给我吗？"
    finally:
        # attachment_path returns its own normalized WAV, never the original.
        path.unlink(missing_ok=True)


async def reply_chain(plugin, event, text, spoken):
    from astrbot.api.message_components import Plain, Record

    if spoken:
        from app.astrbot_singing import selected_voice
        from app.services.voice import synthesize_voice

        try:
            core = plugin.runtime._core
            settings, profile, bot = await selected_voice(core, event)
            result = await asyncio.wait_for(synthesize_voice(text, settings, profile, bot_self_id=bot), 70)
            if result:
                return [Record(file=result, text=text)]
        except Exception as exc:  # noqa: BLE001 -- keep the generated reply if synthesis fails.
            _LOG.info("本轮陪练保留文字（%s）", type(exc).__name__)
    return [Plain(text)]


async def rehearse(plugin, event, session, text, *, opening=False):
    if EXIT.fullmatch(text):
        return "这轮练习先收在这里", True
    review = bool(PAUSE.fullmatch(text))
    if review:
        session.paused = True
    elif RESUME.fullmatch(text):
        session.paused = False
    latest = next((s["content"] for s in reversed(session.turns) if s["role"] == "user"), "")
    if review and not latest:
        return "先停一下。你还没说第一句，想从哪句话开始试？", False
    if session.paused and not review:
        # A paused rehearsal accepts coaching questions; unrelated chat is allowed
        # to pass through at the ingress, and resume is always explicit.
        mode = "回答对方这次关于练习的追问，结合真实练过的原话。留在场外。"
    elif review:
        mode = "跳出对手角色，挑刚才这句原话的一个具体问题，给一种贴近对方平时说法的改写。"
    else:
        mode = ("进入对方指定的练习情境，扮演谈话另一方，直接给出对方可以接的一句话。"
                "场景或双方角色不清时，只问最需要弄清的那件事。每次只演你这一方的一轮，让真人自己回答。")
    async with asyncio.timeout(55):
        provider = await plugin.context.get_using_provider_async(event.unified_msg_origin)
        response = await provider.text_chat(
            system_prompt=QQ_PERSONA_PATH.read_text(encoding="utf-8-sig") + "\n这是受邀进行的一次对话练习。" + mode,
            prompt=json.dumps({"场景和角色": session.scene, "本轮": text, "开场": opening,
                               **({"刚才实际说的原话": latest} if review else {})}, ensure_ascii=False),
            contexts=list(session.turns))
    answer = natural_chat_text(str(response.completion_text or "").strip())
    if not answer or is_silence_control_text(answer):
        raise ValueError("empty practice turn")
    if not session.paused:
        if not opening and not RESUME.fullmatch(text):
            session.turns.append({"role": "user", "content": text[:1800]})
        session.turns.append({"role": "assistant", "content": answer[:1800]})
    return answer, False
