"""A scoped building blueprint and a restartable material order for the Minecraft player."""
from __future__ import annotations

import asyncio
import json
import re
from uuid import uuid4

from app.services import game_clients
from app.services.activity_timer import ActivityTimer
from app.services.social_plans import scheduled_allowed

BLUEPRINTS = {
    "河岸小屋": ("5×5 圆石地基；四角各立三根木柱；木板围墙并留门窗，搭两坡屋顶。",
                {"stone":25, "wood":32}),
    "水族馆": ("7×5 石质底座。长边的观景窗宽7格、高3格，其余墙体用石料。用两桶水在内侧建水源。",
              {"stone":74,"glass":21,"water_bucket":2}),
    "营地灯塔": ("3×3 石基，中央立六根原木。在塔顶用木棍和煤炭做四支火把，照亮四面。",
               {"stone":9,"wood":7,"coal":1}),
}
NAMES = {"wood":"原木","stone":"石料","glass":"玻璃","water_bucket":"水桶","coal":"煤炭"}
_CREATE = re.compile(r"^(?:(?:给我|帮我|来个|开个|创建)?\s*(?:MC|我的世界|Minecraft)\s*(?:建筑)?(?:工程|图纸|蓝图)[：:，, ]*|(?:帮我)?给)(河岸小屋|小木屋|水族馆|营地灯塔)(?:出(?:张|个)(?:MC)?图纸)?$",re.IGNORECASE)
_CHEST = re.compile(r"^(?:MC|Minecraft|我的世界|工程)?\s*(?:工程)?(?:材料箱|备料箱|工地箱子)(?:在|设为|坐标)?[：: (（]*(-?\d{1,8})[,，\s]+(-?\d{1,4})[,，\s]+(-?\d{1,8})[)）]?$",re.IGNORECASE)
_START = {"开始工程备料","开始备料","继续工程备料","继续备料","把工程材料存进箱子"}
_STATUS = {"工程进度","查看工程图纸","工程材料清单","查询工程进度","看看材料箱进度"}
_STOP = {"暂停工程备料","取消工程备料"}
_STATES = {"gathering":"正在采集或存放材料","checking":"正在核对指定箱子","ready":"这次核对时材料已齐",
           "needs_supplies":"在等材料，可把玻璃、水桶或缺的材料放进指定箱子",
           "chest_unavailable":"箱子暂时打不开或没有足够空间","missing_chest":"指定位置没有箱子或木桶",
           "too_far":"材料箱距离玩家超过96格，先让玩家靠近再继续","wrong_dimension":"玩家和材料箱处在不同维度"}


def server():
    config = game_clients.settings().get("minecraft") or {}
    return json.dumps([str(config.get("host") or "").casefold(),int(config.get("port") or 25565)])


def scope_for(key):
    platform, bot, group, user = key
    return (platform, bot, group or "private:"+user, user)


def describe(row):
    description, needs = BLUEPRINTS[row["design"]]
    counts = json.loads(row["counts"]) if row["counts"] else None
    materials = "、".join(f"{NAMES[k]} {counts.get(k,0)}/{v}" if counts is not None else f"{NAMES[k]} ×{v}" for k,v in needs.items())
    lines = [row["design"]+" · "+description, "箱内/所需 · "+materials if counts is not None else "所需材料 · "+materials]
    if row["chest"]: lines.append("材料箱 " + " ".join(map(str,json.loads(row["chest"]))))
    if row["state"] in _STATES: lines.append(_STATES[row["state"]])
    if row["active"] and not counts: lines.append("还在等玩家核对箱内的实际存量。")
    if counts is not None: lines.append("数量取自上次开箱核对。")
    return "\n".join(lines)


class McBuilder(ActivityTimer):
    slug = "mc_builder"
    title = "Minecraft 工程备料"
    schema = """CREATE TABLE IF NOT EXISTS projects(account TEXT PRIMARY KEY, design TEXT, id TEXT,
              origin TEXT, server TEXT, chest TEXT, dimension TEXT, active INTEGER DEFAULT 0,
              counts TEXT, state TEXT DEFAULT 'planned');"""

    def __init__(self):
        super().__init__()
        self.operations = asyncio.Lock()

    def interested(self,key,text,addressed):
        word = text.strip(" 。！？!?")
        return bool(_CREATE.fullmatch(word) or _CHEST.fullmatch(word) or word in _START | _STATUS | _STOP)

    async def handle(self,host,event,key,text):
        async with self.operations:
            return await self.respond(host,event,key,text)

    async def respond(self,host,event,key,text):
        text = text.strip(" 。！？!?")
        store, account, selected = self.store(host), json.dumps(key), server()
        new, chest = _CREATE.fullmatch(text), _CHEST.fullmatch(text)
        def change(db):
            row = db.execute("SELECT * FROM projects WHERE account=?",(account,)).fetchone()
            if new:
                if row and row["active"]: return "当前工程还在备料，先说「暂停工程备料」再换图纸。", None
                design = "河岸小屋" if new[1] == "小木屋" else new[1]
                db.execute("INSERT OR REPLACE INTO projects(account,design,id,origin,server) VALUES (?,?,?,?,?)",
                           (account,design,uuid4().hex,event.unified_msg_origin,selected))
            elif row is None:
                return "先说「MC工程 水族馆」「MC工程 河岸小屋」或「MC工程 营地灯塔」选一份图纸。", None
            elif text in _STOP:
                db.execute("UPDATE projects SET active=0,state='paused' WHERE account=?",(account,))
                return "工程备料已暂停，箱子的位置和记录已保留。", ("end_build",row["id"])
            elif chest:
                coords = [int(x) for x in chest.groups()]
                if max(abs(coords[0]),abs(coords[2]))>30_000_000 or not -2048<=coords[1]<=2048:
                    return "请按 X Y Z 填写有效的方块坐标。", None
                if row["active"]: return "先暂停工程备料，再更换箱子。", None
                db.execute("UPDATE projects SET chest=?,server=?,dimension=NULL,counts=NULL,state='planned',id=? WHERE account=?",
                           (json.dumps(coords),selected,uuid4().hex,account))
            elif text in _START:
                if not row["chest"]: return "告诉我具体的材料箱，例如「工程材料箱 12 64 -8」。", None
                if json.loads(selected)[0] == "": return "先在账号页填写你的 Java 服务器，再设置这个世界的材料箱。", None
                if row["server"] != selected: return "服务器已经换了，先为当前世界重新指定材料箱。", None
                db.execute("UPDATE projects SET active=1 WHERE account=?",(account,))
            latest = db.execute("SELECT * FROM projects WHERE account=?",(account,)).fetchone()
            hint = "\n下一步可以指定「工程材料箱 X Y Z」，再说「开始工程备料」。" if new else ""
            return describe(latest) + hint, ("sync", dict(latest)) if latest["active"] else None

        result = await asyncio.to_thread(store.turn,event,change,self.clock())
        if not result: return True
        response, action = result
        if action:
            if action[0] == "end_build":
                await host._get_companion_turns().games.mc.command(scope_for(key),*action)
            else:
                response = await self.sync(host,key,action[1],connect=text in _START)
        if action and action[0] == "sync": self.start(host)
        await event.send(event.plain_result(response))
        return True

    async def sync(self,host,key,row,*,connect=False):
        if row["server"] != server(): return describe(row)+"\n原服务器不同，备料正在等待。"
        player = host._get_companion_turns().games.mc
        session = scope_for(key)
        status = await player.command(session,"status")
        if connect and status.get("status") == "offline":
            status = await player.command(session,"join")
        if status.get("status") != "in_world":
            reason = {"busy":"玩家正在另一位群友的世界。","login_required":"需要先在账号页给 Minecraft 玩家登录微软账号。",
                      "missing_player":"请先安装 Minecraft 玩家的 Node 依赖。","refused":"服务器拒绝了这次加入。"} .get(
                          status.get("status"),"等玩家进服后会继续；可以说「进我的 Minecraft 服务器」。")
            return describe(row)+"\n"+reason
        current = status.get("project") or {}
        if current.get("id") != row["id"]:
            if current: return describe(row)+"\n玩家正在处理另一项工程。"
            target = {"id":row["id"],"needs":BLUEPRINTS[row["design"]][1],"chest":json.loads(row["chest"]),
                      "dimension":row["dimension"]}
            status = await player.command(session,"build_materials",target)
            current = status.get("project") or {}
        if current.get("id") != row["id"]: return describe(row)+"\n还没有取得游戏端的材料箱回报。"
        observed = current.get("counts")
        counts = json.dumps(observed) if isinstance(observed,dict) else row["counts"]
        state = current.get("state") or "checking"
        ready = state == "ready" and isinstance(observed,dict) and all(
            observed.get(name,0) >= amount for name,amount in BLUEPRINTS[row["design"]][1].items())
        def save(db):
            db.execute("UPDATE projects SET counts=?,state=?,dimension=?,active=? WHERE account=? AND id=? AND active=1",
                       (counts,state,current.get("dimension"),int(not ready),row["account"],row["id"]))
            return db.execute("SELECT * FROM projects WHERE account=?",(row["account"],)).fetchone()
        latest = await asyncio.to_thread(self.store(host).transaction,save)
        if ready: await player.command(session,"end_build",row["id"])
        return describe(latest) if latest else "工程已经结束。"

    async def check_due(self,host):
        async with self.operations:
            rows = await asyncio.to_thread(self.store(host).transaction,lambda db: db.execute(
                "SELECT * FROM projects WHERE active=1").fetchall())
            for row in rows:
                key = tuple(json.loads(row["account"]))
                if await scheduled_allowed(host,key,row["origin"],self.slug):
                    await self.sync(host,key,row)
