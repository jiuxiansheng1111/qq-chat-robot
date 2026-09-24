"""Shared, immutable result contract for images sent through OneBot.

Providers are deliberately allowed to keep their small, legacy ``str`` helpers.
Only the public resolver boundary uses :class:`ImageResolution`, so source
attribution is not accidentally discarded while a candidate travels through a
cache or the OneBot send path.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from urllib.parse import urlparse


@dataclass(frozen=True)
class ImageResolution:
    """An image plus the evidence needed to trace where it came from.

    ``data`` is the existing OneBot-compatible ``base64://`` (or other image)
    value. Keeping that representation at this boundary avoids a broad and
    risky change to the established image normalization/send pipeline.
    """

    data: str
    provider: str
    source_page_url: str = ""
    image_url: str = ""
    label: str = ""
    evidence: str = ""
    cache_hit: bool = False
    width: int = 0
    height: int = 0

    @property
    def pixel_area(self) -> int:
        """Decoded pixel area used to prefer genuinely higher-resolution art."""
        return max(0, int(self.width)) * max(0, int(self.height))

    @property
    def source(self) -> str:
        """Compatibility spelling for existing provider/audit consumers."""
        return self.provider

    @property
    def page_url(self) -> str:
        """Compatibility spelling for existing provider/audit consumers."""
        return self.source_page_url

    def with_cache_hit(self, cache_hit: bool = True) -> ImageResolution:
        return replace(self, cache_hit=cache_hit)

    def attribution_text(self) -> str:
        """Return a compact, user-visible source line suitable for a caption."""
        provider = self.provider or "unknown"
        detail = f"（{self.label}）" if self.label else ""
        if _http_url(self.source_page_url):
            return f"图片来源：{provider}{detail} {self.source_page_url}"
        return f"图片来源：{provider}{detail}"

    def cache_metadata(self) -> dict[str, object]:
        """Serialize only provenance; image bytes remain in the image file."""
        payload = asdict(self)
        payload.pop("data", None)
        return payload

    @classmethod
    def from_cache_metadata(
        cls,
        data: str,
        payload: object,
        *,
        legacy_provider: str = "legacy/unknown",
    ) -> ImageResolution:
        if not isinstance(payload, dict):
            return cls(data=data, provider=legacy_provider, cache_hit=True)
        return cls(
            data=data,
            provider=str(payload.get("provider") or legacy_provider),
            source_page_url=str(payload.get("source_page_url") or ""),
            image_url=str(payload.get("image_url") or ""),
            label=str(payload.get("label") or ""),
            evidence=str(payload.get("evidence") or ""),
            cache_hit=True,
            width=int(payload.get("width") or 0),
            height=int(payload.get("height") or 0),
        )


def coerce_image_resolution(
    value: object,
    *,
    provider: str,
    label: str = "",
    evidence: str = "",
) -> ImageResolution | None:
    """Adapt legacy provider results without making them part of the API."""
    if isinstance(value, ImageResolution):
        return value
    if isinstance(value, str) and value:
        return ImageResolution(
            data=value,
            provider=provider,
            label=label,
            evidence=evidence,
        )
    if value is None:
        return None
    data = getattr(value, "data", None)
    if not isinstance(data, str) or not data:
        return None
    return ImageResolution(
        data=data,
        provider=str(getattr(value, "source", None) or provider),
        source_page_url=str(getattr(value, "page_url", None) or ""),
        image_url=str(getattr(value, "image_url", None) or ""),
        label=str(getattr(value, "label", None) or label),
        evidence=evidence,
        width=int(getattr(value, "width", 0) or 0),
        height=int(getattr(value, "height", 0) or 0),
    )


def append_image_attribution(caption: str, result: ImageResolution) -> str:
    """Append provenance once while preserving captions from legacy callers."""
    line = result.attribution_text()
    return f"{caption.rstrip()}\n{line}" if caption.strip() else line


def _http_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)
