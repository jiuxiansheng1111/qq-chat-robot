"""Local Windows actions explicitly requested by the linked owner in a private QQ chat."""
from __future__ import annotations

import asyncio
import os
import re
import subprocess
from datetime import datetime
from pathlib import Path

from app.services.owner_policy import is_owner_event
from app.services.social_plans import CHINA, PlanStore

_APPS = {"记事本":"notepad.exe", "计算器":"calc.exe", "画图":"mspaint.exe"}
_MEDIA = {"暂停或继续音乐":0xB3, "切到下一首":0xB0, "切到上一首":0xB1, "切换静音":0xAD, "调高音量":0xAF, "调低音量":0xAE}
_OPEN = re.compile(r"(?:帮我|请)?(?:在(?:我的)?电脑(?:上)?打开|打开(?:我的)?电脑(?:上|上的|的)?)(记事本|计算器|画图)[吧。！!]?")
_CONTROL = re.compile(r"(?:帮我|请)?(?:让|把|在)?(?:我的)?电脑(?:上)?(?:的)?(暂停或继续音乐|切到下一首|切到上一首|切换静音|调高音量|调低音量|锁屏|看看当前窗口|当前窗口是什么)[吧。？?！!]?")


def windows_action(action):
    if os.name != "nt":
        return "这个动作需要在运行 Windows 的电脑上执行。"
    import ctypes

    user32 = ctypes.windll.user32
    if action in _APPS:
        executable = Path(os.environ["SystemRoot"]) / "System32" / _APPS[action]
        subprocess.Popen([str(executable)], close_fds=True, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return "已向 Windows 请求打开" + action + "。"
    if action == "锁屏":
        return "Windows 已接受锁屏请求。" if user32.LockWorkStation() else "Windows 没有接受这次锁屏请求。"
    if action in _MEDIA:
        key = _MEDIA[action]
        user32.keybd_event(key, 0, 0, 0)
        user32.keybd_event(key, 0, 2, 0)
        return "已向电脑发送「" + action + "」媒体按键。"
    from ctypes import wintypes
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.GetWindowTextW.argtypes = (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
    title = ctypes.create_unicode_buffer(512)
    user32.GetWindowTextW(user32.GetForegroundWindow(), title, len(title))
    return "当前窗口是「" + title.value + "」。" if title.value else "当前桌面没有可读取标题的窗口。"


class DesktopControl:
    title = "电脑助手"

    def interested(self, key, text, addressed):
        return bool(not key[2] and (_OPEN.fullmatch(text) or _CONTROL.fullmatch(text)))

    async def handle(self, host, event, key, text):
        if key[2] or not is_owner_event(event):
            return False
        match = _OPEN.fullmatch(text) or _CONTROL.fullmatch(text)
        if not match:
            return False
        store = PlanStore(host.runtime.project_root / "data/dafeiyu/desktop-actions.sqlite3", "")
        if store.turn(event, lambda db: True, datetime.now(CHINA)):
            result = await asyncio.to_thread(windows_action, match[1])
            await event.send(event.plain_result(result))
        return True
