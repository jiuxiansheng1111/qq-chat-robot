import argparse
import asyncio
import base64
import hashlib
import json
from dataclasses import asdict, dataclass
from io import BytesIO
from pathlib import Path

from PIL import Image

import app.services.ultraman as ultraman
from app.config import Settings
from app.services.ultraman_encyclopedia import (
    encyclopedia_reference_matches,
    encyclopedia_ultraman_image,
)


@dataclass
class AuditRow:
    name: str
    status: str
    source: str = ""
    label: str = ""
    page_url: str = ""
    sha256: str = ""
    width: int = 0
    height: int = 0
    error: str = ""


async def resolve_exact_image(hero, settings: Settings) -> tuple[str, str, str, str]:
    try:
        data = await ultraman.official_ultraman_image(hero, settings)
        if hero.name in ultraman._FORM_IMAGE_DIRECT_URLS:
            return (
                data,
                "Tsuburaya direct verified mapping",
                ultraman._FORM_IMAGE_DIRECT_URLS[hero.name],
                hero.name,
            )
        if hero.name in ultraman._FORM_IMAGE_PAGE_URLS:
            return (
                data,
                "Tsuburaya dedicated page",
                ultraman._FORM_IMAGE_PAGE_URLS[hero.name],
                hero.name,
            )
        return data, "Tsuburaya exact page image", "", hero.name
    except Exception as exc:
        first_error = f"official: {type(exc).__name__}: {exc}"

    try:
        data = await ultraman.official_ultraman_search_image(hero, settings)
        return data, "Tsuburaya exact labelled search", "", hero.name
    except Exception as exc:
        second_error = f"official-search: {type(exc).__name__}: {exc}"

    try:
        aliases = ultraman.ultraman_image_aliases(hero)
        image = await encyclopedia_ultraman_image(hero.name, aliases, settings)
        if not encyclopedia_reference_matches(hero.name, aliases, image.label):
            raise RuntimeError(
                f"百科图片标签未能精确证明目标形态: {image.label!r}"
            )
        return image.data, image.source, image.page_url, image.label
    except Exception as exc:
        third_error = f"encyclopedia: {type(exc).__name__}: {exc}"

    raise RuntimeError("; ".join((first_error, second_error, third_error)))


async def audit_one(hero, settings: Settings, semaphore: asyncio.Semaphore) -> AuditRow:
    async with semaphore:
        try:
            data, source, page_url, label = await resolve_exact_image(hero, settings)
            raw = base64.b64decode(data.removeprefix("base64://"))
            digest = hashlib.sha256(raw).hexdigest()
            with Image.open(BytesIO(raw)) as image:
                image.verify()
            with Image.open(BytesIO(raw)) as image:
                width, height = image.size
            if width < 160 or height < 160 or width * height < 40_000:
                raise RuntimeError(f"图片尺寸过小: {width}x{height}")
            return AuditRow(
                name=hero.name,
                status="ok",
                source=source,
                label=label,
                page_url=page_url,
                sha256=digest,
                width=width,
                height=height,
            )
        except Exception as exc:
            return AuditRow(
                name=hero.name,
                status="missing",
                error=f"{type(exc).__name__}: {exc}",
            )


async def run(report_path: Path, concurrency: int, require_all: bool) -> int:
    settings = Settings(_env_file=None)
    settings.media_timeout_seconds = min(float(settings.media_timeout_seconds), 12.0)
    settings.media_max_bytes = max(int(settings.media_max_bytes), 8 * 1024 * 1024)

    heroes = [
        hero
        for hero in ultraman.ULTRAMAN_ROSTER
        if ultraman.is_ultraman_form_variant(hero)
    ]
    semaphore = asyncio.Semaphore(max(1, min(concurrency, 6)))
    rows = await asyncio.gather(
        *(audit_one(hero, settings, semaphore) for hero in heroes)
    )

    hashes: dict[str, list[str]] = {}
    for row in rows:
        if row.status == "ok":
            hashes.setdefault(row.sha256, []).append(row.name)

    duplicate_groups = [
        names for names in hashes.values() if len(names) > 1
    ]
    duplicate_names = {name for group in duplicate_groups for name in group}
    for row in rows:
        if row.name in duplicate_names:
            group = next(group for group in duplicate_groups if row.name in group)
            row.status = "mismatch"
            row.error = "不同形态解析到了完全相同的图片: " + " / ".join(group)

    ok = sum(row.status == "ok" for row in rows)
    missing = sum(row.status == "missing" for row in rows)
    mismatch = sum(row.status == "mismatch" for row in rows)

    payload = {
        "total": len(rows),
        "ok": ok,
        "missing": missing,
        "mismatch": mismatch,
        "duplicates": duplicate_groups,
        "rows": [asdict(row) for row in rows],
    }
    report_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(f"Ultraman image audit: total={len(rows)} ok={ok} missing={missing} mismatch={mismatch}")
    for row in rows:
        marker = "OK" if row.status == "ok" else row.status.upper()
        details = (
            f"{row.source} | {row.label} | {row.width}x{row.height}"
            if row.status == "ok"
            else row.error
        )
        print(f"[{marker}] {row.name}: {details}")

    if mismatch:
        return 2
    if require_all and missing:
        return 3
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Audit every independent Ultraman form image using strict exact-name matching."
    )
    parser.add_argument(
        "--report",
        default="ultraman-image-audit.json",
        help="JSON report path",
    )
    parser.add_argument("--concurrency", type=int, default=3)
    parser.add_argument("--require-all", action="store_true")
    args = parser.parse_args()
    return asyncio.run(
        run(Path(args.report), args.concurrency, args.require_all)
    )


if __name__ == "__main__":
    raise SystemExit(main())
