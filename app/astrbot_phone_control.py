"""Owner-directed Android actions on the Termux host that is running the bot."""
from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
from datetime import datetime
from pathlib import Path

from app.services.owner_policy import is_owner_event
from app.services.social_plans import CHINA, PlanStore

_ACTIONS = {
    "battery": ("termux-battery-status",),
    "vibrate": ("termux-vibrate",),
    "light_on": ("termux-torch","on"),
    "light_off": ("termux-torch","off"),
}
_PREFIX = r"(?:(?:请|能不能|可以)?(?:帮我)?(?:把|让)?)?"
_PATTERNS = {
    "battery": re.compile(_PREFIX+r"(?:查(?:一下|下)?|看看)?(?:我的|这台)?手机(?:还剩多少电|剩多少电|电量(?:是多少|多少|怎么样)?|电池状态)[吗呢吧]?"),
    "vibrate": re.compile(_PREFIX+r"(?:我的|这台)?手机(?:震动|振动)(?:一下|一次)?[吧吗]?"),
    "light_on": re.compile(_PREFIX+r"(?:打开|开启|点亮)(?:我的|这台)?手机(?:的)?手电筒[吧吗]?"),
    "light_off": re.compile(_PREFIX+r"(?:关闭|关掉|熄灭)(?:我的|这台)?手机(?:的)?手电筒[吧吗]?"),
}


def requested(text):
    phrase = text.strip(" 。！？!?")
    return next((action for action,pattern in _PATTERNS.items() if pattern.fullmatch(phrase)),None)


def termux_action(action):
    directory = Path(os.environ.get("QQCHAT_TERMUX_BIN",""))
    if not directory.is_absolute() or not (directory/_ACTIONS[action][0]).is_file():
        return "当前机器人未连接手机本机的 Termux:API。"
    path = str(directory/_ACTIONS[action][0])
    env = dict(os.environ, PREFIX=str(directory.parent), PATH=str(directory)+":/system/bin:/usr/bin:/bin")
    try:
        result = subprocess.run([path,*_ACTIONS[action][1:]],env=env,stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,timeout=8,check=False)
    except (OSError,subprocess.TimeoutExpired):
        return "手机没有在八秒内完成请求，请检查 Termux:API 的安装和 Android 授权。"
    if result.returncode: return "手机未接受动作，请检查 Termux:API 的授权。"
    try:
        info = json.loads(result.stdout) if result.stdout.strip() else {}
    except (ValueError, UnicodeError):
        return "这次没有读到手机可用的回报。"
    if not isinstance(info,dict) or any(info.get(k) for k in ("error","API_ERROR","errmsg")):
        return "Termux:API 返回了错误，手机上的动作需要重新核实。"
    if action=="battery":
        pct = info.get("percentage")
        if type(pct) is not int or not 0 <= pct <= 100: return "这次没有读到手机实际电量。"
        state = {"CHARGING":"，正在充电","FULL":"，已充满","DISCHARGING":"，正在使用电池"}.get(str(info.get("status")).upper(),"")
        return f"手机电量是 {pct}%{state}。"
    return {"vibrate":"手机已接收振动请求。","light_on":"手机已接收打开手电筒的请求。",
            "light_off":"手机已接收关闭手电筒的请求。"}[action]


class PhoneControl:
    title = "安卓手机助手"

    def interested(self,key,text,addressed):
        return bool(not key[2] and requested(text))

    async def handle(self,host,event,key,text):
        action = requested(text)
        if key[2] or not action or not is_owner_event(event): return False
        if not os.environ.get("QQCHAT_TERMUX_BIN") and action.startswith("light_"):
            # A non-Android host can still control an explicitly paired phone action via Home Assistant.
            from app.services.home_devices import HomeDevices
            home = HomeDevices(host.runtime.project_root)
            if home.store.path.exists() and any(b["name"] in text for b in home.status()["devices"]): return False
        store = PlanStore(host.runtime.project_root/"data/dafeiyu/phone-actions.sqlite3","")
        if action=="battery" or store.turn(event,lambda db:True,datetime.now(CHINA)):
            await event.send(event.plain_result(await asyncio.to_thread(termux_action,action)))
        return True
