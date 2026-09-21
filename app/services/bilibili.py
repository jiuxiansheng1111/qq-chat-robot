import base64
import html
import re
import unicodedata
from dataclasses import dataclass

import httpx

from app.config import Settings

BILIBILI_HOME_URL = "https://www.bilibili.com/"
BILIBILI_SEARCH_TYPE_URL = "https://api.bilibili.com/x/web-interface/search/type"
BILIBILI_SEARCH_ALL_URL = "https://api.bilibili.com/x/web-interface/search/all/v2"
BILIBILI_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36"
)


@dataclass(frozen=True)
class BilibiliVideo:
    bvid: str
    title: str
    author: str
    description: str
    tags: str
    cover_url: str
    play: int
    duration: str
    pubdate: int

    @property
    def url(self) -> str:
        return f"https://www.bilibili.com/video/{self.bvid}"


def _strip_html(value: object) -> str:
    text = html.unescape(str(value or ""))
    text = re.sub(r"<[^>]+>", "", text)
    return re.sub(r"\s+", " ", text).strip()


def _normalize_cover_url(value: object) -> str:
    url = str(value or "").strip()
    if url.startswith("//"):
        return "https:" + url
    if url.startswith("http://"):
        return "https://" + url.removeprefix("http://")
    return url if url.startswith("https://") else ""


def _parse_play(value: object) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, (int, float)):
        return max(0, int(value))
    text = str(value or "").replace(",", "").strip().casefold()
    if not text or text in {"--", "-", "none", "null"}:
        return 0
    multiplier = 1
    if text.endswith("万"):
        multiplier = 10_000
        text = text[:-1]
    elif text.endswith("亿"):
        multiplier = 100_000_000
        text = text[:-1]
    match = re.search(r"\d+(?:\.\d+)?", text)
    if not match:
        return 0
    return max(0, int(float(match.group(0)) * multiplier))


def parse_bilibili_video(item: dict) -> BilibiliVideo | None:
    bvid = str(item.get("bvid") or "").strip()
    if not re.fullmatch(r"BV[0-9A-Za-z]{8,16}", bvid):
        return None
    title = _strip_html(item.get("title"))
    if not title:
        return None
    return BilibiliVideo(
        bvid=bvid,
        title=title[:160],
        author=_strip_html(item.get("author") or item.get("up_name"))[:80],
        description=_strip_html(
            item.get("description") or item.get("desc") or item.get("description_v2")
        )[:500],
        tags=_strip_html(item.get("tag"))[:300],
        cover_url=_normalize_cover_url(item.get("pic") or item.get("cover")),
        play=_parse_play(item.get("play") or item.get("view")),
        duration=_strip_html(item.get("duration"))[:20],
        pubdate=int(item.get("pubdate") or item.get("senddate") or 0),
    )


def _compact(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", normalized)


def _query_terms(query: str) -> list[str]:
    normalized = unicodedata.normalize("NFKC", query).casefold()
    raw_terms = re.findall(r"[0-9a-z]+|[\u4e00-\u9fff]{2,}", normalized)
    compact = _compact(query)
    terms: list[str] = []
    for term in [*raw_terms, compact]:
        term = _compact(term)
        if len(term) >= 2 and term not in terms:
            terms.append(term)
    return terms[:8]


def _bigrams(value: str) -> set[str]:
    compact = _compact(value)
    if len(compact) < 2:
        return {compact} if compact else set()
    return {compact[index : index + 2] for index in range(len(compact) - 1)}


def bilibili_relevance_score(query: str, video: BilibiliVideo) -> float:
    query_compact = _compact(query)
    title = _compact(video.title)
    description = _compact(video.description)
    tags = _compact(video.tags)
    author = _compact(video.author)
    if not query_compact or not title:
        return 0.0

    score = 0.0
    if title == query_compact:
        score += 180
    elif query_compact in title:
        score += 125

    terms = _query_terms(query)
    title_hits = sum(1 for term in terms if term in title)
    body_hits = sum(1 for term in terms if term in description or term in tags)
    author_hits = sum(1 for term in terms if term in author)
    score += title_hits * 32
    score += body_hits * 9
    score += author_hits * 4

    query_bigrams = _bigrams(query_compact)
    if query_bigrams:
        title_overlap = len(query_bigrams & _bigrams(title)) / len(query_bigrams)
        score += title_overlap * 70

    # A result that only mentions the query outside the title should stay behind
    # genuinely matching titles no matter how many views it has.
    if title_hits == 0 and query_compact not in title:
        score *= 0.55
    return score


def _relevance_tier(query: str, video: BilibiliVideo) -> int:
    query_compact = _compact(query)
    title = _compact(video.title)
    if not query_compact or not title:
        return 0
    if query_compact in title:
        return 3

    terms = [term for term in _query_terms(query) if term != query_compact]
    if terms and all(term in title for term in terms):
        return 2
    if any(term in title for term in terms):
        return 1
    return 0


def choose_bilibili_video(
    query: str,
    videos: list[BilibiliVideo],
) -> BilibiliVideo | None:
    if not videos:
        return None

    scored: list[tuple[int, int, float, int, BilibiliVideo]] = []
    for video in videos:
        relevance = bilibili_relevance_score(query, video)
        tier = _relevance_tier(query, video)
        if relevance < 18 or tier == 0:
            continue
        # Semantic tier comes first. Once two titles are both strong matches,
        # playback becomes the main tie-breaker; the fine-grained score and date
        # only decide between similarly popular candidates.
        scored.append((tier, video.play, relevance, video.pubdate, video))

    if not scored:
        return None
    scored.sort(key=lambda item: item[:4], reverse=True)
    return scored[0][4]


def _extract_video_items(payload: dict) -> list[dict]:
    data = payload.get("data")
    if not isinstance(data, dict):
        return []
    result = data.get("result")
    if not isinstance(result, list):
        return []

    if result and isinstance(result[0], dict) and "result_type" in result[0]:
        for section in result:
            if (
                isinstance(section, dict)
                and section.get("result_type") == "video"
                and isinstance(section.get("data"), list)
            ):
                return [item for item in section["data"] if isinstance(item, dict)]
        return []
    return [item for item in result if isinstance(item, dict)]


async def _request_search(
    client: httpx.AsyncClient,
    url: str,
    params: dict[str, object],
) -> tuple[list[dict], str]:
    response = await client.get(url, params=params)
    response.raise_for_status()
    payload = response.json()
    code = int(payload.get("code", -1))
    if code != 0:
        return [], str(payload.get("message") or payload.get("msg") or code)
    return _extract_video_items(payload), ""


async def search_bilibili_videos(
    query: str,
    settings: Settings,
) -> list[BilibiliVideo]:
    keyword = re.sub(r"\s+", " ", query).strip()[:100]
    if not keyword:
        return []

    headers = {
        "User-Agent": BILIBILI_USER_AGENT,
        "Referer": "https://www.bilibili.com/",
        "Accept": "application/json,text/plain,*/*",
    }
    timeout = max(3.0, min(float(settings.bilibili_timeout_seconds), 20.0))
    async with httpx.AsyncClient(
        timeout=timeout,
        follow_redirects=True,
        headers=headers,
    ) as client:
        try:
            await client.get(BILIBILI_HOME_URL)
        except httpx.HTTPError:
            pass

        raw_items: list[dict] = []
        errors: list[str] = []
        for order in ("totalrank", "click"):
            try:
                items, error = await _request_search(
                    client,
                    settings.bilibili_search_url,
                    {
                        "search_type": "video",
                        "keyword": keyword,
                        "page": 1,
                        "page_size": max(
                            10, min(settings.bilibili_search_result_limit, 30)
                        ),
                        "order": order,
                    },
                )
                raw_items.extend(items)
                if error:
                    errors.append(f"{order}: {error}")
            except (ValueError, httpx.HTTPError) as exc:
                errors.append(f"{order}: {exc}")

        if not raw_items:
            try:
                items, error = await _request_search(
                    client,
                    BILIBILI_SEARCH_ALL_URL,
                    {"keyword": keyword, "page": 1},
                )
                raw_items.extend(items)
                if error:
                    errors.append(f"fallback: {error}")
            except (ValueError, httpx.HTTPError) as exc:
                errors.append(f"fallback: {exc}")

    videos: list[BilibiliVideo] = []
    seen: set[str] = set()
    for item in raw_items:
        video = parse_bilibili_video(item)
        if video is None or video.bvid in seen:
            continue
        seen.add(video.bvid)
        videos.append(video)
    if not videos and errors:
        raise RuntimeError("B站搜索失败：" + "; ".join(errors[-3:]))
    return videos


def format_play_count(play: int) -> str:
    if play >= 100_000_000:
        value = f"{play / 100_000_000:.1f}".rstrip("0").rstrip(".")
        return value + "亿"
    if play >= 10_000:
        value = f"{play / 10_000:.1f}".rstrip("0").rstrip(".")
        return value + "万"
    return str(max(0, play))


def bilibili_card_content(video: BilibiliVideo) -> str:
    parts = []
    if video.author:
        parts.append(f"UP：{video.author}")
    parts.append(f"播放：{format_play_count(video.play)}")
    if video.duration:
        parts.append(f"时长：{video.duration}")
    return " · ".join(parts)[:160]


async def download_bilibili_cover(
    video: BilibiliVideo,
    settings: Settings,
) -> str:
    if not video.cover_url:
        raise RuntimeError("B站视频没有可用封面")
    timeout = max(3.0, min(float(settings.media_timeout_seconds), 20.0))
    headers = {
        "User-Agent": BILIBILI_USER_AGENT,
        "Referer": video.url,
    }
    async with httpx.AsyncClient(
        timeout=timeout,
        follow_redirects=True,
        headers=headers,
    ) as client:
        response = await client.get(video.cover_url)
        response.raise_for_status()
    if not response.headers.get("content-type", "").startswith("image/"):
        raise RuntimeError("B站封面响应不是图片")
    if len(response.content) > settings.media_max_bytes:
        raise RuntimeError("B站封面超过大小限制")
    return "base64://" + base64.b64encode(response.content).decode()
