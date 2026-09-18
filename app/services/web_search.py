import html
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from urllib.parse import urlparse

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


async def search_web(query: str, limit: int = 5, timeout: float = 12) -> list[SearchResult]:
    headers = {"User-Agent": "Mozilla/5.0 qq-chatrobot/0.1"}
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True, headers=headers) as client:
        response = await client.get(
            "https://www.bing.com/search", params={"q": query, "format": "rss"}
        )
        response.raise_for_status()
    return parse_bing_rss(response.text, limit)
