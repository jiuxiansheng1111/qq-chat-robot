import base64
import math
import re
import unicodedata
from dataclasses import dataclass
from html.parser import HTMLParser
from io import BytesIO
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx
from PIL import Image, ImageDraw, ImageFont

from app.config import Settings
from app.services.web_search import search_web

OFFICIAL_HERO_BASE_URL = "https://tsuburaya-prod.com/heroes"
OFFICIAL_USER_AGENT = (
    "qq-chatrobot/0.1 (https://github.com/jiuxiansheng1111/qq-chat-robot)"
)
FONT_CANDIDATES = (
    "C:/Windows/Fonts/msyh.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJKsc-Regular.otf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "DejaVuSans.ttf",
)


@dataclass(frozen=True)
class Ultraman:
    name: str
    slug: str
    page_path: str = ""
    image_hint: str = ""


@dataclass(frozen=True)
class UltramanProfile:
    quote: str
    description: str
    background: str


ULTRAMAN_ROSTER = (
    Ultraman("初代奥特曼", "ultraman"),
    Ultraman("赛文奥特曼", "ultraseven"),
    Ultraman("杰克奥特曼", "ultraman-jack"),
    Ultraman("艾斯奥特曼", "ultraman-ace"),
    Ultraman("泰罗奥特曼", "ultraman-taro"),
    Ultraman("雷欧奥特曼", "ultraman-leo"),
    Ultraman("爱迪奥特曼", "ultraman-80"),
    Ultraman("迪迦奥特曼", "ultraman-tiga"),
    Ultraman("戴拿奥特曼", "ultraman-dyna"),
    Ultraman("盖亚奥特曼", "ultraman-gaia"),
    Ultraman("高斯奥特曼", "ultraman-cosmos"),
    Ultraman("奈克瑟斯奥特曼", "ultraman-nexus"),
    Ultraman("麦克斯奥特曼", "ultraman-max"),
    Ultraman("梦比优斯奥特曼", "ultraman-mebius"),
    Ultraman("赛罗奥特曼", "ultraman-zero"),
    Ultraman("银河奥特曼", "ultraman-ginga"),
    Ultraman("维克特利奥特曼", "ultraman-victory"),
    Ultraman("艾克斯奥特曼", "ultraman-x"),
    Ultraman("欧布奥特曼", "ultraman-orb"),
    Ultraman("捷德奥特曼", "ultraman-geed"),
    Ultraman("罗索奥特曼", "ultraman-rosso"),
    Ultraman("布鲁奥特曼", "ultraman-blu"),
    Ultraman("泰迦奥特曼", "ultraman-taiga"),
    Ultraman("泽塔奥特曼", "ultraman-z"),
    Ultraman("特利迦奥特曼", "ultraman-trigger"),
    Ultraman("德凯奥特曼", "ultraman-decker"),
    Ultraman("布莱泽奥特曼", "ultraman-blazar"),
    Ultraman("亚刻奥特曼", "ultraman-arc"),
    Ultraman("欧米伽奥特曼", "ultraman-omega"),
    Ultraman("提欧奥特曼", "ultraman-teo"),
)
ULTRAMAN_BY_NAME = {hero.name: hero for hero in ULTRAMAN_ROSTER}

_ULTRAMAN_ALIASES = {
    "奥特曼": "初代奥特曼",
    "初代": "初代奥特曼",
    "奥父": "奥特之父",
    "奥母": "奥特之母",
    "赛兔子": "赛罗奥特曼",
    "老贝": "贝利亚奥特曼",
    "贝老黑": "贝利亚奥特曼",
    "贝利亚": "贝利亚奥特曼",
    "诺亚": "诺亚奥特曼",
    "雷杰多": "雷杰多奥特曼",
    "赛迦": "赛迦奥特曼",
    "佐菲": "佐菲奥特曼",
    "阿斯特拉": "阿斯特拉奥特曼",
    "阿古茹": "阿古茹奥特曼",
    "杰斯提斯": "杰斯提斯奥特曼",
    "希卡利": "希卡利奥特曼",
    "托雷基亚": "托雷基亚奥特曼",
    "超级泰罗": "超级奥特曼泰罗",
    "梦比优斯勇者": "梦比优斯奥特曼·勇者形态",
    "梦比优斯燃烧勇者": "梦比优斯奥特曼·燃烧勇者",
    "梦比优斯凤凰勇者": "梦比优斯奥特曼·凤凰勇者",
    "梦比优斯无限": "梦比优斯奥特曼·无限形态",
    "梦比优斯无限形态": "梦比优斯奥特曼·无限形态",
    "强壮日冕赛罗": "赛罗奥特曼·强壮日冕型",
    "月神奇迹赛罗": "赛罗奥特曼·月神奇迹型",
    "闪耀赛罗": "赛罗奥特曼·闪耀型",
    "赛罗闪耀形态": "赛罗奥特曼·闪耀型",
    "赛罗超越形态": "赛罗奥特曼·无限形态",
    "赛罗奥特曼·超越形态": "赛罗奥特曼·无限形态",
    "赛罗无限": "赛罗奥特曼·无限形态",
    "银河斯特利姆": "银河奥特曼·斯特利姆形态",
    "银河维克特利": "银河维克特利奥特曼",
    "艾克斯超越型": "艾克斯奥特曼·超越形态",
    "艾克斯奥特曼·超越型": "艾克斯奥特曼·超越形态",
    "艾克斯超越形态": "艾克斯奥特曼·超越形态",
    "欧布起源": "欧布奥特曼·原生形态",
    "欧布原生": "欧布奥特曼·原生形态",
    "欧布重光": "欧布奥特曼·重光形态",
    "欧布重光形态": "欧布奥特曼·重光形态",
    "欧布斯佩修姆哉佩利敖": "欧布奥特曼·重光形态",
    "欧布奥特曼·斯佩修姆哉佩利敖": "欧布奥特曼·重光形态",
    "斯佩修姆哉佩利敖": "欧布奥特曼·重光形态",
    "欧布暴炎": "欧布奥特曼·暴炎形态",
    "欧布暴炎形态": "欧布奥特曼·暴炎形态",
    "欧布燃烧炸弹": "欧布奥特曼·暴炎形态",
    "欧布奥特曼·燃烧炸弹": "欧布奥特曼·暴炎形态",
    "燃烧炸弹": "欧布奥特曼·暴炎形态",
    "欧布疾风": "欧布奥特曼·疾风形态",
    "欧布雷霆肩章": "欧布奥特曼·暗耀形态",
    "欧布雷霆胸章": "欧布奥特曼·暗耀形态",
    "雷霆胸章": "欧布奥特曼·暗耀形态",
    "雷霆肩章": "欧布奥特曼·暗耀形态",
    "欧布暗耀": "欧布奥特曼·暗耀形态",
    "欧布暗耀形态": "欧布奥特曼·暗耀形态",
    "欧布奥特曼暗耀形态": "欧布奥特曼·暗耀形态",
    "暗耀欧布": "欧布奥特曼·暗耀形态",
    "暗耀形态": "欧布奥特曼·暗耀形态",
    "欧布煌闪": "欧布奥特曼·煌闪形态",
    "欧布煌闪形态": "欧布奥特曼·煌闪形态",
    "欧布闪电攻击者": "欧布奥特曼·煌闪形态",
    "欧布奥特曼·闪电攻击者": "欧布奥特曼·煌闪形态",
    "闪电攻击者": "欧布奥特曼·煌闪形态",
    "欧布智勇": "欧布奥特曼·智勇形态",
    "欧布智勇形态": "欧布奥特曼·智勇形态",
    "欧布艾梅利姆头镖": "欧布奥特曼·智勇形态",
    "欧布奥特曼·艾梅利姆头镖": "欧布奥特曼·智勇形态",
    "艾梅利姆头镖": "欧布奥特曼·智勇形态",
    "罗布": "罗布奥特曼",
    "格罗布": "格罗布奥特曼",
    "令迦": "令迦奥特曼",
    "泰迦三重斯特利姆": "泰迦奥特曼·三重斯特利姆形态",
    "泰迦奥特曼·三重斯特利姆": "泰迦奥特曼·三重斯特利姆形态",
    "特利迦真理形态": "真理特利迦",
    "真理特利迦形态": "真理特利迦",
    "法德兰装甲": "布莱泽奥特曼·法多兰盔甲",
    "布莱泽奥特曼·法德兰装甲": "布莱泽奥特曼·法多兰盔甲",
    "法多兰装甲": "布莱泽奥特曼·法多兰盔甲",
    "法多兰盔甲": "布莱泽奥特曼·法多兰盔甲",
    "亚刻太阳装甲": "亚刻奥特曼·索利斯装甲",
    "亚刻奥特曼·太阳装甲": "亚刻奥特曼·索利斯装甲",
    "太阳装甲": "亚刻奥特曼·索利斯装甲",
    "亚刻月亮装甲": "亚刻奥特曼·露娜装甲",
    "亚刻奥特曼·月亮装甲": "亚刻奥特曼·露娜装甲",
    "月亮装甲": "亚刻奥特曼·露娜装甲",
    "欧米伽雷基尼斯": "欧米伽奥特曼·雷金斯装甲",
    "欧米伽奥特曼·雷基尼斯装甲": "欧米伽奥特曼·雷金斯装甲",
    "雷基尼斯装甲": "欧米伽奥特曼·雷金斯装甲",
    "欧米伽雷金斯": "欧米伽奥特曼·雷金斯装甲",
    "雷金斯装甲": "欧米伽奥特曼·雷金斯装甲",
    "欧米伽特里加隆": "欧米伽奥特曼·特里加隆装甲",
    "特里加隆装甲": "欧米伽奥特曼·特里加隆装甲",
    "欧米伽瓦尔格尼斯": "欧米伽奥特曼·瓦尔根斯装甲",
    "欧米伽奥特曼·瓦尔格尼斯装甲": "欧米伽奥特曼·瓦尔根斯装甲",
    "瓦尔格尼斯装甲": "欧米伽奥特曼·瓦尔根斯装甲",
    "欧米伽瓦尔根斯": "欧米伽奥特曼·瓦尔根斯装甲",
    "瓦尔根斯装甲": "欧米伽奥特曼·瓦尔根斯装甲",
    "欧米伽盖梅顿": "欧米伽奥特曼·加梅顿装甲",
    "欧米伽奥特曼·盖梅顿装甲": "欧米伽奥特曼·加梅顿装甲",
    "盖梅顿装甲": "欧米伽奥特曼·加梅顿装甲",
    "欧米伽加梅顿": "欧米伽奥特曼·加梅顿装甲",
    "加梅顿装甲": "欧米伽奥特曼·加梅顿装甲",
}


def _normalize_ultraman_name(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold().strip()
    normalized = re.sub(r"^[!?！？。，,.~～、;；:：]+|[!?！？。，,.~～、;；:：]+$", "", normalized)
    normalized = re.sub(r"(?:的)?(?:图片|照片|资料|简介|介绍)$", "", normalized)
    return re.sub(r"[\s·・•._—–\-:：/]+", "", normalized)


def _build_ultraman_alias_index() -> dict[str, Ultraman]:
    index: dict[str, Ultraman] = {}
    for hero in ULTRAMAN_ROSTER:
        full_name = _normalize_ultraman_name(hero.name)
        index[full_name] = hero
        without_title = _normalize_ultraman_name(hero.name.replace("奥特曼", ""))
        if without_title:
            index.setdefault(without_title, hero)
    for alias, canonical_name in _ULTRAMAN_ALIASES.items():
        index[_normalize_ultraman_name(alias)] = ULTRAMAN_BY_NAME[canonical_name]
    for mapping_name in ("_FORM_ALT_NAMES", "_RELATED_ALT_NAMES"):
        for canonical_name, aliases in globals().get(mapping_name, {}).items():
            hero = ULTRAMAN_BY_NAME.get(canonical_name)
            if hero is None:
                continue
            for alias in aliases:
                index[_normalize_ultraman_name(alias)] = hero
    return index


ULTRAMAN_ALIAS_INDEX: dict[str, Ultraman] = {}


def resolve_ultraman_query(query: str) -> Ultraman | None:
    """Resolve an exact official name or common nickname without fuzzy chat matches."""
    key = _normalize_ultraman_name(query)
    if not key:
        return None
    direct = ULTRAMAN_ALIAS_INDEX.get(key)
    if direct:
        return direct

    conversational = re.sub(
        r"^(?:请|麻烦)?(?:介绍一下|介绍|查看|看看|查询|给我看看|我想看|来一个|来个)",
        "",
        key,
    )
    conversational = re.sub(r"(?:是谁|是什么|怎么样|厉害吗)$", "", conversational)
    return ULTRAMAN_ALIAS_INDEX.get(conversational)


def ultraman_catalog_text_pages(max_chars: int = 1700) -> list[str]:
    """Return complete plain-text fallback pages that stay below QQ message limits."""
    pages: list[str] = []
    current = f"✦ 奥特曼图鉴 · 共 {len(ULTRAMAN_ROSTER)} 位/形态 ✦\n"
    for index, hero in enumerate(ULTRAMAN_ROSTER, start=1):
        line = f"{index:03d}. {hero.name}\n"
        if len(current) + len(line) > max_chars:
            pages.append(current.rstrip())
            current = "✦ 奥特曼图鉴 · 续 ✦\n" + line
        else:
            current += line
    if current.strip():
        pages.append(current.rstrip())
    return pages


def render_ultraman_catalog() -> str:
    """Render the full roster as one readable, QQ-sendable JPEG catalog."""
    columns = 3
    rows = math.ceil(len(ULTRAMAN_ROSTER) / columns)
    width = 1800
    header_height = 190
    row_height = 47
    footer_height = 90
    height = header_height + rows * row_height + footer_height
    canvas = Image.new("RGB", (width, height), (4, 9, 24))
    draw = ImageDraw.Draw(canvas)
    for y in range(height):
        ratio = y / max(height - 1, 1)
        draw.line(
            (0, y, width, y),
            fill=(4 + int(10 * ratio), 9 + int(17 * ratio), 24 + int(34 * ratio)),
        )

    title_font = _load_font(64)
    subtitle_font = _load_font(30)
    item_font = _load_font(29)
    footer_font = _load_font(26)
    draw.text((70, 42), "小丛雨 · 奥特曼图鉴", font=title_font, fill=(242, 247, 255))
    draw.text(
        (74, 122),
        f"共收录 {len(ULTRAMAN_ROSTER)} 位角色与独立形态 · @机器人 + 名称 可查看详情",
        font=subtitle_font,
        fill=(157, 199, 255),
    )

    column_width = width // columns
    for index, hero in enumerate(ULTRAMAN_ROSTER):
        column = index // rows
        row = index % rows
        x = 62 + column * column_width
        y = header_height + row * row_height
        number = f"{index + 1:03d}"
        draw.text((x, y), number, font=item_font, fill=(90, 164, 255))
        draw.text((x + 70, y), hero.name, font=item_font, fill=(235, 240, 250))

    footer = "查看图鉴不会增加收藏次数；只有“今日奥特曼”会写入我的奥特曼。"
    draw.text((70, height - 62), footer, font=footer_font, fill=(160, 176, 204))
    output = BytesIO()
    canvas.save(output, format="JPEG", quality=91, optimize=True)
    return "base64://" + base64.b64encode(output.getvalue()).decode()
ULTRAMAN_PROFILES = {
    "初代奥特曼": UltramanProfile(
        "守护地球，也要相信人类自己的力量。",
        "来自M78星云的宇宙警备队员，招牌技能是斯派修姆光线。",
        "追击宇宙怪兽百慕拉来到地球，与科学特搜队的早田进共享生命。",
    ),
    "赛文奥特曼": UltramanProfile(
        "地球是无法替代的故乡。",
        "擅长头镖、集束射线和精密战术的红色战士。",
        "以恒点观测员身份来到地球，化身诸星团加入奥特警备队。",
    ),
    "杰克奥特曼": UltramanProfile(
        "重要的是，直到最后也不要放弃。",
        "兼具光线与格斗能力，并能使用奥特手镯变化多种武器。",
        "与乡秀树合为一体，在怪兽频发期与MAT共同守护地球。",
    ),
    "艾斯奥特曼": UltramanProfile(
        "热忱之心不可泯灭。",
        "光线技能极其丰富，被称为光线技大师。",
        "最初由北斗星司与南夕子共同变身，对抗异次元人亚波人和超兽。",
    ),
    "泰罗奥特曼": UltramanProfile(
        "奥特之心，永远与大家同在。",
        "奥特之父与奥特之母之子，爆发力强，也擅长奥特炸弹。",
        "与东光太郎合体，在地球经历战斗并逐渐成长为可靠的战士。",
    ),
    "雷欧奥特曼": UltramanProfile(
        "只有经受磨炼，才能成为真正的强者。",
        "来自L77星的格斗高手，以雷欧飞踢等近身战法闻名。",
        "故乡毁灭后留在地球，在赛文的严厉训练下独自承担守护使命。",
    ),
    "爱迪奥特曼": UltramanProfile(
        "人的负面情绪，也可能孕育怪兽。",
        "动作敏捷、念力和光线技能丰富，同时也是一位教师。",
        "化身中学教师矢的猛，希望从心灵层面阻止怪兽因负面能量出现。",
    ),
    "迪迦奥特曼": UltramanProfile(
        "人类也能凭自己的力量变成光。",
        "能在复合型、强力型和空中型之间转换的超古代光之巨人。",
        "石像与拥有光之遗传因子的大古融合后复苏，与GUTS共同守护人类。",
    ),
    "戴拿奥特曼": UltramanProfile(
        "真正的战斗，现在才开始！",
        "拥有闪亮型、强壮型和奇迹型三种形态，战斗风格大胆奔放。",
        "飞鸟信在火星附近与神秘之光结合，加入SUPER GUTS迎战斯菲亚。",
    ),
    "盖亚奥特曼": UltramanProfile(
        "我想守护的，是这颗星球上的一切。",
        "由大地之光诞生的战士，力量厚重，登场时常震起大片尘土。",
        "高山我梦获得地球之光后变身，与代表海洋之光的阿古茹并肩作战。",
    ),
    "高斯奥特曼": UltramanProfile(
        "能不用战斗解决，就不要战斗。",
        "以慈爱与净化能力著称，也会根据战局切换不同形态。",
        "与春野武藏一心同体，主张理解并保护怪兽，而非单纯消灭。",
    ),
    "奈克瑟斯奥特曼": UltramanProfile(
        "光是纽带，会被一代又一代传承下去。",
        "神秘的光之战士，力量会在不同适能者之间传承。",
        "适能者在黑暗与异生兽的威胁中战斗，最终让希望之光继续延续。",
    ),
    "麦克斯奥特曼": UltramanProfile(
        "未来，应当由人类自己选择。",
        "兼具速度与力量，使用麦克修姆加农和头部的麦克斯银河战斗。",
        "观察地球期间被人类的勇气打动，与东马快斗融合并协助DASH。",
    ),
    "梦比优斯奥特曼": UltramanProfile(
        "伙伴之间的羁绊，就是我们的力量。",
        "年轻的宇宙警备队员，擅长梦比姆射线和梦比姆光剑。",
        "化身日比野未来，与GUYS队员共同成长，并继承奥特兄弟的意志。",
    ),
    "赛罗奥特曼": UltramanProfile(
        "还早两万年呢！",
        "赛文之子，性格自信不羁，使用双头镖和多种形态作战。",
        "曾因触碰等离子火花核心受罚，后经雷欧训练成长为跨宇宙英雄。",
    ),
    "银河奥特曼": UltramanProfile(
        "未来，是可以靠自己的双手改变的。",
        "来自未来的神秘战士，能通过火花人偶借用其他英雄与怪兽之力。",
        "与礼堂光相遇，在降星镇卷入黑暗火花战争。",
    ),
    "维克特利奥特曼": UltramanProfile(
        "这颗星球，由我来守护。",
        "来自地底世界的战士，可使用怪兽能力进行武装。",
        "地底居民翔获得维克特利圣枪后，为保护维克特利水晶而战。",
    ),
    "艾克斯奥特曼": UltramanProfile(
        "并肩战斗吧，我们一心同体。",
        "能进行电子化与怪兽装甲武装，战术适应力很强。",
        "以数据形态寄宿于大空大地的终端中，与Xio共同应对怪兽灾害。",
    ),
    "欧布奥特曼": UltramanProfile(
        "借用一下前辈们的力量！",
        "浪客红凯变身的战士，能融合历代奥特英雄的力量。",
        "背负与伽古拉纠缠已久的过去，在地球继续自己的赎罪与守护之旅。",
    ),
    "捷德奥特曼": UltramanProfile(
        "遇到事情，不能坐以待毙！",
        "继承贝利亚遗传因子，却选择以英雄身份守护他人。",
        "朝仓陆发现自己的身世后，借助奥特胶囊变身并反抗既定命运。",
    ),
    "罗索奥特曼": UltramanProfile(
        "染上我的本色吧！",
        "凑活海变身的哥哥战士，沉稳可靠，擅长配合与正面作战。",
        "与弟弟布鲁共同使用罗布水晶，在家人与城市面临危机时挺身而出。",
    ),
    "布鲁奥特曼": UltramanProfile(
        "就这么一口气冲过去吧！",
        "凑勇海变身的弟弟战士，思路灵活，战斗方式更大胆。",
        "与哥哥罗索并肩成长，在一次次磨合中理解真正的英雄责任。",
    ),
    "泰迦奥特曼": UltramanProfile(
        "伙伴在一起，就不会输。",
        "泰罗之子，与泰塔斯、风马组成三人小队。",
        "三位奥特英雄寄宿于工藤优幸体内，共同面对托雷基亚带来的阴谋。",
    ),
    "泽塔奥特曼": UltramanProfile(
        "请喊出我的名字吧——泽塔奥特曼！",
        "崇拜赛罗的年轻战士，热血认真，但对地球语言并不熟练。",
        "与军械库驾驶员夏川遥辉一心同体，借助勋章力量迎战怪兽。",
    ),
    "特利迦奥特曼": UltramanProfile(
        "微笑吧，微笑。",
        "能在复合、强力和空中形态间切换的新生代光之巨人。",
        "真中剑悟与三千万年前的光产生联系，为守护大家的笑容而战。",
    ),
    "德凯奥特曼": UltramanProfile(
        "现在就去抓住未来！",
        "拥有闪亮、强壮和奇迹形态，作战风格积极果断。",
        "明日见奏大在斯菲亚封锁地球后获得光，与新生GUTS-SELECT并肩战斗。",
    ),
    "布莱泽奥特曼": UltramanProfile(
        "由我来。",
        "充满原始野性的神秘巨人，战斗动作独特，使用螺旋光刃。",
        "在危机中与SKaRD队长比留间弦人融合，共同探寻怪兽与宇宙人的真相。",
    ),
    "亚刻奥特曼": UltramanProfile(
        "奔跑吧，优马！让想象力成为力量。",
        "由想象力塑造的光之巨人，使用亚刻魔方与多种装甲能力。",
        "飞世优马与遥远银河的光之存在路提昂结合，成为童年画中的英雄。",
    ),
    "欧米伽奥特曼": UltramanProfile(
        "为什么奥特曼要保护地球？答案要亲自找到。",
        "使用红色宇宙回旋镖欧米伽头镖的失忆战士。",
        "失去记忆后坠落在首次出现怪兽的地球，以奥奇索拉托之名认识人类。",
    ),
    "提欧奥特曼": UltramanProfile(
        "即使害怕，也要为了守护而向前。",
        "来自H12行星的蓝色光之巨人，必杀技是提欧修姆光线。",
        "故乡被宇宙怪兽毁灭后逃到地球，化身兽医学生三石伊吹继续生活。",
    ),
}

ULTRAMAN_DEBUT_YEARS = {
    "初代奥特曼": 1966,
    "赛文奥特曼": 1967,
    "杰克奥特曼": 1971,
    "艾斯奥特曼": 1972,
    "泰罗奥特曼": 1973,
    "雷欧奥特曼": 1974,
    "爱迪奥特曼": 1980,
    "迪迦奥特曼": 1996,
    "戴拿奥特曼": 1997,
    "盖亚奥特曼": 1998,
    "高斯奥特曼": 2001,
    "奈克瑟斯奥特曼": 2004,
    "麦克斯奥特曼": 2005,
    "梦比优斯奥特曼": 2006,
    "赛罗奥特曼": 2009,
    "银河奥特曼": 2013,
    "维克特利奥特曼": 2014,
    "艾克斯奥特曼": 2015,
    "欧布奥特曼": 2016,
    "捷德奥特曼": 2017,
    "罗索奥特曼": 2018,
    "布鲁奥特曼": 2018,
    "泰迦奥特曼": 2019,
    "泽塔奥特曼": 2020,
    "特利迦奥特曼": 2021,
    "德凯奥特曼": 2022,
    "布莱泽奥特曼": 2023,
    "亚刻奥特曼": 2024,
    "欧米伽奥特曼": 2025,
    "提欧奥特曼": 2026,
}

# The first version only contained one default form for each TV lead.  Keep the
# catalog data here so every form is independently collectible without changing
# the persistence schema (the database stores the display name as its key).
_EXPANDED_ULTRAMAN_DATA = (
    # Official hero encyclopedia entries omitted by the original 30-character pool.
    ("佐菲奥特曼", "zoffy", "", 1967, "宇宙警备队队长，M87光线拥有顶尖威力。", "多次在奥特兄弟陷入绝境时率领援军抵达。"),
    ("奥特之父", "father-of-ultra", "", 1972, "光之国宇宙警备队大队长，象征久经战火的领袖力量。", "在奥特大战争中守护光之国，并培养一代代年轻战士。"),
    ("奥特之母", "mother-of-ultra", "", 1973, "银十字军队长，拥有卓越的治愈与复苏能力。", "她以温柔而坚定的光守护宇宙警备队员。"),
    ("阿斯特拉奥特曼", "astra", "", 1974, "雷欧的弟弟，经历磨难后练就敏捷而坚韧的宇宙拳法。", "L77星毁灭后与兄长重逢，并肩守护宇宙。"),
    ("奥特之王", "ultraman-king", "", 1974, "传说中的超人，能够干涉宇宙尺度的灾难与奇迹。", "独居王者之星，在真正的绝境中为年轻战士指明道路。"),
    ("乔尼亚斯奥特曼", "ultraman-joneus", "", 1979, "来自U40的强大战士，体型与能量均可自由变化。", "与科学警备队员光超一郎并肩迎战怪兽与宇宙威胁。"),
    ("尤莉安奥特曼", "yullian", "", 1981, "光之国王族成员，兼具战斗意志与守护者的气度。", "来到地球后与爱迪并肩作战，也长期活跃于宇宙警备队。"),
    ("史考特奥特曼", "ultraman-scott", "", 1989, "奥特美国三人组的队长型战士，擅长正面格斗。", "追击索尔金特来到地球，与伙伴共同守护美国大陆。"),
    ("贝斯奥特曼", "ultrawoman-beth", "", 1989, "奥特美国三人组中灵活而果敢的女战士。", "与贝斯·奥布莱恩合体，在陌生星球承担守护使命。"),
    ("查克奥特曼", "ultraman-chuck", "", 1989, "沉着稳健的奥特美国战士，善于分析战局。", "与史考特、贝斯组成团队对抗宇宙生物兵器。"),
    ("葛雷奥特曼", "ultraman-great", "", 1990, "招式丰富、气质沉稳，擅长处理戈迪斯细胞引发的危机。", "在火星与杰克·辛多融合，随后来到地球继续战斗。"),
    ("帕瓦特奥特曼", "ultraman-powered", "", 1993, "来自M78星云，以强健体魄和梅加斯佩修姆光线战斗。", "追踪巴尔坦星人来到地球，与W.I.N.R.成员凯伊合体。"),
    ("奈欧斯奥特曼", "ultraman-neos", "", 2000, "宇宙保安厅的精锐战士，攻防均衡且行动迅捷。", "在黑暗物质影响太阳系时，与神乐元气共同守护地球。"),
    ("赛文21奥特曼", "ultraseven-21", "", 2000, "擅长隐秘行动与头镖战法的宇宙保安厅精英。", "经常先行调查威胁，并在关键时刻与奈欧斯并肩作战。"),
    ("阿古茹奥特曼", "ultraman-agul", "", 1998, "由海洋之光诞生，战斗冷峻凌厉，擅长光剑与光子粉碎机。", "藤宫博也承载海洋意志，在冲突与理解后选择守护整个地球。"),
    ("杰斯提斯奥特曼", "ultraman-justice", "", 2002, "贯彻宇宙正义的战士，拥有标准与粉碎两种战斗姿态。", "最初执行宇宙裁决，后来因理解人类的可能性与高斯并肩。"),
    ("杰诺奥特曼", "ultraman-xenon", "", 2005, "麦克斯的可靠同伴，曾送来麦克斯银河扭转战局。", "作为文明监视员的一员，在宇宙危机中支援地球战线。"),
    ("希卡利奥特曼", "ultraman-hikari", "", 2006, "兼具顶尖科学头脑与骑士剑术的蓝族战士。", "曾被复仇执念化为猎手骑士剑，最终重拾光并与梦比优斯并肩。"),
    ("格丽乔奥特曼", "ultrawoman-grigio", "", 2019, "擅长防御与治疗，以温柔之光支援伙伴。", "凑朝阳继承光之力量后，与罗索和布鲁组成真正的三兄妹战线。"),
    ("泰塔斯奥特曼", "ultraman-titas", "", 2019, "来自U40的贤者与力士，肌肉中蕴含冷静判断。", "作为三人小队成员寄宿于工藤优幸体内，为荣誉与友情而战。"),
    ("风马奥特曼", "ultraman-fuma", "", 2019, "来自O-50的速度型战士，忍者般的光轮技变化莫测。", "凭自身努力获得光之力量，并成为三人小队最迅捷的一翼。"),
    ("利布特奥特曼", "ultraman-ribut", "", 2014, "银河救援队成员，使用利布特盾与精确格斗保护生命。", "活跃在多元宇宙救援前线，面对未知灾害总是率先出动。"),
    ("雷古洛思奥特曼", "ultraman-regulos", "", 2021, "掌握赤龙白虎拳的宇宙幻兽拳斗士。", "在D60修行并背负同门意志，于绝境中完成真正的传承。"),
    ("帝纳斯奥特曼", "ultraman-decker", "", 2023, "以怪兽卡片之力战斗的女性光之巨人。", "拉维安星少女帝纳斯得到戴拿之光后，以自己的方式延续希望。"),
    # Legendary and dark Ultras. Some use an official related page when no hero entry exists.
    ("诺亚奥特曼", "ultraman-nexus", "", 2004, "跨越时空的究极光之巨人，拥有诺亚之翼与近乎神迹的力量。", "奈克瑟斯之光不断进化后显现的本来姿态，是传承与希望的终点。"),
    ("雷杰多奥特曼", "ultraman-cosmos", "", 2003, "高斯与杰斯提斯之光融合而成的宇宙传说，能够推动或化解终极能量。", "当两种正义真正达成一致时，宇宙意志让传说之光降临。"),
    ("赛迦奥特曼", "ultraman-zero", "/business/titlelist/8015", 2012, "由赛罗、戴拿与高斯的光和人类勇气共同诞生的奇迹战士。", "在未来地球的绝望战场上，三道跨越宇宙的光合为希望。"),
    ("贝利亚奥特曼", "ultraman-belial", "/encyclopedia/ultraman-belial", 2009, "手持终极战斗仪的黑暗奥特战士，能够统率百体怪兽。", "曾是光之国战士，却因追逐等离子火花的力量而坠入黑暗。"),
    ("贝利亚早期形态", "ultraman-belial", "/encyclopedia/ultraman-belial", 2020, "尚未被雷布朗多之力侵蚀的银红战士，骄傲而好胜。", "奥特大战争时期与健并肩作战，命运尚未滑向黑暗深渊。"),
    ("凯撒贝利亚", "kaiser-belial", "/encyclopedia/kaiser-belial", 2010, "披挂猩红皇袍、统治银河帝国的贝利亚强化姿态。", "在异宇宙建立帝国，以艾美拉鲁矿石发动跨星系侵略。"),
    ("电弧贝利亚", "arch-belial", "/encyclopedia/arch-belial", 2010, "吞噬巨量艾美拉鲁矿石后形成的三百米超巨大形态。", "失控的能量让皇帝化为足以摧毁行星的宇宙巨兽。"),
    ("极恶贝利亚", "ultraman-belial-atrocious", "/encyclopedia/ultraman-belial-atrocious", 2017, "融合黑暗路基艾尔与安培拉星人力量，能吸收奥特之王能量。", "贝利亚以恶魔融合升华抵达极恶形态，成为捷德最终必须跨越的宿命。"),
    ("托雷基亚奥特曼", "ultraman-tregear", "/encyclopedia/ultraman-tregear", 2019, "以优雅言辞玩弄人心、操纵混沌之力的堕落蓝族。", "曾是泰罗挚友与光之国科学家，因质疑光明与正义走向虚无。"),
    ("托雷基亚早期形态", "ultraman-tregear", "/encyclopedia/ultraman-tregear", 2020, "尚在光之国科学技术局时期的蓝族研究者。", "他曾真诚追寻力量与真理，后来却在自我怀疑中偏离道路。"),
    ("黑暗特利迦", "ultraman-trigger", "/encyclopedia/trigger-dark", 2021, "三千万年前的黑暗巨人，以强横蛮力压制对手。", "特利迦选择光明前的旧姿态，也在伊格尼斯手中获得新的意志。"),
    ("邪恶迪迦", "ultraman-tiga", "/encyclopedia/evil-tiga", 1997, "错误之心驾驭巨人石像后诞生的扭曲之光。", "正木敬吾试图凭科学复制光，却因傲慢失去控制。"),
    ("黑暗扎基", "ultraman-nexus", "/encyclopedia/dark-zagi", 2004, "以诺亚为蓝本制造、最终失控的黑暗破坏神。", "跨越漫长布局吸收恐惧，最终在新宿决战中直面诺亚之光。"),
)

_FORM_VARIANTS = (
    ("迪迦奥特曼·强力型", "ultraman-tiga", 1996, "力量与近身战显著强化的红色形态。", "迪迦将复合型能量集中于力量后完成类型转换。"),
    ("迪迦奥特曼·空中型", "ultraman-tiga", 1996, "速度、飞行与远距离技巧强化的紫色形态。", "面对高速敌人时，迪迦以轻盈姿态夺回天空主动权。"),
    ("闪耀迪迦", "ultraman-tiga", 1996, "汇聚全人类希望之光诞生的金色奇迹形态。", "孩子们化作光进入石像，让已经倒下的迪迦再次站起。"),
    ("戴拿奥特曼·强壮型", "ultraman-dyna", 1997, "以红色力量压制敌人的重战形态。", "戴拿面对需要正面突破的战局时进行类型转换。"),
    ("戴拿奥特曼·奇迹型", "ultraman-dyna", 1997, "操纵超能力、速度与空间能量的蓝色形态。", "飞鸟的想象与宇宙之光结合，创造难以预测的奇迹战法。"),
    ("盖亚奥特曼V2", "ultraman-gaia", 1999, "同时拥有大地与部分海洋之光，整体能力全面跃升。", "藤宫将阿古茹之光托付给我梦，两位地球之子的信念合流。"),
    ("盖亚奥特曼·至高型", "ultraman-gaia", 1999, "将大地与海洋力量完全释放的红黑最强姿态。", "只有当两道地球之光真正共鸣，至高形态才会震撼降临。"),
    ("阿古茹奥特曼V2", "ultraman-agul", 1999, "重生后的海洋之光，力量、光剑与防御均大幅强化。", "藤宫重新理解地球意志后，再次获得海洋认可。"),
    ("高斯奥特曼·日冕模式", "ultraman-cosmos", 2001, "面对无法感化之敌时使用的红色战斗模式。", "慈爱的月神之光收起温柔，将意志转化为炽热力量。"),
    ("高斯奥特曼·日蚀模式", "ultraman-cosmos", 2002, "兼具月神的温柔与日冕的力量，攻守与净化并重。", "武藏与高斯心灵进一步融合后诞生的勇气之光。"),
    ("奈克瑟斯奥特曼·青年形态", "ultraman-nexus", 2004, "展开美塔领域、发挥适能者意志的红色战斗形态。", "适能者与光建立更深纽带后，奈克瑟斯完成阶段性进化。"),
    ("奈克瑟斯奥特曼·青年蓝色形态", "ultraman-nexus", 2004, "以速度与弓箭光线见长的蓝色进化形态。", "千树怜短暂而炽烈的生命，让光呈现出自由迅疾的姿态。"),
    ("梦比优斯奥特曼·勇者形态", "ultraman-mebius", 2006, "得到骑士气息后掌握双腕光剑的强化形态。", "希卡利将认可与力量交给未来，友情化作新的剑锋。"),
    ("梦比优斯奥特曼·燃烧勇者", "ultraman-mebius", 2006, "伙伴羁绊化作火焰纹章，爆发力与格斗能力急剧提升。", "GUYS全员的友情让梦比优斯在烈焰中完成再生。"),
    ("梦比优斯奥特曼·凤凰勇者", "ultraman-mebius", 2007, "梦比优斯、希卡利与GUYS伙伴之心融合的最终形态。", "地球最终决战中，彼此信赖让分散的生命化作不灭凤凰。"),
    ("梦比优斯奥特曼·无限形态", "ultraman-mebius", 2006, "与奥特六兄弟力量融合诞生的电影级究极形态。", "面对究极超兽萨乌鲁斯，跨越世代的兄弟之光合而为一。"),
    ("超级奥特曼泰罗", "ultraman-taro", 1984, "奥特五兄弟将全部能量交给泰罗后诞生的合体姿态。", "面对古兰特王，六兄弟把生命与意志汇聚为宇宙奇迹光线。"),
    ("赛罗奥特曼·强壮日冕型", "ultraman-zero", 2012, "继承戴拿强壮型与高斯日冕力量的红色重战形态。", "赛迦分离后留下的伙伴之光，成为赛罗新的战斗可能。"),
    ("赛罗奥特曼·月神奇迹型", "ultraman-zero", 2012, "融合高斯月神与戴拿奇迹力量的蓝色超能力形态。", "温柔与奇迹并存，使赛罗能够操纵空间并净化敌人。"),
    ("赛罗奥特曼·闪耀型", "ultraman-zero", 2013, "情感突破极限后觉醒，能够逆转局部时间的金色形态。", "为了挽回同伴，赛罗让体内全部光芒燃烧成奇迹。"),
    ("赛罗奥特曼·无限形态", "ultraman-zero", 2017, "新生代四位战士力量凝聚而成的高速强化形态。", "在捷德的宇宙中，人们的愿望让受损的赛罗再次超越极限。"),
    ("银河奥特曼·斯特利姆形态", "ultraman-ginga", 2014, "融合泰罗与奥特六兄弟必杀技的强化形态。", "泰罗化作斯特利姆手镯，把兄弟们的战斗记忆托付给银河。"),
    ("银河维克特利奥特曼", "ultraman-ginga", 2015, "银河与维克特利合体，并能使用历代奥特十勇士之力。", "两位年轻战士真正同心后，跨越系列的光汇成一体。"),
    ("维克特利骑士", "ultraman-victory", 2015, "持有骑士剑笛、能够运用净化力量的蓝色强化形态。", "希卡利将骑士之力交给翔，让地底战士承担更广阔的守护责任。"),
    ("艾克斯奥特曼·超越形态", "ultraman-x", 2015, "由彩虹之力进化而成，使用艾克斯头镖战斗。", "大地与艾克斯的羁绊跨过数据与生命边界，唤醒真正进化。"),
    ("欧布奥特曼·重光形态", "ultraman-orb", 2016, "融合初代与迪迦力量，攻守均衡的基本融合形态。", "红凯借用两位前辈之光，重新迈出成为英雄的一步。"),
    ("欧布奥特曼·暴炎形态", "ultraman-orb", 2016, "融合泰罗与梦比优斯力量，擅长烈焰与爆发格斗。", "两道燃烧的奥特之心在欧布体内化作灼热战甲。"),
    ("欧布奥特曼·疾风形态", "ultraman-orb", 2016, "融合杰克与赛罗力量，以长枪和高速连击作战。", "跨越世代的敏捷技巧让欧布如暴风般切开战场。"),
    ("欧布奥特曼·暗耀形态", "ultraman-orb", 2016, "融合佐菲与贝利亚力量的红黑色强力形态，日文原名为サンダーブレスター（Thunder Breastar）。", "红凯借助光与暗两股力量完成融合升级，初期一度难以驾驭贝利亚的黑暗力量。"),
    ("欧布奥特曼·原生形态", "ultraman-orb", 2016, "使用欧布圣剑与四元素之力的本来姿态。", "红凯跨越迷惘，终于不再只借前辈力量，而是找回自己的光。"),
    ("欧布奥特曼·煌闪形态", "ultraman-orb", 2017, "融合银河与艾克斯力量，以雷电、电子装甲和高速突击作战。", "两位数据时代战士的光在欧布身上交织成雷霆铠甲。"),
    ("欧布奥特曼·智勇形态", "ultraman-orb", 2017, "融合赛文与赛罗力量，能驾驭三枚头镖进行立体斩击。", "父子两代的头镖绝技被红凯锤炼成攻防一体的锋刃。"),
    ("捷德奥特曼·原始形态", "ultraman-geed", 2017, "融合初代与贝利亚力量，野性外表下坚持正义。", "朝仓陆拒绝由血统决定命运，以自己的选择成为英雄。"),
    ("捷德奥特曼·刚燃形态", "ultraman-geed", 2017, "融合赛文与雷欧力量，铠甲厚重、格斗刚猛。", "师徒般的两道光让捷德拥有正面击碎强敌的勇气。"),
    ("捷德奥特曼·机敏形态", "ultraman-geed", 2017, "融合希卡利与高斯力量，速度与光线控制出色。", "科学与慈爱之光让捷德用更聪明的方式结束战斗。"),
    ("捷德奥特曼·豪勇形态", "ultraman-geed", 2017, "融合奥特之父与赛罗力量，使用强力武装作战。", "两代守护者的意志化作威严战甲，支撑捷德直面父亲。"),
    ("捷德奥特曼·尊皇形态", "ultraman-geed", 2017, "融合贝利亚与奥特之王力量，能够调用历代战士能力。", "最深的黑暗与最高贵的光在陆的意志下达成平衡。"),
    ("捷德奥特曼·银河初升", "ultraman-geed", 2020, "融合银河、艾克斯与欧布力量，擅长高速连续作战。", "升华器损坏后，遥辉宇宙的新生代勋章让捷德再次升华。"),
    ("罗布奥特曼", "ultraman-rosso", 2018, "罗索与布鲁融合而成，使用罗布光轮统合四元素。", "凑家兄弟放下分歧、真正同心时，双色之光完成融合。"),
    ("格罗布奥特曼", "ultrawoman-grigio", 2019, "罗索、布鲁与格丽乔三兄妹融合的家族究极形态。", "守护家人的愿望让三道光合为一体，爆发超越兄弟的力量。"),
    ("泰迦奥特曼·光子地球", "ultraman-taiga", 2019, "吸收地球大地与水之能量形成的金色强化形态。", "优幸与泰迦理解地球生命后，让脚下星球回应他们的决心。"),
    ("泰迦奥特曼·三重斯特利姆形态", "ultraman-taiga", 2019, "泰迦、泰塔斯与风马力量合一的三人小队最终形态。", "三位战士不再轮流作战，而是把友情化作同一束光。"),
    ("令迦奥特曼", "ultraman-taiga", 2020, "新生代十一位奥特英雄力量融合而成的究极战士。", "面对格里姆德，跨越多个宇宙的伙伴同时回应泰迦。"),
    ("泽塔奥特曼·阿尔法装甲", "ultraman-z", 2020, "融合赛文、雷欧与赛罗力量，擅长宇宙拳法与头镖。", "师徒三代的战斗意志让年轻的泽塔获得锋利身法。"),
    ("泽塔奥特曼·贝塔冲击", "ultraman-z", 2020, "融合初代、艾斯与泰罗力量的红色力量形态。", "昭和战士的热血与刚力在泽塔体内正面爆发。"),
    ("泽塔奥特曼·伽马未来", "ultraman-z", 2020, "融合迪迦、戴拿与盖亚力量，擅长超能力与光线变化。", "平成三杰之光让泽塔能够以幻影和空间技巧掌控战局。"),
    ("泽塔奥特曼·德尔塔天爪", "ultraman-z", 2020, "融合极恶贝利亚、捷德与赛罗力量，使用贝利亚黄昏。", "三股相克力量在遥辉的意志下被驯服为最强之刃。"),
    ("特利迦奥特曼·强力型", "ultraman-trigger", 2021, "以力量和熔岩般能量突破重甲敌人的红色形态。", "剑悟通过胜利超越之钥唤醒特利迦的力量侧面。"),
    ("特利迦奥特曼·空中型", "ultraman-trigger", 2021, "强化高速飞行与远程技巧的紫色形态。", "面对天空与速度战，特利迦让光变得像风一样轻盈。"),
    ("闪耀特利迦永恒", "ultraman-trigger", 2021, "掌握永恒核心力量、使用闪耀利刃的金色形态。", "超古代核心的巨大能量被剑悟以守护笑容的意志驾驭。"),
    ("真理特利迦", "ultraman-trigger", 2022, "光明特利迦与黑暗特利迦力量融合的最终姿态。", "剑悟与伊格尼斯共同跨越光暗对立，让真正的特利迦诞生。"),
    ("德凯奥特曼·强壮型", "ultraman-decker", 2022, "专注怪力、防御与近身压制的红色形态。", "奏大将守护故乡的冲劲凝聚成不会后退的力量。"),
    ("德凯奥特曼·奇迹型", "ultraman-decker", 2022, "操纵空间、念力与速度的蓝色超能力形态。", "宇宙未来的可能性让德凯以不可思议的方式改写战局。"),
    ("德凯奥特曼·强劲型", "ultraman-decker", 2022, "融合三种基础类型优势、使用德凯盾剑的最强形态。", "奏大不再依赖未来答案，以此刻的决心创造自己的力量。"),
    ("布莱泽奥特曼·法多兰盔甲", "ultraman-blazar", 2023, "与炎龙怪兽法德兰共鸣，获得火焰铠甲与双刃武装。", "弦人与布莱泽理解伙伴怪兽后，让野性之光披上烈焰。"),
    ("亚刻奥特曼·索利斯装甲", "ultraman-arc", 2024, "以太阳意象构筑的重装力量形态。", "优马把对炽热守护力的想象化作现实装甲。"),
    ("亚刻奥特曼·露娜装甲", "ultraman-arc", 2024, "以月亮意象构筑的敏捷与技巧形态。", "柔和月光在想象力中化作灵活而精准的战斗能力。"),
    ("亚刻奥特曼·银河装甲", "ultraman-arc", 2024, "将广阔银河意象实体化的终极装甲。", "优马让想象冲出星球边界，塑造足以回应宇宙危机的力量。"),
    ("欧米伽奥特曼·雷金斯装甲", "ultraman-omega", 2025, "借助陨星怪兽雷金斯力量形成的电视强化装甲，配合雷金斯巨剑与念动力作战。", "在《欧米伽奥特曼》TV正剧中，雷金斯与欧米伽共鸣并化作装甲与武器。"),
    ("欧米伽奥特曼·特里加隆装甲", "ultraman-omega", 2025, "借助陨星怪兽特里加隆力量形成的高速近战装甲，以利爪和高机动性压制敌人。", "在《欧米伽奥特曼》TV正剧中，特里加隆与欧米伽共鸣后化作装甲与利爪。"),
    ("欧米伽奥特曼·瓦尔根斯装甲", "ultraman-omega", 2025, "借助陨星怪兽瓦尔根斯力量形成的元素型装甲，能够利用火、水、风、地等自然力量。", "在《欧米伽奥特曼》TV正剧后半段登场，瓦尔根斯化作装甲与长柄武装支援欧米伽。"),
    ("欧米伽奥特曼·加梅顿装甲", "ultraman-omega", 2026, "借助加梅顿力量形成的新装甲形态。", "在2026年TV节目《奥特曼新生代之星》中作为欧米伽的新装甲登场。"),
)

_FORM_ALT_NAMES = {
    "迪迦奥特曼·强力型": ("ウルトラマンティガ パワータイプ", "Ultraman Tiga Power Type"),
    "迪迦奥特曼·空中型": ("ウルトラマンティガ スカイタイプ", "Ultraman Tiga Sky Type"),
    "闪耀迪迦": ("グリッターティガ", "Glitter Tiga"),
    "戴拿奥特曼·强壮型": ("ウルトラマンダイナ ストロングタイプ", "Ultraman Dyna Strong Type"),
    "戴拿奥特曼·奇迹型": ("ウルトラマンダイナ ミラクルタイプ", "Ultraman Dyna Miracle Type"),
    "盖亚奥特曼V2": ("ウルトラマンガイア V2", "Ultraman Gaia V2"),
    "盖亚奥特曼·至高型": ("ウルトラマンガイア スプリーム・ヴァージョン", "Ultraman Gaia Supreme Version"),
    "阿古茹奥特曼V2": ("ウルトラマンアグル V2", "Ultraman Agul V2"),
    "高斯奥特曼·日冕模式": ("ウルトラマンコスモス コロナモード", "Ultraman Cosmos Corona Mode"),
    "高斯奥特曼·日蚀模式": ("ウルトラマンコスモス エクリプスモード", "Ultraman Cosmos Eclipse Mode"),
    "奈克瑟斯奥特曼·青年形态": ("ウルトラマンネクサス ジュネッス", "Ultraman Nexus Junis"),
    "奈克瑟斯奥特曼·青年蓝色形态": ("ウルトラマンネクサス ジュネッスブルー", "Ultraman Nexus Junis Blue"),
    "梦比优斯奥特曼·勇者形态": ("ウルトラマンメビウス メビウスブレイブ", "Ultraman Mebius Mebius Brave"),
    "梦比优斯奥特曼·燃烧勇者": ("ウルトラマンメビウス バーニングブレイブ", "Ultraman Mebius Burning Brave"),
    "梦比优斯奥特曼·凤凰勇者": ("ウルトラマンメビウス フェニックスブレイブ", "Ultraman Mebius Phoenix Brave"),
    "梦比优斯奥特曼·无限形态": ("ウルトラマンメビウス インフィニティー", "Ultraman Mebius Infinity"),
    "超级奥特曼泰罗": ("スーパーウルトラマンタロウ", "Super Ultraman Taro"),
    "赛罗奥特曼·强壮日冕型": ("ストロングコロナゼロ", "Strong Corona Zero"),
    "赛罗奥特曼·月神奇迹型": ("ルナミラクルゼロ", "Luna-Miracle Zero"),
    "赛罗奥特曼·闪耀型": ("シャイニングウルトラマンゼロ", "Shining Ultraman Zero"),
    "赛罗奥特曼·无限形态": ("ウルトラマンゼロ ビヨンド", "Ultraman Zero Beyond"),
    "银河奥特曼·斯特利姆形态": ("ウルトラマンギンガストリウム", "Ultraman Ginga Strium"),
    "银河维克特利奥特曼": ("ウルトラマンギンガビクトリー", "Ultraman Ginga Victory"),
    "维克特利骑士": ("ウルトラマンビクトリーナイト", "Ultraman Victory Knight"),
    "艾克斯奥特曼·超越形态": ("エクシードX", "Exceed X"),
    "欧布奥特曼·重光形态": ("スペシウムゼペリオン", "Specium Zeperion"),
    "欧布奥特曼·暴炎形态": ("バーンマイト", "Burnmite"),
    "欧布奥特曼·疾风形态": ("ハリケーンスラッシュ", "Hurricane Slash"),
    "欧布奥特曼·暗耀形态": ("サンダーブレスター", "Thunder Breastar", "雷霆胸章"),
    "欧布奥特曼·原生形态": ("オーブオリジン", "Orb Origin"),
    "欧布奥特曼·煌闪形态": ("ライトニングアタッカー", "Lightning Attacker", "闪电攻击者"),
    "欧布奥特曼·智勇形态": ("エメリウムスラッガー", "Emerium Slugger", "艾梅利姆头镖"),
    "捷德奥特曼·原始形态": ("ウルトラマンジード プリミティブ", "Ultraman Geed Primitive"),
    "捷德奥特曼·刚燃形态": ("ウルトラマンジード ソリッドバーニング", "Ultraman Geed Solid Burning"),
    "捷德奥特曼·机敏形态": ("ウルトラマンジード アクロスマッシャー", "Ultraman Geed Acro Smasher"),
    "捷德奥特曼·豪勇形态": ("ウルトラマンジード マグニフィセント", "Ultraman Geed Magnificent"),
    "捷德奥特曼·尊皇形态": ("ウルトラマンジード ロイヤルメガマスター", "Ultraman Geed Royal Mega-Master"),
    "捷德奥特曼·银河初升": ("ウルトラマンジード ギャラクシーライジング", "Ultraman Geed Galaxy Rising"),
    "罗布奥特曼": ("ウルトラマンルーブ", "Ultraman Ruebe"),
    "格罗布奥特曼": ("ウルトラマングルーブ", "Ultraman Gruebe"),
    "泰迦奥特曼·光子地球": ("ウルトラマンタイガ フォトンアース", "Ultraman Taiga Photon Earth"),
    "泰迦奥特曼·三重斯特利姆形态": ("ウルトラマンタイガ トライストリウム", "Ultraman Taiga Tri-Strium"),
    "令迦奥特曼": ("ウルトラマンレイガ", "Ultraman Reiga"),
    "泽塔奥特曼·阿尔法装甲": ("ウルトラマンゼット アルファエッジ", "Ultraman Z Alpha Edge", "阿尔法利刃"),
    "泽塔奥特曼·贝塔冲击": ("ウルトラマンゼット ベータスマッシュ", "Ultraman Z Beta Smash"),
    "泽塔奥特曼·伽马未来": ("ウルトラマンゼット ガンマフューチャー", "Ultraman Z Gamma Future"),
    "泽塔奥特曼·德尔塔天爪": ("ウルトラマンゼット デルタライズクロー", "Ultraman Z Delta Rise Claw"),
    "特利迦奥特曼·强力型": ("ウルトラマントリガー パワータイプ", "Ultraman Trigger Power Type"),
    "特利迦奥特曼·空中型": ("ウルトラマントリガー スカイタイプ", "Ultraman Trigger Sky Type"),
    "闪耀特利迦永恒": ("グリッタートリガーエタニティ", "Glitter Trigger Eternity"),
    "真理特利迦": ("トリガートゥルース", "Trigger Truth"),
    "德凯奥特曼·强壮型": ("ウルトラマンデッカー ストロングタイプ", "Ultraman Decker Strong Type"),
    "德凯奥特曼·奇迹型": ("ウルトラマンデッカー ミラクルタイプ", "Ultraman Decker Miracle Type"),
    "德凯奥特曼·强劲型": ("ウルトラマンデッカー ダイナミックタイプ", "Ultraman Decker Dynamic Type"),
    "布莱泽奥特曼·法多兰盔甲": ("ウルトラマンブレーザー ファードランアーマー", "Ultraman Blazar Firdran Armor", "法德兰装甲"),
    "亚刻奥特曼·索利斯装甲": ("ウルトラマンアーク ソリスアーマー", "Ultraman Arc Solis Armor", "太阳装甲"),
    "亚刻奥特曼·露娜装甲": ("ウルトラマンアーク ルーナアーマー", "Ultraman Arc Luna Armor", "月亮装甲"),
    "亚刻奥特曼·银河装甲": ("ウルトラマンアーク ギャラクシーアーマー", "Ultraman Arc Galaxy Armor"),
    "欧米伽奥特曼·雷金斯装甲": ("ウルトラマンオメガ レキネスアーマー", "Ultraman Omega Rekiness Armor", "雷基尼斯装甲"),
    "欧米伽奥特曼·特里加隆装甲": ("ウルトラマンオメガ トライガロンアーマー", "Ultraman Omega Trigaron Armor"),
    "欧米伽奥特曼·瓦尔根斯装甲": ("ウルトラマンオメガ ヴァルジェネスアーマー", "Ultraman Omega Valgenes Armor", "瓦尔格尼斯装甲"),
    "欧米伽奥特曼·加梅顿装甲": ("ウルトラマンオメガ ガメドンアーマー", "Ultraman Omega Gamedon Armor", "盖梅顿装甲"),
}

_FORM_IMAGE_HINTS = {
    "泽塔奥特曼·阿尔法装甲": "AlphaEdge",
    "泽塔奥特曼·贝塔冲击": "BetaSmash",
    "泽塔奥特曼·伽马未来": "GammaFuture",
    "特利迦奥特曼·强力型": "PowerType",
    "特利迦奥特曼·空中型": "SkyType",
}

_FORM_IMAGE_DIRECT_URLS = {
    # Manually verified against Tsuburaya's own page placement / rendered image.
    "格罗布奥特曼": (
        "https://tsuburaya-prod.com/wp-content/uploads/2018/12/"
        "%E3%82%A6%E3%83%AB%E3%83%88%E3%83%A9%E3%83%9E%E3%83%B3"
        "%E3%82%B0%E3%83%AB%E3%83%BC%E3%83%96-277x300.jpg"
    ),
    "泽塔奥特曼·德尔塔天爪": (
        "https://tsuburaya-prod.com/wp-content/uploads/2020/08/"
        "post-5598-ultramanz-1.jpg"
    ),
    "德凯奥特曼·强壮型": (
        "https://tsuburaya-prod.com/wp-content/uploads/2022/03/"
        "%E3%83%87%E3%83%83%E3%82%AB%E3%83%BC_%E3%82%B9%E3%83%88"
        "%E3%83%AD%E3%83%B3%E3%82%B0_50-1-200x300.png"
    ),
    "德凯奥特曼·奇迹型": (
        "https://tsuburaya-prod.com/wp-content/uploads/2022/03/"
        "%E3%83%87%E3%83%83%E3%82%AB%E3%83%BC_%E3%83%9F%E3%83%A9"
        "%E3%82%AF%E3%83%AB_50-1-200x300.png"
    ),
    "亚刻奥特曼·索利斯装甲": (
        "https://tsuburaya-prod.com/wp-content/uploads/2024/04/"
        "ALT4_st303_0577_%E3%82%BD%E3%83%AA%E3%82%B9_3000-4000-1-768x1024.jpg"
    ),
    "亚刻奥特曼·露娜装甲": (
        "https://tsuburaya-prod.com/wp-content/uploads/2024/04/"
        "ALT4_st305_0013_%E3%83%AB%E3%83%BC%E3%83%8A_3000-4000-768x1024.jpg"
    ),
    "欧米伽奥特曼·雷金斯装甲": (
        "https://tsuburaya-prod.com/wp-content/uploads/2025/06/"
        "%E3%82%AA%E3%83%A1%E3%82%AC_%E3%83%AC%E3%82%AD%E3%83%8D"
        "%E3%82%B9%E3%82%A2%E3%83%BC%E3%83%9E%E3%83%BC-683x1024.png"
    ),
    "欧米伽奥特曼·特里加隆装甲": (
        "https://tsuburaya-prod.com/wp-content/uploads/2025/06/"
        "%E3%82%AA%E3%83%A1%E3%82%AC_%E3%83%88%E3%83%A9%E3%82%A4"
        "%E3%82%AC%E3%83%AD%E3%83%B3%E3%82%A2%E3%83%BC%E3%83%9E"
        "%E3%83%BC-683x1024.png"
    ),
    "欧米伽奥特曼·瓦尔根斯装甲": (
        "https://tsuburaya-prod.com/wp-content/uploads/2025/09/"
        "UltramanOmega_ValgenessArmor_12_for_confirmation-683x1024.png"
    ),
    "欧米伽奥特曼·加梅顿装甲": (
        "https://tsuburaya-prod.com/wp-content/uploads/2025/12/"
        "%E3%82%A6%E3%83%AB%E3%83%88%E3%83%A9%E3%83%9E%E3%83%B3"
        "%E3%82%AA%E3%83%A1%E3%82%AC-%E3%82%AC%E3%83%A1%E3%83%89"
        "%E3%83%B3%E3%82%A2%E3%83%BC%E3%83%9E%E3%83%BC_UNS2026-683x1024.jpg"
    ),
}

_FORM_IMAGE_PAGE_URLS = {
    # Tsuburaya's official store page has a dedicated Glitter Tiga product image.
    "闪耀迪迦": "https://store.m-78.jp/collections/tdg/products/4582769901454",
}
_FORM_IMAGE_SEARCH_QUERIES = {
    "梦比优斯奥特曼·无限形态": "梦比优斯奥特曼 无限形态 Mebius Infinity",
    "赛罗奥特曼·强壮日冕型": "赛罗奥特曼 强壮日冕型 Strong Corona Zero",
    "赛罗奥特曼·月神奇迹型": "赛罗奥特曼 月神奇迹型 Luna Miracle Zero",
    "赛罗奥特曼·闪耀型": "赛罗奥特曼 闪耀型 Shining Zero",
    "赛罗奥特曼·无限形态": "赛罗奥特曼 无限形态 Zero Beyond",
    "银河奥特曼·斯特利姆形态": "银河奥特曼 斯特利姆形态 Ginga Strium",
    "银河维克特利奥特曼": "银河维克特利奥特曼 Ginga Victory",
    "艾克斯奥特曼·超越形态": "艾克斯奥特曼 超越形态 Exceed X",
    "欧布奥特曼·重光形态": "欧布奥特曼 重光形态 Specium Zeperion",
    "欧布奥特曼·暴炎形态": "欧布奥特曼 暴炎形态 Burnmite",
    "欧布奥特曼·疾风形态": "欧布奥特曼 疾风形态 Hurricane Slash",
    "欧布奥特曼·暗耀形态": "欧布奥特曼 暗耀形态 雷霆胸章 Thunder Breastar",
    "欧布奥特曼·原生形态": "欧布奥特曼 原生形态 Orb Origin",
    "欧布奥特曼·煌闪形态": "欧布奥特曼 煌闪形态 闪电攻击者 Lightning Attacker",
    "欧布奥特曼·智勇形态": "欧布奥特曼 智勇形态 艾梅利姆头镖 Emerium Slugger",
    "泰迦奥特曼·三重斯特利姆形态": "泰迦奥特曼 三重斯特利姆形态 Tri Strium",
    "真理特利迦": "真理特利迦 Trigger Truth",
    "布莱泽奥特曼·法多兰盔甲": "布莱泽奥特曼 法多兰盔甲 法德兰装甲 Firdran Armor",
    "亚刻奥特曼·索利斯装甲": "亚刻奥特曼 索利斯装甲 Solis Armor",
    "亚刻奥特曼·露娜装甲": "亚刻奥特曼 露娜装甲 Luna Armor",
    "欧米伽奥特曼·雷金斯装甲": "欧米伽奥特曼 雷金斯装甲 Rekiness Armor",
    "欧米伽奥特曼·瓦尔根斯装甲": "欧米伽奥特曼 瓦尔根斯装甲 Valgenes Armor",
    "欧米伽奥特曼·加梅顿装甲": "欧米伽奥特曼 加梅顿装甲 Gamedon Armor",
}
_FORM_VARIANT_NAMES = {item[0] for item in _FORM_VARIANTS}
_RELATED_VARIANT_NAMES = {
    "帝纳斯奥特曼",
    "诺亚奥特曼",
    "雷杰多奥特曼",
    "赛迦奥特曼",
    "贝利亚早期形态",
    "托雷基亚早期形态",
}
_RELATED_ALT_NAMES = {
    "帝纳斯奥特曼": ("ウルトラマンディナス", "Ultraman Dinas"),
    "诺亚奥特曼": ("ウルトラマンノア", "Ultraman Noa"),
    "雷杰多奥特曼": ("ウルトラマンレジェンド", "Ultraman Legend"),
    "贝利亚早期形态": ("ウルトラマンベリアル アーリースタイル", "Ultraman Belial Early Style"),
    "托雷基亚早期形态": ("ウルトラマントレギア アーリースタイル", "Ultraman Tregear Early Style"),
}


_ENCYCLOPEDIA_IMAGE_ALIASES = {
    "闪耀迪迦": ("闪耀迪迦（TV版）", "闪耀迪迦（剧场版）", "闪光迪迦", "黄金迪迦"),
    "超级奥特曼泰罗": ("超级泰罗", "超级奥特曼泰罗"),
    "捷德奥特曼·尊皇形态": ("尊皇形态", "皇家超级大师", "皇家大师"),
    "捷德奥特曼·银河初升": ("银河初升", "银河升华", "银河升华形态"),
}


def ultraman_image_search_query(hero: Ultraman) -> str:
    return _FORM_IMAGE_SEARCH_QUERIES.get(hero.name, hero.name)


def ultraman_image_aliases(hero: Ultraman) -> tuple[str, ...]:
    values: list[str] = [hero.name]
    values.extend(_FORM_ALT_NAMES.get(hero.name, ()))
    values.extend(_RELATED_ALT_NAMES.get(hero.name, ()))
    values.extend(_ENCYCLOPEDIA_IMAGE_ALIASES.get(hero.name, ()))
    values.extend(
        alias
        for alias, canonical_name in _ULTRAMAN_ALIASES.items()
        if canonical_name == hero.name
    )
    # Base/standalone characters may safely use a stable English alias derived
    # from their official slug. Forms that share a parent slug must never inherit
    # that parent alias, otherwise "Ultraman Zero" could validate a Zero form.
    if not is_ultraman_form_variant(hero):
        slug_alias = hero.slug.replace("-", " ").strip()
        if slug_alias:
            values.append(slug_alias)
    return tuple(dict.fromkeys(value for value in values if value))


def is_ultraman_form_variant(hero: Ultraman) -> bool:
    return hero.name in _FORM_VARIANT_NAMES or hero.name in _RELATED_VARIANT_NAMES



for _name, _slug, _page_path, _year, _description, _background in _EXPANDED_ULTRAMAN_DATA:
    ULTRAMAN_ROSTER += (Ultraman(_name, _slug, _page_path),)
    ULTRAMAN_DEBUT_YEARS[_name] = _year
    _dark = any(word in _name for word in ("贝利亚", "托雷基亚", "黑暗", "邪恶", "扎基"))
    ULTRAMAN_PROFILES[_name] = UltramanProfile(
        "力量会证明谁才配支配命运。" if _dark else "光会回应每一个不肯放弃的人。",
        _description,
        _background,
    )

for _name, _slug, _year, _description, _background in _FORM_VARIANTS:
    ULTRAMAN_ROSTER += (
        Ultraman(_name, _slug, image_hint=_FORM_IMAGE_HINTS.get(_name, "")),
    )
    ULTRAMAN_DEBUT_YEARS[_name] = _year
    ULTRAMAN_PROFILES[_name] = UltramanProfile(
        "形态会改变，守护之心不会。",
        _description,
        _background,
    )

ULTRAMAN_BY_NAME = {hero.name: hero for hero in ULTRAMAN_ROSTER}
ULTRAMAN_ALIAS_INDEX = _build_ultraman_alias_index()


class _OpenGraphImageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.image_url = ""
        self.content_image_urls: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {key.lower(): value or "" for key, value in attrs}
        if tag.lower() == "img":
            source = values.get("src") or values.get("data-src")
            if source and "/uploads/" in source and not source.startswith("data:"):
                self.content_image_urls.append(source)
            return
        if (
            tag.lower() == "meta"
            and not self.image_url
            and values.get("property", "").lower() == "og:image"
        ):
            self.image_url = values.get("content", "")


def _normalize_image_descriptor(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value or "").casefold()
    return re.sub(r"[^0-9a-z\u3040-\u30ff\u3400-\u9fff]+", "", normalized)


def _official_form_terms(hero: Ultraman) -> tuple[str, ...]:
    values = list(ultraman_image_aliases(hero))
    if "·" in hero.name:
        values.append(hero.name.split("·", 1)[1])
    generic = {
        _normalize_image_descriptor(value)
        for value in ("奥特曼", "Ultraman", "ウルトラマン", "Ultra")
    }
    terms: list[str] = []
    for value in values:
        normalized = _normalize_image_descriptor(value)
        if len(normalized) >= 3 and normalized not in generic and normalized not in terms:
            terms.append(normalized)
    return tuple(terms)


def _official_form_image_matches(hero: Ultraman, source: str, label: str = "") -> bool:
    descriptor = _normalize_image_descriptor(f"{label} {source}")
    return bool(descriptor) and any(
        term in descriptor for term in _official_form_terms(hero)
    )


class _OfficialSearchImageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.images: list[tuple[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.casefold() != "img":
            return
        values = {key.casefold(): value or "" for key, value in attrs}
        source = (
            values.get("src")
            or values.get("data-src")
            or values.get("data-original")
            or ""
        )
        if not source or source.startswith("data:"):
            return
        label = " ".join(
            part
            for part in (
                values.get("alt", ""),
                values.get("title", ""),
                values.get("aria-label", ""),
            )
            if part
        )
        self.images.append((source, label))


async def official_ultraman_search_image(hero: Ultraman, settings: Settings) -> str:
    """Find a form-specific image on official Tsuburaya/M78 pages.

    This never accepts a page-level hero/banner image. The individual image
    element's label or filename itself must name the requested form.
    """
    if not is_ultraman_form_variant(hero):
        raise RuntimeError("官方站内精确图片搜索仅用于独立形态")

    timeout = max(5.0, min(float(settings.media_timeout_seconds), 15.0))
    query = ultraman_image_search_query(hero)
    search_queries = (
        f'site:tsuburaya-prod.com "{query}"',
        f'site:store.m-78.jp "{query}"',
    )
    allowed_hosts = {"tsuburaya-prod.com", "www.tsuburaya-prod.com", "store.m-78.jp"}
    pages: list[str] = []
    for search_query in search_queries:
        try:
            results = await search_web(search_query, limit=8, timeout=timeout)
        except (ValueError, httpx.HTTPError):
            continue
        for result in results:
            host = (urlsplit(result.url).hostname or "").casefold()
            if host in allowed_hosts and result.url not in pages:
                pages.append(result.url)

    if not pages:
        raise RuntimeError("没有搜到包含该形态的圆谷官方页面")

    headers = {"User-Agent": OFFICIAL_USER_AGENT}
    errors: list[str] = []
    async with httpx.AsyncClient(
        timeout=timeout,
        follow_redirects=True,
        headers=headers,
    ) as client:
        for page_url in pages[:12]:
            try:
                page = await client.get(page_url)
                page.raise_for_status()
            except httpx.HTTPError as exc:
                errors.append(str(exc))
                continue

            parser = _OfficialSearchImageParser()
            parser.feed(page.text)
            for source, label in parser.images:
                image_url = urljoin(str(page.url), source)
                if not _official_form_image_matches(hero, image_url, label):
                    continue
                for candidate in _official_image_candidates(image_url):
                    try:
                        image = await client.get(candidate)
                        image.raise_for_status()
                        if not image.headers.get("content-type", "").startswith("image/"):
                            raise RuntimeError("官方搜索结果图片响应格式无效")
                        if len(image.content) > settings.media_max_bytes:
                            raise RuntimeError("官方搜索结果图片超过大小限制")
                        with Image.open(BytesIO(image.content)) as decoded:
                            width, height = decoded.size
                        if width < 160 or height < 160 or width * height < 40_000:
                            raise RuntimeError("官方搜索结果图片尺寸过小")
                        return "base64://" + base64.b64encode(image.content).decode()
                    except (httpx.HTTPError, RuntimeError, OSError, ValueError) as exc:
                        errors.append(str(exc))

    suffix = "; ".join(errors[-4:])
    raise RuntimeError(
        "圆谷官方页面未找到文件名或标签明确对应该形态的图片"
        + (f"：{suffix}" if suffix else "")
    )


def _official_image_candidates(image_url: str) -> tuple[str, ...]:
    """Keep the encoded path intact when the legacy official image host redirects."""
    if image_url.startswith("http://"):
        image_url = "https://" + image_url.removeprefix("http://")
    if not image_url.startswith("https://"):
        return ()

    parts = urlsplit(image_url)
    candidates: list[str] = []
    if parts.hostname == "en.tsuburaya-prod.co.jp":
        # That host currently redirects Japanese filenames after decoding them as
        # latin-1, producing a mojibake path and a 404. Switching only the host
        # preserves the original percent-encoded UTF-8 path on the same official CDN.
        candidates.append(
            urlunsplit(
                ("https", "tsuburaya-prod.com", parts.path, parts.query, "")
            )
        )
    candidates.append(image_url)
    return tuple(dict.fromkeys(candidates))


async def official_ultraman_image(hero: Ultraman, settings: Settings) -> str:
    dedicated_page = _FORM_IMAGE_PAGE_URLS.get(hero.name, "")
    direct_image = _FORM_IMAGE_DIRECT_URLS.get(hero.name, "")
    page_url = (
        dedicated_page
        or (
            urljoin("https://tsuburaya-prod.com/", hero.page_path.lstrip("/"))
            if hero.page_path
            else f"{OFFICIAL_HERO_BASE_URL}/{hero.slug}"
        )
    )
    timeout = min(float(settings.media_timeout_seconds), 30.0)
    headers = {"User-Agent": OFFICIAL_USER_AGENT}
    async with httpx.AsyncClient(
        timeout=timeout, follow_redirects=True, headers=headers
    ) as client:
        candidate_urls: list[str] = []
        if direct_image:
            candidate_urls.extend(_official_image_candidates(direct_image))
        else:
            page = await client.get(page_url)
            page.raise_for_status()
            parser = _OpenGraphImageParser()
            parser.feed(page.text)
            if hero.image_hint:
                hinted = next(
                    (
                        source
                        for source in parser.content_image_urls
                        if hero.image_hint.casefold() in source.casefold()
                    ),
                    "",
                )
                if hinted:
                    candidate_urls.extend(
                        _official_image_candidates(urljoin(page_url, hinted))
                    )

            # Base characters may safely use the page's og:image. Independent
            # forms need a dedicated page, verified direct image, or exact match.
            if dedicated_page or not is_ultraman_form_variant(hero):
                candidate_urls.extend(_official_image_candidates(parser.image_url))

        candidates = tuple(dict.fromkeys(candidate_urls))
        if not candidates:
            raise RuntimeError("圆谷官方角色页没有返回可用图片")
        errors: list[str] = []
        for image_url in candidates:
            try:
                image = await client.get(image_url)
                image.raise_for_status()
                if not image.headers.get("content-type", "").startswith("image/"):
                    raise RuntimeError("图片响应格式无效")
                if len(image.content) > settings.media_max_bytes:
                    raise RuntimeError("图片超过大小限制")
                with Image.open(BytesIO(image.content)) as decoded:
                    width, height = decoded.size
                if width < 160 or height < 160 or width * height < 40_000:
                    raise RuntimeError("图片尺寸过小")
                return "base64://" + base64.b64encode(image.content).decode()
            except (httpx.HTTPError, RuntimeError, OSError, ValueError) as exc:
                errors.append(str(exc))
    raise RuntimeError("圆谷官方角色图片下载失败：" + "; ".join(errors))


def render_ultraman_card(
    hero: Ultraman, image_file: str, heading: str = "今日奥特曼"
) -> str:
    raw = base64.b64decode(image_file.removeprefix("base64://"))
    with Image.open(BytesIO(raw)) as source:
        source = source.convert("RGB")
        target_width, target_height = 900, 1200
        scale = max(target_width / source.width, target_height / source.height)
        resized = source.resize(
            (round(source.width * scale), round(source.height * scale)),
            Image.Resampling.LANCZOS,
        )
        left = max(0, (resized.width - target_width) // 2)
        top = max(0, (resized.height - target_height) // 2)
        card = resized.crop((left, top, left + target_width, top + target_height))

    overlay = Image.new("RGBA", card.size, (0, 0, 0, 0))
    gradient = ImageDraw.Draw(overlay)
    for y in range(650, target_height):
        alpha = int(215 * ((y - 650) / (target_height - 650)))
        gradient.line((0, y, target_width, y), fill=(3, 8, 20, alpha))
    card = Image.alpha_composite(card.convert("RGBA"), overlay)
    draw = ImageDraw.Draw(card)
    title_font = _load_font(42)
    name_font_size = 70
    while name_font_size > 34:
        name_font = _load_font(name_font_size)
        if draw.textbbox((0, 0), hero.name, font=name_font, stroke_width=3)[2] <= 790:
            break
        name_font_size -= 2
    else:
        name_font = _load_font(34)
    year_font = _load_font(34)
    draw.rounded_rectangle((44, 42, 334, 108), radius=22, fill=(0, 0, 0, 145))
    draw.text((68, 53), heading, font=title_font, fill="white")
    draw.text(
        (56, 1000),
        hero.name,
        font=name_font,
        fill="white",
        stroke_width=3,
        stroke_fill=(0, 0, 0),
    )
    draw.text(
        (60, 1095),
        f"首次登场 · {ULTRAMAN_DEBUT_YEARS[hero.name]}",
        font=year_font,
        fill=(210, 225, 255),
        stroke_width=2,
        stroke_fill=(0, 0, 0),
    )
    output = BytesIO()
    card.convert("RGB").save(output, format="JPEG", quality=90, optimize=True)
    return "base64://" + base64.b64encode(output.getvalue()).decode()


def _load_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """Load a CJK-capable font on Windows/Linux, with a portable final fallback."""
    for candidate in FONT_CANDIDATES:
        try:
            return ImageFont.truetype(candidate, size)
        except OSError:
            continue
    return ImageFont.load_default(size=size)


def ultraman_profile_text(hero: Ultraman) -> str:
    profile = ULTRAMAN_PROFILES[hero.name]
    year = ULTRAMAN_DEBUT_YEARS[hero.name]
    if year <= 1980:
        era = (
            "他诞生于昭和特摄最具开拓精神的年代。那个时代的英雄并非无所不能，"
            "他们会受伤、会失败，却总会在红灯亮起前再次挡在人类与灾难之间。"
        )
    elif year < 2013:
        era = (
            "他来自奥特曼系列重新定义“光”的时代。力量不再只是战胜怪兽，"
            "更是面对恐惧、理解生命，并把选择未来的权利交还给人类。"
        )
    else:
        era = (
            "他是新生代光之战士中的一员。历代英雄的意志、当代伙伴的羁绊与自己的选择"
            "在他身上汇聚，让古老的光在新的时代继续燃烧。"
        )
    return (
        f"首次登场：{year} 年\n"
        f"代表语：『{profile.quote}』\n\n"
        f"⚡ 战士特点\n{profile.description}"
        "真正令人铭记的并不只是必杀技，而是身处绝境仍愿意向前一步、"
        "把自己留在所有人身前的决心。\n\n"
        f"🌌 光之背景\n{profile.background}{era}\n\n"
        "图片：圆谷官方角色图（非 AI 生成）"
    )
