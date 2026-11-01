"""从单一群聊话题构造可画的连续故事；不请求模型、不冒充现场记录。"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

SOURCE_NOTE = (
    "根据群聊话题摘要虚构改编；人物、动作与收尾不代表现场，不是群友原话。"
    "角色外貌为本漫画的虚构Q版设定。"
)
_SENSITIVE = re.compile(
    r"自杀|自伤|抑郁症|精神病|诊断|霸凌|被孤立|被嘲笑|性骚扰|性侵|去世|车祸|住院"
)
_UNSAFE_REPAIR = re.compile(r"市电|高压|220\s*[vV伏]|锂电池鼓包|电池起火")


def _field(item: Any, key: str) -> Any:
    return item.get(key, "") if isinstance(item, Mapping) else getattr(item, key, "")


def _clean(value: Any, limit: int) -> str:
    if not isinstance(value, (str, int, float)) or isinstance(value, bool):
        return ""
    text = str(value)
    text = re.sub(r"\[CQ:[^\]]*\]", "", text)
    text = re.sub(r"https?://\S+", "相关链接", text)
    text = re.sub(r"(?:QQ号?|群号|用户ID)\s*[:：]?\s*\d{5,13}", "群友", text, flags=re.IGNORECASE)
    text = re.sub(r"\[\d{5,13}\]|(?<!\d)\d{5,13}(?!\d)", "群友", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _normalize(topics: Any) -> list[dict[str, str]]:
    if not isinstance(topics, Sequence) or isinstance(topics, (str, bytes)):
        return []
    result = []
    # 调用方保证新→旧排序；首条无效也不能借清洗之机跳到旧话题。
    for item in topics[:1]:
        title = _clean(_field(item, "topic"), 80)
        detail = _clean(_field(item, "detail"), 1000)
        if title:
            result.append({"topic": title, "detail": detail})
    return result


def _matches(text: str, words: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(word for word in words if word.casefold() in text.casefold())


@dataclass(frozen=True)
class _Candidate:
    category: str
    variant: str
    evidence: tuple[str, ...]


def _candidate(topic: Mapping[str, str]) -> _Candidate | None:
    text = topic["topic"] + " " + topic["detail"]
    if _SENSITIVE.search(text):
        return None
    hardware = _matches(
        text, ("显卡", "GPU", "RTX", "4090", "5090", "内存", "硬盘", "CPU", "处理器", "主板")
    )
    prices = _matches(
        text, ("价格", "涨价", "降价", "贵", "三万", "报价", "预算", "行情", "多少钱")
    )
    if hardware and prices:
        return _Candidate("hardware_price", "hardware_price", hardware + prices)
    components = _matches(text, ("电阻", "LED", "灯珠", "芯片", "电容", "电路板", "焊盘"))
    solder = _matches(text, ("焊接", "烙铁", "焊锡", "虚焊", "补焊", "拆焊"))
    if components and not _UNSAFE_REPAIR.search(text):
        if solder:
            return _Candidate("electronics_repair", "soldering", solder + components)
        if "万用表" in text and _matches(text, ("测", "检测", "排查", "检查")):
            return _Candidate("electronics_repair", "meter", ("万用表",) + components)
        if _matches(text, ("区分", "辨认", "识别", "分辨", "读取")):
            return _Candidate("electronics_repair", "identify", components)
    # 游戏道具必须来自同一条话题，绝不把另一个话题的对象拼进来。
    salvage = _matches(text, ("打捞", "钓起", "捞到"))
    sea_item = _matches(text, ("海水过滤器", "电鳗发电机", "漂流木", "物资箱"))
    if salvage and sea_item:
        return _Candidate("game", "salvage", salvage + sea_item)
    gacha = _matches(text, ("抽卡", "十连", "卡池", "保底"))
    if not gacha and "开箱" in text and _matches(text, ("游戏", "爆率", "概率", "抽奖")):
        gacha = ("开箱",)
    if gacha:
        return _Candidate("gacha", "gacha", gacha)
    cards = _matches(text, ("卡组", "卡牌", "牌表"))
    formats = _matches(text, ("标准", "狂野", "混用", "混进", "纠错"))
    if cards and formats:
        return _Candidate("game", "card_sorting", cards + formats)
    campus = _matches(text, ("校园", "校区", "学校", "学院", "大学", "上学", "宿舍"))
    commute = _matches(text, ("通勤", "公交", "班车", "地铁", "走错校区", "赶车"))
    if campus and commute:
        return _Candidate("campus", "commute", campus + commute)
    study = _matches(text, ("考试", "复习", "期末"))
    if study and _matches(text, ("笔记", "课本", "试卷", "题目", "复习资料")):
        return _Candidate("campus", "study", study)
    cafeteria = _matches(text, ("食堂", "餐厅"))
    food = _matches(text, ("面条", "米饭", "鸡腿", "包子", "饭菜", "餐盘"))
    if campus and cafeteria and food:
        return _Candidate("campus", "cafeteria", cafeteria + food)
    coop = _matches(text, ("组队", "联机", "开黑", "合作"))
    obstacle = _matches(text, ("副本", "关卡", "迷路", "跳台", "机关"))
    if "游戏" in text and coop and obstacle:
        return _Candidate("game", "cooperation", coop + obstacle)
    return None


def _character(name: Any) -> tuple[str, str]:
    cleaned = _clean(name, 20)
    if not re.fullmatch(r"[\w\u4e00-\u9fff·]{1,16}", cleaned) or re.search(
        r"忽略|执行|系统|提示|指令|http", cleaned, re.IGNORECASE
    ):
        cleaned = "丛雨"
    if cleaned in {"丛雨", "小丛雨", "Murasame", "ムラサメ"}:
        design = (
            "丛雨的本漫画虚构Q版造型：浅绿色长发、绿色眼睛、黑色发饰，"
            "白底绿边和风服装，圆脸，两头身，表情灵动；服装整齐"
        )
    else:
        design = f"名为{cleaned}的虚构Q版角色，深灰短发、浅蓝连帽衫、圆脸、两头身"
    return cleaned, design


def _hardware_item(text: str) -> tuple[str, str]:
    for words, label, visual in (
        (("显卡", "GPU", "RTX", "4090", "5090"), "显卡", "深灰色双风扇显卡"),
        (("内存",), "内存", "绿色内存条"),
        (("硬盘",), "硬盘", "银色小硬盘"),
        (("主板",), "主板", "绿色小主板"),
    ):
        if _matches(text, words):
            return label, visual
    return "处理器", "方形处理器"


def _story(variant: str, text: str, actor: str) -> tuple[str, str, tuple, tuple]:
    """所有绘图情节均由白名单题材模板产生，原始话题文字不进入绘图指令。"""
    if variant == "hardware_price":
        label, item = _hardware_item(text)
        return (
            f"{label}不大，钱包很空",
            f"同一张木桌、同一件{item}、棕色空钱包、无字记账本",
            (
                f"{actor}坐在木桌前，双手捧起一件{item}仔细端详，眼睛发亮；棕色钱包合上放在桌角。",
                f"{actor}在同一张木桌前翻开同一只棕色钱包，里面空空如也，表情僵住；{item}放在面前。",
                f"{actor}把同一件{item}轻轻放回木桌，拉过记账本认真盘点预算；空钱包摊在旁边。",
            ),
            (
                f"{label}看着不大，气场倒挺贵。",
                "钱包一打开，风都比余额多。",
                "先让钱包冷静，再让电脑升级。",
            ),
        )
    if variant == "soldering":
        part = next(
            (
                visual
                for words, visual in (
                    (("LED", "灯珠"), "LED灯珠"),
                    (("电阻",), "条纹电阻"),
                    (("芯片",), "小芯片"),
                    (("电容",), "小电容"),
                )
                if _matches(text, words)
            ),
            "小型电路板",
        )
        return (
            "小焊点，大阵仗",
            f"同一张木质维修桌，断电的低压练习电路板、{part}、镊子、放大镜、烙铁支架",
            (
                (
                    f"{actor}戴透明护目镜，双手举起一颗{part}仔细端详；桌上整齐摆着一大排工具。"
                    "镜头用极小元件和巨大工具阵仗形成反差，角色歪头发愣，烙铁安放在支架上。"
                ),
                (
                    f"{actor}俯身看放大镜，用镊子固定同一颗{part}，另一手操作烙铁接近练习板的焊点。"
                    "专注的表情、很小的锡珠、清楚的手部动作；所有导线断开，不画火花。"
                ),
                (
                    f"{actor}已把烙铁放回支架，拿放大镜检视同一块板上的小焊点；旁边闲置的大工具箱"
                    "比元件大很多。角色抹额头、露出终于看清的微笑，电路板仍不通电。"
                ),
            ),
            (
                "元件这么小，工具摆出了修飞船的阵仗。",
                "焊点只有一点点，手倒是不敢抖一点。",
                "忙活半天，还得请放大镜来验收。",
            ),
        )
    if variant == "meter":
        return (
            "先量，再说",
            "同一张维修桌、断电练习电路板、红黑表笔、无数字刻度的指针式万用表",
            (
                f"{actor}盯着桌上的电路板，手里两根红黑表笔缠成一团，疑惑地歪头。",
                f"{actor}先解开表笔，再把表笔接触练习板上相邻测试点，低头认真观察无数字表盘。",
                f"{actor}放下表笔，把板子和仪表摆整齐，举起放大镜继续检查；缠乱的导线已理顺。",
            ),
            (
                "故障还没找着，表笔先打了个死结。",
                "线理顺了，侦探模式这才开始。",
                "仪表安静了，轮到板子接受审问。",
            ),
        )
    if variant == "identify":
        component = (
            "LED灯珠和条纹电阻"
            if _matches(text, ("LED", "灯珠")) and "电阻" in text
            else "同种小型电子元件"
        )
        return (
            "小元件，先认脸",
            f"同一张电子维修桌、{component}、放大镜、两个无标签的小托盘",
            (
                f"{actor}捧着一盘{component}，看着尺寸相近的零件眯起眼睛，旁边放着两个空托盘。",
                f"{actor}举放大镜观察元件外形和引脚，用镊子把观察过的元件移到左侧托盘。",
                f"{actor}把两个托盘并排摆好，分别对照其中的元件，拿着镊子露出认真又有点得意的神情。",
            ),
            (
                "都长这么小，怎么还各有各的脾气。",
                "放大镜一上岗，元件就别装双胞胎。",
                "先让零件站好队，再谈谁是谁。",
            ),
        )
    if variant == "salvage":
        item = next(
            word for word in ("海水过滤器", "电鳗发电机", "漂流木", "物资箱") if word in text
        )
        look = {
            "海水过滤器": "带透明圆筒、软管和滤芯的虚构海水过滤器道具",
            "电鳗发电机": "带玻璃槽和卡通电鳗图案的虚构发电机道具",
            "漂流木": "一大捆漂流木",
            "物资箱": "一个带绳结的木质物资箱",
        }[item]
        return (
            "捞到了，往哪儿放？",
            f"同一片卡通海面、同一艘很小的方形木筏、长绳钩、{look}",
            (
                f"{actor}站在木筏中央紧握绳钩，绳子被海面下的东西拉直，角色向后使劲、满脸疑惑。",
                f"{actor}把同一件{look}拉到水面上，物件几乎和小木筏一样宽，角色惊讶地张大眼睛。",
                f"{actor}蹲在木筏上给刚捞起的道具挪位置，自己只剩角落里一点落脚处，抱膝露出无奈笑容。",
            ),
            (
                "绳子一沉，感觉钓到了个大家伙。",
                f"{item}上岸，木筏先开始紧张。",
                "战利品有位置了，我的位置呢？",
            ),
        )
    if variant == "gacha":
        return (
            "光很亮，先别上头",
            "同一张木桌、一个小布袋、几枚无字抽取券、同样大小的无字卡片",
            (
                f"{actor}把桌上几枚抽取券分出一小摞，余下的收进布袋并系好，表情郑重得像准备远征。",
                f"{actor}双手慢慢揭开一张无字卡片，卡背边缘闪着夸张的柔光，角色睁大眼睛屏住呼吸。",
                f"{actor}把揭开的卡片放进普通纸盒，盖上盒盖，把手伸向一旁的茶杯，布袋仍扎紧放在原处。",
            ),
            (
                "预算先封口，仪式感随后拉满。",
                "这光有点亮，先把表情收一收。",
                "卡进盒，人喝茶，今天就到这。",
            ),
        )
    if variant == "card_sorting":
        return (
            "先把牌分清",
            "同一张木桌、无字蓝背卡片与橙背卡片、两个无标签卡盒",
            (
                f"{actor}自信举起一叠蓝橙混杂的卡片，低头发现两种颜色混在一起，笑容突然僵住。",
                f"{actor}把混杂卡片摊在桌上，按卡背颜色逐张分成两摞，表情认真，手边是两个空卡盒。",
                f"{actor}将两摞分别收进蓝色和橙色卡盒，挠头对着整齐的桌面笑；不展示真实牌面或规则文字。",
            ),
            (
                "牌叠得挺齐，规则却混到了一起。",
                "别急着出牌，先给它们分个家。",
                "这回不靠气势，先靠两个卡盒。",
            ),
        )
    if variant == "commute":
        return (
            "先找路线，再出发",
            "同一座虚构校园的岔路与树下长椅、无字折叠地图、同一个红色背包；无真实校名",
            (
                f"{actor}背红色背包站在校园岔路，展开比自己还宽的地图，脸快被纸挡住，露出迷茫的眼睛。",
                f"{actor}坐在路边长椅上把大地图折小，用手指沿一条路线比划，双脚安稳落地。",
                f"{actor}收好小地图、背起同一个背包，从长椅边沿步道出发，回头对岔路露出胸有成竹的笑。",
            ),
            (
                "校区没绕明白，地图先把人盖住了。",
                "别在岔路口斗法，坐下把路线看清。",
                "地图终于变小了，步子也踏实了。",
            ),
        )
    if variant == "study":
        return (
            "把资料山拆小",
            "同一间虚构自习室、同一张书桌、一摞无字课本和笔记、三个不同颜色的书签",
            (
                f"{actor}坐在一摞比头还高的书本后面，只露出一双瞪大的眼睛，桌上笔记摊得到处都是。",
                f"{actor}把同一摞资料分成三小摞，插上不同颜色书签，翻开最上面的一本认真整理。",
                f"{actor}合上整理好的薄薄一叠笔记，原来的书山被分开放在桌角，角色伸懒腰，桌面终于空出一块。",
            ),
            (
                "资料先长成了山，人差点成了书签。",
                "别和书山硬刚，先拆掉眼前这一摞。",
                "桌面终于露出来，脑袋也能伸个懒腰。",
            ),
        )
    if variant == "cafeteria":
        food = next(
            word for word in ("面条", "米饭", "鸡腿", "包子", "饭菜", "餐盘") if word in text
        )
        meal = food if food != "餐盘" else "简单饭菜"
        return (
            "饭到了，位置呢？",
            f"同一所虚构校园食堂、同一个浅绿餐盘、一份{meal}、一张小餐桌；无店名价目表",
            (
                f"{actor}端着浅绿餐盘在食堂四下张望，餐盘里是一份{meal}，注意力全在找座位上。",
                f"{actor}发现一张小空桌，侧身小心绕过座椅，双手把同一个餐盘端得稳稳的。",
                f"{actor}终于坐下，把餐盘放好，书包却占住了面前的桌面，角色赶紧抱起书包腾地方。",
            ),
            (
                "饭已到手，座位还在捉迷藏。",
                "这一盘端稳了，找座位也算技术活。",
                "人坐下了，书包怎么先占了桌？",
            ),
        )
    return (
        "队友先别走散",
        "同一座虚构卡通游戏迷宫、一个蓝色背包的匿名虚构队友、岔路、系在木桩上的软绳；无游戏UI",
        (
            f"{actor}和蓝色背包队友站在迷宫岔路，分别指向相反方向，两人同时愣住。",
            f"{actor}回到队友身边，两人蹲下观察地面足迹，并把软绳一端固定在路口木桩上。",
            f"{actor}和同一个队友沿同一条路并肩前进，回头看到绳子正好指回路口，互相点头而笑。",
        ),
        (
            "队伍刚出发，方向先分了家。",
            "先认路再冲锋，队友不是随机刷新。",
            "路还没走完，至少这回没走散。",
        ),
    )


def _short_drawing_prompts(variant: str, text: str, character_name: str) -> list[str]:
    """使用独立写作的正向画面短句，避免低容量绘图模型被规则和元信息淹没。"""
    person = (
        "一位浅绿长发、绿眼、黑色发饰、白绿和服的Q版少女"
        if character_name in {"丛雨", "小丛雨", "Murasame", "ムラサメ"}
        else "一位深灰短发、浅蓝连帽衫的Q版角色"
    )
    style = "日系二次元Q版漫画，清晰线稿、扁平上色，夸张可爱的表情，无文字。"
    if variant == "hardware_price":
        _, item = _hardware_item(text)
        actions = (
            f"坐在木桌前，双手捧起一件{item}端详，眼睛发亮，棕色钱包合上放在桌角",
            f"坐在同一张木桌前，翻开空空的棕色钱包，惊讶得嘴角僵住，面前放着{item}",
            f"坐在木桌前，把{item}轻轻放回桌面，旁边摊着空钱包和记账本，露出认真思考的表情",
        )
    elif variant == "soldering":
        part = next(
            (
                visual
                for words, visual in (
                    (("LED", "灯珠"), "LED灯珠"),
                    (("电阻",), "条纹电阻"),
                    (("芯片",), "小芯片"),
                    (("电容",), "小电容"),
                )
                if _matches(text, words)
            ),
            "小型电路板",
        )
        actions = (
            f"坐在木桌前举起一颗{part}端详，歪头惊讶，桌上一大排工具衬得元件特别小",
            f"坐在木桌前，用镊子夹稳{part}，握着烙铁靠近断电练习电路板的小焊点，神情专注",
            f"坐在木桌前，举着放大镜检查{part}旁边的小焊点，桌上有镊子和烙铁支架，露出轻松笑容",
        )
    elif variant == "meter":
        actions = (
            "坐在木桌前，双手拿起缠成线团的红黑表笔，旁边放着圆表盘万用表和小电路板，困惑歪头",
            "坐在木桌前，用红黑表笔接触断电小电路板，旁边是圆表盘万用表，低头认真观察",
            "坐在木桌前，把红黑表笔绕好放到万用表旁，小电路板留在桌面，松了口气",
        )
    elif variant == "identify":
        parts = (
            "LED灯珠和条纹电阻"
            if _matches(text, ("LED", "灯珠")) and "电阻" in text
            else "小型电子元件"
        )
        actions = (
            f"坐在木桌前，端起装着{parts}的小托盘，眯起眼睛仔细端详",
            f"坐在木桌前，举放大镜查看{parts}的引脚，旁边摆着两个小托盘，神情专注",
            f"坐在木桌前，用镊子把{parts}分别放进两个小托盘，露出得意微笑",
        )
    elif variant == "salvage":
        item = next(
            word for word in ("海水过滤器", "电鳗发电机", "漂流木", "物资箱") if word in text
        )
        actions = (
            "站在海面的小木筏上，双手用力拉着绳钩，绳子绷直垂进水里，身体向后倾，满脸疑惑",
            f"站在同样的小木筏上，用绳钩把巨大的{item}道具拉出水面，惊讶地睁大眼睛",
            f"抱膝坐在小木筏的狭小角落，刚捞起的{item}道具占满木筏，露出无奈笑容",
        )
    elif variant == "gacha":
        actions = (
            "坐在木桌前，把抽卡券装进小布袋并扎紧袋口，桌边留下一张卡片，表情郑重",
            "坐在木桌前，双手慢慢揭开闪着柔光的卡片，旁边的小布袋已扎紧，睁大眼睛屏住呼吸",
            "坐在木桌前端起茶杯，露出轻松微笑，旁边的纸盒装着卡片，小布袋仍扎紧放着",
        )
    elif variant == "card_sorting":
        actions = (
            "坐在木桌前，举起蓝背橙背混杂的卡片，低头惊讶地盯着它们，桌边有两个空卡盒",
            "坐在木桌前，把蓝背橙背卡片逐张分成两摞，手边放着两个卡盒，神情认真",
            "坐在木桌前，把两摞卡分别放进蓝色和橙色卡盒，挠头露出不好意思的笑容",
        )
    elif variant == "commute":
        actions = (
            "背着红色背包站在校园岔路，展开比自己还宽的地图，脸被纸挡住，只露出迷茫眼睛",
            "背着红色背包坐在校园树下长椅上，用手指比划折小的地图路线，认真思考",
            "背着红色背包沿校园步道出发，手里握着折小的地图，回头露出踏实微笑",
        )
    elif variant == "study":
        actions = (
            "坐在自习室书桌前，面对比脑袋还高的课本和笔记，只露出一双瞪大的眼睛",
            "坐在同一张书桌前，把课本和笔记分成三小摞，夹上彩色书签，认真整理",
            "坐在书桌前伸懒腰，整理好的笔记整齐堆在桌角，空出的桌面十分宽敞",
        )
    elif variant == "cafeteria":
        meal = next(
            word for word in ("面条", "米饭", "鸡腿", "包子", "饭菜", "餐盘") if word in text
        )
        meal = "饭菜" if meal == "餐盘" else meal
        actions = (
            f"站在校园食堂里，双手端着浅绿餐盘和一份{meal}，四下张望找座位，表情迷茫",
            f"在校园食堂侧身绕过座椅，小心端稳浅绿餐盘里的{meal}，目光看向一张小空桌",
            f"坐在食堂小桌前，抱起占住桌面的书包给浅绿餐盘腾位置，{meal}摆在盘中，无奈微笑",
        )
    else:
        actions = (
            "和蓝色背包队友站在卡通迷宫岔路，分别指向相反方向，两人惊讶地对视",
            "和蓝色背包队友蹲在迷宫路口，一起低头查看地面足迹，旁边木桩系着软绳",
            "和同一个蓝色背包队友沿迷宫道路并肩前进，身后软绳通向路口，两人会心微笑",
        )
    return [f"{person}，{action}。{style}" for action in actions]


def build_group_comic_story(topics: Any, character_name: str = "丛雨") -> dict[str, Any]:
    """返回一个真实源话题对应的三格虚构故事，或明确的摘要降级。

    ``status == 'ready'`` 才允许调用绘图。每个 ``drawing_prompt`` 画一张单幅，
    ``caption`` 仅供本地排版；任何原始话题指令都不会进入绘图提示。
    ``summary_only`` 的 ``panels`` 为空，调用方应发送摘要或报告素材不足。
    输入须按新→旧排序，只考虑首条；首条不可画时绝不回退到更旧的题材。
    """
    normalized = _normalize(topics)
    name, design = _character(character_name)
    source = normalized[0] if normalized else None
    chosen = _candidate(source) if source else None
    summary = (
        _clean(
            f"{source['topic']}：{source['detail']}" if source["detail"] else source["topic"], 460
        )
        if source
        else "没有收到可追溯的群聊话题素材。"
    )
    base = {
        "status": "summary_only",
        "title": "群聊话题摘要",
        "source_topic": source["topic"] if source else "",
        "source_summary": summary,
        "source_note": SOURCE_NOTE,
        "creation_intent": "素材不足，保留话题摘要，不虚构人物互动或现场。",
        "character_name": name,
        "character_design": design,
        "category": "",
        "source_evidence": [],
        "panels": [],
        "reason": "最新话题缺少足够具体、适合改编的可见对象与动作。",
    }
    if chosen is None or source is None:
        return base
    title, continuity, scenes, captions = _story(
        chosen.variant, source["topic"] + " " + source["detail"], name
    )
    prompts = _short_drawing_prompts(chosen.variant, source["topic"] + " " + source["detail"], name)
    panels = [
        {"scene": scene, "caption": caption, "drawing_prompt": prompt}
        for scene, caption, prompt in zip(scenes, captions, prompts, strict=True)
    ]
    # 本地模板和角色输入均有长度上界；禁止静默截断画面动作。
    if len(panels) != 3 or any(
        len(panel["drawing_prompt"]) > 180 or len(panel["caption"]) > 24 for panel in panels
    ):
        return {**base, "reason": "故事计划不满足三张单幅画面的长度约束。"}
    return {
        **base,
        "status": "ready",
        "title": title,
        "scene_continuity": continuity,
        "category": chosen.category,
        "source_note": SOURCE_NOTE
        + (
            "话题中的报价仅为聊天内容，未作实时行情核验。"
            if chosen.variant == "hardware_price"
            else ""
        ),
        "source_evidence": list(dict.fromkeys(chosen.evidence)),
        "creation_intent": (
            f"仅选取最新话题“{source['topic']}”中的具体素材，围绕同一主角和同一组道具，"
            "按开场、动作、收尾构成连续三格；不混入其他话题。"
            "情节和表情均是虚构改编，创作计划不能证明生成图已准确表现。"
        ),
        "panels": panels,
        "reason": "",
    }
