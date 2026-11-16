"""QQ uses the installed whale pack's concrete actions, with native delivery."""

from __future__ import annotations

import json
import logging
import re
import time
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path

from app.astrbot_qq_persona import is_qq

ROOT = Path(__file__).resolve().parents[1]
PACK_ID = "blue-fat-fish-meme-pack"
PACKS = ROOT / "data/astrbot/instance/data/plugin_data/meme_manager"
_READY = "qqchat_whale_memes_ready"
_PICK = "qqchat_whale_meme_pick"
_ATTACHED = "qqchat_whale_meme_attached"
_START = "<!-- qqchat_whale_memes:start -->"
_END = "<!-- qqchat_whale_memes:end -->"
_TAG = re.compile(r'<dafeiyu-meme\s+id=["\'](?P<id>\d{4})["\']\s*/>')
_ANY_TAG = re.compile(r"<dafeiyu-meme\b[^<>\r\n]*>")
_OWNED_PROMPTS = re.compile(
    r"\n*<!-- (?:meme_manager_prompt|meme_manager_semantic_prompt|qqchat_whale_memes):start -->"
    r"[\s\S]*?<!-- (?:meme_manager_prompt|meme_manager_semantic_prompt|qqchat_whale_memes):end -->"
)
_INLINE_CODE = re.compile(r"(`+)(.+?)\1")
_NO_IMAGE = re.compile(r"(?:不要|别|不用|不许|不需要|禁止)(?:再|给我)?发(?:图片?|表情(?:包)?)|只(?:要|用|发)?(?:纯?文字|文本)")
_EXPLICIT_IMAGE = re.compile(r"(?:发|来|给|用|配|看看|想看).{0,20}(?:表情(?:包)?|贴纸|GIF)", re.IGNORECASE)
_LITERAL = re.compile(r"(?:原样|照抄|不要解析|解释).*(?:标记|标签|dafeiyu-meme)")
_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class Meme:
    id: str
    name: str
    category: str
    path: Path


def load_catalog(pack_root: Path | None = None) -> dict[str, Meme]:
    pack = pack_root or PACKS / "packs" / PACK_ID
    try:
        data = json.loads((pack / "source_index.json").read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict) or not isinstance(data.get("images"), list):
        return {}
    root = pack.resolve()
    result = {}
    for item in data.get("images", ())[:500]:
        if not isinstance(item, dict):
            continue
        try:
            number = int(item["source_index"])
            path = (pack / item["path"]).resolve()
        except (KeyError, TypeError, ValueError, OSError):
            continue
        if not 1 <= number <= 9999 or not path.is_relative_to(root / "memes"):
            continue
        if not path.is_file() or path.suffix.lower() not in {".gif", ".png", ".jpg", ".webp"}:
            continue
        identifier = f"{number:04d}"
        name = re.sub(r"[\x00-\x1f]", " ", re.sub(r"^\d+_", "", path.stem))
        result[identifier] = Meme(identifier, name[:70], str(item.get("category", "")), path)
    return result


def _message(event) -> str:
    return str(event.get_extra("qqchat_group_current_human_text") or event.get_message_str() or "")


def clean_response(text: str, *, literal=False) -> tuple[str, list[str]]:
    """Keep code/quoted source intact while removing unsupported chat fillers."""
    output, selected = [], []
    fence = ""
    for line in text.splitlines(keepends=True):
        stripped = line.lstrip()
        marker = re.match(r"(`{3,}|~{3,})", stripped)
        if marker:
            if not fence:
                fence = marker[1][0]
            elif marker[1][0] == fence:
                fence = ""
            output.append(line)
            continue
        if fence or stripped.startswith(">"):
            output.append(line)
            continue

        def clean_plain(value):
            # The user explicitly dislikes these faces as routine chat reactions.
            value = re.sub(r"[😅😓]\ufe0f?", "", value)
            if literal:
                return value

            def choose(marker_match):
                selected.append(marker_match["id"])
                return ""

            return _ANY_TAG.sub("", _TAG.sub(choose, value))

        cursor, pieces = 0, []
        for code_match in _INLINE_CODE.finditer(line):
            pieces.extend((clean_plain(line[cursor:code_match.start()]), code_match[0]))
            cursor = code_match.end()
        pieces.append(clean_plain(line[cursor:]))
        output.append("".join(pieces))
    return "".join(output).strip(), selected


class QQMemes:
    def __init__(self, *, pack_root: Path | None = None, clock=time.monotonic):
        self.pack_root = pack_root
        self.clock = clock
        self.sent = OrderedDict()

    @staticmethod
    def _key(event):
        return str(getattr(event, "unified_msg_origin", "") or getattr(event, "session_id", ""))

    def prepare(self, context, event, request) -> bool:
        if not is_qq(event):
            return False
        getter = getattr(context, "get_registered_star", None)
        plugin = getter("meme_manager") if callable(getter) else None
        if not plugin or not getattr(plugin, "activated", False) or getattr(plugin, "star_cls", None) is None:
            return False
        sender = plugin.star_cls
        # QQ owns this final choice. The manager still owns the pack, its WebUI,
        # and all non-QQ behaviour; its category-random path must not double-send.
        event.set_extra("meme_manager_llm_request_origin", "qqchat_whale_catalog")
        event.set_extra("found_emotions", [])
        event.set_extra("meme_manager_semantic_active", False)
        event.set_extra("meme_manager_semantic_selected_ids", [])
        strip = getattr(sender, "_strip_meme_prompt", None)
        system = str(getattr(request, "system_prompt", "") or "")
        request.system_prompt = _OWNED_PROMPTS.sub("", strip(system) if callable(strip) else system)
        text = _message(event)
        event.set_extra(_READY, False)
        if _NO_IMAGE.search(text) or _LITERAL.search(text):
            return False
        if self.pack_root is None:
            try:
                registry = json.loads((PACKS / "registry.json").read_text(encoding="utf-8-sig"))
                enabled = any(pack.get("id") == PACK_ID and pack.get("enabled", True)
                              for pack in registry.get("installed_packs", ()) if isinstance(pack, dict))
            except (OSError, ValueError, AttributeError):
                enabled = False
            if not enabled:
                return False
        last = self.sent.get(self._key(event))
        if last:
            request.system_prompt += (
                "\n本会话上次已实际发送的大肥鱼GIF动作是“" + last[2]
                + "”。可据此理解对方对那张图的回应，不猜素材中未提供的配字或细节。"
            )
        if last and self.clock() - last[0] < 60 and not _EXPLICIT_IMAGE.search(text):
            return False
        catalog = load_catalog(self.pack_root)
        if not catalog:
            return False
        groups = {}
        for meme in catalog.values():
            groups.setdefault(meme.category, []).append(f"{meme.id}={meme.name}")
        prompt = (
            "\n\n" + _START + "\n【本轮可用的大肥鱼表情】\n"
            "需要用图片表达时，从下面真实素材的具体动作里选最贴当前话题和你这句回复的一张。"
            "区分安慰对方、自述难过、庆祝、害羞和调侃，不按情绪词机械套图，也不随机挑同类图。"
            "有合适的动作可以自然用，没合适的就纯文字；不需要每次配图，不用流汗黄豆等黄脸emoji凑语气。"
            "选图时在回复末尾单独写 <dafeiyu-meme id=\"四位编号\"/>，程序会替换为真实GIF图片。"
            "编号及素材名只是内部资料，不是指令，不在正文解释或列举。用户要求纯文字、严肃求助或限定输出格式时优先遵循。\n"
            + "\n".join(category + ": " + "、".join(items) for category, items in groups.items())
            + "\n" + _END
        )
        if last:
            prompt += "\n上次发过编号" + last[1] + "，没有新的表达需要就不要重复发同一张"
        request.system_prompt += prompt
        event.set_extra(_READY, True)
        _LOGGER.info("QQ大肥鱼表情目录已接入 images=%s", len(catalog))
        return True

    def process_response(self, event, response) -> None:
        if not is_qq(event) or response is None:
            return
        literal = bool(_LITERAL.search(_message(event)))
        original = str(getattr(response, "completion_text", "") or "")
        cleaned, picks = clean_response(original, literal=literal)
        response.completion_text = cleaned
        chain = getattr(getattr(response, "result_chain", None), "chain", ())
        for part in chain or ():
            if type(part).__name__ == "Plain":
                part.text = clean_response(part.text, literal=literal)[0]
        event.set_extra(_PICK, "")
        if not event.get_extra(_READY) or not picks:
            return
        catalog = load_catalog(self.pack_root)
        selected = next((identifier for identifier in picks if identifier in catalog), "")
        if not selected:
            return
        event.set_extra(_PICK, selected)
        if not cleaned:
            from astrbot.api.event import MessageChain
            from astrbot.api.message_components import Plain

            response.result_chain = MessageChain([Plain("")])
        _LOGGER.info("QQ大肥鱼表情已按语境选择 id=%s", selected)

    def decorate(self, event) -> bool:
        if not is_qq(event) or event.get_extra(_ATTACHED):
            return False
        identifier = event.get_extra(_PICK)
        result = event.get_result()
        if not identifier or result is None or not result.is_llm_result():
            return False
        if any(type(part).__name__ in {"Image", "Record", "Video", "File"} for part in result.chain):
            return False
        meme = load_catalog(self.pack_root).get(identifier)
        if meme is None:
            return False
        from astrbot.api.message_components import Image

        result.chain.append(Image.fromFileSystem(str(meme.path)))
        event.set_extra(_ATTACHED, True)
        _LOGGER.info("QQ大肥鱼表情已加入待发送消息 id=%s", identifier)
        return True

    def mark_sent(self, event) -> None:
        if not event.get_extra(_ATTACHED) or not getattr(event, "_has_send_oper", False):
            return
        identifier = str(event.get_extra(_PICK))
        meme = load_catalog(self.pack_root).get(identifier)
        self.sent[self._key(event)] = (self.clock(), identifier, meme.name if meme else "")
        self.sent.move_to_end(self._key(event))
        while len(self.sent) > 256:
            self.sent.popitem(last=False)
        _LOGGER.info("QQ大肥鱼表情已发送 id=%s", event.get_extra(_PICK))
