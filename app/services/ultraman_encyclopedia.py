import base64
import html
import re
import unicodedata
from dataclasses import dataclass
from html.parser import HTMLParser
from io import BytesIO
from urllib.parse import quote, urljoin, urlparse

import httpx
from PIL import Image

from app.config import Settings
from app.services.web_search import SearchResult, search_web

BAIDU_BAIKE_HOSTS = {
    "baike.baidu.com",
    "bkso.baidu.com",
    "wapbaike.baidu.com",
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
    "迪迦奥特曼·强力型": ("https://bkso.baidu.com/item/艾克斯奥特曼剧场版来了！我们的奥特曼/59156783",),
    "迪迦奥特曼·空中型": ("https://bkso.baidu.com/item/艾克斯奥特曼剧场版来了！我们的奥特曼/59156783",),
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
    "奈克瑟斯奥特曼·青年蓝色形态": ("https://bkso.baidu.com/item/孤门一辉/3556767",),
    "梦比优斯奥特曼·勇者形态": ("https://bkso.baidu.com/item/梦比优斯奥特曼/2684129",),
    "梦比优斯奥特曼·燃烧勇者": ("https://bkso.baidu.com/item/梦比优斯奥特曼/2684129",),
    "超级奥特曼泰罗": ("https://bkso.baidu.com/item/宇宙奇迹光线/20864681",),
    "维克特利骑士": (
        "https://bkso.baidu.com/item/新奥特曼列传/8010663",
        "https://bkso.baidu.com/item/维克特利奥特曼/53260527",
    ),
    "捷德奥特曼·原始形态": ("https://bkso.baidu.com/item/捷德奥特曼/20825718",),
    "捷德奥特曼·刚燃形态": ("https://bkso.baidu.com/item/捷德奥特曼/20825718",),
    "捷德奥特曼·机敏形态": ("https://bkso.baidu.com/item/捷德奥特曼/20825718",),
    "捷德奥特曼·豪勇形态": ("https://bkso.baidu.com/item/捷德奥特曼/20825718",),
    "捷德奥特曼·尊皇形态": ("https://bkso.baidu.com/item/捷德奥特曼/20825718",),
    "捷德奥特曼·银河初升": ("https://bkso.baidu.com/item/捷德奥特曼/20825718",),
    "泰迦奥特曼·光子地球": ("https://bkso.baidu.com/item/托雷基亚奥特曼/23366789",),
    "泰迦奥特曼·三重斯特利姆形态": ("https://bkso.baidu.com/item/托雷基亚奥特曼/23366789",),
    "闪耀特利迦永恒": ("https://bkso.baidu.com/item/特利迦奥特曼/56750285",),
    "真理特利迦": ("https://bkso.baidu.com/item/特利迦奥特曼/56750285",),
    "德凯奥特曼·强劲型": ("https://bkso.baidu.com/item/德凯奥特曼/59714231",),
    "布莱泽奥特曼·法多兰盔甲": (
        "https://bkso.baidu.com/item/法德兰辉石/65767412",
        "https://bkso.baidu.com/item/布莱泽奥特曼/62550634",
    ),
    "亚刻奥特曼·银河装甲": (
        "https://bkso.baidu.com/item/亚刻奥特曼/63972321",
        "https://bkso.baidu.com/item/亚刻魔方/64252054",
    ),
    "贝利亚早期形态": ("https://bkso.baidu.com/item/贝利亚奥特曼/7743176",),
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
    response = await client.get(
        image_url,
        headers={"Referer": page_url, "Accept": "image/avif,image/webp,image/*,*/*"},
    )
    response.raise_for_status()
    content_type = response.headers.get("content-type", "").casefold()
    if not content_type.startswith("image/"):
        raise RuntimeError("百科图片响应不是图片")
    if len(response.content) > settings.media_max_bytes:
        raise RuntimeError("百科图片超过大小限制")
    try:
        with Image.open(BytesIO(response.content)) as image:
            width, height = image.size
    except (OSError, ValueError) as exc:
        raise RuntimeError("百科图片无法解码") from exc
    if width < 160 or height < 160 or width * height < 40_000:
        raise RuntimeError("百科图片尺寸过小")
    return "base64://" + base64.b64encode(response.content).decode()


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
        start = max(0, match.start() - 700)
        end = min(len(wikitext), match.end() + 700)
        context = html.unescape(wikitext[start:end])
        file_title = match.group(1).strip()
        descriptor = f"{file_title} {context}"
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


async def encyclopedia_ultraman_image(
    name: str,
    aliases: tuple[str, ...],
    settings: Settings,
) -> EncyclopediaImage:
    # Baidu Baike usually has denser Chinese form galleries for the Ultra Series.
    # Prefer it first, then use Wikipedia/Wikimedia as the secondary source.
    baidu = await baidu_baike_ultraman_image(name, aliases, settings)
    if baidu is not None:
        return baidu
    wikipedia = await wikipedia_ultraman_image(name, aliases, settings)
    if wikipedia is not None:
        return wikipedia
    raise RuntimeError(f"没有找到“{name}”的可靠百科代表图")
