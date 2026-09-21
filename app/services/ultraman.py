import base64
from dataclasses import dataclass
from html.parser import HTMLParser
from io import BytesIO
from urllib.parse import urlsplit, urlunsplit

import httpx
from PIL import Image, ImageDraw, ImageFont

from app.config import Settings

OFFICIAL_HERO_BASE_URL = "https://tsuburaya-prod.com/heroes"
OFFICIAL_USER_AGENT = (
    "qq-chatrobot/0.1 (https://github.com/jiuxiansheng1111/qq-chat-robot)"
)


@dataclass(frozen=True)
class Ultraman:
    name: str
    slug: str


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


class _OpenGraphImageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.image_url = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "meta" or self.image_url:
            return
        values = {key.lower(): value or "" for key, value in attrs}
        if values.get("property", "").lower() == "og:image":
            self.image_url = values.get("content", "")


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
    page_url = f"{OFFICIAL_HERO_BASE_URL}/{hero.slug}"
    timeout = min(float(settings.media_timeout_seconds), 30.0)
    headers = {"User-Agent": OFFICIAL_USER_AGENT}
    async with httpx.AsyncClient(
        timeout=timeout, follow_redirects=True, headers=headers
    ) as client:
        page = await client.get(page_url)
        page.raise_for_status()
        parser = _OpenGraphImageParser()
        parser.feed(page.text)
        candidates = _official_image_candidates(parser.image_url)
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
                return "base64://" + base64.b64encode(image.content).decode()
            except (httpx.HTTPError, RuntimeError) as exc:
                errors.append(str(exc))
    raise RuntimeError("圆谷官方角色图片下载失败：" + "; ".join(errors))


def render_ultraman_card(hero: Ultraman, image_file: str) -> str:
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
    font_path = "C:/Windows/Fonts/msyh.ttc"
    title_font = ImageFont.truetype(font_path, 42)
    name_font = ImageFont.truetype(font_path, 70)
    year_font = ImageFont.truetype(font_path, 34)
    draw.rounded_rectangle((44, 42, 334, 108), radius=22, fill=(0, 0, 0, 145))
    draw.text((68, 53), "今日奥特曼", font=title_font, fill="white")
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
