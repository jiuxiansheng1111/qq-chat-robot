"""清理 QQ 文本里的来源链接。"""

from __future__ import annotations

import ipaddress
import re
from urllib.parse import urlsplit

_ALLOWED_DOMAINS = frozenset(
    {
        "163.com",
        "qq.com",
        "bilibili.com",
        "b23.tv",
        "baidu.com",
        "zhihu.com",
        "weibo.com",
        "weibo.cn",
        "douyin.com",
        "acfun.cn",
        "gitee.com",
        "xiaohongshu.com",
        "miyoushe.com",
    }
)

_KNOWN_SOURCE_NAMES = {
    "giphy.com": "GIPHY",
    "pixiv.net": "Pixiv",
    "wikipedia.org": "维基百科",
    "wikimedia.org": "维基百科",
    "github.com": "GitHub",
    "youtube.com": "YouTube",
    "youtu.be": "YouTube",
    "twitter.com": "Twitter",
    "x.com": "X",
    "reddit.com": "Reddit",
    "instagram.com": "Instagram",
    "facebook.com": "Facebook",
    "twitch.tv": "Twitch",
    "tiktok.com": "TikTok",
    "soundcloud.com": "SoundCloud",
}
_COMPOUND_SUFFIXES = frozenset({"co.uk", "com.cn", "net.cn", "org.cn", "com.au", "co.jp"})

_MARKDOWN_LINK_RE = re.compile(
    r"!?\[(?P<label>[^\]\r\n]+)\]"
    r"\((?:<[^>\r\n]*>|(?:[^()\r\n]|\([^()\r\n]*\))*)"
    r"(?:\s+[\"'][^\"']*[\"'])?\s*\)"
)
_URL_RE = re.compile(
    r"(?i)(?<![\w@])(?:https?://|www\.|(?:[a-z0-9-]+\.)+"
    r"(?:com|cn|net|org|io|tv|jp|uk|be|au|ru|de)(?=/|\b))[^\s<>\[\]{}\"'“”‘’]*"
)
_TRAILING_PUNCTUATION = ".,!?;:，。！？；：、…）】》"


def _split_url_and_punctuation(raw_url: str) -> tuple[str, str]:
    url = raw_url
    while url and url[-1] in _TRAILING_PUNCTUATION:
        url = url[:-1]
    while url.endswith(")") and url.count(")") > url.count("("):
        url = url[:-1]
    return url, raw_url[len(url) :]


def _host_from_url(url: str) -> str | None:
    candidate = url if "://" in url else f"http://{url}"
    try:
        return urlsplit(candidate).hostname
    except ValueError:
        return None


def _is_allowed_domain(host: str) -> bool:
    normalized = host.casefold().rstrip(".")
    return any(
        normalized == domain or normalized.endswith(f".{domain}")
        for domain in _ALLOWED_DOMAINS
    )


def _source_name(host: str | None) -> str:
    if not host:
        return "外部来源"
    normalized = host.casefold().rstrip(".")
    normalized = normalized.removeprefix("www.")

    for domain, label in _KNOWN_SOURCE_NAMES.items():
        if normalized == domain or normalized.endswith(f".{domain}"):
            return label

    try:
        ipaddress.ip_address(normalized)
    except ValueError:
        pass
    else:
        return "外部来源"

    labels = normalized.split(".")
    if len(labels) < 2:
        return "外部来源"
    suffix = ".".join(labels[-2:])
    source = labels[-3] if suffix in _COMPOUND_SUFFIXES and len(labels) >= 3 else labels[-2]
    try:
        source = source.encode("ascii").decode("idna")
    except (UnicodeError, UnicodeEncodeError):
        pass
    source = re.sub(r"[-_]+", " ", source).strip()
    return source.title() if source else "外部来源"


def sanitize_qq_source_links(text: str) -> str:
    """保留常见国内站点链接，其他直链改成文字来源。"""

    if not text:
        return text

    def replace_markdown(match: re.Match[str]) -> str:
        destination = match.group(0).split("](", 1)[1]
        url_match = _URL_RE.search(destination)
        if url_match:
            url, _ = _split_url_and_punctuation(url_match.group(0))
            host = _host_from_url(url)
            if host and _is_allowed_domain(host):
                return match.group(0)
            return match.group("label")
        return match.group(0)

    text = _MARKDOWN_LINK_RE.sub(replace_markdown, text)

    def replace_bare_url(match: re.Match[str]) -> str:
        raw_url = match.group(0)
        url, punctuation = _split_url_and_punctuation(raw_url)
        if not url:
            return raw_url
        host = _host_from_url(url)
        if host and _is_allowed_domain(host):
            return url + punctuation
        return _source_name(host) + punctuation

    return _URL_RE.sub(replace_bare_url, text)
