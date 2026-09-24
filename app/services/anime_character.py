import asyncio
import base64
import hashlib
import html
import json
import re
import unicodedata
from dataclasses import dataclass
from html.parser import HTMLParser
from io import BytesIO
from pathlib import Path
from urllib.parse import quote, urljoin

import httpx
from PIL import Image

from app.config import Settings
from app.llm.providers import LLMError
from app.services.web_search import search_web


@dataclass(frozen=True)
class AnimeCharacter:
    name: str
    series: str
    description: str
    aliases: tuple[str, ...] = ()


ANIME_CHARACTER_ROSTER = (
    AnimeCharacter(
        "丛雨",
        "《千恋＊万花》",
        "寄宿于丛雨丸中的刀灵，也是建实神社的神使。",
        (
            "ムラサメ",
            "Murasame",
            "Senren Banka Murasame",
            "千恋万花 丛雨",
        ),
    ),
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
    AnimeCharacter(
        "阿尼亚·福杰",
        "《间谍过家家》",
        "拥有读心能力的少女，是福杰家的养女。",
        ("阿尼亚·佛杰", "安妮亚·福杰", "アーニャ・フォージャー", "Anya Forger", "Anya"),
    ),
    AnimeCharacter(
        "约尔·福杰",
        "《间谍过家家》",
        "表面是市政府职员，暗中是职业杀手。",
        ("约尔·佛杰", "ヨル・フォージャー", "Yor Forger", "Yor"),
    ),
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

ANIME_SERIES_ALIASES: dict[str, tuple[str, ...]] = {
    "《千恋＊万花》": ("Senren * Banka", "Senren Banka", "千恋＊万花"),
    "《魔女的夜宴》": ("Sanoba Witch", "サノバウィッチ"),
    "《Re:从零开始的异世界生活》": (
        "Re:ZERO -Starting Life in Another World-",
        "Re:ゼロから始める異世界生活",
    ),
    "《某科学的超电磁炮》": ("A Certain Scientific Railgun", "とある科学の超電磁砲"),
    "《孤独摇滚！》": ("Bocchi the Rock!", "ぼっち・ざ・ろっく！"),
    "《莉可丽丝》": ("Lycoris Recoil", "リコリス・リコイル"),
    "《约会大作战》": ("Date A Live", "デート・ア・ライブ"),
    "《我的青春恋爱物语果然有问题。》": (
        "My Teen Romantic Comedy SNAFU",
        "やはり俺の青春ラブコメはまちがっている。",
    ),
    "《路人女主的养成方法》": (
        "Saekano",
        "冴えない彼女の育てかた",
    ),
    "《青春猪头少年系列》": (
        "Rascal Does Not Dream",
        "青春ブタ野郎",
    ),
    "《关于邻家的天使大人不知不觉把我惯成了废人这档子事》": (
        "The Angel Next Door Spoils Me Rotten",
        "お隣の天使様",
    ),
    "《SSSS.GRIDMAN》": ("SSSS.GRIDMAN",),
    "《辉夜大小姐想让我告白》": (
        "Kaguya-sama: Love is War",
        "かぐや様は告らせたい",
    ),
    "《五等分的新娘》": ("The Quintessential Quintuplets", "五等分の花嫁"),
    "《间谍过家家》": ("SPY x FAMILY", "SPY×FAMILY"),
    "《葬送的芙莉莲》": ("Frieren: Beyond Journey's End", "葬送のフリーレン"),
    "《药屋少女的呢喃》": ("The Apothecary Diaries", "薬屋のひとりごと"),
    "《鬼灭之刃》": ("Demon Slayer: Kimetsu no Yaiba", "鬼滅の刃"),
    "《东方Project》": ("Touhou Project", "東方Project"),
}

ANIME_CHARACTER_SEARCH_HINTS: dict[str, tuple[str, ...]] = {
    "丛雨": (
        "千恋万花 丛雨 绿色头发 女角色",
        "千恋＊万花 ムラサメ 緑髪",
        "Senren Banka Murasame green hair",
    ),
    "猫猫": (
        "药屋少女的呢喃 猫猫 角色",
        "薬屋のひとりごと 猫猫",
        "The Apothecary Diaries Maomao",
    ),
    "雷姆": ("Re Zero Rem", "Re:ゼロ レム"),
    "拉姆": ("Re Zero Ram", "Re:ゼロ ラム"),
    "初音未来": ("初音ミク Hatsune Miku",),
}


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


async def _download_image(
    client: httpx.AsyncClient,
    url: str,
    referer: str,
    settings: Settings,
) -> str:
    response = await client.get(
        url,
        headers={
            "Referer": referer,
            "Accept": "image/avif,image/webp,image/*,*/*",
        },
    )
    response.raise_for_status()
    if not response.headers.get("content-type", "").casefold().startswith("image/"):
        raise RuntimeError("搜索结果不是图片")
    if len(response.content) > max(settings.media_max_bytes * 3, 12 * 1024 * 1024):
        raise RuntimeError("原始图片过大")

    try:
        with Image.open(BytesIO(response.content)) as source:
            width, height = source.size
            if width < 180 or height < 180 or width * height < 50_000:
                raise RuntimeError("图片尺寸过小")
            image = source.convert("RGB")
            image.thumbnail((1800, 1800), Image.Resampling.LANCZOS)
            output = BytesIO()
            image.save(output, format="JPEG", quality=90, optimize=True)
    except RuntimeError:
        raise
    except (OSError, ValueError) as exc:
        raise RuntimeError("图片无法解码") from exc

    payload = output.getvalue()
    if len(payload) > settings.media_max_bytes:
        # QQ/NapCat 对过大的 base64 图片不稳定，再压一档。
        with Image.open(BytesIO(payload)) as source:
            image = source.convert("RGB")
            image.thumbnail((1400, 1400), Image.Resampling.LANCZOS)
            output = BytesIO()
            image.save(output, format="JPEG", quality=82, optimize=True)
        payload = output.getvalue()
    if len(payload) > settings.media_max_bytes:
        raise RuntimeError("转换后的图片仍超过大小限制")
    return "base64://" + base64.b64encode(payload).decode()


def _series_terms(character: AnimeCharacter) -> tuple[str, ...]:
    raw = character.series.strip("《》 ")
    values = [raw]
    values.extend(
        part.strip()
        for part in re.split(r"[／/|｜·・:：]+", raw)
        if part.strip()
    )
    return tuple(
        dict.fromkeys(
            normalized
            for value in values
            if (normalized := _normalize(value)) and len(normalized) >= 3
        )
    )


def _name_terms(
    character: AnimeCharacter,
    aliases: tuple[str, ...],
) -> tuple[str, ...]:
    values = (character.name, *character.aliases, *aliases)
    return tuple(
        dict.fromkeys(
            normalized
            for value in values
            if (normalized := _normalize(value)) and len(normalized) >= 2
        )
    )


def _candidate_score(
    character: AnimeCharacter,
    aliases: tuple[str, ...],
    descriptor: str,
) -> int:
    normalized = _normalize(descriptor)
    if not normalized:
        return 0

    canonical = _normalize(character.name)
    score = 0
    if canonical and canonical in normalized:
        score += 14

    for alias in _name_terms(character, aliases):
        if alias == canonical:
            continue
        if alias in normalized:
            score += 9 if len(alias) >= 5 else 6
            break

    if any(term in normalized for term in _series_terms(character)):
        score += 5

    # A character match is mandatory. Series-only results are not enough.
    has_name = any(term in normalized for term in _name_terms(character, aliases))
    return score if has_name else 0


def _search_queries(
    character: AnimeCharacter,
    aliases: tuple[str, ...],
) -> tuple[str, ...]:
    series = character.series.strip("《》 ")
    values = [character.name, *character.aliases, *aliases]
    queries: list[str] = [
        f"{character.name} {series}",
        f'"{character.name}" "{series}"',
    ]
    queries.extend(ANIME_CHARACTER_SEARCH_HINTS.get(character.name, ()))
    for value in values:
        value = value.strip()
        if not value:
            continue
        queries.append(f"{value} {series}")
        queries.append(f'"{value}" "{series}"')
        queries.append(f"{value} {series} character")
    return tuple(dict.fromkeys(queries))[:16]


def _series_match_terms(character: AnimeCharacter) -> tuple[str, ...]:
    values = [character.series.strip("《》 ")]
    values.extend(ANIME_SERIES_ALIASES.get(character.series, ()))
    return tuple(
        dict.fromkeys(
            normalized
            for value in values
            if (normalized := _normalize(value))
        )
    )


def _candidate_name_matches(
    character: AnimeCharacter,
    aliases: tuple[str, ...],
    values: list[str],
) -> bool:
    descriptor = _normalize(" ".join(values))
    return any(
        term in descriptor
        for term in _name_terms(character, aliases)
    )


async def _vndb_image(
    character: AnimeCharacter,
    aliases: tuple[str, ...],
    settings: Settings,
) -> str | None:
    """Resolve visual-novel character art through VNDB's structured API."""
    queries = tuple(
        dict.fromkeys(
            value
            for value in (
                *character.aliases,
                character.name,
                *aliases,
            )
            if value
        )
    )[:6]
    series_terms = _series_match_terms(character)
    timeout = max(3.0, min(float(settings.media_timeout_seconds), 7.0))
    headers = {
        "User-Agent": "qq-chatrobot/0.1 anime-character-image",
        "Content-Type": "application/json",
    }
    async with httpx.AsyncClient(
        timeout=timeout,
        follow_redirects=True,
        headers=headers,
    ) as client:
        for query in queries:
            try:
                response = await client.post(
                    "https://api.vndb.org/kana/character",
                    json={
                        "filters": ["search", "=", query],
                        "fields": (
                            "id,name,original,aliases,image.url,image.dims,"
                            "vns.title,vns.alttitle"
                        ),
                        "sort": "searchrank",
                        "results": 10,
                    },
                )
                response.raise_for_status()
                payload = response.json()
            except (httpx.HTTPError, ValueError):
                continue

            results = payload.get("results", [])
            if not isinstance(results, list):
                continue
            for item in results:
                if not isinstance(item, dict):
                    continue
                name_values = [
                    str(item.get("name") or ""),
                    str(item.get("original") or ""),
                ]
                raw_aliases = item.get("aliases") or []
                if isinstance(raw_aliases, list):
                    name_values.extend(str(value) for value in raw_aliases)
                if not _candidate_name_matches(
                    character,
                    aliases,
                    name_values,
                ):
                    continue

                vns = item.get("vns") or []
                vn_values: list[str] = []
                if isinstance(vns, list):
                    for vn in vns:
                        if not isinstance(vn, dict):
                            continue
                        vn_values.extend(
                            (
                                str(vn.get("title") or ""),
                                str(vn.get("alttitle") or ""),
                            )
                        )
                if vn_values:
                    normalized_vns = _normalize(" ".join(vn_values))
                    if not any(term in normalized_vns for term in series_terms):
                        continue

                image = item.get("image") or {}
                if not isinstance(image, dict):
                    continue
                image_url = str(image.get("url") or "")
                if not image_url.startswith(("https://", "http://")):
                    continue
                try:
                    return await _download_image(
                        client,
                        image_url,
                        "https://vndb.org/",
                        settings,
                    )
                except (
                    httpx.HTTPError,
                    RuntimeError,
                    OSError,
                    ValueError,
                ):
                    continue
    return None


async def _anilist_image(
    character: AnimeCharacter,
    aliases: tuple[str, ...],
    settings: Settings,
) -> str | None:
    """Resolve anime/manga character art through AniList GraphQL."""
    queries = tuple(
        dict.fromkeys(
            value
            for value in (
                *character.aliases,
                character.name,
                *aliases,
            )
            if value
        )
    )[:6]
    timeout = max(3.0, min(float(settings.media_timeout_seconds), 7.0))
    query_doc = """
    query ($search: String) {
      Character(search: $search) {
        id
        name {
          full
          native
          alternative
        }
        image {
          large
          medium
        }
        media(perPage: 10) {
          nodes {
            title {
              romaji
              english
              native
            }
          }
        }
      }
    }
    """
    series_terms = _series_match_terms(character)
    headers = {
        "User-Agent": "qq-chatrobot/0.1 anime-character-image",
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(
        timeout=timeout,
        follow_redirects=True,
        headers=headers,
    ) as client:
        for query in queries:
            try:
                response = await client.post(
                    "https://graphql.anilist.co",
                    json={
                        "query": query_doc,
                        "variables": {"search": query},
                    },
                )
                response.raise_for_status()
                payload = response.json()
            except (httpx.HTTPError, ValueError):
                continue

            item = payload.get("data", {}).get("Character")
            if not isinstance(item, dict):
                continue
            names = item.get("name") or {}
            name_values = [
                str(names.get("full") or ""),
                str(names.get("native") or ""),
            ]
            alternatives = names.get("alternative") or []
            if isinstance(alternatives, list):
                name_values.extend(str(value) for value in alternatives)
            if not _candidate_name_matches(
                character,
                aliases,
                name_values,
            ):
                continue

            media = item.get("media") or {}
            nodes = media.get("nodes") or []
            media_values: list[str] = []
            if isinstance(nodes, list):
                for node in nodes:
                    if not isinstance(node, dict):
                        continue
                    title = node.get("title") or {}
                    if isinstance(title, dict):
                        media_values.extend(
                            str(title.get(key) or "")
                            for key in ("romaji", "english", "native")
                        )
            if media_values:
                normalized_media = _normalize(" ".join(media_values))
                if not any(term in normalized_media for term in series_terms):
                    # Exact character aliases are often unique, but when media
                    # metadata exists and contradicts the requested series,
                    # reject the candidate to avoid same-name characters.
                    continue

            image = item.get("image") or {}
            if not isinstance(image, dict):
                continue
            image_url = str(
                image.get("large")
                or image.get("medium")
                or ""
            )
            if not image_url.startswith(("https://", "http://")):
                continue
            try:
                return await _download_image(
                    client,
                    image_url,
                    "https://anilist.co/",
                    settings,
                )
            except (
                httpx.HTTPError,
                RuntimeError,
                OSError,
                ValueError,
            ):
                continue
    return None


async def _wikipedia_image(
    character: AnimeCharacter,
    aliases: tuple[str, ...],
    settings: Settings,
) -> str | None:
    timeout = max(5.0, min(float(settings.media_timeout_seconds), 15.0))
    headers = {"User-Agent": "qq-chatrobot/0.1 anime-character-image"}
    names = tuple(dict.fromkeys((character.name, *aliases, *character.aliases)))
    terms = _name_terms(character, aliases)
    async with httpx.AsyncClient(
        timeout=timeout,
        follow_redirects=True,
        headers=headers,
    ) as client:
        for host in ("zh.wikipedia.org", "ja.wikipedia.org", "en.wikipedia.org"):
            endpoint = f"https://{host}/w/api.php"
            for query in names[:8]:
                try:
                    response = await client.get(
                        endpoint,
                        params={
                            "action": "query",
                            "generator": "search",
                            "gsrsearch": f"{query} {character.series}",
                            "gsrlimit": 6,
                            "prop": "pageimages",
                            "piprop": "thumbnail|original",
                            "pithumbsize": 1200,
                            "format": "json",
                            "formatversion": 2,
                        },
                    )
                    response.raise_for_status()
                    payload = response.json()
                except (httpx.HTTPError, ValueError):
                    continue

                pages = payload.get("query", {}).get("pages", [])
                if not isinstance(pages, list):
                    continue
                for page in pages:
                    if not isinstance(page, dict):
                        continue
                    title = str(page.get("title") or "")
                    norm_title = _normalize(title)
                    if not any(term in norm_title for term in terms):
                        continue
                    image = page.get("thumbnail") or page.get("original") or {}
                    url = str(image.get("source") or "")
                    if not url.startswith(("https://", "http://")):
                        continue
                    try:
                        return await _download_image(
                            client,
                            url,
                            f"https://{host}/wiki/{quote(title)}",
                            settings,
                        )
                    except (httpx.HTTPError, RuntimeError, OSError, ValueError):
                        continue
    return None


class _BingImageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.items: list[dict] = []

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        if tag.casefold() != "a":
            return
        values = {key.casefold(): value or "" for key, value in attrs}
        classes = {value.casefold() for value in values.get("class", "").split()}
        if "iusc" not in classes:
            return
        try:
            payload = json.loads(html.unescape(values.get("m", "")))
        except (TypeError, ValueError, json.JSONDecodeError):
            return
        if isinstance(payload, dict):
            self.items.append(payload)


class _CharacterPageImageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.title = ""
        self.og_image = ""
        self.images: list[tuple[str, str]] = []
        self._in_title = False
        self._title_parts: list[str] = []

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        values = {key.casefold(): value or "" for key, value in attrs}
        lowered = tag.casefold()
        if lowered == "title":
            self._in_title = True
            return
        if lowered == "meta":
            prop = (values.get("property") or values.get("name") or "").casefold()
            if prop in {"og:image", "twitter:image"} and not self.og_image:
                self.og_image = values.get("content", "")
            return
        if lowered != "img":
            return
        source = (
            values.get("data-src")
            or values.get("data-original")
            or values.get("src")
            or ""
        )
        label = " ".join(
            value
            for value in (
                values.get("alt", ""),
                values.get("title", ""),
                values.get("aria-label", ""),
            )
            if value
        )
        if source:
            self.images.append((source, label))

    def handle_endtag(self, tag: str) -> None:
        if tag.casefold() == "title":
            self._in_title = False
            if not self.title:
                self.title = " ".join(self._title_parts).strip()

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self._title_parts.append(data)


async def _web_page_character_image(
    character: AnimeCharacter,
    aliases: tuple[str, ...],
    settings: Settings,
) -> str | None:
    series = character.series.strip("《》 ")
    query_names = tuple(dict.fromkeys((character.name, *character.aliases, *aliases)))
    queries = [
        f'"{query_names[0]}" "{series}" 公式 character',
        f'"{query_names[0]}" "{series}" 角色 官网',
    ]
    for alias in query_names[1:5]:
        queries.append(f'"{alias}" "{series}" official character')

    seen_urls: set[str] = set()
    timeout = max(5.0, min(float(settings.media_timeout_seconds), 15.0))
    headers = {
        "User-Agent": "Mozilla/5.0 qq-chatrobot/0.1",
        "Accept-Language": "zh-CN,zh;q=0.9,ja;q=0.8,en;q=0.7",
    }

    for query in tuple(dict.fromkeys(queries))[:6]:
        try:
            results = await search_web(query, limit=8, timeout=timeout)
        except (httpx.HTTPError, ValueError):
            continue
        for result in results:
            if result.url in seen_urls:
                continue
            seen_urls.add(result.url)
            evidence_score = _candidate_score(
                character,
                aliases,
                f"{result.title} {result.snippet} {result.url}",
            )
            if evidence_score <= 0:
                continue

            try:
                async with httpx.AsyncClient(
                    timeout=timeout,
                    follow_redirects=True,
                    headers=headers,
                ) as client:
                    response = await client.get(result.url)
                    response.raise_for_status()
                    parser = _CharacterPageImageParser()
                    parser.feed(response.text)
                    page_descriptor = (
                        f"{result.title} {result.snippet} {parser.title} {response.url}"
                    )
                    page_score = _candidate_score(
                        character,
                        aliases,
                        page_descriptor,
                    )
                    if page_score <= 0:
                        continue

                    candidates: list[tuple[int, str, str]] = []
                    for source, label in parser.images:
                        image_url = urljoin(str(response.url), html.unescape(source))
                        score = _candidate_score(
                            character,
                            aliases,
                            f"{label} {image_url}",
                        )
                        if score > 0:
                            candidates.append((score + 20, image_url, label))
                    if parser.og_image:
                        og_url = urljoin(
                            str(response.url),
                            html.unescape(parser.og_image),
                        )
                        candidates.append(
                            (page_score, og_url, parser.title or result.title)
                        )

                    for _, image_url, _ in sorted(
                        candidates,
                        key=lambda item: item[0],
                        reverse=True,
                    ):
                        if not image_url.startswith(("https://", "http://")):
                            continue
                        try:
                            return await _download_image(
                                client,
                                image_url,
                                str(response.url),
                                settings,
                            )
                        except (
                            httpx.HTTPError,
                            RuntimeError,
                            OSError,
                            ValueError,
                        ):
                            continue
            except (httpx.HTTPError, ValueError):
                continue
    return None


async def _bing_image_relaxed(
    character: AnimeCharacter,
    aliases: tuple[str, ...],
    settings: Settings,
) -> str | None:
    """Last-resort exact-query image search.

    The exact character + series query is strong evidence by itself. We still
    require either the character identity or the series to appear in Bing tile
    metadata, but do not require both.
    """
    timeout = max(5.0, min(float(settings.media_timeout_seconds), 15.0))
    headers = {
        "User-Agent": "Mozilla/5.0 qq-chatrobot/0.1",
        "Referer": "https://www.bing.com/images/",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8,ja;q=0.7",
    }
    name_terms = _name_terms(character, aliases)
    series_terms = _series_terms(character)
    queries = _search_queries(character, aliases)

    async with httpx.AsyncClient(
        timeout=timeout,
        follow_redirects=True,
        headers=headers,
    ) as client:
        for query in queries[:10]:
            try:
                response = await client.get(
                    "https://www.bing.com/images/search",
                    params={
                        "q": query,
                        "first": "1",
                        "count": "50",
                        "adlt": "strict",
                    },
                )
                response.raise_for_status()
            except httpx.HTTPError:
                continue

            parser = _BingImageParser()
            parser.feed(response.text)
            for item in parser.items[:50]:
                descriptor = _normalize(
                    " ".join(
                        str(item.get(key) or "")
                        for key in ("t", "desc", "purl", "murl")
                    )
                )
                has_name = any(term in descriptor for term in name_terms)
                has_series = any(term in descriptor for term in series_terms)
                if not has_name and not has_series:
                    continue
                page_url = html.unescape(
                    str(item.get("purl") or "https://www.bing.com/images/")
                )
                image_urls = tuple(
                    dict.fromkeys(
                        html.unescape(str(item.get(key) or ""))
                        for key in ("turl", "turl2", "murl")
                    )
                )
                for image_url in image_urls:
                    if not image_url.startswith(("https://", "http://")):
                        continue
                    try:
                        return await _download_image(
                            client,
                            image_url,
                            page_url,
                            settings,
                        )
                    except (
                        httpx.HTTPError,
                        RuntimeError,
                        OSError,
                        ValueError,
                    ):
                        continue
    return None


async def _baidu_image(
    character: AnimeCharacter,
    aliases: tuple[str, ...],
    settings: Settings,
) -> str | None:
    timeout = max(5.0, min(float(settings.media_timeout_seconds), 10.0))
    headers = {
        "User-Agent": "Mozilla/5.0 qq-chatrobot/0.1",
        "Referer": "https://image.baidu.com/",
    }
    async with httpx.AsyncClient(
        timeout=timeout,
        follow_redirects=True,
        headers=headers,
    ) as client:
        for query in _search_queries(character, aliases)[:8]:
            try:
                response = await client.get(
                    "https://image.baidu.com/search/acjson",
                    params={
                        "tn": "resultjson_com",
                        "ipn": "rj",
                        "ct": "201326592",
                        "fp": "result",
                        "queryWord": query,
                        "word": query,
                        "ie": "utf-8",
                        "oe": "utf-8",
                        "pn": "0",
                        "rn": "30",
                        "newReq": "1",
                    },
                )
                response.raise_for_status()
                payload = response.json()
            except (httpx.HTTPError, ValueError):
                continue

            data = payload.get("data", [])
            if not isinstance(data, list):
                continue
            ranked: list[tuple[int, dict]] = []
            for item in data:
                if not isinstance(item, dict):
                    continue
                descriptor = " ".join(
                    str(item.get(key) or "")
                    for key in (
                        "fromPageTitleEnc",
                        "fromPageTitle",
                        "title",
                        "picInfo",
                        "bdImgNewsInfo",
                        "fromURL",
                        "middleURL",
                    )
                )
                score = _candidate_score(character, aliases, html.unescape(descriptor))
                if score >= 9:
                    ranked.append((score, item))

            for _, item in sorted(ranked, key=lambda pair: pair[0], reverse=True):
                page_url = html.unescape(
                    str(item.get("fromURL") or "https://image.baidu.com/")
                )
                image_urls = tuple(
                    dict.fromkeys(
                        html.unescape(str(item.get(key) or ""))
                        for key in (
                            "middleURL",
                            "hoverURL",
                            "thumbURL",
                            "objURL",
                            "replaceUrl",
                        )
                    )
                )
                for url in image_urls:
                    if not url.startswith(("https://", "http://")):
                        continue
                    try:
                        return await _download_image(
                            client,
                            url,
                            page_url,
                            settings,
                        )
                    except (httpx.HTTPError, RuntimeError, OSError, ValueError):
                        continue
    return None


async def _bing_image(
    character: AnimeCharacter,
    aliases: tuple[str, ...],
    settings: Settings,
) -> str | None:
    timeout = max(5.0, min(float(settings.media_timeout_seconds), 12.0))
    headers = {
        "User-Agent": "Mozilla/5.0 qq-chatrobot/0.1",
        "Referer": "https://www.bing.com/images/",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8,ja;q=0.7",
    }
    endpoints = (
        ("https://www.bing.com/images/async", {"scenario": "ImageBasicHover"}),
        ("https://www.bing.com/images/search", {"form": "HDRSC3"}),
    )
    async with httpx.AsyncClient(
        timeout=timeout,
        follow_redirects=True,
        headers=headers,
    ) as client:
        for query in _search_queries(character, aliases):
            for endpoint, extra_params in endpoints:
                params = {
                    "q": query,
                    "first": "1",
                    "count": "40",
                    "adlt": "strict",
                    **extra_params,
                }
                try:
                    response = await client.get(endpoint, params=params)
                    response.raise_for_status()
                except httpx.HTTPError:
                    continue

                parser = _BingImageParser()
                parser.feed(response.text)
                ranked: list[tuple[int, dict]] = []
                for item in parser.items[:50]:
                    descriptor = " ".join(
                        str(item.get(key) or "")
                        for key in ("t", "desc", "purl", "murl")
                    )
                    score = _candidate_score(
                        character,
                        aliases,
                        html.unescape(descriptor),
                    )
                    if score >= 9:
                        ranked.append((score, item))

                for _, item in sorted(
                    ranked,
                    key=lambda pair: pair[0],
                    reverse=True,
                ):
                    page_url = html.unescape(
                        str(item.get("purl") or "https://www.bing.com/images/")
                    )
                    image_urls = tuple(
                        dict.fromkeys(
                            html.unescape(str(item.get(key) or ""))
                            for key in ("murl", "turl", "turl2")
                        )
                    )
                    for url in image_urls:
                        if not url.startswith(("https://", "http://")):
                            continue
                        try:
                            return await _download_image(
                                client,
                                url,
                                page_url,
                                settings,
                            )
                        except (
                            httpx.HTTPError,
                            RuntimeError,
                            OSError,
                            ValueError,
                        ):
                            continue
    return None


async def _search_engine_first_image(
    character: AnimeCharacter,
    aliases: tuple[str, ...],
    settings: Settings,
) -> str | None:
    """Return the first downloadable exact-query image without source filtering.

    This is deliberately permissive: if strict matching fails, a Tencent Video,
    iQIYI, Bilibili, article, wiki, or other search-result source is acceptable.
    """
    queries = [
        f"{character.name} {character.series}",
        character.name,
    ]
    queries.extend(
        f"{alias} {character.series}"
        for alias in aliases
        if alias
    )
    queries = list(dict.fromkeys(query.strip() for query in queries if query.strip()))[:6]
    timeout = max(3.0, min(float(settings.media_timeout_seconds), 6.0))

    async with httpx.AsyncClient(
        timeout=timeout,
        follow_redirects=True,
        headers={"User-Agent": "Mozilla/5.0 qq-chatrobot/0.1"},
    ) as client:
        for query in queries:
            try:
                response = await client.get(
                    "https://image.baidu.com/search/acjson",
                    params={
                        "tn": "resultjson_com",
                        "ipn": "rj",
                        "ct": "201326592",
                        "fp": "result",
                        "queryWord": query,
                        "word": query,
                        "ie": "utf-8",
                        "oe": "utf-8",
                        "pn": "0",
                        "rn": "10",
                        "newReq": "1",
                    },
                    headers={"Referer": "https://image.baidu.com/"},
                )
                response.raise_for_status()
                payload = response.json()
            except (httpx.HTTPError, ValueError):
                continue

            items = payload.get("data", [])
            if isinstance(items, list):
                for item in items[:10]:
                    if not isinstance(item, dict):
                        continue
                    page_url = html.unescape(
                        str(item.get("fromURL") or "https://image.baidu.com/")
                    )
                    image_urls = tuple(
                        dict.fromkeys(
                            html.unescape(str(item.get(key) or ""))
                            for key in (
                                "middleURL",
                                "thumbURL",
                                "hoverURL",
                                "objURL",
                            )
                        )
                    )
                    for image_url in image_urls:
                        if not image_url.startswith(("https://", "http://")):
                            continue
                        try:
                            return await _download_image(
                                client,
                                image_url,
                                page_url,
                                settings,
                            )
                        except (
                            httpx.HTTPError,
                            RuntimeError,
                            OSError,
                            ValueError,
                        ):
                            continue

        for query in queries:
            try:
                response = await client.get(
                    "https://www.bing.com/images/async",
                    params={
                        "q": query,
                        "first": "1",
                        "count": "20",
                        "adlt": "strict",
                        "scenario": "ImageBasicHover",
                    },
                    headers={"Referer": "https://www.bing.com/images/"},
                )
                response.raise_for_status()
            except httpx.HTTPError:
                continue

            parser = _BingImageParser()
            parser.feed(response.text)
            for item in parser.items[:20]:
                page_url = html.unescape(
                    str(item.get("purl") or "https://www.bing.com/images/")
                )
                image_urls = tuple(
                    dict.fromkeys(
                        html.unescape(str(item.get(key) or ""))
                        for key in ("turl", "turl2", "murl")
                    )
                )
                for image_url in image_urls:
                    if not image_url.startswith(("https://", "http://")):
                        continue
                    try:
                        return await _download_image(
                            client,
                            image_url,
                            page_url,
                            settings,
                        )
                    except (
                        httpx.HTTPError,
                        RuntimeError,
                        OSError,
                        ValueError,
                    ):
                        continue
    return None


async def _llm_search_aliases(
    character: AnimeCharacter,
    llm,
) -> tuple[str, ...]:
    if llm is None:
        return ()
    try:
        answer = await llm.ask(
            [
                {
                    "role": "system",
                    "content": (
                        "你只负责生成动漫/游戏角色的图片搜索关键词。"
                        "禁止编造图片URL。输出4到8行，每行一个精确搜索短语。"
                        "优先给出官方中文译名、其他常见中文译名、日文名、英文名，"
                        "每行都带作品名或足以排除同名角色的信息。"
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"角色：{character.name}\n"
                        f"作品：{character.series}\n"
                        f"已知别名：{'、'.join(character.aliases)}\n"
                        "常规 Wikipedia、百度图片、Bing 图片搜索没有成功。"
                    ),
                },
            ]
        )
    except (LLMError, httpx.HTTPError, RuntimeError, ValueError):
        return ()

    values: list[str] = []
    for row in answer.splitlines():
        term = re.sub(r"^[-*•\d.、)）\s]+", "", row).strip()
        term = term.strip('"').strip("'").strip("“").strip("”")
        if 2 <= len(term) <= 100 and term not in values:
            values.append(term)
    return tuple(values[:8])


def _anime_image_cache_path(
    character: AnimeCharacter,
    settings: Settings,
) -> Path:
    cache_dir = Path(settings.anime_image_cache_dir).expanduser().resolve()
    cache_dir.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(
        f"{character.name}|{character.series}".encode()
    ).hexdigest()[:24]
    return cache_dir / f"{digest}.jpg"


def _load_anime_image_cache(
    character: AnimeCharacter,
    settings: Settings,
) -> str | None:
    path = _anime_image_cache_path(character, settings)
    if not path.exists():
        return None
    try:
        raw = path.read_bytes()
        if not raw:
            return None
        with Image.open(BytesIO(raw)) as source:
            width, height = source.size
            if width < 180 or height < 180:
                return None
        return "base64://" + base64.b64encode(raw).decode()
    except (OSError, ValueError):
        return None


def _save_anime_image_cache(
    character: AnimeCharacter,
    settings: Settings,
    image_file: str,
) -> None:
    if not image_file.startswith("base64://"):
        return
    try:
        raw = base64.b64decode(
            image_file.removeprefix("base64://"),
            validate=True,
        )
        with Image.open(BytesIO(raw)) as source:
            width, height = source.size
            if width < 180 or height < 180:
                return
        path = _anime_image_cache_path(character, settings)
        tmp = path.with_suffix(".tmp")
        tmp.write_bytes(raw)
        tmp.replace(path)
    except (OSError, ValueError):
        return


async def _anime_source_result(
    character: AnimeCharacter,
    aliases: tuple[str, ...],
    settings: Settings,
    source_name: str,
    resolver,
) -> str:
    image = await resolver(character, aliases, settings)
    if image is None:
        raise RuntimeError(f"{source_name} no-match")
    _save_anime_image_cache(character, settings, image)
    return image


async def _delayed_anime_first_image(
    character: AnimeCharacter,
    aliases: tuple[str, ...],
    settings: Settings,
) -> str:
    # Give stricter sources a short head start. After that, responsiveness wins.
    await asyncio.sleep(1.2)
    image = await _search_engine_first_image(character, aliases, settings)
    if image is None:
        raise RuntimeError("搜索引擎首图 no-match")
    _save_anime_image_cache(character, settings, image)
    return image


async def resolve_anime_character_image(
    character: AnimeCharacter,
    settings: Settings,
    llm=None,
) -> str:
    """Resolve every catalog character with cache, parallel search and hard deadline.

    LLM is intentionally not part of the critical image path. Exact character
    names, series names and known aliases are deterministic and faster.
    """
    del llm

    cached = _load_anime_image_cache(character, settings)
    if cached is not None:
        return cached

    aliases = tuple(character.aliases)
    source_specs = (
        ("VNDB", _vndb_image),
        ("AniList", _anilist_image),
        ("角色/官方网页", _web_page_character_image),
        ("Wikipedia", _wikipedia_image),
        ("百度图片", _baidu_image),
        ("Bing图片", _bing_image),
        ("Bing放宽匹配", _bing_image_relaxed),
    )
    tasks = [
        asyncio.create_task(
            _anime_source_result(
                character,
                aliases,
                settings,
                source_name,
                resolver,
            )
        )
        for source_name, resolver in source_specs
    ]
    tasks.append(
        asyncio.create_task(
            _delayed_anime_first_image(character, aliases, settings)
        )
    )

    timeout = max(
        0.2,
        min(float(settings.anime_image_resolve_timeout_seconds), 10.0),
    )
    errors: list[str] = []
    try:
        async with asyncio.timeout(timeout):
            for completed in asyncio.as_completed(tasks):
                try:
                    return await completed
                except (
                    httpx.HTTPError,
                    RuntimeError,
                    OSError,
                    ValueError,
                ) as exc:
                    errors.append(str(exc))
    except TimeoutError:
        errors.append(f"总搜索超过 {timeout:.1f}s")
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    cached = _load_anime_image_cache(character, settings)
    if cached is not None:
        return cached

    detail = "; ".join(errors[-6:])
    raise RuntimeError(
        f"没有找到“{character.name}”的可下载角色图片"
        + (f"；{detail}" if detail else "")
    )
