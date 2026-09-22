from __future__ import annotations

import argparse
import asyncio
import base64
import json
import sys
import time
from io import BytesIO
from pathlib import Path
from typing import Any

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import Settings
import app.services.ultraman as ultraman_module
from app.services.ultraman import (
    ULTRAMAN_ROSTER,
    is_ultraman_form_variant,
    official_ultraman_image,
    official_ultraman_search_image,
    ultraman_image_aliases,
)
from app.services.ultraman_encyclopedia import (
    encyclopedia_reference_matches,
    encyclopedia_ultraman_image,
)


def _image_size(data: str) -> tuple[int, int]:
    raw = base64.b64decode(data.removeprefix("base64://"))
    with Image.open(BytesIO(raw)) as image:
        return image.size


def _official_page_url(hero) -> str:
    dedicated = ultraman_module._FORM_IMAGE_PAGE_URLS.get(hero.name, "")
    if dedicated:
        return dedicated
    if hero.page_path:
        return "https://tsuburaya-prod.com/" + hero.page_path.lstrip("/")
    return f"{ultraman_module.OFFICIAL_HERO_BASE_URL}/{hero.slug}"


async def audit_one(hero, settings: Settings, semaphore: asyncio.Semaphore) -> dict[str, Any]:
    started = time.perf_counter()
    aliases = ultraman_image_aliases(hero)
    result: dict[str, Any] = {
        "name": hero.name,
        "variant": is_ultraman_form_variant(hero),
        "status": "missing",
        "source": "",
        "label": "",
        "page_url": "",
        "width": 0,
        "height": 0,
        "elapsed_ms": 0,
        "official_error": "",
        "encyclopedia_error": "",
    }

    async with semaphore:
        try:
            data = await official_ultraman_image(hero, settings)
        except Exception as exc:  # audit must continue through the whole roster
            result["official_error"] = f"{type(exc).__name__}: {exc}"[:500]
        else:
            try:
                width, height = _image_size(data)
            except Exception as exc:
                result["status"] = "decode_error"
                result["source"] = "圆谷官方"
                result["page_url"] = _official_page_url(hero)
                result["encyclopedia_error"] = f"{type(exc).__name__}: {exc}"[:500]
            else:
                result.update(
                    status="ok",
                    source="圆谷官方",
                    label=hero.name,
                    page_url=_official_page_url(hero),
                    width=width,
                    height=height,
                )

        if result["status"] == "missing" and is_ultraman_form_variant(hero):
            try:
                data = await official_ultraman_search_image(hero, settings)
            except Exception as exc:
                previous = result["official_error"]
                exact_error = f"{type(exc).__name__}: {exc}"[:500]
                result["official_error"] = (
                    (previous + " | exact-search: " + exact_error) if previous else exact_error
                )[:900]
            else:
                try:
                    width, height = _image_size(data)
                except Exception as exc:
                    result.update(
                        status="decode_error",
                        source="圆谷官方精确搜索",
                        encyclopedia_error=f"{type(exc).__name__}: {exc}"[:500],
                    )
                else:
                    result.update(
                        status="ok",
                        source="圆谷官方精确搜索",
                        label=hero.name,
                        width=width,
                        height=height,
                    )

        if result["status"] == "missing":
            try:
                found = await encyclopedia_ultraman_image(hero.name, aliases, settings)
            except Exception as exc:
                result["encyclopedia_error"] = f"{type(exc).__name__}: {exc}"[:500]
            else:
                if not encyclopedia_reference_matches(hero.name, aliases, found.label):
                    result.update(
                        status="mismatch",
                        source=found.source,
                        label=found.label,
                        page_url=found.page_url,
                    )
                else:
                    try:
                        width, height = _image_size(found.data)
                    except Exception as exc:
                        result.update(
                            status="decode_error",
                            source=found.source,
                            label=found.label,
                            page_url=found.page_url,
                            encyclopedia_error=f"{type(exc).__name__}: {exc}"[:500],
                        )
                    else:
                        result.update(
                            status="ok",
                            source=found.source,
                            label=found.label,
                            page_url=found.page_url,
                            width=width,
                            height=height,
                        )

    result["elapsed_ms"] = round((time.perf_counter() - started) * 1000)
    return result


async def run(args: argparse.Namespace) -> int:
    settings = Settings(_env_file=None)
    # Keep the full audit bounded on CI while still allowing normal image sizes.
    settings.media_timeout_seconds = min(float(settings.media_timeout_seconds), args.timeout)
    semaphore = asyncio.Semaphore(max(1, args.concurrency))

    heroes = list(ULTRAMAN_ROSTER)
    if args.variants_only:
        heroes = [hero for hero in heroes if is_ultraman_form_variant(hero)]
    if args.limit:
        heroes = heroes[: args.limit]

    tasks = [audit_one(hero, settings, semaphore) for hero in heroes]
    rows = await asyncio.gather(*tasks)

    for index, row in enumerate(rows, 1):
        size = f"{row['width']}x{row['height']}" if row["width"] else "-"
        print(
            f"[{index:03d}/{len(rows):03d}] {row['status'].upper():12} "
            f"{row['name']} | {row['source'] or '-'} | {size} | "
            f"{row['label'] or '-'}"
        )

    summary = {
        "total": len(rows),
        "ok": sum(row["status"] == "ok" for row in rows),
        "missing": sum(row["status"] == "missing" for row in rows),
        "mismatch": sum(row["status"] == "mismatch" for row in rows),
        "decode_error": sum(row["status"] == "decode_error" for row in rows),
    }
    payload = {"summary": summary, "items": rows}
    output = Path(args.output)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\nSUMMARY " + json.dumps(summary, ensure_ascii=False))
    print(f"REPORT {output}")

    if summary["mismatch"] or summary["decode_error"]:
        return 2
    if args.require_image and summary["missing"]:
        return 3
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Audit every Ultraman card image source without saving the images."
    )
    parser.add_argument("--output", default="ultraman-image-audit.json")
    parser.add_argument("--concurrency", type=int, default=3)
    parser.add_argument("--timeout", type=float, default=12.0)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--variants-only", action="store_true")
    parser.add_argument(
        "--require-image",
        action="store_true",
        help="Also fail when a character has no reliable image. By default missing is safer than a wrong image.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run(parse_args())))
