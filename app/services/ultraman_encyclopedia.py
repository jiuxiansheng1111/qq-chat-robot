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
    "闪耀迪迦": "https://baike.baidu.com/item/闪耀迪迦/1023751",
    "捷德奥特曼·尊皇形态": "https://baike.baidu.com/item/捷德奥特曼/20825718",
    "捷德奥特曼·银河初升": "https://baike.baidu.com/item/捷德奥特曼/20825718",
    "梦比优斯奥特曼·无限形态": (
        "https://baike.baidu.com/item/梦比优斯无限形态/8944775"
    ),
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
    # Do not auto-add a bare form suffix such as "强力型" / "空中型".
    # Those labels are shared by multiple Ultras and can validate the wrong image.
    candidates = [name, *aliases]
    terms: list[str] = []
    generic = {_normalize(value) for value in _GENERIC_IMAGE_TERMS}
    for value in candidates:
        normalized = _normalize(value)
        if (
            len(normalized) >= 3
            and normalized not in generic
            and normalized not in terms
        ):
            terms.append(normalized)
    return tuple(terms)


def _matches_specific(value: str, terms: tuple[str, ...]) -> bool:
    normalized = _normalize(value)
    return bool(normalized) and any(term in normalized for term in terms)


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

    for source, label in parser.images:
        image_url = _clean_image_url(source, page_url)
        if not image_url:
            continue
        score = 0
        if _matches_specific(label, terms):
            score += 150
        if _matches_specific(image_url, terms):
            score += 30
        if score:
            candidates.append((score, image_url, label))

    # Unlabelled JSON/HTML image URLs are only safe when the encyclopedia page
    # itself is specifically about the requested form. On a parent character
    # page they can sit beside unrelated form text and cause a wrong image match.
    if page_is_specific:
        candidates.extend(_raw_image_candidates(payload, page_url, terms))

    if page_is_specific and parser.og_image:
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
    searches = [name]
    searches.extend(alias for alias in aliases if re.search(r"[\u3400-\u9fff]", alias))
    seen_pages: set[str] = set()
    results = []

    direct_url = BAIDU_DIRECT_PAGES.get(name)
    if direct_url:
        seen_pages.add(direct_url)
        results.append(SearchResult(name, direct_url, "direct"))

    for query in searches[:3]:
        try:
            found = await search_web(f'"{query}" 百度百科', limit=6)
        except (ValueError, httpx.HTTPError):
            continue
        for result in found:
            host = (urlparse(result.url).hostname or "").casefold()
            if host in BAIDU_BAIKE_HOSTS and result.url not in seen_pages:
                seen_pages.add(result.url)
                results.append(result)

    if not results:
        return None

    timeout = max(5.0, min(float(settings.media_timeout_seconds), 20.0))
    headers = {"User-Agent": ENCYCLOPEDIA_USER_AGENT}
    async with httpx.AsyncClient(
        timeout=timeout,
        follow_redirects=True,
        headers=headers,
    ) as client:
        for result in results[:8]:
            try:
                page = await client.get(result.url)
                page.raise_for_status()
            except httpx.HTTPError:
                continue
            # Candidate labels/context perform the exact-form validation. Do not
            # reject a page merely because the form name lives in an image alt/JSON field.
            candidates = baidu_page_image_candidates(
                page.text,
                str(page.url),
                (name, *aliases),
            )
            for _, image_url, label in candidates[:8]:
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
    queries = [name, *aliases][:6]
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
    # Prefer Wikipedia/Wikimedia first: file titles and page titles provide
    # cleaner machine-checkable identity metadata than generic image search URLs.
    wikipedia = await wikipedia_ultraman_image(name, aliases, settings)
    if wikipedia is not None:
        return wikipedia
    baidu = await baidu_baike_ultraman_image(name, aliases, settings)
    if baidu is not None:
        return baidu
    raise RuntimeError(f"没有找到“{name}”的可靠百科代表图")
