"""把真实群聊话题排成清晰可读的本地三格示意卡。"""

from __future__ import annotations

import asyncio
import html
import re
from pathlib import Path

from app.astrbot_comic import normalize_comic_topics
from app.services.local_report_render import report_renderer

_WIDTH = 960
_PANEL_HEIGHT = 396
_DISCLAIMER = "按群聊话题改编的示意，不代表现场"


def _excerpt(value: str, limit: int) -> str:
    """去掉标识和链接，保留原文连续片段，不生成新的情节。"""
    value = re.sub(r"(?:QQ号?|群号|用户ID)\s*[:：]?\s*\d{5,13}", "群友", value, flags=re.IGNORECASE)
    value = re.sub(r"(?<!\d)\d{5,13}(?!\d)", "群友", value)
    value = re.sub(r"https?://\S+", "相关链接", value)
    value = re.sub(r"\s+", " ", value).strip()
    if len(value) <= limit:
        return value
    return value[: limit - 1].rstrip() + "…"


def _prop(text: str) -> tuple[str, str]:
    """选择抽象道具，只说明话题类别，不把示意人物当成真实群友。"""
    if any(word in text for word in ("抽卡", "爆率", "保底", "开箱")):
        return (
            "抽卡话题",
            """<path d="M125 173h70v45h-70z" fill="#ffcb79" stroke="#9b6241" stroke-width="3"/>
            <path d="M119 168l72-14 6 16-72 14z" fill="#ffe6ad" stroke="#9b6241" stroke-width="3"/>
            <path d="M161 122l5 13 14 1-11 9 3 14-12-8-12 8 3-14-11-9 14-1z" fill="#ffe16b" stroke="#c49334" stroke-width="2"/>
            <path d="M151 185h19v33h-19z" fill="#fa9278"/>""",
        )
    if any(word in text for word in ("翻唱", "唱歌", "语音", "点歌")):
        return (
            "声音话题",
            """<rect x="149" y="147" width="23" height="41" rx="11" fill="#b5a5ea" stroke="#62558d" stroke-width="3"/>
            <path d="M141 171v7a20 20 0 0040 0v-7m-20 27v20m-14 0h28" fill="none" stroke="#62558d" stroke-width="4" stroke-linecap="round"/>
            <path d="M199 155v-22l17-4v23" fill="none" stroke="#b38de5" stroke-width="4"/>
            <ellipse cx="193" cy="155" rx="6" ry="4" fill="#b38de5"/><ellipse cx="210" cy="152" rx="6" ry="4" fill="#b38de5"/>""",
        )
    if any(word in text for word in ("卡组", "牌表", "卡牌", "配牌")):
        return (
            "卡组话题",
            """<g stroke="#5b7586" stroke-width="3">
            <rect x="124" y="160" width="37" height="55" rx="6" fill="#acd8f0" transform="rotate(-12 142 187)"/>
            <rect x="162" y="160" width="37" height="55" rx="6" fill="#f4c8a5" transform="rotate(12 180 187)"/>
            <path d="M141 174l6 9-6 9-6-9z" fill="#fff"/><circle cx="181" cy="184" r="9" fill="#fff"/>
            </g>""",
        )
    if any(word in text for word in ("新闻", "热点", "时事", "资讯")):
        return (
            "新闻话题",
            """<path d="M121 158h77v59h-77z" fill="#fffdf3" stroke="#9a8f6a" stroke-width="3"/>
            <circle cx="143" cy="181" r="13" fill="#9ed8cf"/><path d="M132 181h22m-11-12v24m20-21h25m-25 10h25m-57 21h57" stroke="#77a79f" stroke-width="3"/>""",
        )
    if any(word in text for word in ("机器人", "设置", "功能", "自动", "开关")):
        return (
            "功能话题",
            """<rect x="120" y="159" width="81" height="58" rx="11" fill="#dcf1ec" stroke="#629b91" stroke-width="3"/>
            <path d="M134 177h51m-51 23h51" stroke="#7fada4" stroke-width="4" stroke-linecap="round"/>
            <circle cx="147" cy="177" r="7" fill="#fff" stroke="#629b91" stroke-width="3"/>
            <circle cx="176" cy="200" r="7" fill="#fff" stroke="#629b91" stroke-width="3"/>""",
        )
    return (
        "交流话题",
        """<path d="M121 158h78v43h-35l-13 13v-13h-30z" fill="#fff" stroke="#7ea6a4" stroke-width="3"/>
        <circle cx="142" cy="180" r="4" fill="#7ea6a4"/><circle cx="160" cy="180" r="4" fill="#7ea6a4"/><circle cx="178" cy="180" r="4" fill="#7ea6a4"/>""",
    )


def _scene(prop: str) -> str:
    """使用无身份的固定卡通角色讨论抽象道具。"""
    return (
        """<svg viewBox="0 0 320 240" role="img" aria-label="匿名卡通角色与话题道具示意">
        <ellipse cx="160" cy="221" rx="141" ry="13" fill="#d7e8e5"/>
        <path d="M22 28h78v35H66L55 73V63H22z" fill="#fff" stroke="#cfdfdc" stroke-width="2"/>
        <circle cx="42" cy="46" r="3" fill="#a8c6bf"/><circle cx="60" cy="46" r="3" fill="#a8c6bf"/><circle cx="78" cy="46" r="3" fill="#a8c6bf"/>
        <path d="M223 26h76v35h-32l-12 11V61h-32z" fill="#fff" stroke="#cfdfdc" stroke-width="2"/>
        <circle cx="242" cy="44" r="3" fill="#a8c6bf"/><circle cx="260" cy="44" r="3" fill="#a8c6bf"/><circle cx="278" cy="44" r="3" fill="#a8c6bf"/>
        <rect x="28" y="128" width="62" height="82" rx="23" fill="#f3b098"/>
        <path d="M41 206v13m34-13v13" stroke="#856b69" stroke-width="8" stroke-linecap="round"/>
        <circle cx="59" cy="96" r="28" fill="#ffdfc6"/><path d="M31 94q-3-42 35-31 27 5 21 29-20-3-32-17-7 15-24 19" fill="#665858"/>
        <circle cx="50" cy="97" r="2.7" fill="#655c5a"/><circle cx="70" cy="97" r="2.7" fill="#655c5a"/>
        <path d="M52 110q8 6 16-1m20 42 30 19" stroke="#b77b68" stroke-width="5" fill="none" stroke-linecap="round"/>
        <rect x="232" y="128" width="62" height="82" rx="23" fill="#96c6db"/>
        <path d="M246 206v13m34-13v13" stroke="#637889" stroke-width="8" stroke-linecap="round"/>
        <circle cx="262" cy="96" r="28" fill="#ffdfc6"/><path d="M234 92q-2-36 31-30 29 2 26 30l-15-11-9 8-13-8-20 11" fill="#566b7b"/>
        <circle cx="252" cy="97" r="2.7" fill="#655c5a"/><circle cx="272" cy="97" r="2.7" fill="#655c5a"/>
        <path d="M255 110q8 6 16-1m-38 42-28 18" stroke="#7a9ca9" stroke-width="5" fill="none" stroke-linecap="round"/>
        <path d="M160 61v-12" stroke="#5b9e95" stroke-width="4"/><circle cx="160" cy="47" r="6" fill="#6db9ab"/>
        <rect x="132" y="67" width="56" height="46" rx="17" fill="#fff" stroke="#79b2a7" stroke-width="3"/>
        <circle cx="149" cy="89" r="4" fill="#4b857d"/><circle cx="171" cy="89" r="4" fill="#4b857d"/>
        <path d="M153 102h14" stroke="#4b857d" stroke-width="3" stroke-linecap="round"/>
        <rect x="140" y="120" width="40" height="38" rx="12" fill="#81c7b9"/>
        <path d="M141 134 124 145m55-11 17 11" stroke="#5b9e95" stroke-width="5" stroke-linecap="round"/>
        """
        + prop
        + "</svg>"
    )


def _comic_html(topics: object, title: str) -> tuple[str, int]:
    """装配最多三个按输入顺序排列的真实话题。"""
    normalized = normalize_comic_topics(topics)[:3]
    if not normalized:
        raise ValueError("没有可改编的真实群聊话题")
    panels = []
    for index, topic in enumerate(normalized, 1):
        heading = _excerpt(topic["topic"], 38)
        excerpt = _excerpt(topic["detail"], 88)
        label, prop = _prop(topic["topic"] + " " + topic["detail"])
        panels.append(
            f'<section class="panel"><div class="heading"><span class="index">{index:02}</span>'
            f'<h2>{html.escape(heading)}</h2></div><div class="body">'
            f'<div class="drawing">{_scene(prop)}<span>{label} · 人物为通用示意</span></div>'
            '<div class="copy"><div class="source-label">话题原文节选</div>'
            f"<p>{html.escape(excerpt) if excerpt else '该话题未提供详细原文。'}</p></div>"
            "</div></section>"
        )
    height = 56 + 140 + len(panels) * _PANEL_HEIGHT + (len(panels) - 1) * 18 + 54
    content = f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
        <meta name="viewport" content="width={_WIDTH}, height={height}">
        <title>{html.escape(_excerpt(title, 26))}</title><style>
        *{{box-sizing:border-box}}html,body{{margin:0;width:{_WIDTH}px;height:{height}px;overflow:hidden}}
        body{{color:#243d41;background:#edf5f1;font-family:'Microsoft YaHei','Noto Sans SC',sans-serif}}
        .page{{padding:28px;width:100%;height:100%}}header{{height:140px}}
        .kicker{{font-size:18px;color:#58897e;letter-spacing:2px;font-weight:700}}
        h1{{font-size:42px;line-height:1.25;margin:7px 0 8px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
        .disclaimer{{font-size:24px;margin:0;color:#62776f}}
        main{{display:grid;gap:18px}}.panel{{height:{_PANEL_HEIGHT}px;padding:24px;background:#fffdf8;border:2px solid #d6e5dc;border-radius:24px;box-shadow:0 4px 0 #dae7df}}
        .heading{{display:flex;gap:16px;align-items:center;height:84px;margin-bottom:12px}}
        .index{{display:grid;place-items:center;flex:0 0 54px;height:54px;border-radius:16px;color:#fff;background:#4b9e8c;font-size:26px;font-weight:800}}
        h2{{font-size:32px;line-height:1.3;margin:0;overflow-wrap:anywhere;font-weight:800;display:-webkit-box;-webkit-box-orient:vertical;-webkit-line-clamp:2;overflow:hidden}}
        .body{{display:grid;grid-template-columns:300px 1fr;gap:24px;height:248px;align-items:center}}
        .drawing{{text-align:center}}svg{{display:block;width:300px;height:225px}}.drawing span{{font-size:17px;line-height:22px;color:#6b8b83}}
        .copy{{align-self:center;max-height:248px;overflow:hidden}}.source-label{{color:#5b8b7d;font-size:19px;font-weight:700;margin-bottom:10px}}
        p{{margin:0;font-size:28px;line-height:1.45;overflow-wrap:anywhere;display:-webkit-box;-webkit-box-orient:vertical;-webkit-line-clamp:5;overflow:hidden}}
        footer{{height:54px;display:flex;align-items:flex-end;justify-content:space-between;font-size:19px;color:#6b8177}}
        </style></head><body><div class="page"><header><div class="kicker">群聊话题 · 清晰示意</div>
        <h1>{html.escape(_excerpt(title, 26))}</h1><div class="disclaimer">{_DISCLAIMER}</div></header>
        <main>{"".join(panels)}</main><footer><span>正规中文排版 · 仅摘取原话题</span><span>共 {len(panels)} 个真实话题</span></footer>
        </div></body></html>"""
    return content, height


async def render_readable_comic(
    topics: object, output_dir: Path, *, title: str = "群聊三格漫画"
) -> bytes:
    """用本机字体与内联矢量图绘制话题示意，不请求生图或语言模型。

    Args:
        topics: 含 topic/detail 的字典或领域对象序列，保持输入次序。
        output_dir: 保存本地临时图片的目录。
        title: 图片标题，会去标识并限制显示长度。

    Returns:
        宽960且高不超过1600的PNG图片字节。

    Raises:
        ValueError: 没有可用的真实话题。
    """
    content, height = _comic_html(topics, title)
    image = await report_renderer.render_to_file(
        content,
        {
            "type": "png",
            "viewport_width": _WIDTH,
            "viewport_height": height,
            "device_scale_factor": 1,
            "full_page": False,
        },
        Path(output_dir),
    )
    return await asyncio.to_thread(Path(image).read_bytes)
