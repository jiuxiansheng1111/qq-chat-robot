"""Resume an explicitly saved hobby project with the next step and optional reference photo."""
import re

from app.services.activity_notes import personal_note

_SAVE = re.compile(r"^(?:帮我|请)?(?:存个进度|保存进度|记下项目|保存项目|记一下项目|记下进度|保存草稿)[：:，, ]*[《「]?([^，,：:《》「」\n]{1,55})[》」]?[，,：:\n ]+(.{1,1200})$", re.DOTALL)
_GET = re.compile(r"^(?:继续上次(?:的)?(?:项目|那件事|手工|作品)|上次(?:的项目|做到哪了|卡在哪了)|周末接着做|接着做上次的)[，,：: ]*(.{0,55})$")
_DEL = re.compile(r"^(?:删除|忘掉|清除)(?:我的)?(?:项目|草稿)[：: ]*(.{1,55})$")
_LIST = re.compile(r"^(?:查看|看看|列出)?(?:我的)?(?:项目进度清单|项目清单|手工进度清单)$")


class ProjectResume:
    title = "周末接着做"

    def interested(self, key, text, addressed):
        return bool((addressed or not key[2]) and any(rule.fullmatch(text.strip(" 。！？!?")) for rule in (_SAVE,_GET,_DEL,_LIST)))

    async def handle(self, host, event, key, text):
        for rule, action in ((_SAVE,"save"), (_GET,"get"), (_DEL,"forget"), (_LIST,"list")):
            match = rule.fullmatch(text.strip(" 。！？!?"))
            if match:
                return await personal_note(host,event,key,"project_resume",action,*(p.strip(" 《》「」") for p in match.groups()))
        return False
