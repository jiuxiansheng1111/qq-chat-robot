import argparse
import asyncio
import base64
import hashlib
import json
from dataclasses import asdict, dataclass
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageStat

from app.config import Settings
from app.services import ultraman
from app.services.http_routing import install_outbound_proxy_environment
from app.services.ultraman_encyclopedia import encyclopedia_ultraman_image


@dataclass
class AuditRow:
    name: str
    status: str
    source: str = ""
    provider: str = ""
    label: str = ""
    page_url: str = ""
    source_page: str = ""
    image_url: str = ""
    cache_hit: bool = False
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
    # Try each independent source and preserve its diagnostic in the report.
    except Exception as exc:  # noqa: BLE001
        first_error = f"official: {type(exc).__name__}: {exc}"

    try:
        data = await ultraman.official_ultraman_search_image(hero, settings)
        return data, "Tsuburaya exact labelled search", "", hero.name
    # A single unavailable provider must not stop the audit fallback chain.
    except Exception as exc:  # noqa: BLE001
        second_error = f"official-search: {type(exc).__name__}: {exc}"

    try:
        aliases = ultraman.ultraman_image_aliases(hero)
        image = await encyclopedia_ultraman_image(hero.name, aliases, settings)
        return image.data, image.source, image.page_url, image.label
    # The audit needs a combined diagnostic if every source is unavailable.
    except Exception as exc:  # noqa: BLE001
        third_error = f"encyclopedia: {type(exc).__name__}: {exc}"

    raise RuntimeError(f"{first_error}; {second_error}; {third_error}")


async def audit_one(hero, settings: Settings, semaphore: asyncio.Semaphore) -> AuditRow:
    async with semaphore:
        try:
            data, source, page_url, label = await resolve_exact_image(hero, settings)
            raw = base64.b64decode(data.removeprefix("base64://"))
            digest = hashlib.sha256(raw).hexdigest()
            with Image.open(BytesIO(raw)) as image:
                image.verify()
            with Image.open(BytesIO(raw)) as image:
                rgb = ultraman.normalize_ultraman_source_image(image)
                width, height = rgb.size
                sample = rgb.copy()
                sample.thumbnail((256, 256), Image.Resampling.BILINEAR)
                stats = ImageStat.Stat(sample)
                mean_luma = sum(stats.mean) / 3
                channel_spread = max(high - low for low, high in sample.getextrema())
                entropy = sample.entropy()
                histogram = sample.convert("L").histogram()
                pixels = max(1, sum(histogram))
                near_black_fraction = sum(histogram[:24]) / pixels
                visible_fraction = sum(histogram[48:]) / pixels
            if width < 160 or height < 160 or width * height < 40_000:
                raise RuntimeError(f"图片尺寸过小: {width}x{height}")
            if entropy < 0.75:
                raise RuntimeError(f"图片近似纯色/空白: entropy={entropy:.3f}")
            if near_black_fraction >= 0.90 and visible_fraction <= 0.08:
                raise RuntimeError(
                    "图片近似全黑: "
                    f"dark={near_black_fraction:.3f}, visible={visible_fraction:.3f}"
                )
            if mean_luma < 26 and visible_fraction <= 0.12:
                raise RuntimeError(
                    f"图片过暗不可见: luma={mean_luma:.1f}, visible={visible_fraction:.3f}"
                )
            if mean_luma < 42 and channel_spread < 35 and entropy < 2.2:
                raise RuntimeError(
                    f"图片近似全黑: luma={mean_luma:.1f}, spread={channel_spread}, entropy={entropy:.3f}"
                )
            card = ultraman.render_ultraman_card(hero, data)
            card_raw = base64.b64decode(card.removeprefix("base64://"))
            with Image.open(BytesIO(card_raw)) as rendered:
                rendered_rgb = rendered.convert("RGB")
                card_sample = rendered_rgb.copy()
                card_sample.thumbnail((256, 256), Image.Resampling.BILINEAR)
                card_entropy = card_sample.entropy()
                card_mean = sum(ImageStat.Stat(card_sample).mean) / 3
            if card_entropy < 1.0 or card_mean < 24:
                raise RuntimeError(
                    f"渲染卡片疑似黑屏: luma={card_mean:.1f}, entropy={card_entropy:.3f}"
                )
            return AuditRow(
                name=hero.name,
                status="ok",
                source=source,
                provider=source,
                label=label,
                page_url=page_url,
                source_page=page_url,
                sha256=digest,
                width=width,
                height=height,
            )
        # Keep auditing remaining heroes even if this candidate is corrupt or
        # a provider fails unexpectedly; the row records the exact exception.
        except Exception as exc:  # noqa: BLE001
            return AuditRow(
                name=hero.name,
                status="missing",
                error=f"{type(exc).__name__}: {exc}",
            )


async def run(
    report_path: Path,
    concurrency: int,
    require_all: bool,
    forms_only: bool = False,
) -> int:
    settings = Settings()
    await install_outbound_proxy_environment(settings)
    settings.media_timeout_seconds = min(float(settings.media_timeout_seconds), 12.0)
    settings.media_max_bytes = max(int(settings.media_max_bytes), 8 * 1024 * 1024)

    # Audit the complete roster, not only independent forms. A green report must
    # mean every drawable entry used by 今日奥特曼/图鉴 has a decodable image.
    heroes = list(ultraman.ULTRAMAN_ROSTER)
    if forms_only:
        heroes = [hero for hero in heroes if ultraman.is_ultraman_form_variant(hero)]
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
        description="Audit every Ultraman roster image using strict name/source validation."
    )
    parser.add_argument(
        "--report",
        default="ultraman-image-audit.json",
        help="JSON report path",
    )
    parser.add_argument("--concurrency", type=int, default=3)
    parser.add_argument("--require-all", action="store_true")
    parser.add_argument(
        "--forms-only",
        action="store_true",
        help="Audit only independent form variants instead of the full roster",
    )
    args = parser.parse_args()
    return asyncio.run(
        run(
            Path(args.report),
            args.concurrency,
            args.require_all,
            args.forms_only,
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())
