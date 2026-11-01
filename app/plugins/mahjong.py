"""Bounded replay input, per-owner reports, and authorized recipient-pinned AI coaching."""

from __future__ import annotations

import asyncio
import json
import re
import time
import uuid
from collections import OrderedDict
from dataclasses import replace
from pathlib import Path

from app.services.mahjong.ai_coach import coach_decisions
from app.services.mahjong.intent import complete_request
from app.services.mahjong.records import (
    RecordError,
    inbox_file,
    parse_record,
    read_local,
    seat_number,
)
from app.services.mahjong.review import Review, brief_decision, markdown, review, summary

HELP = (
    "雀魂四麻牌谱复盘（规则计算＋GPT-6 Luna 匿名当时局面教学）\n"
    "/牌谱分析 座位=东 赛制=半庄 文件=sample.json\n"
    "或 /牌谱分析 座位=0 赛制=东风 JSON={...}\n"
    "座位是开局固定座位0东1南2西3北；支持标准mjai及tenhou.net/6兼容导出（内部格式仅供调试）。\n"
    "文件须先由本机放入项目data/mahjong/inbox；群消息不能读取任意本机路径或任意附件URL。\n"
    "也可直接发：雀魂分析 雀魂牌谱:https://game.maj-soul.com/1/?paipu=...\n"
    "管理员先在本机‘雀魂账号设置’连接账号。链接自动识别赛制与玩家视角，读取全部局次；不需要截图。\n"
    "逐局挑选关键节点交给 GPT-6 Luna：脱敏当时局面、计算与相关本地教材；复用已连接的 ChatGPT 订阅。"
    "默认发送逐局图文卡片，直接列出具体切牌、保留牌与场况计划；图片生成失败自动分段发文字。不发QQ身份、原谱、暗手、未来或结局，不走备用；失败回本地。AI=关闭 可仅用本地规则版。"
)


class ReportStore:
    def __init__(self) -> None:
        self.jobs: OrderedDict[str, tuple[float, tuple[str, str, str], Review]] = OrderedDict()

    def _expire(self) -> None:
        now = time.monotonic()
        for job, (created, _, _) in list(self.jobs.items()):
            if now - created > 1800:
                self.jobs.pop(job, None)

    def put(self, owner: tuple[str, str, str], result: Review) -> str:
        self._expire()
        while len(self.jobs) >= 8:
            self.jobs.popitem(last=False)
        job_id = uuid.uuid4().hex[:12]
        self.jobs[job_id] = (time.monotonic(), owner, result)
        return job_id

    def get(self, owner: tuple[str, str, str], job_id: str) -> Review:
        self._expire()
        value = self.jobs.get(job_id)
        if value is None or value[1] != owner:
            raise RecordError("找不到你的复盘，可能已过30分钟或不属于当前账号/群/用户")
        return value[2]


def _owner(context) -> tuple[str, str, str]:
    return (str(context.event.get("self_id", "")), str(context.group_id), str(context.user_id))


def _root(context) -> Path:
    # Fixed application path, never supplied by a group member or record contents.
    return Path(__file__).resolve().parents[2]


def _arguments(text: str) -> tuple[int | None, str | None, str, str]:
    header, marker, body = text.partition(" JSON=")
    matches = re.findall(r"(?:^|\s)(座位|赛制|文件|链接|AI)=([^\s]+)", header)
    params = dict(matches)
    if len(matches) != len(params):
        raise RecordError("参数重复，请只提供一个座位、赛制和输入")
    if "链接" not in params and ("座位" not in params or "赛制" not in params):
        raise RecordError("请明确座位=东/南/西/北和赛制=东风/半庄")
    seat = seat_number(params["座位"]) if "座位" in params else None
    sources = [(k, params[k]) for k in ("文件", "链接") if k in params]
    if marker:
        sources.append(("JSON", body))
    if len(sources) != 1:
        raise RecordError("请提供且仅提供一个 文件= / 链接= / JSON= 输入")
    return seat, params.get("赛制"), *sources[0]


async def handle_mahjong(context) -> str:
    args = context.args.strip()
    if args in {"帮助", "help"}:
        return HELP
    state = context.request.app.state
    if not hasattr(state, "mahjong_reports"):
        state.mahjong_reports = ReportStore()
        state.mahjong_review_lock = asyncio.Lock()
    store = state.mahjong_reports
    owner = _owner(context)
    try:
        if args.startswith("详情 "):
            parts = args.split()
            if len(parts) not in (3, 4) or not all(p.isdecimal() for p in parts[2:]):
                raise RecordError("用法：详情 复盘ID 节点号 [页码]")
            result = store.get(owner, parts[1])
            number = int(parts[2])
            page = int(parts[3]) if len(parts) == 4 else 1
            if not 1 <= number <= len(result.analysis.decisions) or not 1 <= page <= 100:
                raise RecordError("节点号或页码不存在")
            decision = result.analysis.decisions[number - 1]
            # QQ: two candidate rows per request; full table is available in local export.
            start = (page - 1) * 2
            if start >= len(decision.candidates):
                raise RecordError("该节点没有这一页")
            return parts[1] + "\n" + brief_decision(result, number, page)
        if args.startswith("导出 "):
            parts = args.split()
            if len(parts) != 2:
                raise RecordError("用法：导出 复盘ID")
            result = store.get(owner, parts[1])
            root = _root(context)
            directory = root / "data" / "mahjong" / "reports"
            # No user-controlled output path. Refuse junctions outside the project.
            if not directory.resolve().is_relative_to(root.resolve()):
                raise RecordError("报告目录必须在项目内")
            directory.mkdir(parents=True, exist_ok=True)
            target = directory / f"{parts[1]}.md"
            if target.is_symlink():
                raise RecordError("报告目标不能是符号链接")
            content = markdown(result)
            if target.exists():
                if target.read_text(encoding="utf-8") != content:
                    raise RecordError("同名报告已存在且内容不同，拒绝覆盖")
            else:
                with target.open("x", encoding="utf-8") as stream:
                    stream.write(content)
            return (
                f"完整复盘已保存到本机 data/mahjong/reports/{parts[1]}.md；群内可继续按节点查看。"
            )
        if args.startswith("https://"):
            args = "链接=" + args
        identity_event = {**context.event, "group_id": context.group_id, "user_id": context.user_id}
        prepared, prompt = complete_request(state, identity_event, args)
        if prompt:
            return prompt
        args = prepared
        ai_option = re.search(r"(?:^|\s)AI=([^\s]+)", args.partition(" JSON=")[0])
        if ai_option and ai_option[1] not in {"开启", "关闭"}:
            raise RecordError("AI参数只支持 AI=开启 或 AI=关闭")
        ai_enabled = ai_option is None or ai_option[1] == "开启"
        seat, match, kind, source = _arguments(args)
        if state.mahjong_review_lock.locked():
            return "正在处理另一份牌谱，请稍后再试。"
        if kind == "JSON":
            if len(source.encode("utf-8")) > 64 * 1024:
                raise RecordError("群内JSON上限64KiB，请使用本地导出文件")
            payload = source.encode("utf-8")
        elif kind == "文件":
            payload = read_local(inbox_file(_root(context), source))
        async with state.mahjong_review_lock:
            overview = ()
            if kind == "链接":
                from app.services.mahjong.majsoul_client import service_for
                from app.services.mahjong.majsoul_records import convert_game, whole_game_overview

                head, records, target_id = await service_for(_root(context)).fetch(source)
                replay, seat = convert_game(head, records, target_id, seat, match)
                overview = whole_game_overview(replay, seat)
            else:
                replay = parse_record(payload, match=match)
            result = await asyncio.to_thread(review, replay, seat)
            result = replace(result, overview=overview)
            # Keep this explicit Luna route independent of general chat and media providers.
            coaching = await coach_decisions(result.analysis.decisions, enabled=ai_enabled)
            result = replace(result, coaching=coaching)
        job_id = store.put(owner, result)
        return summary(result, job_id)
    except RecordError as exc:
        return "牌谱分析：" + str(exc)
    except (OSError, ValueError, RecursionError, json.JSONDecodeError):
        return "牌谱分析：输入数据或本地文件不完整，已停止；未将原始牌谱发送给第三方。"
