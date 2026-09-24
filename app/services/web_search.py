import html
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import parse_qs, unquote, urlparse

import httpx


@dataclass(frozen=True)
class SearchResult:
    title: str
    url: str
    snippet: str


def _plain_text(value: str) -> str:
    value = re.sub(r"<[^>]+>", " ", html.unescape(value or ""))
    return re.sub(r"\s+", " ", value).strip()


def parse_bing_rss(payload: str, limit: int = 5) -> list[SearchResult]:
    root = ET.fromstring(payload)
    results: list[SearchResult] = []
    for item in root.findall(".//item"):
        title = _plain_text(item.findtext("title", ""))
        url = (item.findtext("link", "") or "").strip()
        snippet = _plain_text(item.findtext("description", ""))
        if title and urlparse(url).scheme in {"http", "https"}:
            results.append(SearchResult(title, url, snippet[:400]))
        if len(results) >= limit:
            break
    return results


def _duckduckgo_target(url: str) -> str:
    url = html.unescape(url or "").strip()
    if url.startswith("//"):
        url = "https:" + url
    parsed = urlparse(url)
    if parsed.hostname and parsed.hostname.endswith("duckduckgo.com"):
        target = parse_qs(parsed.query).get("uddg", [])
        if target:
            url = unquote(target[0])
    return url


class _DuckDuckGoParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.results: list[SearchResult] = []
        self._href = ""
        self._title_parts: list[str] = []
        self._snippet_parts: list[str] = []
        self._in_title = False
        self._in_snippet = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {key.casefold(): value or "" for key, value in attrs}
        classes = set(values.get("class", "").split())
        if tag.casefold() == "a" and "result__a" in classes:
            self._href = _duckduckgo_target(values.get("href", ""))
            self._title_parts = []
            self._in_title = True
        elif "result__snippet" in classes:
            self._snippet_parts = []
            self._in_snippet = True

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self._title_parts.append(data)
        if self._in_snippet:
            self._snippet_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.casefold() == "a" and self._in_title:
            self._in_title = False
            title = _plain_text(" ".join(self._title_parts))
            if title and urlparse(self._href).scheme in {"http", "https"}:
                self.results.append(SearchResult(title, self._href, ""))
        if self._in_snippet and tag.casefold() in {"a", "div", "span"}:
            self._in_snippet = False
            snippet = _plain_text(" ".join(self._snippet_parts))[:400]
            if snippet and self.results:
                latest = self.results[-1]
                if not latest.snippet:
                    self.results[-1] = SearchResult(latest.title, latest.url, snippet)


def parse_duckduckgo_html(payload: str, limit: int = 5) -> list[SearchResult]:
    parser = _DuckDuckGoParser()
    parser.feed(payload)
    return parser.results[:limit]


def _deduplicate(results: list[SearchResult], limit: int) -> list[SearchResult]:
    unique = []
    seen_urls: set[str] = set()
    seen_titles: set[str] = set()
    for item in results:
        title_key = re.sub(r"\s+", "", item.title).casefold()
        if not item.url or item.url in seen_urls or title_key in seen_titles:
            continue
        seen_urls.add(item.url)
        seen_titles.add(title_key)
        unique.append(item)
        if len(unique) >= limit:
            break
    return unique


async def search_web(query: str, limit: int = 5, timeout: float = 12) -> list[SearchResult]:
    query = re.sub(r"\s+", " ", str(query or "")).strip()[:160]
    if not query:
        raise ValueError("搜索关键词不能为空")
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 Chrome/131 Safari/537.36 qq-chatrobot/0.1"
        ),
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.7",
    }
    collected: list[SearchResult] = []
    errors: list[str] = []
    async with httpx.AsyncClient(
        timeout=timeout,
        follow_redirects=True,
        headers=headers,
    ) as client:
        try:
            response = await client.get(
                "https://www.bing.com/search",
                params={"q": query, "format": "rss"},
            )
            response.raise_for_status()
            collected.extend(parse_bing_rss(response.text, limit))
        except (ET.ParseError, ValueError, httpx.HTTPError) as exc:
            errors.append(f"Bing RSS: {type(exc).__name__}: {exc}")

        if len(collected) < limit:
            try:
                response = await client.get(
                    "https://html.duckduckgo.com/html/",
                    params={"q": query, "kl": "cn-zh"},
                )
                response.raise_for_status()
                collected.extend(parse_duckduckgo_html(response.text, limit))
            except (ValueError, httpx.HTTPError) as exc:
                errors.append(f"DuckDuckGo: {type(exc).__name__}: {exc}")

    results = _deduplicate(collected, limit)
    if results:
        return results
    if errors:
        raise RuntimeError("；".join(errors))
    return []
