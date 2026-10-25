"""Traceable local Markdown registration; unverified videos are not training data."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

from app.services.mahjong.records import RecordError

if TYPE_CHECKING:
    from app.services.mahjong.analysis import Candidate, DecisionView


@dataclass(frozen=True)
class KnowledgeSource:
    id: str
    title: str
    kind: str
    status: str
    file: str
    sha256: str
    source: str
    verified_by: str
    timecodes: tuple[str, ...] = ()


@dataclass(frozen=True)
class KnowledgePassage:
    topic: str
    text: str
    applicability: str
    source_id: str


def markdown_sha256(data: bytes) -> str:
    """Git/Windows line endings are equivalent; all other content remains integrity checked."""
    return hashlib.sha256(data.replace(b"\r\n", b"\n")).hexdigest()


def retrieve_passages(
    root: Path,
    view: DecisionView,
    candidates: tuple[Candidate, ...],
    sources: tuple[KnowledgeSource, ...] | None = None,
) -> tuple[KnowledgePassage, ...]:
    """Read the self-contained local guide; references/URLs are never fetched."""
    selected = sources if sources is not None else load_sources(root)
    guide = next(
        (s for s in selected if s.kind == "teaching" and s.file.endswith("LOCAL_GUIDE.md")), None
    )
    if guide is None:
        raise RecordError("本地主题教材未登记或未核验，不能声称已调用知识")
    path = (root / guide.file).resolve(strict=True)
    if not path.is_relative_to(root.resolve()):
        raise RecordError("教材路径不在项目内")
    raw = path.read_bytes()
    if markdown_sha256(raw) != guide.sha256:
        raise RecordError("教材哈希变化，需重新核验")
    sections = {}
    heading = None
    for line in raw.decode("utf-8").splitlines():
        if line.startswith("## "):
            heading = line.removeprefix("## ").strip()
            sections[heading] = []
        elif heading is not None:
            sections[heading].append(line)
    topics = []
    if any(c.id.startswith("riichi:") for c in candidates):
        topics.append("七 立直与默听比较低目和代价")
    if any(c.id.startswith(("ankan:", "kakan:", "daiminkan:")) for c in candidates):
        topics.append("九 杠要算全桌收益")
    if any(c.id.startswith(("chi:", "pon:")) for c in candidates):
        topics.extend(["三 好形率不是看起来能碰几张", "八 领先时副露要计算失去的防守牌"])
    if any(t is not None for a, t in enumerate(view.riichi_at) if a != view.seat):
        topics.extend(["五 现物、筋、壁分别能证明什么", "十 早中晚巡都要随证据更新"])
    if any(c.shanten is not None and c.shanten >= 2 for c in candidates):
        topics.append("六 弱牌提前留退路")
    topics.extend(["一 先分清牌形与能否和牌", "二 牌效要算受入也要算后续价值"])
    result = []
    for topic in dict.fromkeys(topics):
        if topic not in sections:
            raise RecordError("教材缺少所需主题，不能声称已调用完整知识")
        body = "\n".join(sections[topic]).strip()
        result.append(
            KnowledgePassage(
                topic,
                body[:2300],
                "教材中的条件例不是当前牌谱事实；按整手、实际役、逐家风险与点差复核，不能作为唯一训练标签",
                guide.id,
            )
        )
        if len(result) == 3:
            break
    return tuple(result)


def load_sources(root: Path) -> tuple[KnowledgeSource, ...]:
    manifest = root / "docs/mahjong/knowledge/index.json"
    if not manifest.exists():
        return ()
    try:
        if manifest.stat().st_size > 128 * 1024:
            raise RecordError("知识索引过大")
        data = json.loads(manifest.read_text(encoding="utf-8"))
        entries = data["sources"]
        if not isinstance(entries, list) or len(entries) > 100:
            raise RecordError("知识来源数量或格式错误")
        found = []
        for item in entries:
            source = KnowledgeSource(**{**item, "timecodes": tuple(item.get("timecodes", []))})
            if source.kind not in {"rules", "teaching", "video", "prompt"} or source.status not in {
                "pending",
                "verified",
                "reference",
            }:
                raise RecordError("知识来源分类或核验状态错误")
            path = (root / source.file).resolve(strict=True)
            if not path.is_relative_to(root.resolve()) or path.suffix.lower() != ".md":
                raise RecordError("知识文件必须是项目内Markdown")
            if path.stat().st_size > 128 * 1024:
                raise RecordError("知识文件过大")
            if markdown_sha256(path.read_bytes()) != source.sha256:
                raise RecordError("知识内容哈希不匹配，需重新核验")
            if source.status == "verified" and (not source.source or not source.verified_by):
                raise RecordError("已核验知识必须有来源和核验人")
            if source.kind == "video" and source.status == "verified" and not source.timecodes:
                raise RecordError("视频摘要须有经核验片段时间戳，不能凭标题声称已学习")
            if source.status == "verified":
                found.append(source)
        return tuple(found)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        if isinstance(exc, RecordError):
            raise
        raise RecordError("知识来源索引缺失或格式错误") from exc


def register_source(
    root: Path,
    markdown_file: Path,
    *,
    title: str,
    kind: str,
    source: str,
    verified_by: str,
    timecodes: tuple[str, ...] = (),
    verified: bool = False,
) -> KnowledgeSource:
    """Offline CLI use only. Does not execute Markdown, retrieve URLs, or train a model."""
    path = markdown_file.resolve(strict=True)
    if (
        not path.is_relative_to(root.resolve())
        or path.suffix.lower() != ".md"
        or path.stat().st_size > 128 * 1024
    ):
        raise RecordError("请先把不超过128KiB的Markdown放在项目目录内")
    if kind not in {"rules", "teaching", "video", "prompt"} or not title.strip():
        raise RecordError("知识分类或标题错误")
    if not source.strip() or (source.startswith("http") and urlsplit(source).scheme != "https"):
        raise RecordError("来源不能为空；网页来源请用HTTPS，仅保存引用、不抓取")
    if verified and (not verified_by.strip() or (kind == "video" and not timecodes)):
        raise RecordError("核验人不可为空；视频核验还需要实际时间戳")
    if any(not isinstance(t, str) or len(t) > 80 for t in timecodes):
        raise RecordError("时间戳格式不正确")
    digest = markdown_sha256(path.read_bytes())
    relative = path.relative_to(root.resolve()).as_posix()
    identifier = hashlib.sha256(relative.encode()).hexdigest()[:16]
    item = KnowledgeSource(
        identifier,
        title[:200],
        kind,
        "verified" if verified else "pending",
        relative,
        digest,
        source[:1000],
        verified_by[:200],
        timecodes,
    )
    manifest = root / "docs/mahjong/knowledge/index.json"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    if not manifest.parent.resolve().is_relative_to(root.resolve()) or manifest.is_symlink():
        raise RecordError("知识索引不得指向项目外")
    data = (
        json.loads(manifest.read_text(encoding="utf-8"))
        if manifest.exists()
        else {"version": 1, "sources": []}
    )
    from dataclasses import asdict

    data["sources"] = [s for s in data["sources"] if s.get("id") != identifier] + [asdict(item)]
    data["hash_normalization"] = "UTF-8 bytes; CRLF normalized to LF"
    manifest.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return item
