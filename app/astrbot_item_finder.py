"""Remember and retrieve the owner's explicit description and original object photo."""
import json
import re

from app.services.activity_notes import notes_store, personal_note

_SAVE = re.compile(r"^(?:帮我|请)?(?:记下|记住|记录|记一下|存一下)(?:物品位置)?[：:，, ]*(?:我的)?(.{1,48}?)(?:放在|放到|收在|藏在|搁在)(.{1,220})$")
_GET = re.compile(r"^(?:帮我找找|帮我找一下|找一下|我(?:上次)?(?:把)?|我的)?(.{1,48}?)(?:上次)?(?:放哪了|放在哪(?:里|儿)?(?:了)?|放哪儿了|收在哪(?:里|儿)?|在哪里|在哪个抽屉)[？?呢啊呀]*$")
_DEL = re.compile(r"^(?:忘掉|删除|清除)(?:我(?:的)?)?(.{1,48}?)(?:的)?(?:存放位置|物品位置|位置记录|位置)$")
_LIST = re.compile(r"^(?:看看|查看|列出)?(?:我的)?(?:物品位置清单|存放位置清单|物品记录)$")


class ItemFinder:
    title = "用照片找物品"

    def interested(self, key, text, addressed):
        text = text.strip(" 。！？!?")
        return bool((addressed or not key[2]) and any(rule.fullmatch(text) for rule in (_SAVE,_GET,_DEL,_LIST)))

    async def handle(self, host, event, key, text):
        text = text.strip(" 。！？!?")
        for rule, action in ((_SAVE,"save"), (_DEL,"forget"), (_LIST,"list"), (_GET,"get")):
            match = rule.fullmatch(text)
            if match:
                if action == "get":
                    store = notes_store(host, "item_finder")
                    # "深圳在哪里" is an ordinary question unless a matching item really exists.
                    if not re.search(r"放哪|收在哪|哪个抽屉", text) and (not store.path.exists() or
                            not store.transaction(lambda db, name=match[1]: db.execute(
                                "SELECT 1 FROM notes WHERE owner=? AND name=?", (json.dumps(key), name)).fetchone())):
                        return False
                return await personal_note(host,event,key,"item_finder",action,*match.groups())
        return False
