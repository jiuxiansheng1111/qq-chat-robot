"""Audit Moegirl metadata for the anime catalog without downloading images.

This is a low-rate inventory, not proof that every remote image can be sent to
QQ. The report belongs under ignored data/ and keeps missing entries explicit.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from urllib.parse import urlparse

import httpx

from app.config import Settings
from app.services.anime_character import (
    ANIME_CHARACTER_ROSTER,
    _candidate_name_matches,
    _is_moegirl_disambiguation,
    _moegirl_page_evidence,
    _moegirl_pages,
    _moegirl_title_candidates,
    _normalize,
    _series_match_terms,
)
from app.services.http_routing import install_outbound_proxy_environment

API_URL = "https://zh.moegirl.org.cn/api.php"


def error_text(exc: Exception) -> str:
    """Keep timeout/status error classes visible when their message is empty."""
    return (str(exc).strip() or type(exc).__name__)[:240]


def _retryable_http_error(exc: httpx.HTTPError) -> bool:
    """Retry transport errors and throttling/server failures, never ordinary 4xx."""
    if not isinstance(exc, httpx.HTTPStatusError):
        return True
    status = exc.response.status_code
    return status == 429 or 500 <= status < 600


def match_page(character, pages: list[dict]) -> tuple[dict | None, dict[str, int]]:
    """Return a strict source and auditable reasons for a non-match."""
    series_terms = _series_match_terms(character)
    diagnostics = {
        "returned_pages": len(pages),
        "disambiguation_pages": 0,
        "identity_title_matches": 0,
        "series_evidence_misses": 0,
        "without_pageimage": 0,
        "untrusted_image_host": 0,
    }
    for page in pages:
        if page.get("missing"):
            continue
        if _is_moegirl_disambiguation(page):
            diagnostics["disambiguation_pages"] += 1
            continue
        title = str(page.get("title") or "")
        if not _candidate_name_matches(character, character.aliases, [title]):
            continue
        diagnostics["identity_title_matches"] += 1
        evidence = _normalize(_moegirl_page_evidence(page))
        if series_terms and not any(term in evidence for term in series_terms):
            diagnostics["series_evidence_misses"] += 1
            continue
        source_page = str(page.get("fullurl") or "")
        original = page.get("original")
        thumbnail = page.get("thumbnail")
        image = next(
            (
                str(value.get("source") or "")
                for value in (original, thumbnail)
                if isinstance(value, dict) and value.get("source")
            ),
            "",
        )
        if not image:
            diagnostics["without_pageimage"] += 1
            continue
        if urlparse(source_page).hostname != "zh.moegirl.org.cn":
            diagnostics["untrusted_image_host"] += 1
            continue
        if not (urlparse(image).hostname or "").endswith(".moegirl.org.cn"):
            diagnostics["untrusted_image_host"] += 1
            continue
        return {
            "title": title,
            "source_page_url": source_page,
            "image_url": image,
            "original_width": int(original.get("width") or 0) if isinstance(original, dict) else 0,
            "original_height": int(original.get("height") or 0) if isinstance(original, dict) else 0,
        }, diagnostics
    return None, diagnostics


def non_match_classification(diagnostics: dict[str, int]) -> str:
    """Name the strongest observed reason without treating it as a source."""
    if diagnostics["without_pageimage"]:
        return "identified_without_pageimage"
    if diagnostics["untrusted_image_host"]:
        return "untrusted_image_host"
    if diagnostics["series_evidence_misses"]:
        return "series_evidence_missing"
    if diagnostics["disambiguation_pages"] and not diagnostics["identity_title_matches"]:
        return "disambiguation_only"
    if diagnostics["identity_title_matches"]:
        return "identified_but_unusable"
    if diagnostics["returned_pages"]:
        return "title_or_alias_not_matched"
    return "no_results"


def returned_titles(pages: list[dict]) -> list[str]:
    """Keep a small, non-sensitive review trail for alias/title mismatches."""
    return list(
        dict.fromkeys(
            str(page.get("title") or "").strip()
            for page in pages
            if not page.get("missing") and str(page.get("title") or "").strip()
        )
    )[:10]


async def fetch_pages(
    client: httpx.AsyncClient,
    *,
    titles: list[str] | None = None,
    search: str | None = None,
    retries: int = 0,
    retry_backoff: float = 0.5,
) -> list[dict]:
    if bool(titles) == bool(search):
        raise ValueError("pass exactly one of titles or search")
    params = {
        "action": "query",
        "redirects": "1",
        "prop": "pageimages|info|extracts|categories|pageprops",
        "ppprop": "disambiguation",
        "piprop": "original|thumbnail",
        "pithumbsize": "1200",
        "inprop": "url",
        "exintro": "1",
        "explaintext": "1",
        "exsentences": "3",
        "cllimit": "max",
        "format": "json",
        "formatversion": "2",
    }
    if titles:
        params["titles"] = "|".join(titles)
    else:
        params.update({"generator": "search", "gsrsearch": search, "gsrnamespace": "0", "gsrlimit": "10"})
    for attempt in range(retries + 1):
        try:
            response = await client.get(API_URL, params=params)
            response.raise_for_status()
            return _moegirl_pages(response.json())
        except httpx.HTTPError as exc:
            if attempt >= retries or not _retryable_http_error(exc):
                raise
            await asyncio.sleep(retry_backoff * (2 ** attempt))
    raise RuntimeError("unreachable retry loop")


async def run(
    report_path: Path,
    *,
    batch_size: int,
    limit: int,
    delay: float,
    retries: int,
    retry_backoff: float,
) -> dict:
    settings = Settings()
    if not settings.moegirl_image_provider_enabled or not settings.anime_moegirl_only:
        raise RuntimeError("enable MOEGIRL_IMAGE_PROVIDER_ENABLED and ANIME_MOEGIRL_ONLY first")
    await install_outbound_proxy_environment(settings)
    characters = list(ANIME_CHARACTER_ROSTER[:limit] if limit else ANIME_CHARACTER_ROSTER)
    rows: list[dict] = []
    async with httpx.AsyncClient(
        timeout=20,
        follow_redirects=True,
        headers={"User-Agent": "qq-chatrobot/0.1 (low-rate source audit)"},
    ) as client:
        # Request a few characters' exact titles/aliases together.  Matching
        # still runs per character, so a returned alias or redirect cannot be
        # silently credited to another catalog record.  This keeps a complete
        # 300-entry audit practical without increasing the request rate.
        for start in range(0, len(characters), batch_size):
            batch = characters[start : start + batch_size]
            direct_titles = list(
                dict.fromkeys(
                    title
                    for character in batch
                    for title in _moegirl_title_candidates(character)
                )
            )
            try:
                pages = await fetch_pages(
                    client,
                    titles=direct_titles,
                    retries=retries,
                    retry_backoff=retry_backoff,
                )
            except (httpx.HTTPError, ValueError, AttributeError) as exc:
                rows.extend(
                    {
                        "name": character.name,
                        "series": character.series,
                        "status": "request_failed",
                        "error": error_text(exc),
                    }
                    for character in batch
                )
            else:
                for character in batch:
                    source, direct_diagnostics = match_page(character, pages)
                    diagnostics = {f"direct_{key}": value for key, value in direct_diagnostics.items()}
                    candidates = {"direct": returned_titles(pages)}
                    via = "direct"
                    # A page may exist yet legitimately omit pageimages.
                    # Search after direct titles/aliases fail to produce a
                    # usable strict result, retaining that fact in the report.
                    if source is None:
                        try:
                            search_pages = await fetch_pages(
                                client,
                                search=f"{character.name} {character.series.strip('《》 ')}",
                                retries=retries,
                                retry_backoff=retry_backoff,
                            )
                        except (httpx.HTTPError, ValueError, AttributeError) as exc:
                            rows.append({
                                "name": character.name,
                                "series": character.series,
                                "status": "request_failed",
                                "error": error_text(exc),
                            })
                            await asyncio.sleep(delay)
                            continue
                        source, search_diagnostics = match_page(character, search_pages)
                        diagnostics.update(
                            {f"search_{key}": value for key, value in search_diagnostics.items()}
                        )
                        candidates["search"] = returned_titles(search_pages)
                        via = "search"
                        await asyncio.sleep(delay)
                    combined_diagnostics = {
                        key: sum(
                            value for label, value in diagnostics.items() if label.endswith(key)
                        )
                        for key in direct_diagnostics
                    }
                    classification = (
                        "matched" if source else non_match_classification(combined_diagnostics)
                    )
                    rows.append({
                        "name": character.name,
                        "series": character.series,
                        "status": "matched" if source else (
                            "identified_without_pageimage"
                            if classification == "identified_without_pageimage" else "not_verified"
                        ),
                        "match_path": via if source else None,
                        "classification": classification,
                        "diagnostics": diagnostics,
                        "candidate_titles": candidates if source is None else None,
                        **(source or {}),
                    })
            if start + batch_size < len(characters):
                await asyncio.sleep(delay)
    request_failed = sum(row["status"] == "request_failed" for row in rows)
    matched = sum(row["status"] == "matched" for row in rows)
    completed_requests = len(rows) - request_failed
    report = {
        "total": len(rows),
        "matched": matched,
        "identified_without_pageimage": sum(row["status"] == "identified_without_pageimage" for row in rows),
        "not_verified": sum(row["status"] == "not_verified" for row in rows),
        "request_failed": request_failed,
        "strict_match_rate_all_catalog": round(matched / len(rows), 4) if rows else 0,
        "strict_match_rate_completed_requests": (
            round(matched / completed_requests, 4) if completed_requests else 0
        ),
        "classification_counts": {
            name: sum(row.get("classification") == name for row in rows)
            for name in (
                "identified_without_pageimage",
                "untrusted_image_host",
                "series_evidence_missing",
                "disambiguation_only",
                "identified_but_unusable",
                "title_or_alias_not_matched",
                "no_results",
            )
        },
        "metadata_only": True,
        "rows": rows,
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, default=Path("data/anime-moegirl-source-audit.json"))
    parser.add_argument("--batch-size", type=int, default=3, help="exact-title requests per API call")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--delay", type=float, default=0.35, help="seconds between catalog entries")
    parser.add_argument("--retries", type=int, default=3, help="retries for transient API failures")
    parser.add_argument("--retry-backoff", type=float, default=0.7, help="initial retry delay in seconds")
    args = parser.parse_args()
    if (
        not 1 <= args.batch_size <= 5
        or args.limit < 0
        or args.delay < 0.2
        or args.retries < 0
        or args.retry_backoff < 0.1
    ):
        parser.error("batch-size 1..5, non-negative limit/retries, delay >=0.2, retry-backoff >=0.1")
    report = asyncio.run(
        run(
            args.report,
            batch_size=args.batch_size,
            limit=args.limit,
            delay=args.delay,
            retries=args.retries,
            retry_backoff=args.retry_backoff,
        )
    )
    print(
        f"Moegirl metadata audit: total={report['total']} matched={report['matched']} "
        f"no_pageimage={report['identified_without_pageimage']} "
        f"not_verified={report['not_verified']} request_failed={report['request_failed']}"
    )
    return 0 if report["request_failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
