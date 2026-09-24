import argparse
import asyncio
import base64
import hashlib
import json
from io import BytesIO
from pathlib import Path

from PIL import Image

from app.config import Settings
from app.services.anime_character import (
    ANIME_CHARACTER_ROSTER,
    resolve_anime_character_image,
)
from app.services.http_routing import install_outbound_proxy_environment


async def audit_character(character, settings: Settings, semaphore: asyncio.Semaphore):
    async with semaphore:
        row = {
            "name": character.name,
            "series": character.series,
            "aliases": list(character.aliases),
        }
        try:
            result = await resolve_anime_character_image(
                character,
                settings,
                None,
            )
            raw = base64.b64decode(
                result.data.removeprefix("base64://"),
                validate=True,
            )
            with Image.open(BytesIO(raw)) as image:
                image.load()
                width, height = image.size
                image_format = image.format
            if width < 180 or height < 180:
                raise RuntimeError(f"image too small: {width}x{height}")
            row.update(
                {
                    "status": "ok",
                    "width": width,
                    "height": height,
                    "format": image_format,
                    "bytes": len(raw),
                    "sha256": hashlib.sha256(raw).hexdigest(),
                    "provider": result.provider,
                    "source_page": result.source_page_url,
                    "image_url": result.image_url,
                    "label": result.label,
                    "cache_hit": result.cache_hit,
                }
            )
        # Audits must report a failure for this character and continue through
        # the complete catalog, regardless of which provider/decoder failed.
        except Exception as exc:  # noqa: BLE001
            row.update(
                {
                    "status": "missing",
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
        return row


async def run(
    report_path: Path,
    concurrency: int,
    timeout: float,
    require_all: bool,
) -> int:
    cache_dir = report_path.parent / ".anime-image-audit-cache"
    settings = Settings(
        anime_image_cache_dir=str(cache_dir),
        anime_image_resolve_timeout_seconds=timeout,
    )
    await install_outbound_proxy_environment(settings)
    semaphore = asyncio.Semaphore(max(1, concurrency))
    rows = await asyncio.gather(
        *(
            audit_character(character, settings, semaphore)
            for character in ANIME_CHARACTER_ROSTER
        )
    )

    digest_names: dict[str, list[str]] = {}
    for row in rows:
        digest = row.get("sha256")
        if digest:
            digest_names.setdefault(digest, []).append(row["name"])

    duplicate_groups = [
        names
        for names in digest_names.values()
        if len(names) > 1
    ]
    missing = [row["name"] for row in rows if row["status"] != "ok"]
    report = {
        "total": len(rows),
        "ok": len(rows) - len(missing),
        "missing": len(missing),
        "missing_names": missing,
        "duplicate_groups": duplicate_groups,
        "rows": rows,
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(
        f"Anime image audit: total={report['total']} "
        f"ok={report['ok']} missing={report['missing']} "
        f"duplicate_groups={len(duplicate_groups)}"
    )
    for row in rows:
        if row["status"] == "ok":
            print(
                f"[OK] {row['name']} | {row['width']}x{row['height']} "
                f"| {row['bytes']} bytes"
            )
        else:
            print(f"[MISSING] {row['name']} | {row.get('error', '')}")
    for names in duplicate_groups:
        print("[DUPLICATE] " + " / ".join(names))

    if require_all and missing:
        return 1
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Audit image availability for every anime character in the catalog."
    )
    parser.add_argument(
        "--report",
        default="anime-character-image-audit.json",
    )
    parser.add_argument("--concurrency", type=int, default=6)
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument("--require-all", action="store_true")
    args = parser.parse_args()
    return asyncio.run(
        run(
            Path(args.report),
            args.concurrency,
            args.timeout,
            args.require_all,
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())
