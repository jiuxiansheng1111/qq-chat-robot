"""Deterministic local JPG cards for a reviewed game's visible decisions."""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from app.services.help_menu import _find_font_path
from app.services.mahjong.highlights import (
    decision_digest,
    readable_tiles,
    select_decisions,
    split_report,
)
from app.services.mahjong.records import RecordError

WIDTH = 1200
MARGIN = 56
INK = "#203535"
GREEN = "#126757"


@dataclass(frozen=True)
class ReportMessage:
    text: str
    image: Path | None = None


def wrap_text(draw, text, font, width):
    """Measure every line; preserve characters instead of truncating long prose."""
    lines = []
    for paragraph in text.split("\n"):
        line = ""
        for char in paragraph:
            if line and draw.textlength(line + char, font=font) > width:
                if len(line) > 1 and (char in "，。；：！？、）》」】" or line[-1] in "（《「【"):
                    lines.append(line[:-1])
                    line = line[-1]
                else:
                    lines.append(line)
                    line = ""
            line += char
        lines.append(line)
    return lines


def _tile(draw, code, x, y, font, *, width=66, height=86, highlight=False):
    draw.rounded_rectangle((x, y + 4, x + width, y + height + 4), 7, fill="#c7d3ca")
    draw.rounded_rectangle((x, y, x + width, y + height), 7,
                           fill="#fff2d7" if highlight else "#fffefa", outline="#c9d2c9", width=2)
    digit, suit = code
    red = digit == "0" or suit == "m" or code == "7z"
    color = "#b43731" if red else ("#126757" if suit == "s" or code == "6z" else "#285879")
    if suit == "z":
        label = readable_tiles(code)
        draw.text((x + width / 2, y + height / 2), label, font=font, anchor="mm", fill=color)
    else:
        draw.text((x + width / 2, y + height * .28), "5" if digit == "0" else digit,
                  font=font, anchor="mm", fill=color)
        draw.text((x + width / 2, y + height * .71), {"m": "万", "p": "筒", "s": "索"}[suit],
                  font=font, anchor="mm", fill=color)
        if digit == "0":
            draw.rounded_rectangle((x + width - 23, y + 3, x + width - 3, y + 23), 3, fill="#b43731")
            draw.text((x + width - 13, y + 13), "赤", font=font.font_variant(size=14), anchor="mm", fill="white")


def render_cards(result, job_id: str, root: Path) -> tuple[Path, ...]:
    """One page per selected node, including repeated rounds and local-only nodes."""
    if not re.fullmatch(r"[a-zA-Z0-9_-]{1,48}", job_id):
        raise ValueError("Invalid report identifier")
    directory = root / "data/mahjong/reports" / job_id
    if not directory.resolve().is_relative_to(root.resolve()):
        raise ValueError("Report directory outside project")
    directory.mkdir(parents=True, exist_ok=True)
    font_path = _find_font_path()
    fonts = {n: ImageFont.truetype(str(font_path), n) for n in (22, 26, 28, 32, 36, 42)}
    measure = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    selected = select_decisions(result.analysis.decisions)
    paths = []
    for page, (number, decision) in enumerate(selected, 1):
        lines = decision_digest(decision, number, result.coaching).splitlines()
        title, scene, _, actions, *details = lines
        action_parts = actions.removesuffix("。").split("；", 1)
        scene_lines = wrap_text(measure, scene, fonts[28], WIDTH - MARGIN * 2)
        action_lines = [wrap_text(measure, part, fonts[32], 490) for part in action_parts]
        action_height = max(map(len, action_lines)) * 47 + 52
        groups = [wrap_text(measure, line, fonts[28], WIDTH - MARGIN * 2 - 40) for line in details]
        own_melds = decision.view.melds[decision.view.seat]
        context = "宝牌指示牌：" + readable_tiles(" ".join(decision.view.indicators))
        context += "    本人副露/杠：" + (readable_tiles(" / ".join(" ".join(m.tiles) for m in own_melds)) or "无")
        context_lines = wrap_text(measure, context, fonts[26], WIDTH - MARGIN * 2)
        height = (190 + len(scene_lines) * 42 + 35 + 126 + len(context_lines) * 38 + 34
                  + action_height + 28 + sum(len(g) * 42 + 30 for g in groups) + 102)
        if height > 6000:
            raise ValueError("Card exceeds rendering budget")
        canvas = Image.new("RGB", (WIDTH, height), "#f4f5ed")
        draw = ImageDraw.Draw(canvas)
        draw.rectangle((0, 0, WIDTH, 150), fill=GREEN)
        draw.text((MARGIN, 24), "雀魂 · 逐局复盘", font=fonts[22], fill="#bbd9cd")
        draw.text((MARGIN, 63), title.strip("【】"), font=fonts[42], fill="white")
        y = 185
        for line in scene_lines:
            draw.text((MARGIN, y), line, font=fonts[28], fill=INK)
            y += 42
        y += 26
        draw.text((MARGIN, y), "当时手牌", font=fonts[22], fill=GREEN)
        y += 40
        # Preserve the replay order; red fives remain explicitly red.
        for i, code in enumerate(decision.view.hand):
            _tile(draw, code, MARGIN + i * 77, y, fonts[28])
        y += 113
        for line in context_lines:
            draw.text((MARGIN, y), line, font=fonts[26], fill="#526962")
            y += 38
        y += 28
        for col, group in enumerate(action_lines):
            left = MARGIN + col * 554
            draw.rounded_rectangle((left, y, left + 534, y + action_height), 15,
                                   fill="#e9e0d3" if col == 0 else "#d9ebdf")
            for row, line in enumerate(group):
                draw.text((left + 20, y + 22 + row * 47), line, font=fonts[32], fill=INK if col == 0 else GREEN)
        y += action_height + 28
        for group in groups:
            block_height = len(group) * 42 + 16
            draw.rounded_rectangle((MARGIN, y, WIDTH - MARGIN, y + block_height), 12, fill="white")
            for row, line in enumerate(group):
                draw.text((MARGIN + 20, y + 8 + row * 42), line, font=fonts[28], fill=INK)
            y += block_height + 14
        # Use measured content height; no crop can remove text or the footer.
        footer = y + 12
        draw.text((MARGIN, footer), "基于当时可见信息；未见有效牌≠实际山余；建议不等于最优解。", font=fonts[22], fill="#526962")
        draw.text((MARGIN, footer + 34), f"{job_id} · {page}/{len(selected)} · 节点 {number}", font=fonts[22], fill="#526962")
        if footer + 70 > height:
            raise ValueError("Card layout overflow")
        path = directory / f"{page:02d}-node-{number}.jpg"
        if path.is_symlink():
            raise ValueError("Symlink report target")
        canvas.save(path, "JPEG", quality=92, optimize=True, progressive=False, subsampling=0)
        paths.append(path)
    return tuple(paths)


async def report_messages(state, text: str, owner: tuple[str, str, str], *, root=None):
    """Use only the requesting owner's stored report; gracefully keep all text on failure."""
    fallback = tuple(ReportMessage(part) for part in split_report(text))
    match = re.match(r"雀魂[^\n]*复盘 ([a-f0-9]{12})｜", text)
    store = getattr(state, "mahjong_reports", None)
    if not match or store is None:
        return fallback
    try:
        result = store.get(owner, match[1])
        paths = await asyncio.to_thread(render_cards, result, match[1], root or Path(__file__).resolve().parents[3])
        if not paths:
            return fallback
    except (RecordError, OSError, ValueError, RuntimeError):
        return fallback
    intro, _, remaining = text.partition("\n\n【")
    # Postgame results and commands remain searchable plain text after the pictures.
    _, marker, outro = remaining.partition("\n\n【逐局结果")
    tail = (marker.strip() + outro) if marker else (
        f"逐巡查看：牌谱分析 详情 {match[1]} 节点号\n完整报告：牌谱分析 导出 {match[1]}")
    return (ReportMessage(intro + f"\n下面发送 {len(paths)} 张复盘图，含具体切牌、保留理由与场况计划。"),
            *(ReportMessage(f"复盘图 {i}/{len(paths)}", path) for i, path in enumerate(paths, 1)),
            *(ReportMessage(part) for part in split_report(tail)))
