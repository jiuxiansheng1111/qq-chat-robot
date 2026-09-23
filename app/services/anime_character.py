import base64
import html
import json
import re
import unicodedata
from dataclasses import dataclass
from html.parser import HTMLParser
from io import BytesIO
from urllib.parse import quote

import httpx
from PIL import Image

from app.config import Settings
from app.llm.providers import LLMError


@dataclass(frozen=True)
class AnimeCharacter:
    name: str
    series: str
    description: str
    aliases: tuple[str, ...] = ()


ANIME_CHARACTER_ROSTER = (
    AnimeCharacter("丛雨", "《千恋＊万花》", "寄宿于丛雨丸中的刀灵，也是建实神社的神使。", ("ムラサメ", "Murasame")),
    AnimeCharacter("朝武芳乃", "《千恋＊万花》", "建实神社的巫女姬，性格认真而有责任感。", ("Tomotake Yoshino",)),
    AnimeCharacter("常陆茉子", "《千恋＊万花》", "芳乃的青梅竹马兼护卫，身手敏捷。", ("Hitachi Mako",)),
    AnimeCharacter("蕾娜·列支敦瑙尔", "《千恋＊万花》", "来自海外的少女，活泼直率。", ("レナ・リヒテナウアー", "Lena Liechtenauer")),
    AnimeCharacter("绫地宁宁", "《魔女的夜宴》", "拥有特殊能力的少女，外表沉稳，内心细腻。", ("綾地寧々", "Ayachi Nene")),
    AnimeCharacter("因幡巡", "《魔女的夜宴》", "性格开朗、行动力强的少女。", ("因幡めぐる", "Inaba Meguru")),
    AnimeCharacter("雷姆", "《Re:从零开始的异世界生活》", "罗兹瓦尔宅邸的双胞胎女仆之一，认真而忠诚。", ("レム", "Rem")),
    AnimeCharacter("拉姆", "《Re:从零开始的异世界生活》", "罗兹瓦尔宅邸的双胞胎女仆之一，言辞犀利。", ("ラム", "Ram")),
    AnimeCharacter("艾米莉亚", "《Re:从零开始的异世界生活》", "银发半精灵少女，性格善良。", ("エミリア", "Emilia")),
    AnimeCharacter("御坂美琴", "《某科学的超电磁炮》", "学园都市Level 5超能力者，能力为电击使。", ("御坂 美琴", "Misaka Mikoto")),
    AnimeCharacter("白井黑子", "《某科学的超电磁炮》", "风纪委员，拥有空间移动能力。", ("白井 黒子", "Shirai Kuroko")),
    AnimeCharacter("初音未来", "VOCALOID / Piapro Characters", "以歌声合成软件角色形象闻名的虚拟歌手。", ("初音ミク", "Hatsune Miku")),
    AnimeCharacter("后藤一里", "《孤独摇滚！》", "极度怕生却热爱吉他的少女，绰号“波奇”。", ("後藤ひとり", "Gotoh Hitori", "Bocchi")),
    AnimeCharacter("喜多郁代", "《孤独摇滚！》", "结束乐队的主唱兼吉他手，性格阳光。", ("喜多郁代", "Kita Ikuyo")),
    AnimeCharacter("锦木千束", "《莉可丽丝》", "实力出众、性格开朗的Lycoris成员。", ("錦木千束", "Nishikigi Chisato")),
    AnimeCharacter("井之上泷奈", "《莉可丽丝》", "冷静认真的Lycoris成员。", ("井ノ上たきな", "Inoue Takina")),
    AnimeCharacter("时崎狂三", "《约会大作战》", "拥有操纵时间相关能力的精灵。", ("時崎狂三", "Tokisaki Kurumi")),
    AnimeCharacter("五河琴里", "《约会大作战》", "五河士道的妹妹，也是Ratatoskr司令。", ("五河琴里", "Itsuka Kotori")),
    AnimeCharacter("雪之下雪乃", "《我的青春恋爱物语果然有问题。》", "侍奉部成员，理性而严格。", ("雪ノ下雪乃", "Yukinoshita Yukino")),
    AnimeCharacter("由比滨结衣", "《我的青春恋爱物语果然有问题。》", "侍奉部成员，性格亲和开朗。", ("由比ヶ浜結衣", "Yuigahama Yui")),
    AnimeCharacter("霞之丘诗羽", "《路人女主的养成方法》", "轻小说作家，学业优秀且善于言辞。", ("霞ヶ丘詩羽", "Kasumigaoka Utaha")),
    AnimeCharacter("加藤惠", "《路人女主的养成方法》", "性格平静、存在感较淡的少女。", ("加藤恵", "Kato Megumi")),
    AnimeCharacter("樱岛麻衣", "《青春猪头少年系列》", "演员兼学生，性格成熟稳重。", ("桜島麻衣", "Sakurajima Mai")),
    AnimeCharacter("牧之原翔子", "《青春猪头少年系列》", "与咲太人生经历密切相关的神秘少女。", ("牧之原翔子", "Makinohara Shoko")),
    AnimeCharacter("椎名真昼", "《关于邻家的天使大人不知不觉把我惯成了废人这档子事》", "成绩与外貌都很出众，被同学称为“天使”。", ("椎名真昼", "Shiina Mahiru")),
    AnimeCharacter("宝多六花", "《SSSS.GRIDMAN》", "性格随和的高中生，是古立特同盟的重要伙伴。", ("宝多六花", "Takarada Rikka")),
    AnimeCharacter("新条茜", "《SSSS.GRIDMAN》", "与作品核心事件密切相关的少女。", ("新条アカネ", "Shinjo Akane")),
    AnimeCharacter("四宫辉夜", "《辉夜大小姐想让我告白》", "秀知院学园学生会副会长。", ("四宮かぐや", "Shinomiya Kaguya")),
    AnimeCharacter("藤原千花", "《辉夜大小姐想让我告白》", "秀知院学园学生会书记，性格活泼。", ("藤原千花", "Fujiwara Chika")),
    AnimeCharacter("中野三玖", "《五等分的新娘》", "中野家五姐妹之一，性格内向，喜欢战国历史。", ("中野三玖", "Nakano Miku")),
    AnimeCharacter("中野二乃", "《五等分的新娘》", "中野家五姐妹之一，性格强势直接。", ("中野二乃", "Nakano Nino")),
    AnimeCharacter("阿尼亚·福杰", "《间谍过家家》", "拥有读心能力的少女，是福杰家的养女。", ("アーニャ・フォージャー", "Anya Forger")),
    AnimeCharacter("约尔·福杰", "《间谍过家家》", "表面是市政府职员，暗中是职业杀手。", ("ヨル・フォージャー", "Yor Forger")),
    AnimeCharacter("芙莉莲", "《葬送的芙莉莲》", "寿命漫长的精灵魔法使，在旅途中重新理解人与时间。", ("フリーレン", "Frieren")),
    AnimeCharacter("菲伦", "《葬送的芙莉莲》", "芙莉莲的弟子，年轻的人类魔法使。", ("フェルン", "Fern")),
    AnimeCharacter("猫猫", "《药屋少女的呢喃》", "对药物与毒物知识极其丰富的少女。", ("猫猫", "Maomao")),
    AnimeCharacter("甘露寺蜜璃", "《鬼灭之刃》", "鬼杀队恋柱，性格热情温柔。", ("甘露寺蜜璃", "Kanroji Mitsuri")),
    AnimeCharacter("蝴蝶忍", "《鬼灭之刃》", "鬼杀队虫柱，擅长毒与高速剑技。", ("胡蝶しのぶ", "Kocho Shinobu")),
    AnimeCharacter("祢豆子", "《鬼灭之刃》", "灶门炭治郎的妹妹，变成鬼后仍努力守护人类。", ("竈門禰豆子", "Kamado Nezuko")),
    AnimeCharacter("琪露诺", "《东方Project》", "居住在雾之湖附近的冰之妖精。", ("チルノ", "Cirno")),
    AnimeCharacter("博丽灵梦", "《东方Project》", "博丽神社的巫女，负责处理幻想乡异变。", ("博麗霊夢", "Hakurei Reimu")),
    AnimeCharacter("雾雨魔理沙", "《东方Project》", "人类魔法使，擅长强力光束魔法。", ("霧雨魔理沙", "Kirisame Marisa")),
)
ANIME_CHARACTER_BY_NAME = {item.name: item for item in ANIME_CHARACTER_ROSTER}


def anime_character_profile_text(character: AnimeCharacter) -> str:
    return f"作品来源：{character.series}\n角色简介：{character.description}"


def anime_character_catalog_text_pages(max_chars: int = 1700) -> list[str]:
    pages: list[str] = []
    current = f"✦ 二次元角色图鉴 · 共 {len(ANIME_CHARACTER_ROSTER)} 位 ✦\n"
    for index, character in enumerate(ANIME_CHARACTER_ROSTER, start=1):
        line = f"{index:03d}. {character.name}｜{character.series}\n"
        if len(current) + len(line) > max_chars:
            pages.append(current.rstrip())
            current = "✦ 二次元角色图鉴 · 续 ✦\n" + line
        else:
            current += line
    if current.strip():
        pages.append(current.rstrip())
    return pages


def _normalize(value: str) -> str:
    value = unicodedata.normalize("NFKC", value or "").casefold()
    return re.sub(r"[^0-9a-z\u3040-\u30ff\u3400-\u9fff]+", "", value)


def resolve_anime_character_query(text: str) -> AnimeCharacter | None:
    key = _normalize(text)
    if not key:
        return None
    for character in ANIME_CHARACTER_ROSTER:
        for alias in (character.name, *character.aliases):
            alias_key = _normalize(alias)
            if key == alias_key or key in {
                _normalize(f"介绍{alias}"),
                _normalize(f"看看{alias}"),
                _normalize(f"{alias}图片"),
                _normalize(f"{alias}资料"),
            }:
                return character
    return None


async def _download_image(client: httpx.AsyncClient, url: str, referer: str, settings: Settings) -> str:
    response = await client.get(url, headers={"Referer": referer, "Accept": "image/avif,image/webp,image/*,*/*"})
    response.raise_for_status()
    if not response.headers.get("content-type", "").casefold().startswith("image/"):
        raise RuntimeError("搜索结果不是图片")
    if len(response.content) > settings.media_max_bytes:
        raise RuntimeError("图片超过大小限制")
    with Image.open(BytesIO(response.content)) as source:
        width, height = source.size
        if width < 180 or height < 180 or width * height < 50_000:
            raise RuntimeError("图片尺寸过小")
        output = BytesIO()
        source.convert("RGB").save(output, format="JPEG", quality=92, optimize=True)
    return "base64://" + base64.b64encode(output.getvalue()).decode()


async def _wikipedia_image(character: AnimeCharacter, aliases: tuple[str, ...], settings: Settings) -> str | None:
    timeout = max(5.0, min(float(settings.media_timeout_seconds), 15.0))
    headers = {"User-Agent": "qq-chatrobot/0.1 anime-character-image"}
    names = tuple(dict.fromkeys((character.name, *aliases, *character.aliases)))
    terms = tuple(_normalize(name) for name in names if len(_normalize(name)) >= 2)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True, headers=headers) as client:
        for host in ("zh.wikipedia.org", "ja.wikipedia.org", "en.wikipedia.org"):
            endpoint = f"https://{host}/w/api.php"
            for query in names[:6]:
                try:
                    response = await client.get(endpoint, params={
                        "action": "query",
                        "generator": "search",
                        "gsrsearch": f"{query} {character.series}",
                        "gsrlimit": 5,
                        "prop": "pageimages",
                        "piprop": "thumbnail|original",
                        "pithumbsize": 1200,
                        "format": "json",
                        "formatversion": 2,
                    })
                    response.raise_for_status()
                    payload = response.json()
                except (httpx.HTTPError, ValueError):
                    continue
                pages = payload.get("query", {}).get("pages", [])
                if not isinstance(pages, list):
                    continue
                for page in pages:
                    title = str(page.get("title") or "")
                    norm_title = _normalize(title)
                    if not any(term and term in norm_title for term in terms):
                        continue
                    image = page.get("thumbnail") or page.get("original") or {}
                    url = str(image.get("source") or "")
                    if not url.startswith("http"):
                        continue
                    try:
                        return await _download_image(client, url, f"https://{host}/wiki/{quote(title)}", settings)
                    except (httpx.HTTPError, RuntimeError, OSError, ValueError):
                        continue
    return None


class _BingImageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.items: list[dict] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.casefold() != "a":
            return
        values = {k.casefold(): v or "" for k, v in attrs}
        if "iusc" not in {x.casefold() for x in values.get("class", "").split()}:
            return
        try:
            payload = json.loads(html.unescape(values.get("m", "")))
        except (TypeError, ValueError, json.JSONDecodeError):
            return
        if isinstance(payload, dict):
            self.items.append(payload)


async def _bing_image(character: AnimeCharacter, aliases: tuple[str, ...], settings: Settings) -> str | None:
    names = tuple(dict.fromkeys((character.name, *aliases, *character.aliases)))
    terms = tuple(_normalize(name) for name in names if len(_normalize(name)) >= 2)
    timeout = max(5.0, min(float(settings.media_timeout_seconds), 10.0))
    headers = {"User-Agent": "Mozilla/5.0 qq-chatrobot/0.1", "Referer": "https://www.bing.com/images/"}
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True, headers=headers) as client:
        for query in names[:6]:
            try:
                response = await client.get("https://www.bing.com/images/async", params={
                    "q": f'"{query}" {character.series} character',
                    "first": "1",
                    "count": "35",
                    "adlt": "strict",
                    "scenario": "ImageBasicHover",
                })
                response.raise_for_status()
            except httpx.HTTPError:
                continue
            parser = _BingImageParser()
            parser.feed(response.text)
            for item in parser.items[:35]:
                descriptor = " ".join(str(item.get(k) or "") for k in ("t", "desc", "purl", "murl"))
                if not any(term in _normalize(descriptor) for term in terms):
                    continue
                page_url = html.unescape(str(item.get("purl") or "https://www.bing.com/images/"))
                for url in (
                    html.unescape(str(item.get("murl") or "")),
                    html.unescape(str(item.get("turl") or item.get("turl2") or "")),
                ):
                    if not url.startswith(("https://", "http://")):
                        continue
                    try:
                        return await _download_image(client, url, page_url, settings)
                    except (httpx.HTTPError, RuntimeError, OSError, ValueError):
                        continue
    return None


async def resolve_anime_character_image(character: AnimeCharacter, settings: Settings, llm=None) -> str:
    base_aliases = tuple(character.aliases)
    image = await _wikipedia_image(character, base_aliases, settings)
    if image is not None:
        return image
    image = await _bing_image(character, base_aliases, settings)
    if image is not None:
        return image

    if llm is not None:
        try:
            answer = await llm.ask([
                {
                    "role": "system",
                    "content": (
                        "你只生成二次元角色图片搜索关键词，不回答其他内容。"
                        "输出3到6行，每行一个精确搜索短语。不要生成URL。"
                        "优先角色官方中文名、日文名、英文名，并包含作品名以避免同名误匹配。"
                    ),
                },
                {
                    "role": "user",
                    "content": f"角色：{character.name}\n作品：{character.series}\n已知别名：{'、'.join(character.aliases)}",
                },
            ])
            generated: list[str] = []
            for row in answer.splitlines():
                term = re.sub(r"^[-*•\d.、)）\s]+", "", row).strip(" \t\"'“”")
                if 2 <= len(term) <= 100 and term not in generated:
                    generated.append(term)
            if generated:
                aliases = tuple(dict.fromkeys((*base_aliases, *generated[:6])))
                image = await _wikipedia_image(character, aliases, settings)
                if image is not None:
                    return image
                image = await _bing_image(character, aliases, settings)
                if image is not None:
                    return image
        except (LLMError, httpx.HTTPError, RuntimeError, ValueError):
            pass

    raise RuntimeError(f"没有找到“{character.name}”的可靠角色图片")
