"""Natural multiplayer invitations in QQ, with game-specific participation."""

from __future__ import annotations

import asyncio
import random
import re
from dataclasses import dataclass, field

from app.services import game_clients
from app.services.hearthstone.decks import CODE
from app.services.mahjong import tiles
from app.services.owner_policy import is_owner_event

NAMES = {"mj": "雀魂", "hs": "炉石", "mc": "Minecraft", "steam": "Steam"}
_NAMES = {
    "mj": r"雀魂|友人场|友人房|majsoul|mahjong\s*soul",
    "hs": r"炉石(?:传说)?|爐石|hearthstone",
    "mc": r"我的世界|minecraft|(?<![a-z0-9])mc(?![a-z0-9])|java\s*(?:版\s*)?(?:服务器|服)",
    "steam": r"steam",
}
_INVITE = re.compile(r"陪我|一起(?:来|玩|打|开|联机|下|搓|进|组|上|整|启动)|来(?:一|打|玩|我|开|联机|搓|进|组)|约[一场局人]|组队|联机|跟我(?:玩|打|进)|你也来|加(?:入|进来)|进(?:我|服|房|世界)|缺[一1]人|[三四二]缺一|开(?:个|一)?(?:房|黑|友人)|拉你|友谊赛|好友挑战|约战|谁(?:来|玩)|有人.{0,6}(?:一起|来玩|打|玩|搓)|有人吗")
_CAPABILITY = re.compile(r"(?:可不可以|能不能|你会|你能|你可以|支持|能玩吗|能联机吗).{0,14}(?:玩吗|一起|玩|联机|进服|打牌|操作|加入)|能玩吗|能一起吗")
_NEGATIVE = re.compile(r"(?:别|不要|不用|先不|不想|不打算|不需要).{0,8}(?:陪|玩|约|进|来|打|跟|联机|加入|组队)|^(?:听说|据说|他说|她说|有人说|[>“「])")
_LOBBY = re.compile(r"(?<![A-Za-z0-9./_-])(?:steam://joinlobby/|https://steamcommunity\.com/joinlobby/)([1-9]\d{0,9})/([1-9]\d{14,19})/([1-9]\d{16})(?=$|[\s，。！？、）)！?!])", re.IGNORECASE)
_YES = re.compile(r"^(?:好[呀啊的吧]?|可以[呀啊]?|对|行[呀啊]?|确认|进吧|来吧|加入吧)[！!。~～\s]*$")
_NO = re.compile(r"^(?:算了|不用了|取消|先不玩了|不了)[！!。\s]*$")
_FOLLOW = re.compile(r"(?:跟着|跟随|跟上|跟我走|来找我|跟我来)\s*(?:我[，,：:\s]*|玩家\s*)?(?:[，,：:\s]*(?:我的)?(?:游戏名|名字|ID|id)(?:是|叫)?[：:\s]*)?([a-zA-Z0-9_]{1,16})?", re.IGNORECASE)
_STOP = re.compile(r"别跟了|停止跟随|原地等|不用跟了|(?:停|歇|休息)(?:一下|一会儿?|会儿|会吧|下吧)|停下来|别(?:挖|采|砍)了|先别干了|^(?:先)?(?:停|停止|停下|休息)[了吧呀。！!\s]*$")
_LEAVE = re.compile(r"退出(?:服务器|世界|游戏)|退服|下线吧|离开(?:服务器|世界)")
_STATUS = re.compile(r"进(?:去|来|服|房)了(?:没|吗)|你(?:在不在|在哪|进了吗)|联机状态|进服状态|房间状态")
_MC_ACTIVITY = re.compile(r"在(?:做|干|忙|玩)(?:什么|啥|嘛)|刚才(?:都)?(?:做|干)了什么|在挖什么|现在在做啥|在休息吗|你在干嘛|在做什么")
_MC_BAG = re.compile(r"背包.{0,8}(?:有啥|有些什么|有什么|有哪些|里|状况)|(?:看[看下]?|查查|打开).{0,5}背包|(?:捡|采|挖|收集)(?:到|了)(?:些|了)?(?:多少|什么|哪些)")
_MC_AUTO = re.compile(r"(?:自己|自主|自由)(?:去|随便)?(?:玩|干|逛|找事|找点事|探索|行动|安排)|继续(?:玩|采集|干活|探索)|随便逛逛")
_MC_COLLECT = re.compile(r"(?:(?:采集|收集|采点|找点|挖|砍|采|捡).{0,7})(?:木头|木材|原木|树|石头|圆石|石料|煤炭?|资源|材料)")
_MC_MATERIALS = {"wood": ("木头", r"木头|木材|原木|砍树"), "stone": ("石料", r"石头|圆石|石料"), "coal": ("煤炭", r"煤")}
_MC_STATES = {"looking": "正在附近看看有什么可采", "wood": "正在找树采木头", "stone": "正在挖石料",
              "coal": "正在找煤矿", "crafting": "正在做工具或整理合成材料", "picking_up": "正在捡掉落的材料",
              "exploring": "正在附近走走", "rest": "正在原地歇一会儿", "eating": "正在吃东西"}
_ROOM = re.compile(r"(?:房间号?|房号|房码|桌号|房)\s*[:：#]?\s*(\d{5,6})(?!\d)")
_TAG = re.compile(r"(?<![\w#])([\w]{2,16}#[0-9]{4,10})(?![\w#])", re.UNICODE)
_REPLAY = re.compile(r"https://[^\s，。<>]+[?&]paipu=[^\s，。<>]+", re.IGNORECASE)
_PRACTICE = re.compile(r"何切|牌效(?:题|练习)|练(?:一下|一把|练)?牌效")
_HAND = re.compile(r"(?:[0-9]+[mpsz]\s*){2,}", re.IGNORECASE)
_SIGN = re.compile(r"^(?:我也来|算我一个|我报名|带我一个|加我一个|我也想玩)[啊呀！!。\s]*$")
_ROSTER = re.compile(r"谁(?:报[了名]|要来|报名了)|报名名单|有几个人(?:报名|来)|约局名单")
_DROP = re.compile(r"取消报名|我先鸽了|我不参加了")
_ENDPOINT = re.compile(r"(?:服务器地址|服务器是|服地址|地址(?:是)?|服务器)?\s*[:：]?\s*((?:\[[0-9a-fA-F:]+\]|localhost|[a-zA-Z0-9-]+(?:\.[a-zA-Z0-9-]+)+)(?::\d{1,5})?)\s*[。!！]?$", re.IGNORECASE)
_STEAM_ACCOUNT = re.compile(r"(?:绑定|设置|设为|设成|绑定为).{0,16}steam.{0,12}?([1-9]\d{16})(?!\d)|steam(?:专用)?(?:账号|帐号|账户|id)\s*(?:设为|设成|绑定为|是|设置为)[：:\s]*([1-9]\d{16})(?!\d)", re.IGNORECASE)
_LABEL = {"mj": "雀魂友人场", "hs": "炉石好友挑战", "mc": "Minecraft 联机", "steam": "Steam 联机"}


@dataclass
class GameTurn:
    game: str
    step: str
    expires: float
    value: object = None


@dataclass(frozen=True)
class Request:
    game: str
    verb: str
    value: object = None


def _lobby(text):
    match = _LOBBY.search(text)
    if not match:
        return None
    app, lobby, user = (int(n) for n in match.groups())
    if app >= 2**32 or lobby >= 2**64 or not game_clients.STEAM_BASE < user < game_clients.STEAM_BASE + 2**32:
        return None
    return str(app), str(lobby), str(user)


def intent(text, previous, now, tables=(), *, active_minecraft=False):
    if not text or len(text) > 4500:
        return None
    prev = previous if previous and previous.expires > now else None
    named = next((g for g, pattern in _NAMES.items() if re.search(pattern, text, re.IGNORECASE)), "")
    if prev and prev.step == "pick_table" and named in tables and re.fullmatch(
            "(?:" + _NAMES[named] + r")[吧啊呀。！!\s]*", text, re.IGNORECASE):
        return Request(named, prev.value)
    sign_text = re.sub(_NAMES[named], "", text, flags=re.IGNORECASE).strip(" ，,：:") if named else text
    if _SIGN.fullmatch(sign_text) or _ROSTER.search(text) or _DROP.search(text):
        which = named or (prev.game if prev and prev.game in tables else "") or (tables[0] if len(tables) == 1 else "")
        if which or tables:
            return Request(which, "roster" if _ROSTER.search(text) else "drop" if _DROP.search(text) else "signup")
    if prev and _NO.fullmatch(text):
        return Request(prev.game, "cancel")
    if _NEGATIVE.search(text) and not _STOP.search(text) and not re.search(r"(?:不用|不要|别)跟.{0,8}(?:自己|自主)玩", text):
        return None
    if not named and re.search(r"(?:你|能陪我|你们支持).{0,12}(?:玩什么游戏|玩哪些游戏|联机哪些游戏|支持哪些游戏|会玩哪.{0,2}游戏)", text):
        return Request("", "capabilities")
    game = named or (prev.game if prev else "")
    if not game and (active_minecraft or "进服" in text and (_MC_AUTO.search(text) or _MC_COLLECT.search(text))):
        game = "mc"
    if game == "mc":
        setting_server = prev and prev.step == "server" or named and re.search(r"设为|服地址|服务器地址", text)
        if setting_server and (address := _ENDPOINT.search(text)) and (endpoint := game_clients.server_address(address[1])):
            return Request(game, "server", endpoint)
        if _LEAVE.search(text):
            return Request(game, "leave")
        if _MC_BAG.search(text):
            return Request(game, "inventory")
        if _STATUS.search(text) or _MC_ACTIVITY.search(text):
            return Request(game, "status")
        if _STOP.search(text) or _STOP.fullmatch(sign_text):
            return Request(game, "stop")
        if _MC_AUTO.search(text) or _MC_COLLECT.search(text):
            if re.search(r"(?:别|不要|不用|不想).{0,4}(?:自己玩|采集|收集|挖|砍)", text):
                return None
            if re.search(r"你会|能不能|会不会|支持|怎么采|[吗么]？?$", text):
                return Request(game, "offer")
            material = next((key for key, (_, pattern) in _MC_MATERIALS.items() if re.search(pattern, text)), "")
            return Request(game, "work", material)
        if follow := _FOLLOW.search(text):
            return Request(game, "follow", follow[1] or "")
        if prev and prev.step == "name" and re.fullmatch(r"[A-Za-z0-9_]{1,16}", text):
            return Request(game, "follow", text)
    if game == "mj":
        if prev and prev.step == "offer" and re.fullmatch(r"(?:约|玩)?友人[场房][吧啊。！!]*", text):
            return Request(game, "invite")
        if prev and (replay := _REPLAY.search(text)):
            return Request(game, "replay", replay[0])
        if _PRACTICE.search(text) or prev and prev.step == "practice" and _HAND.fullmatch(text):
            hand = _HAND.search(text)
            return Request(game, "practice", hand[0] if hand else "")
        if prev and prev.step == "practice" and (cut := re.fullmatch(r"(?:我(?:会|想|选)?)?(?:切|打|选|丢)?\s*([0-9][mpsz])[。！!]?", text, re.IGNORECASE)):
            return Request(game, "discard", cut[1].lower())
        if room := _ROOM.search(text):
            return Request(game, "room", room[1])
        if prev and prev.step == "room" and re.fullmatch(r"\d{5,6}", text):
            return Request(game, "room", text)
    if game == "hs" and (prev or named and _INVITE.search(text)):
        if prev and re.fullmatch(r"(?:打|想玩|想打|准备打)?(?:标准|狂野)(?:模式)?(?:的)?(?:牧师|20牧|死亡骑士|法师|战士|术士|猎人|骑士|萨满|德鲁伊|盗贼|恶魔猎手)?[吧啊。！!]*", text):
            return Request(game, "deck", "推荐" + text + "卡组")
        if CODE.search(text) or re.search(r"竞技场.{0,7}(?:选牌|选卡|三选一)|(?:卡组|套牌|标准|狂野|20牧).{0,12}(?:推荐|分析|强度)|推荐.{0,10}(?:卡组|套牌|20牧)", text):
            return Request(game, "deck", text)
        if tag := _TAG.search(re.sub(r"(?:战网(?:名|昵称)?|我的战网名是|我的战网昵称是)[：:\s]*", " ", text)):
            return Request(game, "tag", tag[1])
    if game == "steam" or not named:
        if named == "steam" and (binding := _STEAM_ACCOUNT.search(text)):
            account = binding[1] or binding[2]
            if game_clients.STEAM_BASE < int(account) < game_clients.STEAM_BASE + 2**32:
                return Request("steam", "bind", account)
        if lobby := _lobby(text):
            return Request("steam", "lobby", lobby)
        if game == "steam" and _STATUS.search(text):
            return Request(game, "status")
    if prev and _YES.fullmatch(text) and prev.step in {"offer", "confirm"}:
        return Request(prev.game, "submit" if prev.step == "confirm" else "invite", prev.value)
    if prev and prev.step == "choose" and named and re.fullmatch(_NAMES[named] + r"[吧啊呀。！!\s]*", text, re.IGNORECASE):
        return Request(named, "invite")
    if named and (_INVITE.search(text) or _CAPABILITY.search(text)):
        if re.search(r"教程|怎么联机|联机失败|联机报错|怎么加入|联机原理", text):
            return None
        return Request(named, "offer" if _CAPABILITY.search(text) or re.search(r"有人|谁来|谁玩|缺一|缺1", text) else "invite")
    if not named and re.search(r"(?:陪我|一起|我们|咱们).{0,5}(?:玩(?:个|什么)?游戏|开黑|玩联机游戏|玩多人游戏)", text):
        return Request("", "choose")
    return None


@dataclass
class Table:
    expires: float
    host: str
    info: str = ""
    members: dict = field(default_factory=dict)


def _scope(event):
    raw = event.message_obj.raw_message
    group = str(raw.get("group_id") or "")
    user = str(raw["user_id"])
    chat = (str(getattr(event, "get_platform_id", lambda: "")()), str(raw["self_id"]), group or "private:" + user)
    sender = raw.get("sender") or {}
    return chat, user, str(sender.get("card") or sender.get("nickname") or "群友")[:32]


def _mc_item(name):
    basic = {"cobblestone": "圆石", "stone": "石头", "cobbled_deepslate": "深板岩圆石", "coal": "煤炭",
             "stick": "木棍", "crafting_table": "工作台", "wooden_pickaxe": "木镐", "stone_pickaxe": "石镐",
             "iron_pickaxe": "铁镐", "apple": "苹果", "bread": "面包"}
    woods = {"oak": "橡", "dark_oak": "深色橡", "spruce": "云杉", "birch": "白桦", "jungle": "丛林",
             "acacia": "金合欢", "mangrove": "红树", "cherry": "樱", "pale_oak": "苍白橡"}
    prefix, _, suffix = name.rpartition("_")
    if prefix in woods and suffix in {"log", "planks"}:
        return woods[prefix] + ("木原木" if suffix == "log" else "木板")
    return basic.get(name, name)


def _hand(notation):
    if notation:
        hand = tuple(tiles.tile(n + suit.lower()) for nums, suit in re.findall(r"([0-9]+)([mpsz])", notation, re.IGNORECASE) for n in nums)
        if len(hand) != 14:
            raise ValueError("需要一副闭门的 14 张手牌")
        tiles.counts(hand)
        return hand
    wall = [tiles.name(k) for k in range(34)] * 4
    return tuple(sorted(random.SystemRandom().sample(wall, 14), key=tiles.index))


def _discard(hand, chosen):
    if chosen not in hand:
        return f"这副牌里没有 {chosen}，你想切哪一张？"
    visible = tiles.counts(hand)
    branches = {}
    for cut in dict.fromkeys(hand):
        rest = list(hand)
        rest.remove(cut)
        outs = tiles.improvements(tuple(rest), 0, visible)
        branches[cut] = tiles.shanten(rest), sum(n for _, n in outs)
    best = min(branches, key=lambda x: (branches[x][0], -branches[x][1], x.startswith("0")))
    def line(cut):
        count, outs = branches[cut]
        return f"切 {cut} 后{'听牌' if count == 0 else str(count) + ' 向听'}，有 {outs} 枚未见的有效牌"
    if branches[chosen] == branches[best]:
        tied = any(k != chosen and v == branches[best] for k, v in branches.items())
        return line(chosen) + ("。只看这副手牌的牌效，你的选择并列最优。" if tied else "。只看这副手牌的牌效，我也会这样切。")
    return line(chosen) + "。我会" + line(best) + "。这里按自己手里的牌计算，没扣牌河。"


class GameCompanion:
    def __init__(self):
        self.mc = game_clients.MinecraftPlayer()
        self.tables = {}
        self.steam_lock = asyncio.Lock()
        self.last_steam = None

    def active_tables(self, event, now):
        chat, _, _ = _scope(event)
        for key in list(self.tables):
            if self.tables[key].expires <= now:
                del self.tables[key]
        return tuple(game for room, game in self.tables if room == chat)

    def playing_minecraft(self, event):
        chat, user, _ = _scope(event)
        return self.mc.scope == (*chat, user)

    async def answer(self, plugin, event, state, request, now):
        game, verb, value = request.game, request.verb, request.value
        chat, user, nickname = _scope(event)
        prev = state.game if state.game and state.game.expires > now and state.game.game == game else None
        def remember(step, data=None):
            state.game = GameTurn(game, step, now + (1800 if step == "playing" else 600), data)
        if verb == "cancel":
            state.game = None
            return "好，这次先不约了"
        if verb == "capabilities":
            return ("Minecraft Java 我可以独立进服，自己采材料、做工具和休息。雀魂可以约友人场、练何切、看牌谱。"
                    "炉石可以约好友挑战、挑卡组和选竞技场卡。其他支持 Steam 大厅的游戏，也可以发邀请给我。")
        if verb == "choose":
            remember("choose")
            return "想玩哪个？Minecraft、雀魂、炉石，还是发个 Steam 大厅邀请给我？"
        if verb in {"signup", "drop", "roster"}:
            if not game:
                remember("pick_table", verb)
                choices = "、".join(NAMES[g] for g in self.active_tables(event, now))
                return f"这里正在约{choices}，你说的是哪一场？"
            table = self.tables.get((chat, game))
            if not table:
                return "这个聊天里还没约这款游戏"
            if verb == "drop":
                table.members.pop(user, None)
                if not table.members:
                    self.tables.pop((chat, game), None)
                    return f"这次{_LABEL[game]}已经没有人报名，先收起来了"
                if table.host == user:
                    table.host = next(iter(table.members))
            elif verb == "signup":
                table.members[user] = nickname
            return f"{_LABEL[game]}，目前 {len(table.members)} 人报名" + (
                "，" + "、".join(table.members.values()) if table.members else "") + (f"。{table.info}" if table.info else "")
        if verb == "offer":
            remember("offer")
            return {"mj": "想约雀魂友人场，还是在这里和我练一道何切？",
                    "hs": "想约一局炉石好友挑战，还是先让我看看你准备带的卡组？",
                    "mc": "我可以用独立玩家进 Java 服，自己采材料、做工具，累了就歇会儿。要我现在进服吗？",
                    "steam": "要叫我一起进 Steam 大厅吗？可以发好友邀请链接给我。"}[game]
        if game == "mc":
            return await self._minecraft(event, chat + (user,), verb, value, prev, remember)
        if game == "steam":
            if verb == "bind":
                if event.get_group_id() or not is_owner_event(event):
                    return "大肥鱼的 Steam 账号可以由主人在私聊里设置"
                game_clients.set_steam_account(value)
                return "大肥鱼的 Steam 账号记好了。这个账号登录本机客户端后，就可以接大厅邀请。"
            return await self._steam(chat + (user,), verb, value, prev, remember, now)
        if verb in {"room", "tag", "invite"}:
            table = self.tables.get((chat, game))
            if table is None:
                table = self.tables[chat, game] = Table(now + 3600, user)
            if value and table.info and table.host != user and str(value) not in table.info:
                return "这里已经有人开了一场，现有约局的信息由发起人修改"
            table.members[user] = nickname
            table.expires = now + 3600
            if game == "mj":
                remember("room")
                if verb == "room":
                    table.info = "房号 " + value
                return ((table.info + "。") if table.info else "") + (
                    "可以在这条群聊说「我也来」报名。友人场缺一位的话，房主可以加游戏自带的电脑。要和我玩，我们可以在 QQ 练何切。"
                    if event.get_group_id() else "在友人场用房号约朋友，缺位可以加电脑；你也可以和我在这里练何切。")
            remember("deck")
            if verb == "tag":
                table.info = "战网昵称 " + value
            return ((table.info + "。") if table.info else "") + (
                "真人对局在战网好友列表里发起。你可以在群里约人，我来陪你备战。准备打标准还是狂野？把卡组代码贴给我就能一起看。"
                if event.get_group_id() else "好友挑战要在战网里发起。我可以陪你备战，你想看标准还是狂野的卡组？")
        if verb == "practice":
            try:
                hand = _hand(value)
            except ValueError:
                return "可以写成 123457m345p678s11z 这样的 14 张闭门手牌，同一种最多四枚。"
            if tiles.shanten(hand) == -1:
                remember("finished")
                return ("这 14 张已经组成和牌牌形了，能不能宣和还要看役和场况。想再练一手的话，"
                        "说「练何切」就好。")
            remember("practice", hand)
            return "我们练一手四麻何切。手牌是 " + " ".join(hand) + "。你会切哪张？比如「切 " + hand[0] + "」"
        if verb == "discard":
            return await asyncio.to_thread(_discard, prev.value, value)
        if verb == "replay":
            from app.astrbot_mahjong import deliver_native_review
            from app.services.mahjong.records import RecordError, share_link
            try:
                share_link(value)
            except RecordError as exc:
                return str(exc)
            await deliver_native_review(plugin.runtime, event, "链接=" + value)
            return None  # The review handler owns delivery, including progress and the finished report.
        if verb == "deck":
            return await self._hearthstone(plugin, event, value)
        return "想约哪款游戏？"

    async def _minecraft(self, event, scope, verb, value, prev, remember):
        if verb == "server":
            if not is_owner_event(event):
                return "服务器是大家共用的，要请主人在聊天里设置一次地址。设置好后你就能叫我进服。"
            status = await self.mc.command(scope, "status")
            if status["status"] in {"in_world", "respawning", "busy"}:
                return "我还在一个世界里，等这次联机结束后才能换服务器"
            game_clients.set_server(value)
            verb, value = prev.value if prev and prev.step == "server" and prev.value else ("invite", "")
        if verb == "follow" and not value:
            remember("name")
            return "你的 Java 游戏名叫什么？我进服后按这个名字找你。"
        action = "join" if verb in {"invite", "work"} else verb
        result = await self.mc.command(scope, action, value or "")
        if verb == "work" and result["status"] == "in_world":
            result = await self.mc.command(scope, "work", value or "")
        status = result["status"]
        if status == "no_server":
            remember("server", (verb, value))
        else:
            remember("playing")
        if status == "in_world":
            if verb == "inventory":
                items = result.get("inventory", {})
                if not items:
                    return "背包现在是空的"
                rows = [f"{_mc_item(name)} ×{count}" for name, count in list(items.items())[:10]]
                return "背包里有" + "、".join(rows) + (f"，一共 {len(items)} 种" if len(items) > len(rows) else "")
            if verb == "stop":
                return "好，我留在原地歇一会儿，歇好了自己接着玩"
            if verb == "work" and value in _MC_MATERIALS:
                return "我去找" + _MC_MATERIALS[value][0] + "，需要的工具我自己准备"
            follow = result.get("following")
            activity = "正在跟着 " + follow if follow else _MC_STATES.get(result.get("activity"), "正在附近看看")
            msg = ("我已经进到 Minecraft 世界了，" if verb == "invite" else "我") + activity
            last = result.get("last") or {}
            if verb == "status" and last.get("kind") == "collected" and last.get("name") in _MC_MATERIALS:
                msg += f"，上次捡到了 {last['count']} 份{_MC_MATERIALS[last['name']][0]}"
            elif verb == "status" and last.get("kind") == "crafted":
                msg += "，刚做了" + _mc_item(last["name"])
            return msg
        return {"no_server": "我这边还没有连接你的 Java 世界。要进哪台服务器？需要它的地址和端口。",
                "login_required": "要先给我的 Minecraft 玩家登录微软账号。主人可以在机器人这台电脑打开账号管理页，选 Minecraft 登录。",
                "login_in_progress": "我的 Minecraft 账号正在登录，完成后就能再叫我进服。",
                "offline": "我现在没在 Minecraft 世界里",
                "respawning": "我还在服务器里，正在等重生",
                "left": "我已经退出这个 Minecraft 世界了",
                "busy": "我现在正和另一位玩家联机，等那边退出后可以来你这边。",
                "player_unseen": "我在世界里，附近还看不到这个游戏名。你在我附近吗？",
                "refused": "服务器没让我进世界。可以核对版本、白名单和服务器要求的模组。",
                "missing_player": "我这边的 Minecraft 玩家组件还没装好。",
                "bad_config": "我这边的 Minecraft 服务器设置还不完整。"}.get(
                    status, "我这次没连进 Minecraft 世界。服务器开着吗？")

    async def _steam(self, scope, verb, value, previous, remember, now):
        if verb == "invite":
            remember("link")
            return "把 Steam 好友邀请里复制的大厅链接发我，我可以让本机的专用 Steam 账号接这个邀请。"
        if verb == "lobby":
            remember("confirm", value)
            return f"要我用大肥鱼的 Steam 账号加入这个大厅吗？游戏编号 {value[0]}。"
        if verb == "status":
            if not previous or previous.step != "playing":
                return "这次聊天里还没有已提交的 Steam 大厅邀请"
            status = await game_clients.steam_status(*previous.value[:2])
            return {"in_lobby": "Steam 显示我已经在你邀请的那个大厅里了",
                    "in_game": "Steam 显示游戏已启动，大厅席位还没确认"}.get(status, "邀请已经交给 Steam，还没有查到我在这个大厅的记录")
        async with self.steam_lock:
            if self.last_steam and now < self.last_steam[0] and self.last_steam[1:] != (scope, value):
                return "我刚接了另一个大厅的邀请，等它处理完再试吧"
            if self.last_steam and now < self.last_steam[0] and self.last_steam[1:] == (scope, value):
                status = "submitted"
            else:
                status = await game_clients.steam_join(*value)
            if status in {"submitted", "unconfirmed"}:
                self.last_steam = now + 120, scope, value
                remember("playing", value)
                return ("已经把邀请交给 Steam，等游戏确认进房。你可以问我「进房了吗？」" if status == "submitted"
                        else "Steam 的接收结果还没确认，暂时看不到大厅席位")
        remember("confirm", value)
        return {"no_account": "这台电脑还没配置大肥鱼的 Steam 账号，邀请还在，可以配置好后再试。",
                "wrong_account": "当前 Steam 登录的账号和大肥鱼的专用账号不同，切好账号再叫我。",
                "no_client": "我没找到已登录的 Steam 客户端，打开后可以再试。"}.get(status, "Steam 这次没有接下邀请")

    @staticmethod
    async def _hearthstone(plugin, event, text):
        from app import astrbot_arena, astrbot_hearthstone
        from app.astrbot_chat_actions import _model
        from app.services.chat_punctuation import natural_chat_text

        arena = astrbot_arena.intent(text, recent=astrbot_arena.has_draft(plugin, event))
        if arena:
            result = await astrbot_arena.query(plugin, event, **arena)
            rule = astrbot_arena._POLICY
        else:
            args = astrbot_hearthstone.intent("炉石 " + text, recent=True)
            if not args:
                return "把你想带的卡组代码、想玩的职业或竞技场的三张备选发给我吧"
            result = await astrbot_hearthstone.query(plugin, event, **args)
            rule = astrbot_hearthstone._POLICY
        if result.get("status") in {"unavailable", "invalid_or_unavailable", "missing_deck", "missing_card"}:
            return str(result.get("message") or "这次没查到这套卡组的资料")
        return natural_chat_text(await _model(plugin, event, "你是大肥鱼，和发起人一起准备炉石对局。" + rule,
            {"玩家的问题": text, "查到的资料": result}))
