"""Opt-in importer for a Moegirl character category/search result.

This is intentionally a command-line import rather than an automatic startup
scraper. The operator must confirm they have permission for the deployment
scenario, then choose the exact categories/search terms to import. The output
is the existing ``app/data/anime_characters_extra.json`` format, so the bot's
normal aliases, image validation and confirmation flow continue to apply.
"""

import argparse
import asyncio
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote

import httpx

API_URL = "https://zh.moegirl.org.cn/api.php"
USER_AGENT = "qq-chatrobot/0.1 (opt-in character catalog importer)"


def _clean(text: str, limit: int = 260) -> str:
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    return text[:limit].rstrip("，。；; ")


async def _get(client: httpx.AsyncClient, params: dict) -> dict:
    response = await client.get(API_URL, params=params)
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict) or "error" in payload:
        raise RuntimeError(f"萌娘百科 API 返回错误：{payload.get('error', payload)}")
    return payload


async def category_titles(client: httpx.AsyncClient, category: str, limit: int) -> list[str]:
    titles: list[str] = []
    continuation: dict = {}
    while len(titles) < limit:
        payload = await _get(
            client,
            {
                "action": "query",
                "list": "categorymembers",
                "cmtitle": category if category.startswith("Category:") else f"Category:{category}",
                "cmnamespace": "0",
                "cmtype": "page",
                "cmlimit": min(500, limit - len(titles)),
                "format": "json",
                "formatversion": "2",
                **continuation,
            },
        )
        titles.extend(
            str(item.get("title") or "").strip()
            for item in payload.get("query", {}).get("categorymembers", [])
            if str(item.get("title") or "").strip()
        )
        continuation = payload.get("continue") or {}
        if not continuation:
            break
    return list(dict.fromkeys(titles))[:limit]


async def search_titles(client: httpx.AsyncClient, query: str, limit: int) -> list[str]:
    payload = await _get(
        client,
        {
            "action": "query",
            "list": "search",
            "srsearch": query,
            "srnamespace": "0",
            "srlimit": min(500, limit),
            "format": "json",
            "formatversion": "2",
        },
    )
    return list(
        dict.fromkeys(
            str(item.get("title") or "").strip()
            for item in payload.get("query", {}).get("search", [])
            if str(item.get("title") or "").strip()
        )
    )[:limit]


async def page_records(client: httpx.AsyncClient, titles: list[str], series: str) -> list[dict]:
    records: list[dict] = []
    for start in range(0, len(titles), 50):
        batch = titles[start : start + 50]
        payload = await _get(
            client,
            {
                "action": "query",
                "titles": "|".join(batch),
                "prop": "extracts|info",
                "inprop": "url",
                "exintro": "1",
                "explaintext": "1",
                "exsentences": "5",
                "format": "json",
                "formatversion": "2",
            },
        )
        for page in payload.get("query", {}).get("pages", []):
            title = _clean(page.get("title"), 100)
            extract = _clean(page.get("extract"), 300)
            if not title or not extract or title.startswith(("萌娘百科:", "Template:", "Category:")):
                continue
            records.append(
                {
                    "name": title,
                    "series": series,
                    "description": extract or f"{title}的公开角色资料。",
                    "aliases": [],
                    "source": {
                        "provider": "萌娘百科",
                        "page_url": str(page.get("fullurl") or "")
                        or "https://zh.moegirl.org.cn/" + quote(title.replace(" ", "_")),
                    },
                    "review_status": "needs_review",
                    "imported_at": datetime.now(UTC).isoformat(),
                }
            )
    return records


async def run(args: argparse.Namespace) -> int:
    if not args.i_have_permission:
        raise SystemExit("请明确传入 --i-have-permission；不会默认抓取萌娘百科。")
    if not args.category and not args.search:
        raise SystemExit("至少提供 --category 或 --search。")
    headers = {"User-Agent": USER_AGENT}
    timeout = min(max(float(args.timeout), 5), 60)
    async with httpx.AsyncClient(timeout=timeout, headers=headers, trust_env=False) as client:
        titles: list[str] = []
        for category in args.category:
            titles.extend(await category_titles(client, category, args.limit))
        for query in args.search:
            titles.extend(await search_titles(client, query, args.limit))
        records = await page_records(client, list(dict.fromkeys(titles))[: args.limit * max(1, len(args.category) + len(args.search))], args.series)
    output = Path(args.output)
    existing: list[dict] = []
    if output.exists():
        payload = json.loads(output.read_text(encoding="utf-8"))
        if isinstance(payload, list):
            existing = [item for item in payload if isinstance(item, dict)]
    merged: dict[str, dict] = {
        str(item.get("name")): item for item in existing if str(item.get("name") or "").strip()
    }
    for item in records:
        merged.setdefault(item["name"], item)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(list(merged.values()), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"导入完成：新增 {len(merged) - len(existing)} 条，当前额外角色 {len(merged)} 条，文件：{output}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Opt-in import of Moegirl character pages")
    parser.add_argument("--i-have-permission", action="store_true")
    parser.add_argument("--category", action="append", default=[])
    parser.add_argument("--search", action="append", default=[])
    parser.add_argument("--series", default="萌娘百科角色（待人工核对作品归属）")
    parser.add_argument("--limit", type=int, default=1000)
    parser.add_argument("--timeout", type=float, default=15)
    parser.add_argument("--output", default="app/data/anime_characters_extra.json")
    return asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
