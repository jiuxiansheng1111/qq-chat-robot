"""Natural owner requests for lights, climate, music and paired computer/phone actions."""
import re

from app.services.home_devices import DeviceError, HomeDevices
from app.services.owner_policy import is_owner_event

_DEVICE = re.compile(r"设备|场景|空调|灯|音箱|音响|电脑|手机|窗帘")
_REQUEST = re.compile(r"打开|关闭|关掉|开启|点亮|熄灭|开着吗|开了吗|关了吗|关着吗|亮着吗|开还是关|状态|调到|调成|设为|设成|执行|切换|暂停|播放|启动|按一下|按下|有哪些设备|[开关].{0,8}(?:灯|空调)|(?:开|关)了吧")
_COMMENT = re.compile(r"别|不要|不用|不需要|取消|没有让|没让|如果|假如|要是|为什么|怎么(?:才能|设置|连接)|他说|她说|听说|打开过|"
                      r"(?:刚才|刚刚|之前|昨天|上次|有人|自己去|准备去|忘了|忘记).{0,14}(?:开|关|调|按|执行)")


class HomeControl:
    title = "设备联动"

    def interested(self, key, text, addressed):
        return bool((addressed or not key[2]) and _REQUEST.search(text) and not _COMMENT.search(text))

    async def handle(self, host, event, key, text):
        if key[2] or not is_owner_event(event):
            return False
        home = HomeDevices(host.runtime.project_root)
        if not _DEVICE.search(text) and (not home.store.path.exists() or not any(
                b["name"] in text for b in home.status()["devices"])):
            return False
        try:
            reply = await home.execute(event, text)
        except DeviceError as exc:
            reply = str(exc)
        if reply:
            await event.send(event.plain_result(reply))
        return True
