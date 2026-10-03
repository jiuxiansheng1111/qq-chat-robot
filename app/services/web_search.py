import html
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
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


def parse_google_news_rss(
    payload: str,
    limit: int = 20,
    *,
    now: datetime | None = None,
    max_age_hours: int = 48,
) -> list[SearchResult]:
    """解析 Google News RSS 中较新的条目，并使用 RSS 发布时间。"""
    root = ET.fromstring(payload)
    current = (now or datetime.now(UTC)).astimezone(UTC)
    oldest = current - timedelta(hours=max(1, max_age_hours))
    results: list[SearchResult] = []
    for item in root.findall(".//item"):
        title = _plain_text(item.findtext("title", ""))
        url = (item.findtext("link", "") or "").strip()
        source = _plain_text(item.findtext("source", ""))
        published_text = (item.findtext("pubDate", "") or "").strip()
        try:
            published = parsedate_to_datetime(published_text)
            if published.tzinfo is None:
                published = published.replace(tzinfo=UTC)
            published = published.astimezone(UTC)
        except (TypeError, ValueError, OverflowError):
            continue
        # 过滤未来时间和过期的搜索页；允许一点
        # 允许小幅时钟偏差，确保刚刚发布的有效新闻仍可使用。
        if published < oldest or published > current + timedelta(minutes=15):
            continue
        if not title or urlparse(url).scheme not in {"http", "https"}:
            continue
        age = current - published
        if age < timedelta(hours=1):
            age_text = f"{max(1, int(age.total_seconds() // 60))} 分钟前"
        else:
            age_text = f"{max(1, int(age.total_seconds() // 3600))} 小时前"
        snippet = f"{source + ' · ' if source else ''}发布于 {age_text}"
        results.append(SearchResult(title, url, snippet))
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


async def search_news_feed(
    topic: str = "TOP",
    *,
    limit: int = 20,
    timeout: float = 12,
    max_age_hours: int = 48,
) -> list[SearchResult]:
    """获取带实时发布时间的中文 Google News RSS 新闻版块。"""
    topic = re.sub(r"[^A-Z]", "", str(topic or "TOP").upper()) or "TOP"
    allowed_topics = {
        "TOP",
        "NATION",
        "WORLD",
        "BUSINESS",
        "TECHNOLOGY",
        "ENTERTAINMENT",
        "SPORTS",
        "SCIENCE",
        "HEALTH",
    }
    if topic not in allowed_topics:
        raise ValueError(f"不支持的新闻分类：{topic}")
    if topic == "TOP":
        url = "https://news.google.com/rss"
    else:
        url = f"https://news.google.com/rss/headlines/section/topic/{topic}"
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 Chrome/131 Safari/537.36 qq-chatrobot/0.1"
        ),
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.7",
    }
    async with httpx.AsyncClient(
        timeout=timeout,
        follow_redirects=True,
        headers=headers,
    ) as client:
        response = await client.get(
            url,
            params={"hl": "zh-CN", "gl": "CN", "ceid": "CN:zh-Hans"},
        )
        response.raise_for_status()
    try:
        return parse_google_news_rss(
            response.text,
            limit=limit,
            max_age_hours=max_age_hours,
        )
    except ET.ParseError as exc:
        raise RuntimeError(f"新闻 RSS 解析失败：{exc}") from exc
