"""Recipe suggestions based on this turn's ingredients, photo evidence, and available time."""
import re
import time
from collections import OrderedDict

from app.astrbot_chat_actions import _model
from app.astrbot_compare import image_refs, parse_object
from app.astrbot_turn_features import observe_image

_REQUEST = re.compile(r"冰箱|冰柜|这些食材|这(?:张|个)食材照|剩菜|剩下的菜")
_ASK = re.compile(r"配(?:一下|菜)|能做|做(?:点|些什么|什么)|做饭|搭配|推荐|先吃|怎么吃|怎么做|食谱|菜谱")


class Fridge:
    title = "冰箱食材搭配"

    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.waiting = OrderedDict()

    def interested(self, key, text, addressed):
        if key in self.waiting and self.waiting[key][0] < self.clock():
            self.waiting.pop(key)
        return bool((addressed or not key[2]) and _REQUEST.search(text) and _ASK.search(text) or key in self.waiting)

    async def handle(self, host, event, key, text):
        quoted, current = image_refs(event)
        refs = current or quoted
        pending = self.waiting.get(key)
        if pending:
            if text.strip("。！ ") in {"算了","不做了","取消配菜"}:
                self.waiting.pop(key)
                await event.send(event.plain_result("好，这次配菜先收起来。"))
                return True
            if refs or re.search(r"有|剩|蛋|肉|番茄|豆腐|牛奶|菜|土豆|面条|米饭", text):
                text = pending[1] + "。" + text
            elif not (_REQUEST.search(text) and _ASK.search(text)):
                return False
        visible = []
        if refs:
            try:
                evidence = parse_object(await observe_image(host,refs[:3],
                    '只识别清楚可见的食材，只返回 JSON {"ingredients":["食材名"]}。看不清的不填。不要判断保质期、新鲜程度或安全性。'))
                rows = evidence.get("ingredients")
                if isinstance(rows,list):
                    visible = [x.strip()[:35] for x in rows if isinstance(x,str) and x.strip()][:30]
            except (ValueError, TypeError, RuntimeError, TimeoutError):
                visible = []
        # Avoid having a model invent fridge contents when the user only attached an unreadable photo.
        named = re.search(r"(?:有|剩下|只剩|食材是|食材[：:]|快过期(?:的)?是)\s*([^，。？?]{1,90})", text)
        if not visible and not named and not re.search(r"蛋|肉|菜|米饭|牛奶|面条|豆腐|土豆|番茄|西红柿|蘑菇|黄瓜|青椒|胡萝卜", text.replace("剩菜","").replace("菜谱","").replace("配菜","")):
            reply = ("这张照片里我没辨清食材。" if refs else "") + "冰箱里有哪些东西？把食材名或照片发来，也可以说哪样想先吃。"
            self.waiting[key] = self.clock()+300, text[-400:]
            self.waiting.move_to_end(key)
            if len(self.waiting) > 150:
                self.waiting.popitem(last=False)
        else:
            answer = await _model(host,event,"你是大肥鱼。只根据给出的可见食材和对方明确说有的食材，按可用时间给一到两道具体菜和简短步骤。"
                "优先使用对方点名要先吃的材料。额外调料说明可选。不要凭画面推断日期、新鲜、安全或过敏情况。不要发星号。",
                {"用户的要求":text,"能从照片确认的食材":visible})
            if not answer:
                raise ValueError("empty recipe")
            reply = ("我从照片里认出了" + "、".join(visible) + "。\n" if visible else "") + answer
            self.waiting.pop(key, None)
        await event.send(event.plain_result(reply))
        return True
