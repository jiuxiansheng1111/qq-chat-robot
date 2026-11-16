"""Natural QQ turns for comparing pictures, rehearsing and catching up with a group."""

from __future__ import annotations

import asyncio
import logging
import re
import time
from collections import OrderedDict, deque
from dataclasses import dataclass, field

from app import astrbot_chat_actions as actions
from app import astrbot_game_companion as games
from app.astrbot_compare import compare, image_refs
from app.astrbot_group_chat import (
    _COMMAND,
    _extra,
    _human_message_text,
    _segment_data,
    _segments,
    _strip_name,
)
from app.astrbot_listening import ALIASES
from app.astrbot_qq_persona import is_qq
from app.astrbot_rehearsal import EXIT, PAUSE, RESUME, Rehearsal, hear, rehearse, reply_chain

_LOG = logging.getLogger(__name__)
_PAIR = re.compile(r"(?:两|二|2|几|这组|前后|新旧|原|参考|对照)(?:张|幅|个|版)?(?:的)?(?:图|照片|截图|海报|稿|版本)|(?:原图|旧版).{0,15}(?:新|改)")
_COMPARE = re.compile(r"对比|对照|比一比|比较|圈(?:一下|一圈|出来|出)|标(?:出来|一下)|找(?:不同|区别)|变(?:了|化)|改(?:在|哪|动|了)|区别|差别|差异|不同|一样吗|哪个|哪张|更像|更高|变低|低了|是不是|看得出")
_LOOK = re.compile(r"(?:看下|看看|看一下|改了[一新]?版|改了稿|出[了个]*新版)")
_VISUAL_REF = re.compile(r"图[里中上]|这[两俩张幅版]|[ABab]\s*图|画面|刚(?:刚|才)发|前后.{0,4}版|尾巴|尾鳍|刘海|轮廓|配色|底色|构图|海报|截图|原稿|新版")
_RELATIVE = re.compile(r"对比|对照|比较|比(?:上|之|以|原|刚|前)|和(?:之前|刚才|原图)|改了哪|圈出.{0,3}(?:不同|变化)|找不同|差别|区别")
_PRACTICE = re.compile(r"(?:陪我|跟我|和我|帮我|咱们|我们|想|能不能|可以|来个|来一[轮次场]|要不).{0,12}(?:练练|练一?下|练习|模拟|演练|对练|练口语|练面试|练对话)|(?:模拟面试|语音陪练|对话陪练|口语陪练|陪我练)|(?:你(?:来|当|扮演)|假装你是).{0,8}(?:面试官|老板|客户|同事|室友|店员|老师|考官)")
_NERVOUS = re.compile(r"(?:面试|道歉|开口|谈薪|答辩|跟.{1,8}谈).{0,12}(?:紧张|没底|没把握|怕说不好|不知道怎么说)|(?:紧张|没底|不知道怎么).{0,10}(?:面试|开口|道歉)")
_RECAP = re.compile(r"(?:刚回[来群]|刚上线|没看群|爬楼|群里|群聊|你们.{0,3}刚才|我不在).{0,22}(?:错过|聊(?:了|到|啥|什么)|重点|定(?:了|下)|安排|进展|等我|发生|说(?:了|到)|补课)|(?:我|刚才)错过(?:了)?(?:什么|啥|哪些)|(?:捋|补|说|理).{0,6}(?:群聊重点|群里.{0,4}进展)|(?:今晚|今天|明天|这事|活动|聚会).{0,5}(?:到底|最后|最终|定下来).{0,5}(?:安排|定|约|几点|怎么)|(?:群里|刚才|现在|还有).{0,10}(?:等我确认|需要我确认|要我拍板)|所以.{0,8}(?:定了没|怎么安排|几点集合)")
_RETURN = re.compile(r"^(?:我)?(?:刚回来|刚回群|刚上线)(?:了|啦|啊)?[。！!\s]*$")
_OWED = re.compile(r"(?:谁|有没有人|有人|什么事|哪些事).{0,5}(?:在)?等我(?:确认|回[话复]|决定|拍板)")
_NEGATIVE = re.compile(r"(?:别|先别|不想|不用|不要|不需要|不打算|不打扰).{0,7}(?:对比|看图|比较|练|模拟|总结|补重点)|^(?:听说|据说|有人说|他说|她说|[>“「\x60])|(?:功能|指令|命令).{0,8}(?:怎么用|叫什么)|^(?:你)?(?:会不会|支持|会).{0,8}(?:陪练|看图|总结).{0,2}吗")
_YES = re.compile(r"^(?:可以(?:啊|呀)?|好(?:啊|呀|的|吧)?|嗯(?:嗯)?|行(?:啊)?|要(?:啊)?|来(?:吧)?|帮我看看|那就试试|试试)[。！!～~\s]*$")
_NO = re.compile(r"^(?:不了|不用(?:了)?|算了|下次吧|先不用|不需要|不要|先?别(?:比|看|练|总结)了|(?:不用|不要)(?:对比|比较|练|总结)了)[。！!\s]*$")
_COACH_QUESTION = re.compile(r"刚[才刚]|那句|我的回答|我说的|措辞|怎么(?:改|说|回答)|换个说法|举个例子|点评|改[一下改]")
_SCENE = re.compile(r"面试|道歉|谈薪|口语|英语|日语|汇报|答辩|表白|拒绝|开口|同事|老板|客户|室友|领导|家[人长]|老师|考官|店员|扮演|你当|练.{0,4}(?:怎么|如何)|和.{1,12}(?:说|聊|谈)")


@dataclass
class _Pending:
    action: str
    question: str
    expires: float
    refs: tuple = ()


@dataclass
class _TurnState:
    touched: float
    images: deque = field(default_factory=lambda: deque(maxlen=8))
    seen: deque = field(default_factory=lambda: deque(maxlen=24))
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    pending: _Pending | None = None
    practice: Rehearsal | None = None
    practice_at: float = 0
    dialog: actions.Dialog | None = None
    last_reminder: tuple | None = None
    game: games.GameTurn | None = None


def _key(event):
    raw = event.message_obj.raw_message
    return (getattr(event, "get_platform_id", lambda: "")(), str(raw.get("self_id") or ""),
            str(raw.get("group_id") or "private"), str(raw.get("user_id") or ""))


def _target(event, raw, use_quoted, named):
    if raw.get("message_type") == "private":
        return True, True
    bot = str(raw.get("self_id") or "")
    targets = {str(_segment_data(s).get("qq")) for s in _segments(raw) if s.get("type") == "at"}
    replies = {str(p.sender_id) for p in getattr(event.message_obj, "message", ()) or ()
               if getattr(p, "sender_id", None) and type(p).__name__ == "Reply"}
    direct = bot in targets or bot in replies
    # QQ can attach an @ of the original speaker to a reply. The user's explicit
    # request about the quote still belongs here; an unrelated @ keeps priority.
    if targets and not direct and not (use_quoted and targets == replies):
        return False, False
    if replies and not direct and not use_quoted:
        return False, False
    return True, direct or named


def _accept_pair(refs, question):
    if len(refs) == 2:
        return refs
    if len(refs) > 2:
        indices = re.findall(r"第?\s*([一二三四五六七八1-8])\s*(?=张|幅|和|跟|与)", question)
        if len(indices) == 2:
            numbers = [int(n) if n.isdigit() else "一二三四五六七八".index(n) + 1 for n in indices]
            if numbers[0] != numbers[1] and max(numbers) <= len(refs):
                return [refs[i - 1] for i in numbers]
    return []


class CompanionTurns:
    def __init__(self, *, clock=time.monotonic):
        self.clock = clock
        self.sessions = OrderedDict()
        self.games = games.GameCompanion()
        self.meetups = None
        self.followups = None

    def start_followups(self, plugin):
        root = getattr(getattr(plugin, "runtime", None), "project_root", None)
        if root is not None:
            from app.astrbot_followups import Followups

            if self.followups is None:
                self.followups = Followups(root)
            self.followups.start(plugin)

    async def stop_followups(self):
        if self.followups is not None:
            await self.followups.close()
            self.followups = None

    async def _social_reply(self, plugin, event, text, features, addressed, group):
        root = getattr(getattr(plugin, "runtime", None), "project_root", None)
        if root is None:
            return None
        if features is None or "followups" in features:
            if self.followups is None:
                self.start_followups(plugin)
            reply = await self.followups.handle(plugin, event, text, addressed=addressed)
            if reply is not None:
                return reply
        if group and (features is None or "meetups" in features):
            if self.meetups is None:
                from app.astrbot_meetups import Meetups

                self.meetups = Meetups(root)
            return await self.meetups.handle(event, text)
        return None

    def _state(self, key, now):
        for k, state in list(self.sessions.items()):
            if not state.lock.locked() and (now - state.touched > 1200 or (len(self.sessions) >= 256 and key not in self.sessions)):
                self.sessions.pop(k)
        state = self.sessions.setdefault(key, _TurnState(now))
        self.sessions.move_to_end(key)
        state.touched = now
        if now - state.practice_at > 1200:
            state.practice = None
        if state.pending and now >= state.pending.expires:
            state.pending = None
        while state.images and now - state.images[0][0] > 360:
            state.images.popleft()
        return state

    def voice_off(self, event):
        state = self.sessions.get(_key(event))
        if state and state.practice:
            state.practice.spoken = False

    async def handle(self, plugin, event, *, features=None):
        raw = getattr(getattr(event, "message_obj", None), "raw_message", None)
        if (not isinstance(raw, dict) or not is_qq(event) or raw.get("message_type") not in {"private", "group"}
                or not raw.get("user_id") or not raw.get("self_id") or str(raw["user_id"]) == str(raw["self_id"])):
            return False
        text, named = _strip_name(_human_message_text(event).strip(), ALIASES)
        if text.startswith("/") or _extra(event, _COMMAND):
            return False
        quoted, current = image_refs(event)
        now = self.clock()
        existing = self.sessions.get(_key(event))
        use_quoted = (quoted and _COMPARE.search(text) or actions.uses_quote(text, existing and existing.dialog, now))
        visible, addressed = _target(event, raw, use_quoted, named)
        if not visible:
            return False
        group = raw.get("message_type") == "group"
        if group and not plugin._get_group_router().allows_companion_turn(event, addressed):
            return False
        state = self._state(_key(event), self.clock())
        mid = str(raw.get("message_id") or "")
        async with state.lock:
            if mid and any(m == mid for m, _ in state.seen):
                handled = next(used for m, used in state.seen if m == mid)
                if handled:
                    event.should_call_llm(False)
                    event.stop_event()
                return handled
            now = self.clock()
            previous = [ref for _, ref in state.images]
            state.images.extend((now, ref) for ref in current[:8])
            refs = quoted + current
            if len(refs) < 2:
                if refs:
                    refs = [*previous[-1:], *refs] if not quoted else list(quoted)
                else:
                    refs = previous[-2:]
            pending = state.pending
            if pending and now >= pending.expires:
                state.pending = pending = None
            if not (pending or state.dialog and state.dialog.expires > now or state.practice):
                answer = await self._social_reply(plugin, event, text, features, addressed, group)
                if answer is not None:
                    if mid:
                        state.seen.append((mid, True))
                    event.should_call_llm(False)
                    event.stop_event()
                    if answer:
                        from astrbot.api.message_components import Plain

                        await event.send(event.chain_result([Plain(answer)]))
                    return True
            if features is not None and features <= {"meetups", "followups"}:
                return False
            if pending and pending.refs:
                refs = [*pending.refs, *current] if not quoted else [*quoted, *current]
            record = next((p for p in getattr(event.message_obj, "message", ()) or ()
                           if type(p).__name__ == "Record"), None)
            spoken = record is not None or any(s.get("type") == "record" for s in _segments(raw))
            hearing_error = ""
            if record is not None and (features is None or "practice" in features or "draft" in features or "remind" in features):
                try:
                    transcript, hearing_error = await asyncio.wait_for(hear(plugin, event, record), 135)
                except Exception as exc:  # noqa: BLE001 -- unread speech must never become a practice turn.
                    _LOG.info("陪练语音未读到（%s）", type(exc).__name__)
                    hearing_error = "这条语音我没听清，能再说一遍，或者把原话打给我吗？"
                if (not hearing_error and group and not (addressed or state.practice or pending or state.dialog)
                        and not actions.uses_quote(text + transcript, None, now)):
                    if mid:
                        state.seen.append((mid, False))
                    return False
                if not hearing_error:
                    from astrbot.api.message_components import Plain

                    # Also make ordinary spoken questions usable by native chat.
                    text = " ".join([*(str(_segment_data(s).get("text", "")) for s in _segments(raw)
                                       if s.get("type") == "text"), transcript]).strip()
                    text, _ = _strip_name(text, ALIASES)
                    event.message_str = text
                    event.message_obj.message_str = text
                    parts = event.message_obj.message
                    parts[parts.index(record)] = Plain(transcript)
                elif not (addressed or state.practice or pending or state.dialog or actions.uses_quote(text, None, now)):
                    hearing_error = ""
            mentions_pair = _PAIR.search(text)
            compare_sources = (mentions_pair or len(current) + len(quoted) >= 2
                               or (current or quoted) and _RELATIVE.search(text)
                               or previous and _VISUAL_REF.search(text) and _RELATIVE.search(text))
            ask_pair = bool(_COMPARE.search(text) and compare_sources or mentions_pair and _LOOK.search(text))
            action = ""
            question = text
            has_quote = any(type(p).__name__ == "Reply" and str(getattr(p, "sender_id", "")) != str(raw["self_id"])
                            for p in getattr(event.message_obj, "message", ()) or ())
            chat_action = actions.action_for(text, state, now, has_material=bool(current or quoted or has_quote))
            if not chat_action and (features is None or "remind" in features) and actions.mentions_completion(text):
                # Explicitly requested reminders survive session expiry and plugin restarts.
                try:
                    if await actions.outstanding(plugin, event):
                        chat_action = "complete"
                except Exception as exc:  # noqa: BLE001 -- let ordinary conversation proceed if storage is unavailable.
                    _LOG.info("未读取到当前会话待办（%s）", type(exc).__name__)
            game_action = games.intent(text, state.game, now, self.games.active_tables(event, now),
                                       active_minecraft=self.games.playing_minecraft(event))
            if features is not None:
                dialog_feature = "draft" if state.dialog and state.dialog.kind in {"draft", "revision"} else "remind"
                if chat_action and {"dismiss": dialog_feature, "complete": "remind", "save": "remind"}.get(chat_action, chat_action) not in features:
                    chat_action = ""
                if game_action and (game_action.game not in features if game_action.game else
                                    not features.intersection(games.NAMES)):
                    game_action = None
            if hearing_error:
                action, question = "unheard", hearing_error
            elif chat_action and (not game_action or actions.uses_quote(text, None, now)
                                  or chat_action in {"save", "complete", "dismiss"}):
                action = chat_action
                refs = actions.action_images(text, quoted, current, previous)
            elif game_action:
                action = "game"
            elif pending and _NO.fullmatch(text):
                state.pending = None
                action = "decline"
            elif pending and (_YES.fullmatch(text) or pending.action == "compare" and (current or quoted)
                              or pending.action == "choose" and _accept_pair(refs, text)
                              or pending.action == "scene" and text and not _NEGATIVE.search(text)):
                action, old_question = pending.action, pending.question
                if action == "scene":
                    action, question = "start", old_question + "，" + text
                elif action == "choose":
                    action, question = "compare", old_question + "，" + text
                else:
                    question = old_question
                state.pending = None
            elif state.practice and (EXIT.fullmatch(text) or PAUSE.fullmatch(text) or RESUME.fullmatch(text)):
                action = "turn"
            elif not _NEGATIVE.search(text):
                if ask_pair:
                    action = "compare"
                elif group and (_RECAP.search(text) or _OWED.search(text)):
                    action = "recap"
                elif _PRACTICE.search(text):
                    action = "start"
                elif state.practice and (text or record) and (not state.practice.paused or _COACH_QUESTION.search(text)):
                    action = "turn"
                elif _NERVOUS.search(text) and not pending:
                    action = "offer_practice"
                elif group and _RETURN.fullmatch(text) and not pending:
                    action = "offer_recap"
                elif len(refs) >= 2 and (current or quoted) and (addressed or _LOOK.search(text)) and not pending:
                    action = "offer_compare"
            if features is not None and action:
                family = {"start": "practice", "turn": "practice", "offer_practice": "practice",
                          "offer_compare": "compare", "offer_recap": "recap"}.get(action, action)
                if family in {"practice", "compare", "recap"} and family not in features:
                    action = ""
            if mid:
                state.seen.append((mid, bool(action)))
            if not action:
                return False
            event.should_call_llm(False)
            event.stop_event()
            try:
                if action == "game" or chat_action and action == chat_action:
                    from astrbot.api.message_components import Plain

                    state.pending = None
                    if action == "game":
                        state.dialog = None
                        if features is not None and not game_action.game and game_action.verb in {"capabilities", "choose"}:
                            answer = "这边可以一起玩的有" + "、".join(name for key, name in games.NAMES.items() if key in features)
                        else:
                            answer = await self.games.answer(plugin, event, state, game_action, now)
                    else:
                        if state.game and state.game.step in {"offer", "confirm", "choose"}:
                            state.game = None
                        answer = await actions.chat_action(plugin, event, state, action, question, refs, now)
                    chain = [Plain(answer)] if answer is not None else []
                else:
                    if action in {"start", "recap", "compare", "offer_recap", "offer_practice", "offer_compare"}:
                        state.dialog = None
                        if state.game and state.game.step in {"offer", "confirm", "choose"}:
                            state.game = None
                    chain = await self._answer(plugin, event, state, action, question, refs, spoken)
            except Exception as exc:  # noqa: BLE001 -- provider or attachment failures still need one reply.
                from astrbot.api.message_components import Plain

                from app.astrbot_failure_reply import Failure, describe_failure

                _LOG.warning("QQ陪伴功能未完成 action=%s error=%s", action, type(exc).__name__)
                feature = {"compare": "两张图的对照", "recap": "群聊重点", "draft": "帮你拟回复",
                           "game": "一起玩游戏", "remind": "提取提醒", "save": "保存提醒",
                           "complete": "收掉提醒"}.get(action, "这轮陪练")
                kind = "chat_unavailable" if action in {"start", "turn"} else "unavailable"
                chain = [Plain(await describe_failure(plugin.context, event, Failure(feature, kind)))]
            if chain:
                await event.send(event.chain_result(chain))
            return True

    async def _answer(self, plugin, event, state, action, question, refs, spoken):
        from astrbot.api.message_components import Plain

        now = self.clock()
        if action == "unheard":
            return [Plain(question)]
        if action == "decline":
            return [Plain("好呀")]
        offers = {
            "offer_practice": ("start", "要不我当你的谈话对象，陪你把开头那句练一遍？"),
            "offer_recap": ("recap", "要我帮你翻翻刚才群里定了什么，还有谁在等你回话吗？"),
            "offer_compare": ("compare", "要我把两张图并排放，帮你圈一下改了哪里吗？"),
        }
        if action in offers:
            kind, invitation = offers[action]
            state.pending = _Pending(kind, question, now + 180, tuple(refs) if kind == "compare" else ())
            return [Plain(invitation)]
        if action == "compare":
            pair = _accept_pair(refs, question)
            if not pair:
                kind = "choose" if len(refs) > 2 else "compare"
                state.pending = _Pending(kind, question, now + 360, tuple(refs))
                tip = (f"我看到 {len(refs)} 张，你想比第几张和第几张？" if len(refs) > 2 else
                       "我手里有一张，能再发另一张吗？" if refs else "能把想比的两张图发给我吗？分两条发也行")
                return [Plain(tip)]
            async with asyncio.timeout(85):
                return await compare(plugin.context, pair, question)
        if action == "recap":
            from app.astrbot_catchup import catch_up

            return await catch_up(plugin, event, question)
        if action == "start" and not _SCENE.search(question):
            state.pending = _Pending("scene", ("语音陪练。" if spoken else "") + question, now + 180)
            return [Plain("想练哪种对话，我来当谁？")]
        if action == "start":
            state.practice = Rehearsal(question[:700], spoken=bool(re.search(r"语音|口语|说给我听", question)))
        state.practice_at = now
        session = state.practice
        if spoken:
            session.spoken = True
        answer, finished = await rehearse(plugin, event, session, question, opening=action == "start")
        if finished:
            state.practice = None
        return await reply_chain(plugin, event, answer, session.spoken and not finished)
