import asyncio
import base64
import html
import json
import re
import unicodedata
from dataclasses import dataclass
from html.parser import HTMLParser
from io import BytesIO
from urllib.parse import quote, unquote, urljoin, urlparse

import httpx
from PIL import Image

from app.config import Settings
from app.services.ultraman import normalize_ultraman_source_image
from app.services.web_search import SearchResult, search_web

BAIDU_BAIKE_HOSTS = {
    "baike.baidu.com",
    "bkso.baidu.com",
    "wapbaike.baidu.com",
}
OFFICIAL_MERCH_HOSTS = {
    "tamashiiweb.com",
    "www.tamashiiweb.com",
    "toy.bandai.co.jp",
    "www.toy.bandai.co.jp",
    "p-bandai.jp",
    "www.p-bandai.jp",
}
BAIDU_IMAGE_TRUSTED_HOSTS = BAIDU_BAIKE_HOSTS | OFFICIAL_MERCH_HOSTS | {
    "zh.wikipedia.org",
    "en.wikipedia.org",
    "ja.wikipedia.org",
    "commons.wikimedia.org",
    "tsuburaya-prod.com",
    "www.tsuburaya-prod.com",
    "store.m-78.jp",
}
BAIDU_DIRECT_PAGES = {
    "帝纳斯奥特曼": "https://bkso.baidu.com/item/帝纳斯奥特曼/62373572",
    "闪耀迪迦": "https://bkso.baidu.com/item/闪耀迪迦/1023751",
    "梦比优斯奥特曼·凤凰勇者": "https://bkso.baidu.com/item/梦比优斯凤凰形态/8865567",
    "梦比优斯奥特曼·无限形态": "https://bkso.baidu.com/item/梦比优斯无限形态/8944775",
    "赛罗奥特曼·无限形态": "https://bkso.baidu.com/item/超限赛罗/23742584",
    "银河奥特曼·斯特利姆形态": (
        "https://bkso.baidu.com/item/银河斯特利姆·奥特曼/19250392"
    ),
    "银河维克特利奥特曼": (
        "https://bkso.baidu.com/item/银河维克特利奥特曼/18857816"
    ),
    "艾克斯奥特曼·超越形态": "https://bkso.baidu.com/item/艾克斯超越型/66335709",
    "欧布奥特曼·重光形态": "https://bkso.baidu.com/item/斯佩修姆哉佩利敖/20203467",
    "欧布奥特曼·暴炎形态": "https://bkso.baidu.com/item/燃烧炸弹/8072217",
    "欧布奥特曼·疾风形态": "https://bkso.baidu.com/item/飓风切割/20203456",
    "欧布奥特曼·暗耀形态": "https://bkso.baidu.com/item/雷霆胸章/19969982",
    "欧布奥特曼·原生形态": "https://bkso.baidu.com/item/欧布起源/20397989",
    "欧布奥特曼·煌闪形态": "https://bkso.baidu.com/item/闪电攻击者/20203503",
    "欧布奥特曼·智勇形态": "https://bkso.baidu.com/item/艾梅利姆头镖/20620536",
    "捷德奥特曼·尊皇形态": "https://bkso.baidu.com/item/捷德奥特曼/20825718",
    "捷德奥特曼·银河初升": "https://bkso.baidu.com/item/捷德奥特曼/20825718",
    "赛迦奥特曼": "https://bkso.baidu.com/item/赛迦奥特曼/3008291",
    "令迦奥特曼": "https://bkso.baidu.com/item/令迦奥特曼/24350007",
}

BAIDU_PARENT_PAGES = {
    "迪迦奥特曼·强力型": (
        "https://bkso.baidu.com/item/艾克斯奥特曼剧场版来了！我们的奥特曼/59156783",
        "https://bkso.baidu.com/item/手掌光箭/17972611",
        "https://bkso.baidu.com/item/迪拉修姆光流/17836218",
    ),
    "迪迦奥特曼·空中型": (
        "https://bkso.baidu.com/item/艾克斯奥特曼剧场版来了！我们的奥特曼/59156783",
        "https://bkso.baidu.com/item/手掌光箭/17972611",
        "https://bkso.baidu.com/item/兰帕尔特光弹/17836257",
    ),
    "戴拿奥特曼·强壮型": ("https://bkso.baidu.com/item/戴拿奥特曼/24257648",),
    "戴拿奥特曼·奇迹型": ("https://bkso.baidu.com/item/戴拿奥特曼/24257648",),
    "盖亚奥特曼V2": ("https://bkso.baidu.com/item/盖亚奥特曼/5384276",),
    "盖亚奥特曼·至高型": ("https://bkso.baidu.com/item/盖亚奥特曼/5384276",),
    "高斯奥特曼·日冕模式": (
        "https://bkso.baidu.com/item/高斯奥特曼VS杰斯提斯奥特曼：最终决战/55592251",
    ),
    "高斯奥特曼·日蚀模式": (
        "https://bkso.baidu.com/item/高斯奥特曼VS杰斯提斯奥特曼：最终决战/55592251",
    ),
    "奈克瑟斯奥特曼·青年形态": ("https://bkso.baidu.com/item/姬矢准/3556866",),
    "奈克瑟斯奥特曼·青年蓝色形态": (
        "https://bkso.baidu.com/item/孤门一辉/3556767",
        "https://bkso.baidu.com/item/奥特次元卡牌/60698155",
    ),
    "梦比优斯奥特曼·勇者形态": ("https://bkso.baidu.com/item/梦比优斯奥特曼/2684129",),
    "梦比优斯奥特曼·燃烧勇者": ("https://bkso.baidu.com/item/梦比优斯奥特曼/2684129",),
    "超级奥特曼泰罗": (
        "https://bkso.baidu.com/item/宇宙奇迹光线/20864681",
        "https://bkso.baidu.com/item/究极奥特战士/19465516",
    ),
    "赛罗奥特曼·月神奇迹型": (
        "https://bkso.baidu.com/item/奥特勋章/49862711",
        "https://bkso.baidu.com/item/奥特融合卡牌/55316379",
    ),
    "赛罗奥特曼·闪耀型": (
        "https://bkso.baidu.com/item/奥特勋章/49862711",
        "https://bkso.baidu.com/item/究极奥特战士/19465516",
        "https://bkso.baidu.com/item/艾梅利姆光线/9966205",
    ),
    "维克特利骑士": (
        "https://bkso.baidu.com/item/新奥特曼列传/8010663",
        "https://bkso.baidu.com/item/维克特利奥特曼/53260527",
    ),
    "捷德奥特曼·原始形态": (
        "https://bkso.baidu.com/item/捷德奥特曼/20825718",
        "https://bkso.baidu.com/item/捷德奥特曼/20832863",
        "https://bkso.baidu.com/item/奥特融合卡牌/55316379",
    ),
    "捷德奥特曼·刚燃形态": (
        "https://bkso.baidu.com/item/捷德奥特曼/20825718",
        "https://bkso.baidu.com/item/捷德奥特曼/20832863",
        "https://bkso.baidu.com/item/奥特融合卡牌/55316379",
    ),
    "捷德奥特曼·机敏形态": (
        "https://bkso.baidu.com/item/捷德奥特曼/20825718",
        "https://bkso.baidu.com/item/捷德奥特曼/20832863",
        "https://bkso.baidu.com/item/奥特融合卡牌/55316379",
    ),
    "捷德奥特曼·豪勇形态": (
        "https://bkso.baidu.com/item/捷德奥特曼/20825718",
        "https://bkso.baidu.com/item/捷德奥特曼/20832863",
        "https://bkso.baidu.com/item/奥特融合卡牌/55316379",
    ),
    "捷德奥特曼·尊皇形态": (
        "https://bkso.baidu.com/item/捷德奥特曼/20825718",
        "https://bkso.baidu.com/item/捷德奥特曼/20832863",
        "https://bkso.baidu.com/item/奥特勋章/49862711",
        "https://bkso.baidu.com/item/奥特融合卡牌/55316379",
    ),
    "捷德奥特曼·银河初升": (
        "https://bkso.baidu.com/item/捷德奥特曼/20825718",
        "https://bkso.baidu.com/item/捷德奥特曼/20832863",
        "https://bkso.baidu.com/item/奥特融合卡牌/55316379",
    ),
    "泰迦奥特曼·光子地球": (
        "https://bkso.baidu.com/item/托雷基亚奥特曼/23366789",
        "https://bkso.baidu.com/item/奥特泰迦光饰/24232587",
        "https://bkso.baidu.com/item/奥特次元卡牌/60698155",
    ),
    "泰迦奥特曼·三重斯特利姆形态": (
        "https://bkso.baidu.com/item/托雷基亚奥特曼/23366789",
        "https://bkso.baidu.com/item/奥特次元卡牌/60698155",
        "https://bkso.baidu.com/item/奥特勋章/49862711",
    ),
    "闪耀特利迦永恒": ("https://bkso.baidu.com/item/特利迦奥特曼/56750285",),
    "真理特利迦": ("https://bkso.baidu.com/item/特利迦奥特曼/56750285",),
    "德凯奥特曼·强劲型": (
        "https://bkso.baidu.com/item/德凯奥特曼/59714231",
        "https://bkso.baidu.com/item/布莱泽辉石/62917896",
    ),
    "布莱泽奥特曼·法多兰盔甲": (
        "https://bkso.baidu.com/item/法德兰辉石/65767412",
        "https://bkso.baidu.com/item/布莱泽奥特曼/62550634",
    ),
    "亚刻奥特曼·银河装甲": (
        "https://bkso.baidu.com/item/亚刻奥特曼/63972321",
        "https://bkso.baidu.com/item/亚刻奥特曼/63972289",
        "https://bkso.baidu.com/item/亚刻魔方/64252054",
        "https://bkso.baidu.com/item/亚刻银河刃/64905046",
    ),
    "贝利亚早期形态": (
        "https://bkso.baidu.com/item/贝利亚奥特曼/7743176",
        "https://bkso.baidu.com/item/奥特银河格斗：巨大阴谋/59853212",
    ),
    "托雷基亚早期形态": ("https://bkso.baidu.com/item/托雷基亚奥特曼/23366789",),
}

WIKIPEDIA_API_HOSTS = (
    "zh.wikipedia.org",
    "en.wikipedia.org",
    "ja.wikipedia.org",
)
ENCYCLOPEDIA_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36 "
    "qq-chatrobot/0.1"
)
_BAD_IMAGE_HINTS = (
    "logo",
    "icon",
    "avatar",
    "default",
    "loading",
    "qrcode",
    "qr_code",
    "sprite",
)

_GENERIC_IMAGE_TERMS = {
    "奥特曼",
    "超人",
    "ultraman",
    "ultra",
    "ウルトラマン",
}


# Some forms live only inside the parent hero's Baidu/Wikipedia article.
# These terms are for page discovery only; image validation still requires the
# requested form's strong aliases, so a parent page cannot silently supply a base image.
_PARENT_DISCOVERY_TERMS = {
    "闪耀迪迦": ("迪迦奥特曼",),
    "盖亚奥特曼V2": ("盖亚奥特曼",),
    "阿古茹奥特曼V2": ("阿古茹奥特曼",),
    "超级奥特曼泰罗": ("泰罗奥特曼",),
    "银河维克特利奥特曼": ("银河奥特曼", "维克特利奥特曼"),
    "维克特利骑士": ("维克特利奥特曼",),
    "闪耀特利迦永恒": ("特利迦奥特曼",),
    "真理特利迦": ("特利迦奥特曼",),
    "贝利亚早期形态": ("贝利亚奥特曼",),
    "托雷基亚早期形态": ("托雷基亚奥特曼",),
}


@dataclass(frozen=True)
class EncyclopediaImage:
    data: str
    source: str
    page_url: str
    label: str


def _normalize(value: str) -> str:
    value = unicodedata.normalize("NFKC", html.unescape(value or "")).casefold()
    value = re.sub(r"<[^>]+>", " ", value)
    return re.sub(r"[^0-9a-z\u3040-\u30ff\u3400-\u9fff]+", "", value)


def _specific_terms(name: str, aliases: tuple[str, ...]) -> tuple[str, ...]:
    # Keep the character identity + form identity together. We accept both
    # "Ultraman Tiga Power Type" and "Tiga Power Type", but never a bare
    # generic suffix such as only "Power Type" / "强力型".
    candidates = [name, *aliases]
    terms: list[str] = []
    generic = {_normalize(value) for value in _GENERIC_IMAGE_TERMS}
    generic_tokens = sorted((token for token in generic if token), key=len, reverse=True)
    bare_suffix = _normalize(name.split("·", 1)[1]) if "·" in name else ""
    for value in candidates:
        normalized = _normalize(value)
        variants = [normalized]
        stripped = normalized
        for token in generic_tokens:
            stripped = stripped.replace(token, "")
        if stripped and stripped != normalized:
            variants.append(stripped)
        for variant in variants:
            if (
                len(variant) >= 3
                and variant not in generic
                and variant != bare_suffix
                and variant not in terms
            ):
                terms.append(variant)
    return tuple(terms)


def _encyclopedia_search_terms(name: str, aliases: tuple[str, ...]) -> tuple[str, ...]:
    values: list[str] = [name]
    if "·" in name:
        parent, _ = name.split("·", 1)
        values.extend((parent, name.replace("·", ""), name.replace("·", " ")))
    values.extend(_PARENT_DISCOVERY_TERMS.get(name, ()))
    values.extend(aliases)
    return tuple(dict.fromkeys(value.strip() for value in values if value.strip()))


def _matches_specific(value: str, terms: tuple[str, ...]) -> bool:
    normalized = _normalize(value)
    return bool(normalized) and any(term in normalized for term in terms)


def _parent_page_form_terms(
    name: str,
    aliases: tuple[str, ...],
    page_title: str,
) -> tuple[str, ...]:
    """Allow short form labels only after the page title proves the parent hero."""
    parent_values: list[str] = []
    if "·" in name:
        parent_values.append(name.split("·", 1)[0])
    parent_values.extend(_PARENT_DISCOVERY_TERMS.get(name, ()))

    parent_norms: list[str] = []
    ultra_cn = _normalize("奥特曼")
    for parent in parent_values:
        normalized = _normalize(parent)
        if normalized and normalized not in parent_norms:
            parent_norms.append(normalized)
        without_ultra = normalized.replace(ultra_cn, "")
        if len(without_ultra) >= 2 and without_ultra not in parent_norms:
            parent_norms.append(without_ultra)

    normalized_title = _normalize(page_title)
    if not any(parent and parent in normalized_title for parent in parent_norms):
        return ()

    values: list[str] = []
    if "·" in name:
        values.append(name.split("·", 1)[1])
    values.extend(aliases)

    generic_tokens = sorted(
        (_normalize(value) for value in _GENERIC_IMAGE_TERMS),
        key=len,
        reverse=True,
    )
    removable_suffixes = tuple(_normalize(value) for value in ("形态", "模式", "类型"))
    terms: list[str] = []

    for value in values:
        normalized = _normalize(value)
        for parent in parent_norms:
            normalized = normalized.replace(parent, "")
        for token in generic_tokens:
            normalized = normalized.replace(token, "")
        candidates = [normalized]
        shortened = normalized
        for suffix in removable_suffixes:
            if shortened.endswith(suffix):
                shortened = shortened[: -len(suffix)]
                break
        if shortened and shortened != normalized:
            candidates.append(shortened)
        for candidate in candidates:
            if len(candidate) >= 2 and candidate not in terms:
                terms.append(candidate)
    return tuple(terms)


def _clean_image_url(value: str, page_url: str) -> str:
    url = html.unescape(value or "").replace("\\/", "/").strip()
    url = url.replace("\\u002F", "/").replace("\\u002f", "/")
    if url.startswith("//"):
        url = "https:" + url
    elif url.startswith("/"):
        url = urljoin(page_url, url)
    elif not url.startswith(("http://", "https://")):
        return ""
    if url.startswith("http://"):
        url = "https://" + url.removeprefix("http://")
    if any(hint in url.casefold() for hint in _BAD_IMAGE_HINTS):
        return ""
    return url


class _BingImageResultParser(HTMLParser):
    """Collect Bing image-tile metadata from <a class="iusc" m="...">."""

    def __init__(self) -> None:
        super().__init__()
        self.items: list[dict[str, object]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.casefold() != "a":
            return
        values = {key.casefold(): value or "" for key, value in attrs}
        classes = {value.casefold() for value in values.get("class", "").split()}
        if "iusc" not in classes:
            return
        metadata = values.get("m", "")
        if not metadata:
            return
        try:
            payload = json.loads(html.unescape(metadata))
        except (TypeError, ValueError, json.JSONDecodeError):
            return
        if isinstance(payload, dict):
            self.items.append(payload)


class _EncyclopediaPageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.title = ""
        self.og_image = ""
        self.images: list[tuple[str, str]] = []
        self._in_title = False
        self._title_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {key.casefold(): value or "" for key, value in attrs}
        if tag.casefold() == "title":
            self._in_title = True
            return
        if tag.casefold() == "meta":
            prop = (values.get("property") or values.get("name") or "").casefold()
            if prop == "og:title" and values.get("content"):
                self.title = values["content"]
            elif prop in {"og:image", "twitter:image"} and not self.og_image:
                self.og_image = values.get("content", "")
            return
        if tag.casefold() != "img":
            return
        source = (
            values.get("data-src")
            or values.get("data-original")
            or values.get("data-imgurl")
            or values.get("objurl")
            or values.get("src")
            or ""
        )
        label = " ".join(
            part
            for part in (
                values.get("alt", ""),
                values.get("title", ""),
                values.get("aria-label", ""),
            )
            if part
        )
        if source:
            self.images.append((source, label))

    def handle_endtag(self, tag: str) -> None:
        if tag.casefold() == "title":
            self._in_title = False
            if not self.title and self._title_parts:
                self.title = " ".join(self._title_parts).strip()

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self._title_parts.append(data)


def _raw_image_candidates(
    payload: str,
    page_url: str,
    terms: tuple[str, ...],
) -> list[tuple[int, str, str]]:
    pattern = re.compile(
        r"https?:\\?/\\?/[^\"'<>\s]+?\.(?:jpe?g|png|webp)"
        r"(?:\?[^\"'<>\s]*)?",
        re.IGNORECASE,
    )
    candidates: list[tuple[int, str, str]] = []
    for match in pattern.finditer(payload):
        raw_url = match.group(0)
        image_url = _clean_image_url(raw_url, page_url)
        if not image_url:
            continue
        start = max(0, match.start() - 260)
        end = min(len(payload), match.end() + 260)
        context = html.unescape(payload[start:end])
        if not _matches_specific(context, terms):
            continue
        candidates.append((90, image_url, re.sub(r"\s+", " ", context)[:180]))
    return candidates


def baidu_page_image_candidates(
    payload: str,
    page_url: str,
    aliases: tuple[str, ...],
) -> list[tuple[int, str, str]]:
    terms = _specific_terms(aliases[0], aliases[1:])
    parser = _EncyclopediaPageParser()
    parser.feed(payload)
    candidates: list[tuple[int, str, str]] = []
    page_is_specific = _matches_specific(parser.title, terms)
    parent_form_terms = _parent_page_form_terms(
        aliases[0],
        aliases[1:],
        parser.title,
    )
    image_terms = tuple(dict.fromkeys((*terms, *parent_form_terms)))

    for source, label in parser.images:
        image_url = _clean_image_url(source, page_url)
        if not image_url:
            continue
        score = 0
        if _matches_specific(label, image_terms):
            score += 150
        if _matches_specific(image_url, terms):
            score += 30
        if score:
            candidates.append((score, image_url, label))

    # Baidu/Wikipedia parent pages often contain a form gallery whose image URL
    # itself is generic. The local HTML/JSON context can still prove the exact
    # form as long as it contains a strong "character + form" term.
    candidates.extend(_raw_image_candidates(payload, page_url, image_terms))

    # Baidu's no-ID /item/<name> route can return a generic 350x350 placeholder
    # while still echoing the requested title. Only trust an og:image when the
    # resolved page has a canonical numeric lemma id.
    parsed_page = urlparse(page_url)
    has_numeric_lemma_id = bool(
        re.search(r"/item/[^/?]+/\d+(?:$|/)", parsed_page.path)
    )
    if page_is_specific and has_numeric_lemma_id and parser.og_image:
        image_url = _clean_image_url(parser.og_image, page_url)
        if image_url:
            candidates.append((110, image_url, parser.title))

    unique: dict[str, tuple[int, str, str]] = {}
    for item in candidates:
        current = unique.get(item[1])
        if current is None or item[0] > current[0]:
            unique[item[1]] = item
    return sorted(unique.values(), key=lambda item: item[0], reverse=True)


async def _download_verified_image(
    client: httpx.AsyncClient,
    image_url: str,
    page_url: str,
    settings: Settings,
) -> str:
    urls = [image_url]
    if (urlparse(image_url).hostname or "").casefold() == "storage.moegirl.org.cn":
        # The storage CDN can fail TLS from some domestic hosts. wsrv.nl is
        # used only as a transport mirror; attribution remains the Moegirl page.
        urls.append("https://wsrv.nl/?url=" + quote(image_url, safe=""))
    response = None
    last_error: Exception | None = None
    for candidate_url in urls:
        try:
            candidate = await client.get(
                candidate_url,
                headers={"Referer": page_url, "Accept": "image/avif,image/webp,image/*,*/*"},
            )
            candidate.raise_for_status()
            response = candidate
            break
        except (httpx.HTTPError, RuntimeError) as exc:
            last_error = exc
    if response is None:
        raise last_error or RuntimeError("百科图片下载失败")
    content_type = response.headers.get("content-type", "").casefold()
    if not content_type.startswith("image/"):
        raise RuntimeError("百科图片响应不是图片")
    if len(response.content) > settings.media_max_bytes:
        raise RuntimeError("百科图片超过大小限制")
    try:
        with Image.open(BytesIO(response.content)) as source:
            width, height = source.size
            if width < 160 or height < 160 or width * height < 40_000:
                raise RuntimeError("百科图片尺寸过小")
            # QQ/NapCat 对部分 WebP/AVIF/PNG 外链兼容性不稳定。
            # 在机器人侧统一解码并转成 JPEG；透明 PNG 先铺到可见背景，
            # 避免直接丢弃 alpha 后透明区域被转换成大面积黑色。
            if source.mode in {"RGBA", "LA"} or "transparency" in source.info:
                alpha = source.convert("RGBA").getchannel("A")
                alpha_sample = alpha.copy()
                alpha_sample.thumbnail((256, 256), Image.Resampling.BILINEAR)
                alpha_histogram = alpha_sample.histogram()
                alpha_pixels = max(1, sum(alpha_histogram))
                visible_alpha_fraction = sum(alpha_histogram[16:]) / alpha_pixels
                if visible_alpha_fraction < 0.01:
                    raise RuntimeError("百科图片几乎完全透明")
            image = normalize_ultraman_source_image(source)
            sample = image.copy()
            sample.thumbnail((256, 256), Image.Resampling.BILINEAR)
            histogram = sample.convert("L").histogram()
            pixels = max(1, sum(histogram))
            near_black_fraction = sum(histogram[:24]) / pixels
            visible_fraction = sum(histogram[48:]) / pixels
            if near_black_fraction >= 0.90 and visible_fraction <= 0.08:
                raise RuntimeError("百科图片近似全黑")
            output = BytesIO()
            image.save(output, format="JPEG", quality=92, optimize=True)
    except RuntimeError:
        raise
    except (OSError, ValueError) as exc:
        raise RuntimeError("百科图片无法解码") from exc
    payload = output.getvalue()
    if len(payload) > settings.media_max_bytes:
        raise RuntimeError("转换后的百科图片超过大小限制")
    return "base64://" + base64.b64encode(payload).decode()


async def moegirl_ultraman_image(
    name: str,
    aliases: tuple[str, ...],
    settings: Settings,
) -> EncyclopediaImage | None:
    """Resolve Ultraman/form art from Moegirlpedia with page-search fallback."""
    terms = _specific_terms(name, aliases)
    if not terms:
        return None
    queries = tuple(dict.fromkeys((name, *aliases, *_encyclopedia_search_terms(name, aliases))))[:12]
    domains = (
        "https://zh.moegirl.org.cn",
        "https://moegirl.icu",
        "https://moegirl.uk",
    )
    timeout = max(8.0, min(float(settings.media_timeout_seconds), 30.0))
    headers = {
        "User-Agent": ENCYCLOPEDIA_USER_AGENT,
        "Accept-Language": "zh-CN,zh;q=0.9,ja;q=0.8,en;q=0.7",
    }

    async with httpx.AsyncClient(
        timeout=timeout,
        follow_redirects=True,
        headers=headers,
    ) as client:
        for domain in domains:
            page_titles: list[str] = []

            # Exact title/redirect lookup.
            for query in queries[:8]:
                try:
                    response = await client.get(
                        f"{domain}/api.php",
                        params={
                            "action": "query",
                            "format": "json",
                            "redirects": "1",
                            "prop": "pageimages",
                            "piprop": "original|thumbnail|name",
                            "pithumbsize": "1400",
                            "titles": query,
                        },
                    )
                    response.raise_for_status()
                    payload = response.json()
                except (httpx.HTTPError, ValueError):
                    continue

                pages = payload.get("query", {}).get("pages", {})
                if not isinstance(pages, dict):
                    continue
                for page in pages.values():
                    if not isinstance(page, dict) or page.get("missing") is not None:
                        continue
                    page_title = str(page.get("title") or query).strip()
                    # The exact-title endpoint must prove the requested form
                    # from the returned page title itself; the query string is
                    # user input and cannot be identity evidence.
                    if not _matches_specific(page_title, terms):
                        continue
                    if _matches_specific(page_title, terms):
                        page_titles.append(page_title)

                    image_urls = []
                    for key in ("original", "thumbnail"):
                        image = page.get(key) or {}
                        if isinstance(image, dict):
                            image_urls.append(str(image.get("source") or ""))
                    page_url = f"{domain}/{quote(page_title)}"
                    for image_url in dict.fromkeys(
                        value.strip() for value in image_urls if value.strip()
                    ):
                        if not image_url.startswith(("https://", "http://")):
                            continue
                        try:
                            data_b64 = await _download_verified_image(
                                client,
                                image_url,
                                page_url,
                                settings,
                            )
                        except (
                            httpx.HTTPError,
                            RuntimeError,
                            OSError,
                            ValueError,
                        ):
                            continue
                        return EncyclopediaImage(
                            data=data_b64,
                            source="萌娘百科",
                            page_url=page_url,
                            label=page_title or query,
                        )

            # Moegirl internal search catches disambiguated titles and aliases.
            search_values = list(queries[:8])
            search_values.extend(f"{query} 奥特曼" for query in queries[:6])
            for query in tuple(dict.fromkeys(search_values))[:14]:
                try:
                    response = await client.get(
                        f"{domain}/api.php",
                        params={
                            "action": "query",
                            "format": "json",
                            "generator": "search",
                            "gsrsearch": query,
                            "gsrlimit": "8",
                            "prop": "pageimages",
                            "piprop": "original|thumbnail|name",
                            "pithumbsize": "1400",
                        },
                    )
                    response.raise_for_status()
                    payload = response.json()
                except (httpx.HTTPError, ValueError):
                    continue

                pages = payload.get("query", {}).get("pages", {})
                if not isinstance(pages, dict):
                    continue
                for page in pages.values():
                    if not isinstance(page, dict):
                        continue
                    page_title = str(page.get("title") or "").strip()
                    if not page_title:
                        continue
                    # Prefer a direct search-result lead image when the result
                    # itself clearly names the requested hero/form.
                    if _matches_specific(page_title, terms):
                        page_titles.append(page_title)
                        image_urls = []
                        for key in ("original", "thumbnail"):
                            image = page.get(key) or {}
                            if isinstance(image, dict):
                                image_urls.append(str(image.get("source") or ""))
                        page_url = f"{domain}/{quote(page_title)}"
                        for image_url in dict.fromkeys(
                            value.strip() for value in image_urls if value.strip()
                        ):
                            if not image_url.startswith(("https://", "http://")):
                                continue
                            try:
                                data_b64 = await _download_verified_image(
                                    client,
                                    image_url,
                                    page_url,
                                    settings,
                                )
                            except (
                                httpx.HTTPError,
                                RuntimeError,
                                OSError,
                                ValueError,
                            ):
                                continue
                            return EncyclopediaImage(
                                data=data_b64,
                                source="萌娘百科站内搜索",
                                page_url=page_url,
                                label=page_title,
                            )

            # If the form has no standalone page, inspect the found character /
            # series pages and require the image metadata itself to name the form.
            for page_title in tuple(dict.fromkeys(page_titles))[:24]:
                page_url = f"{domain}/{quote(page_title)}"
                try:
                    page = await client.get(page_url)
                    page.raise_for_status()
                    parser = _EncyclopediaPageParser()
                    parser.feed(page.text)
                except (httpx.HTTPError, ValueError):
                    continue

                candidates: list[tuple[int, str, str]] = []
                for source, label in parser.images:
                    image_url = _clean_image_url(source, str(page.url))
                    if not image_url:
                        continue
                    descriptor = f"{label} {image_url}"
                    if _matches_specific(descriptor, terms):
                        candidates.append((200, image_url, label))
                candidates.extend(
                    _raw_image_candidates(page.text, str(page.url), terms)
                )

                unique: dict[str, tuple[int, str, str]] = {}
                for item in candidates:
                    current = unique.get(item[1])
                    if current is None or item[0] > current[0]:
                        unique[item[1]] = item

                for _, image_url, label in sorted(
                    unique.values(),
                    key=lambda item: item[0],
                    reverse=True,
                )[:16]:
                    try:
                        data_b64 = await _download_verified_image(
                            client,
                            image_url,
                            str(page.url),
                            settings,
                        )
                    except (RuntimeError, httpx.HTTPError):
                        continue
                    return EncyclopediaImage(
                        data=data_b64,
                        source="萌娘百科角色/系列页",
                        page_url=str(page.url),
                        label=label or page_title,
                    )
    return None


async def baidu_baike_ultraman_image(
    name: str,
    aliases: tuple[str, ...],
    settings: Settings,
) -> EncyclopediaImage | None:
    searches = _encyclopedia_search_terms(name, aliases)
    seen_pages: set[str] = set()
    direct_results: list[SearchResult] = []

    direct_url = BAIDU_DIRECT_PAGES.get(name)
    if direct_url:
        seen_pages.add(direct_url)
        direct_results.append(SearchResult(name, direct_url, "direct"))

    for parent_url in BAIDU_PARENT_PAGES.get(name, ()):
        if parent_url not in seen_pages:
            seen_pages.add(parent_url)
            direct_results.append(SearchResult(name, parent_url, "parent-gallery"))

    # Try direct lemma URLs first. Parent-character terms are included by
    # _encyclopedia_search_terms, so forms hosted only in a parent gallery can
    # still be found without waiting for a general web search.
    baidu_lemma_terms = [
        query for query in searches if re.search(r"[\u3400-\u9fff]", query)
    ][:4]
    for query in baidu_lemma_terms:
        encoded = quote(query, safe="")
        for base in (
            "https://bkso.baidu.com/item/",
            "https://baike.baidu.com/item/",
            "https://wapbaike.baidu.com/item/",
        ):
            item_url = base + encoded
            if item_url not in seen_pages:
                seen_pages.add(item_url)
                direct_results.append(SearchResult(query, item_url, "baidu-item"))

    timeout = max(4.0, min(float(settings.media_timeout_seconds), 8.0))
    headers = {"User-Agent": ENCYCLOPEDIA_USER_AGENT}

    async with httpx.AsyncClient(
        timeout=timeout,
        follow_redirects=True,
        headers=headers,
    ) as client:
        async def try_pages(page_results: list[SearchResult]) -> EncyclopediaImage | None:
            for result in page_results[:8]:
                try:
                    page = await client.get(result.url)
                    page.raise_for_status()
                except httpx.HTTPError:
                    continue
                candidates = baidu_page_image_candidates(
                    page.text,
                    str(page.url),
                    (name, *aliases),
                )
                for _, image_url, label in candidates[:10]:
                    try:
                        data = await _download_verified_image(
                            client,
                            image_url,
                            str(page.url),
                            settings,
                        )
                    except (RuntimeError, httpx.HTTPError):
                        continue
                    return EncyclopediaImage(
                        data=data,
                        source="百度百科",
                        page_url=str(page.url),
                        label=label or result.title,
                    )
            return None

        direct_match = await try_pages(direct_results)
        if direct_match is not None:
            return direct_match

        # Only pay the search-engine cost when direct Baidu lemmas did not work.
        discovered: list[SearchResult] = []
        for query in searches[:3]:
            try:
                found = await search_web(
                    f'"{query}" 百度百科',
                    limit=8,
                    timeout=timeout,
                )
            except (ValueError, httpx.HTTPError):
                continue
            for result in found:
                host = (urlparse(result.url).hostname or "").casefold()
                if host in BAIDU_BAIKE_HOSTS and result.url not in seen_pages:
                    seen_pages.add(result.url)
                    discovered.append(result)

        return await try_pages(discovered)


def _wikipedia_wikitext_image_candidates(
    wikitext: str,
    name: str,
    aliases: tuple[str, ...],
    page_title: str,
) -> list[str]:
    """Return File:/Image: titles whose local wikitext context names the exact form."""
    strong_terms = _specific_terms(name, aliases)
    parent_terms = _parent_page_form_terms(name, aliases, page_title)
    match_terms = tuple(dict.fromkeys((*strong_terms, *parent_terms)))
    if not match_terms:
        return []

    candidates: list[str] = []
    pattern = re.compile(
        r"\[\[(?:File|Image|文件|檔案|ファイル):([^\]|\n]+)(?:\|[^\]]*)?\]\]",
        re.IGNORECASE,
    )
    for match in pattern.finditer(wikitext or ""):
        # Validate only this File/Image link (filename + its own caption/options).
        # Looking hundreds of characters around the link can accidentally borrow
        # the caption of the next form in a gallery (e.g. Tiga Power vs Sky).
        link_text = html.unescape(match.group(0))
        file_title = match.group(1).strip()
        descriptor = f"{file_title} {link_text}"
        if not _matches_specific(descriptor, match_terms):
            continue
        normalized_title = file_title
        if not re.match(r"^(?:File|Image):", normalized_title, re.IGNORECASE):
            normalized_title = "File:" + normalized_title
        if normalized_title not in candidates:
            candidates.append(normalized_title)
    return candidates


async def _wikipedia_file_image(
    client: httpx.AsyncClient,
    endpoint: str,
    file_title: str,
    page_url: str,
    source: str,
    settings: Settings,
) -> EncyclopediaImage | None:
    try:
        response = await client.get(
            endpoint,
            params={
                "action": "query",
                "titles": file_title,
                "prop": "imageinfo",
                "iiprop": "url|mime|size",
                "iiurlwidth": 1200,
                "format": "json",
                "formatversion": 2,
            },
        )
        response.raise_for_status()
        payload = response.json()
    except (ValueError, httpx.HTTPError):
        return None
    pages = payload.get("query", {}).get("pages", [])
    if not isinstance(pages, list):
        return None
    for page in pages:
        if not isinstance(page, dict):
            continue
        infos = page.get("imageinfo", [])
        if not isinstance(infos, list) or not infos:
            continue
        info = infos[0] if isinstance(infos[0], dict) else {}
        image_url = str(info.get("thumburl") or info.get("url") or "")
        if not image_url.startswith("https://"):
            continue
        try:
            data = await _download_verified_image(
                client,
                image_url,
                page_url,
                settings,
            )
        except (RuntimeError, httpx.HTTPError):
            continue
        return EncyclopediaImage(
            data=data,
            source=source,
            page_url=page_url,
            label=file_title,
        )
    return None


def _wikipedia_result_matches(page: dict, terms: tuple[str, ...]) -> bool:
    title = str(page.get("title") or "")
    # For a form, accepting a base-character page image is unsafe. Only use the
    # page image when the page title itself identifies the requested form.
    return _matches_specific(title, terms)


async def wikipedia_ultraman_image(
    name: str,
    aliases: tuple[str, ...],
    settings: Settings,
) -> EncyclopediaImage | None:
    timeout = max(5.0, min(float(settings.media_timeout_seconds), 20.0))
    headers = {"User-Agent": ENCYCLOPEDIA_USER_AGENT}
    terms = _specific_terms(name, aliases)
    queries = list(_encyclopedia_search_terms(name, aliases))[:6]
    async with httpx.AsyncClient(
        timeout=timeout,
        follow_redirects=True,
        headers=headers,
    ) as client:
        for host in WIKIPEDIA_API_HOSTS:
            endpoint = f"https://{host}/w/api.php"
            for query in queries:
                try:
                    response = await client.get(
                        endpoint,
                        params={
                            "action": "query",
                            "generator": "search",
                            "gsrsearch": query,
                            "gsrnamespace": 0,
                            "gsrlimit": 5,
                            "prop": "pageimages|images",
                            "piprop": "thumbnail|original",
                            "pithumbsize": 1200,
                            "pilicense": "any",
                            "imlimit": 100,
                            "format": "json",
                            "formatversion": 2,
                        },
                    )
                    response.raise_for_status()
                    payload = response.json()
                except (ValueError, httpx.HTTPError):
                    continue
                pages = payload.get("query", {}).get("pages", [])
                if not isinstance(pages, list):
                    continue
                for page in pages:
                    if not isinstance(page, dict):
                        continue
                    page_title = str(page.get("title") or query)
                    page_url = (
                        f"https://{host}/wiki/"
                        + quote(page_title.replace(" ", "_"), safe="():,_-")
                    )

                    images = page.get("images", [])
                    if isinstance(images, list):
                        for image_entry in images:
                            if not isinstance(image_entry, dict):
                                continue
                            file_title = str(image_entry.get("title") or "")
                            if not _matches_specific(file_title, terms):
                                continue
                            resolved = await _wikipedia_file_image(
                                client,
                                endpoint,
                                file_title,
                                page_url,
                                f"{host} embedded image",
                                settings,
                            )
                            if resolved is not None:
                                return resolved

                    # A form can live as a subsection/gallery on the parent
                    # Wikipedia article. First inspect wikitext because captions
                    # often name the form even when the uploaded file name is generic.
                    page_id = page.get("pageid")
                    if page_id:
                        try:
                            parsed_response = await client.get(
                                endpoint,
                                params={
                                    "action": "parse",
                                    "pageid": page_id,
                                    "prop": "text|wikitext",
                                    "format": "json",
                                    "formatversion": 2,
                                },
                            )
                            parsed_response.raise_for_status()
                            parsed_payload = parsed_response.json()
                            parsed = parsed_payload.get("parse") or {}
                            rendered_html = str(parsed.get("text") or "")
                            wikitext = str(parsed.get("wikitext") or "")
                        except (ValueError, httpx.HTTPError):
                            rendered_html = ""
                            wikitext = ""

                        for file_title in _wikipedia_wikitext_image_candidates(
                            wikitext,
                            name,
                            aliases,
                            page_title,
                        )[:12]:
                            resolved = await _wikipedia_file_image(
                                client,
                                endpoint,
                                file_title,
                                page_url,
                                f"{host} wikitext image",
                                settings,
                            )
                            if resolved is not None:
                                return resolved

                        if rendered_html:
                            candidates = baidu_page_image_candidates(
                                rendered_html,
                                page_url,
                                (name, *aliases),
                            )
                            for _, image_url, label in candidates[:12]:
                                try:
                                    data = await _download_verified_image(
                                        client,
                                        image_url,
                                        page_url,
                                        settings,
                                    )
                                except (RuntimeError, httpx.HTTPError):
                                    continue
                                return EncyclopediaImage(
                                    data=data,
                                    source=f"{host} article context",
                                    page_url=page_url,
                                    label=label or page_title,
                                )

                    if not _wikipedia_result_matches(page, terms):
                        continue
                    image = page.get("original") or page.get("thumbnail") or {}
                    if not isinstance(image, dict):
                        continue
                    image_url = str(image.get("source") or "")
                    if not image_url.startswith("https://"):
                        continue
                    try:
                        data = await _download_verified_image(
                            client,
                            image_url,
                            page_url,
                            settings,
                        )
                    except (RuntimeError, httpx.HTTPError):
                        continue
                    return EncyclopediaImage(
                        data=data,
                        source=f"{host} PageImages",
                        page_url=page_url,
                        label=page_title,
                    )

        commons_endpoint = "https://commons.wikimedia.org/w/api.php"
        for query in queries:
            try:
                response = await client.get(
                    commons_endpoint,
                    params={
                        "action": "query",
                        "generator": "search",
                        "gsrsearch": query,
                        "gsrnamespace": 6,
                        "gsrlimit": 8,
                        "prop": "imageinfo",
                        "iiprop": "url|mime|size",
                        "iiurlwidth": 1200,
                        "format": "json",
                        "formatversion": 2,
                    },
                )
                response.raise_for_status()
                payload = response.json()
            except (ValueError, httpx.HTTPError):
                continue
            pages = payload.get("query", {}).get("pages", [])
            if not isinstance(pages, list):
                continue
            for page in pages:
                if not isinstance(page, dict):
                    continue
                file_title = str(page.get("title") or "")
                if not _matches_specific(file_title, terms):
                    continue
                infos = page.get("imageinfo", [])
                if not isinstance(infos, list) or not infos:
                    continue
                info = infos[0] if isinstance(infos[0], dict) else {}
                image_url = str(info.get("thumburl") or info.get("url") or "")
                if not image_url.startswith("https://"):
                    continue
                page_url = (
                    "https://commons.wikimedia.org/wiki/"
                    + quote(file_title.replace(" ", "_"), safe="():,_-")
                )
                try:
                    data = await _download_verified_image(
                        client,
                        image_url,
                        page_url,
                        settings,
                    )
                except (RuntimeError, httpx.HTTPError):
                    continue
                return EncyclopediaImage(
                    data=data,
                    source="Wikimedia Commons",
                    page_url=page_url,
                    label=file_title,
                )
    return None


def encyclopedia_reference_matches(
    name: str,
    aliases: tuple[str, ...],
    label: str,
) -> bool:
    """Return whether a selected encyclopedia image explicitly names the target."""
    terms = _specific_terms(name, aliases)
    return bool(terms) and _matches_specific(label, terms)


async def baidu_image_search_ultraman_image(
    name: str,
    aliases: tuple[str, ...],
    settings: Settings,
) -> EncyclopediaImage | None:
    """Use Baidu Images only for exact-form results backed by trusted source sites."""
    terms = _specific_terms(name, aliases)
    if not terms:
        return None

    searches = [name]
    searches.extend(
        alias
        for alias in aliases
        if alias and (re.search(r"[A-Za-z]", alias) or re.search(r"[\u3400-\u9fff]", alias))
    )
    searches = list(dict.fromkeys(searches))[:8]
    timeout = max(4.0, min(float(settings.media_timeout_seconds), 7.0))
    headers = {
        "User-Agent": ENCYCLOPEDIA_USER_AGENT,
        "Referer": "https://image.baidu.com/",
    }

    async with httpx.AsyncClient(
        timeout=timeout,
        follow_redirects=True,
        headers=headers,
    ) as client:
        for query in searches:
            try:
                response = await client.get(
                    "https://image.baidu.com/search/acjson",
                    params={
                        "tn": "resultjson_com",
                        "ipn": "rj",
                        "ct": "201326592",
                        "fp": "result",
                        "queryWord": query,
                        "word": query,
                        "ie": "utf-8",
                        "oe": "utf-8",
                        "pn": "0",
                        "rn": "20",
                        "newReq": "1",
                    },
                )
                response.raise_for_status()
                payload = response.json()
            except (ValueError, httpx.HTTPError):
                continue

            data = payload.get("data", [])
            if not isinstance(data, list):
                continue
            for item in data:
                if not isinstance(item, dict):
                    continue
                source_host = str(item.get("fromURLHost") or "").casefold().strip()
                if source_host not in BAIDU_IMAGE_TRUSTED_HOSTS:
                    continue

                title = html.unescape(
                    str(
                        item.get("fromPageTitleEnc")
                        or item.get("fromPageTitle")
                        or item.get("title")
                        or ""
                    )
                )
                image_name = html.unescape(
                    str(item.get("picInfo") or item.get("bdImgNewsInfo") or "")
                )
                descriptor = f"{title} {image_name}"
                if not _matches_specific(descriptor, terms):
                    continue

                image_url = str(
                    item.get("middleURL")
                    or item.get("thumbURL")
                    or item.get("hoverURL")
                    or ""
                )
                if image_url.startswith("http://"):
                    image_url = "https://" + image_url.removeprefix("http://")
                if not image_url.startswith("https://"):
                    continue

                page_url = str(item.get("fromURL") or "")
                if not page_url.startswith(("http://", "https://")):
                    page_url = f"https://{source_host}/"
                else:
                    page_url = quote(page_url, safe=":/?&=%#")
                try:
                    data_b64 = await _download_verified_image(
                        client,
                        image_url,
                        page_url,
                        settings,
                    )
                except (RuntimeError, httpx.HTTPError):
                    continue
                return EncyclopediaImage(
                    data=data_b64,
                    source="百度图片（可信百科/官方来源）",
                    page_url=page_url,
                    label=title or query,
                )
    return None


async def bing_image_search_ultraman_image(
    name: str,
    aliases: tuple[str, ...],
    settings: Settings,
) -> EncyclopediaImage | None:
    """Fallback to Bing Images, but only accept metadata that names the exact form."""
    terms = _specific_terms(name, aliases)
    if not terms:
        return None

    searches = [name]
    searches.extend(
        alias
        for alias in aliases
        if alias and (
            re.search(r"[A-Za-z]", alias)
            or re.search(r"[\u3040-\u30ff\u3400-\u9fff]", alias)
        )
    )
    searches = list(dict.fromkeys(searches))[:3]
    timeout = max(4.0, min(float(settings.media_timeout_seconds), 8.0))
    headers = {
        "User-Agent": ENCYCLOPEDIA_USER_AGENT,
        "Referer": "https://www.bing.com/images/",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.7,ja;q=0.5",
    }

    async with httpx.AsyncClient(
        timeout=timeout,
        follow_redirects=True,
        headers=headers,
    ) as client:
        for query in searches:
            try:
                response = await client.get(
                    "https://www.bing.com/images/async",
                    params={
                        "q": query,
                        "first": "1",
                        "count": "50",
                        "adlt": "strict",
                        "scenario": "ImageBasicHover",
                    },
                )
                response.raise_for_status()
            except httpx.HTTPError:
                continue

            parser = _BingImageResultParser()
            parser.feed(response.text)
            for item in parser.items[:50]:
                title = html.unescape(str(item.get("t") or ""))
                description = html.unescape(str(item.get("desc") or ""))
                original_url = html.unescape(str(item.get("murl") or "")).strip()
                thumb_url = html.unescape(
                    str(item.get("turl") or item.get("turl2") or "")
                ).strip()
                page_url = html.unescape(str(item.get("purl") or "")).strip()
                descriptor = f"{title} {description} {original_url} {page_url}"
                if not _matches_specific(descriptor, terms):
                    continue

                referer = (
                    page_url
                    if page_url.startswith(("https://", "http://"))
                    else "https://www.bing.com/images/"
                )
                referer = quote(referer, safe=":/?&=%#")
                image_urls = tuple(
                    dict.fromkeys(
                        url
                        for url in (original_url, thumb_url)
                        if url.startswith(("https://", "http://"))
                    )
                )
                for image_url in image_urls:
                    try:
                        data_b64 = await _download_verified_image(
                            client,
                            image_url,
                            referer,
                            settings,
                        )
                    except (RuntimeError, httpx.HTTPError):
                        continue
                    return EncyclopediaImage(
                        data=data_b64,
                        source=(
                            "Bing 图片精确形态匹配"
                            if image_url == original_url
                            else "Bing 缩略图精确形态匹配"
                        ),
                        page_url=page_url
                        or f"https://www.bing.com/images/search?q={quote(query)}",
                        label=title or description or query,
                    )
    return None


async def official_merch_ultraman_image(
    name: str,
    aliases: tuple[str, ...],
    settings: Settings,
) -> EncyclopediaImage | None:
    """Use official Bandai/TAMASHII product pages as a trusted form-image source.

    Older or alternate Ultra forms often lack a dedicated Tsuburaya character
    page but do have exact Bandai/TAMASHII product pages with official images.
    """
    terms = _specific_terms(name, aliases)
    if not terms:
        return None

    values = [name]
    values.extend(
        alias
        for alias in aliases
        if alias and (
            re.search(r"[A-Za-z]", alias)
            or re.search(r"[\u3040-\u30ff]", alias)
        )
    )
    values.extend(alias for alias in aliases if alias)
    values = list(dict.fromkeys(values))[:6]

    queries: list[str] = []
    for value in values:
        queries.extend(
            (
                f'site:tamashiiweb.com "{value}"',
                f'site:toy.bandai.co.jp "{value}"',
                f'site:p-bandai.jp "{value}"',
            )
        )
    queries = list(dict.fromkeys(queries))[:12]
    timeout = max(4.0, min(float(settings.media_timeout_seconds), 10.0))

    batches = await asyncio.gather(
        *(
            search_web(query, limit=6, timeout=timeout)
            for query in queries
        ),
        return_exceptions=True,
    )

    pages: list[SearchResult] = []
    seen: set[str] = set()
    for batch in batches:
        if isinstance(batch, BaseException):
            continue
        for result in batch:
            host = (urlparse(result.url).hostname or "").casefold()
            if host not in OFFICIAL_MERCH_HOSTS or result.url in seen:
                continue
            descriptor = f"{result.title} {result.snippet} {result.url}"
            if not _matches_specific(descriptor, terms):
                continue
            seen.add(result.url)
            pages.append(result)

    if not pages:
        return None

    headers = {
        "User-Agent": ENCYCLOPEDIA_USER_AGENT,
        "Accept-Language": "ja,en;q=0.9,zh-CN;q=0.8",
    }
    async with httpx.AsyncClient(
        timeout=timeout,
        follow_redirects=True,
        headers=headers,
    ) as client:
        for result in pages[:12]:
            try:
                page = await client.get(result.url)
                page.raise_for_status()
            except httpx.HTTPError:
                continue

            parser = _EncyclopediaPageParser()
            parser.feed(page.text)
            page_url = str(page.url)
            descriptor = (
                f"{result.title} {result.snippet} {parser.title} {page_url}"
            )
            if not _matches_specific(descriptor, terms):
                continue

            candidates: list[tuple[int, str, str]] = []
            if parser.og_image:
                image_url = _clean_image_url(parser.og_image, page_url)
                if image_url:
                    candidates.append(
                        (220, image_url, parser.title or result.title)
                    )

            for source, label in parser.images:
                image_url = _clean_image_url(source, page_url)
                if not image_url:
                    continue
                score = 0
                if _matches_specific(label, terms):
                    score += 180
                if _matches_specific(image_url, terms):
                    score += 80
                if score:
                    candidates.append((score, image_url, label))

            unique: dict[str, tuple[int, str, str]] = {}
            for candidate in candidates:
                current = unique.get(candidate[1])
                if current is None or candidate[0] > current[0]:
                    unique[candidate[1]] = candidate

            for _, image_url, label in sorted(
                unique.values(),
                key=lambda item: item[0],
                reverse=True,
            )[:10]:
                try:
                    data = await _download_verified_image(
                        client,
                        image_url,
                        page_url,
                        settings,
                    )
                except (RuntimeError, httpx.HTTPError):
                    continue
                return EncyclopediaImage(
                    data=data,
                    source="Bandai/TAMASHII 官方商品页",
                    page_url=page_url,
                    label=label or parser.title or result.title,
                )
    return None


async def web_page_ultraman_image(
    name: str,
    aliases: tuple[str, ...],
    settings: Settings,
) -> EncyclopediaImage | None:
    """Search normal web pages and extract a page-specific image.

    This bypasses image-search throttling. A result page must itself identify
    the requested hero/form before its OG image can be accepted.
    """
    terms = _specific_terms(name, aliases)
    if not terms:
        return None

    searches = [f'"{name}" 奥特曼 形态']
    searches.extend(
        f'"{alias}" Ultraman form'
        for alias in aliases[:6]
        if alias
    )
    searches.extend(
        (
            f'"{name}" 百度百科',
            f'"{name}" 円谷 公式',
            f'"{name}" official',
        )
    )

    timeout = max(5.0, min(float(settings.media_timeout_seconds), 15.0))
    headers = {
        "User-Agent": ENCYCLOPEDIA_USER_AGENT,
        "Accept-Language": "zh-CN,zh;q=0.9,ja;q=0.8,en;q=0.7",
    }
    seen: set[str] = set()

    for query in tuple(dict.fromkeys(searches))[:10]:
        try:
            results = await search_web(query, limit=10, timeout=timeout)
        except (ValueError, httpx.HTTPError):
            continue

        for result in results:
            if result.url in seen:
                continue
            seen.add(result.url)
            search_descriptor = f"{result.title} {result.snippet} {result.url}"
            if not _matches_specific(search_descriptor, terms):
                continue

            try:
                async with httpx.AsyncClient(
                    timeout=timeout,
                    follow_redirects=True,
                    headers=headers,
                ) as client:
                    page = await client.get(result.url)
                    page.raise_for_status()
                    parser = _EncyclopediaPageParser()
                    parser.feed(page.text)
                    page_url = str(page.url)
                    page_descriptor = (
                        f"{result.title} {result.snippet} "
                        f"{parser.title} {page_url}"
                    )
                    page_specific = _matches_specific(page_descriptor, terms)
                    if not page_specific:
                        continue

                    candidates: list[tuple[int, str, str]] = []
                    for source, label in parser.images:
                        image_url = _clean_image_url(source, page_url)
                        if not image_url:
                            continue
                        score = 0
                        if _matches_specific(label, terms):
                            score += 140
                        if _matches_specific(image_url, terms):
                            score += 60
                        if score:
                            candidates.append((score, image_url, label))

                    candidates.extend(
                        _raw_image_candidates(page.text, page_url, terms)
                    )

                    if parser.og_image:
                        og_url = _clean_image_url(parser.og_image, page_url)
                        if og_url:
                            candidates.append(
                                (80, og_url, parser.title or result.title)
                            )

                    unique: dict[str, tuple[int, str, str]] = {}
                    for item in candidates:
                        current = unique.get(item[1])
                        if current is None or item[0] > current[0]:
                            unique[item[1]] = item

                    for _, image_url, label in sorted(
                        unique.values(),
                        key=lambda item: item[0],
                        reverse=True,
                    )[:12]:
                        try:
                            data = await _download_verified_image(
                                client,
                                image_url,
                                page_url,
                                settings,
                            )
                        except (RuntimeError, httpx.HTTPError):
                            continue
                        return EncyclopediaImage(
                            data=data,
                            source="网页精确角色页",
                            page_url=page_url,
                            label=label or parser.title or result.title,
                        )
            except (ValueError, httpx.HTTPError):
                continue
    return None


async def search_engine_first_ultraman_image(
    name: str,
    aliases: tuple[str, ...],
    settings: Settings,
    extra_queries: tuple[str, ...] = (),
    *,
    require_metadata_match: bool = True,
) -> EncyclopediaImage | None:
    """Last-resort exact-name image search.

    Source sites are unrestricted here (Tencent Video, iQIYI, Bilibili,
    ordinary articles, etc.), but a candidate is accepted only when its own
    title/page/image metadata names the requested character or independent form.
    The search query itself never counts as identity evidence.
    """
    query_values = [query.strip() for query in extra_queries if query.strip()]
    query_values.append(name)
    query_values.extend(
        alias
        for alias in aliases
        if alias
        and (
            re.search(r"[A-Za-z]", alias)
            or re.search(r"[\u3040-\u30ff]", alias)
        )
    )
    queries = list(dict.fromkeys(query_values))[:8]
    strict_terms = _specific_terms(name, aliases)
    if require_metadata_match and not strict_terms:
        return None
    timeout = max(3.0, min(float(settings.media_timeout_seconds), 6.0))
    headers = {
        "User-Agent": ENCYCLOPEDIA_USER_AGENT,
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8,ja;q=0.7",
    }

    async with httpx.AsyncClient(
        timeout=timeout,
        follow_redirects=True,
        headers=headers,
    ) as client:
        # Baidu Images first: its ordered result list is usually strong for
        # Chinese Ultra form names, and middle/thumb URLs are QQ-friendly.
        for query in queries:
            try:
                response = await client.get(
                    "https://image.baidu.com/search/acjson",
                    params={
                        "tn": "resultjson_com",
                        "ipn": "rj",
                        "ct": "201326592",
                        "fp": "result",
                        "queryWord": query,
                        "word": query,
                        "ie": "utf-8",
                        "oe": "utf-8",
                        "pn": "0",
                        "rn": "10",
                        "newReq": "1",
                    },
                    headers={
                        "User-Agent": ENCYCLOPEDIA_USER_AGENT,
                        "Referer": "https://image.baidu.com/",
                    },
                )
                response.raise_for_status()
                payload = response.json()
            except (ValueError, httpx.HTTPError):
                continue

            items = payload.get("data", [])
            if isinstance(items, list):
                for item in items[:10]:
                    if not isinstance(item, dict):
                        continue
                    image_url = str(
                        item.get("middleURL")
                        or item.get("thumbURL")
                        or item.get("hoverURL")
                        or ""
                    ).strip()
                    if image_url.startswith("http://"):
                        image_url = "https://" + image_url.removeprefix("http://")
                    if not image_url.startswith("https://"):
                        continue
                    page_url = str(item.get("fromURL") or "").strip()
                    source_host = str(
                        item.get("fromURLHost") or ""
                    ).strip()
                    if not page_url.startswith(("https://", "http://")):
                        page_url = "https://image.baidu.com/"
                    title = html.unescape(
                        str(
                            item.get("fromPageTitleEnc")
                            or item.get("fromPageTitle")
                            or item.get("title")
                            or ""
                        )
                    )
                    descriptor = " ".join(
                        part
                        for part in (
                            title,
                            unquote(page_url),
                            source_host,
                            unquote(image_url),
                        )
                        if part
                    )
                    if require_metadata_match and not _matches_specific(
                        descriptor, strict_terms
                    ):
                        continue
                    try:
                        data_b64 = await _download_verified_image(
                            client,
                            image_url,
                            page_url,
                            settings,
                        )
                    except (RuntimeError, httpx.HTTPError):
                        continue
                    return EncyclopediaImage(
                        data=data_b64,
                        source=(
                            f"百度图片首图（{source_host}）"
                            if source_host
                            else "百度图片首图"
                        ),
                        page_url=page_url,
                        label=title or name,
                    )

        # Bing Images is the second unrestricted first-result source.
        for query in queries:
            try:
                response = await client.get(
                    "https://www.bing.com/images/async",
                    params={
                        "q": query,
                        "first": "1",
                        "count": "20",
                        "adlt": "strict",
                        "scenario": "ImageBasicHover",
                    },
                    headers={
                        "User-Agent": ENCYCLOPEDIA_USER_AGENT,
                        "Referer": "https://www.bing.com/images/",
                    },
                )
                response.raise_for_status()
            except httpx.HTTPError:
                continue

            parser = _BingImageResultParser()
            parser.feed(response.text)
            for item in parser.items[:20]:
                title = html.unescape(str(item.get("t") or ""))
                description = html.unescape(str(item.get("desc") or ""))
                page_url = html.unescape(
                    str(item.get("purl") or "")
                ).strip()
                thumb_url = html.unescape(
                    str(item.get("turl") or item.get("turl2") or "")
                ).strip()
                original_url = html.unescape(
                    str(item.get("murl") or "")
                ).strip()
                descriptor = " ".join(
                    part
                    for part in (
                        title,
                        description,
                        unquote(page_url),
                        unquote(thumb_url),
                        unquote(original_url),
                    )
                    if part
                )
                if require_metadata_match and not _matches_specific(
                    descriptor, strict_terms
                ):
                    continue
                referer = (
                    page_url
                    if page_url.startswith(("https://", "http://"))
                    else "https://www.bing.com/images/"
                )
                for image_url in dict.fromkeys(
                    url
                    for url in (thumb_url, original_url)
                    if url.startswith(("https://", "http://"))
                ):
                    try:
                        data_b64 = await _download_verified_image(
                            client,
                            image_url,
                            referer,
                            settings,
                        )
                    except (RuntimeError, httpx.HTTPError):
                        continue
                    return EncyclopediaImage(
                        data=data_b64,
                        source="Bing 图片首图",
                        page_url=page_url
                        or f"https://www.bing.com/images/search?q={quote(query)}",
                        label=title or description or name,
                    )
    return None


async def bing_image_relaxed_ultraman_image(
    name: str,
    aliases: tuple[str, ...],
    settings: Settings,
) -> EncyclopediaImage | None:
    """Final exact-query fallback when strict metadata is too sparse.

    The search query contains the exact hero/form name. For form variants we
    still require Bing metadata to mention the parent hero, which avoids using
    a totally unrelated Ultra image while preventing sparse titles from causing
    a false "no image" result.
    """
    strict_terms = _specific_terms(name, aliases)
    if not strict_terms:
        return None

    parent_name = name.split("·", 1)[0] if "·" in name else name
    parent_terms = _specific_terms(parent_name, ())
    query_values = [name]
    query_values.extend(alias for alias in aliases if alias)
    searches = [
        f'"{value}" 奥特曼 角色 形态 官方 设定'
        for value in tuple(dict.fromkeys(query_values))[:8]
    ]

    timeout = max(5.0, min(float(settings.media_timeout_seconds), 12.0))
    headers = {
        "User-Agent": ENCYCLOPEDIA_USER_AGENT,
        "Referer": "https://www.bing.com/images/",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8,ja;q=0.7",
    }
    endpoints = (
        ("https://www.bing.com/images/async", {"scenario": "ImageBasicHover"}),
        ("https://www.bing.com/images/search", {}),
    )

    async with httpx.AsyncClient(
        timeout=timeout,
        follow_redirects=True,
        headers=headers,
    ) as client:
        for query in searches:
            for endpoint, extras in endpoints:
                try:
                    response = await client.get(
                        endpoint,
                        params={
                            "q": query,
                            "first": "1",
                            "count": "50",
                            "adlt": "strict",
                            **extras,
                        },
                    )
                    response.raise_for_status()
                except httpx.HTTPError:
                    continue

                parser = _BingImageResultParser()
                parser.feed(response.text)
                for item in parser.items[:50]:
                    title = html.unescape(str(item.get("t") or ""))
                    description = html.unescape(str(item.get("desc") or ""))
                    original_url = html.unescape(
                        str(item.get("murl") or "")
                    ).strip()
                    thumb_url = html.unescape(
                        str(item.get("turl") or item.get("turl2") or "")
                    ).strip()
                    page_url = html.unescape(
                        str(item.get("purl") or "")
                    ).strip()
                    descriptor = (
                        f"{title} {description} {original_url} {page_url}"
                    )
                    exact_hit = _matches_specific(descriptor, strict_terms)
                    parent_hit = (
                        bool(parent_terms)
                        and _matches_specific(descriptor, parent_terms)
                    )
                    if not exact_hit and not parent_hit:
                        continue

                    referer = (
                        page_url
                        if page_url.startswith(("https://", "http://"))
                        else "https://www.bing.com/images/"
                    )
                    referer = quote(referer, safe=":/?&=%#")
                    # Prefer Bing's thumbnail on the relaxed fallback because
                    # original hosts often block hotlink downloads.
                    image_urls = tuple(
                        dict.fromkeys(
                            url
                            for url in (thumb_url, original_url)
                            if url.startswith(("https://", "http://"))
                        )
                    )
                    for image_url in image_urls:
                        try:
                            data_b64 = await _download_verified_image(
                                client,
                                image_url,
                                referer,
                                settings,
                            )
                        except (RuntimeError, httpx.HTTPError):
                            continue
                        return EncyclopediaImage(
                            data=data_b64,
                            source="Bing 精确查询最终兜底",
                            page_url=page_url
                            or (
                                "https://www.bing.com/images/search?q="
                                + quote(query)
                            ),
                            label=title or description or query,
                        )
    return None


async def encyclopedia_ultraman_image(
    name: str,
    aliases: tuple[str, ...],
    settings: Settings,
) -> EncyclopediaImage:
    baidu = await baidu_baike_ultraman_image(name, aliases, settings)
    if baidu is not None:
        return baidu
    # Moegirlpedia: exact character page -> internal search ->
    # character/series page image metadata. Use it after Baidu so existing
    # encyclopedia callers keep their established source order.
    moegirl = await moegirl_ultraman_image(name, aliases, settings)
    if moegirl is not None:
        return moegirl
    wikipedia = await wikipedia_ultraman_image(name, aliases, settings)
    if wikipedia is not None:
        return wikipedia
    baidu_image = await baidu_image_search_ultraman_image(name, aliases, settings)
    if baidu_image is not None:
        return baidu_image
    bing_image = await bing_image_search_ultraman_image(name, aliases, settings)
    if bing_image is not None:
        return bing_image
    official_merch = await official_merch_ultraman_image(name, aliases, settings)
    if official_merch is not None:
        return official_merch
    web_page = await web_page_ultraman_image(name, aliases, settings)
    if web_page is not None:
        return web_page
    relaxed = await bing_image_relaxed_ultraman_image(name, aliases, settings)
    if relaxed is not None:
        return relaxed
    first_result = await search_engine_first_ultraman_image(name, aliases, settings)
    if first_result is not None:
        return first_result
    raise RuntimeError(f"没有找到“{name}”的可下载代表图")
