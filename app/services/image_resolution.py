"""通过 OneBot 发送图片时使用的共享、不可变结果结构。

允许各来源继续使用小型旧版 ``str`` helper。只有公开解析器边界使用 :class:`ImageResolution`，避免候选图片经过缓存或 OneBot 发送流程时丢失来源信息。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from urllib.parse import urlparse


@dataclass(frozen=True)
class ImageResolution:
    """图片内容，以及追溯来源所需的证据。

    ``data`` 保留现有 OneBot 兼容的 ``base64://`` 或其他图片值。在这个边界沿用该格式，可避免大幅改动现有图片规范化和发送流程。
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
        """解码后的像素面积，用于优先选择分辨率更高的图片。"""
        return max(0, int(self.width)) * max(0, int(self.height))

    @property
    def source(self) -> str:
        """兼容旧版来源方/审计脚本使用的字段名。"""
        return self.provider

    @property
    def page_url(self) -> str:
        """兼容旧版来源方/审计脚本使用的字段名。"""
        return self.source_page_url

    def with_cache_hit(self, cache_hit: bool = True) -> ImageResolution:
        return replace(self, cache_hit=cache_hit)

    def attribution_text(self) -> str:
        """返回适合放进图片说明的简短来源文字。"""
        provider = self.provider or "unknown"
        detail = f"（{self.label}）" if self.label else ""
        if _http_url(self.source_page_url):
            return f"图片来源：{provider}{detail} {self.source_page_url}"
        return f"图片来源：{provider}{detail}"

    def cache_metadata(self) -> dict[str, object]:
        """只序列化来源信息；图片字节仍保存在图片文件中。"""
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
    """转换旧版来源方的结果，不把旧格式扩展成公共 API。"""
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
    """只追加一次来源说明，并保留旧调用方传入的图片说明。"""
    line = result.attribution_text()
    return f"{caption.rstrip()}\n{line}" if caption.strip() else line


def _http_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)
