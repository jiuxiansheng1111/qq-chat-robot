"""按北京时间日历窗口生成本群的昨日、今日群报；发送由调用方负责。"""

from __future__ import annotations

import asyncio
import copy
import hashlib
import html
import json
import logging
import math
import os
import re
import time
import uuid
from collections.abc import Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from typing import Any

from PIL import Image

from app.astrbot_plugin_status import _loaded_plugin
from app.services.local_report_render import report_renderer

def _load_report_groups(path: Path | None = None) -> frozenset[str]:
    """Read local group permissions, failing closed when unavailable."""
    path = path or Path(__file__).resolve().parents[1] / "data/calendar_report_groups.json"
    try:
        groups = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return frozenset()
    if not isinstance(groups, list):
        return frozenset()
    return frozenset(group for group in groups if isinstance(group, str) and re.fullmatch(r"\d{5,20}", group))


ALLOWED_REPORT_GROUPS = _load_report_groups()
SHANGHAI = timezone(timedelta(hours=8), "Asia/Shanghai")
_PLUGIN = "astrbot_plugin_qq_group_daily_analysis"
_CACHE_ROOT = Path(__file__).resolve().parents[1] / "data/cache/calendar_group_reports"
_CACHE_SECONDS = 300
_CACHE_VERSION = 2
_MAX_IMAGE_BYTES = 20 * 1024 * 1024
_LOG = logging.getLogger(__name__)


@dataclass(frozen=True)
class ReportWindow:
    kind: str
    start_ts: int
    end_ts: int  # exclusive

    @property
    def date(self) -> str:
        return datetime.fromtimestamp(self.start_ts, SHANGHAI).strftime("%Y-%m-%d")

    @property
    def header(self) -> str:
        name = {"yesterday": "昨日群报", "today": "今日群报"}.get(self.kind, "群报")
        if self.end_ts <= self.start_ts:
            return f"{name}｜{self.date}\n今天尚无完整分钟可统计（北京时间）。"
        end = datetime.fromtimestamp(self.end_ts - 1, SHANGHAI)
        return f"{name}｜{self.date}\n范围：00:00:00—{end:%H:%M:%S}（北京时间）"


@dataclass(frozen=True)
class GroupReportItem:
    window: ReportWindow
    status: str
    text: str = ""
    image_path: str | None = None
    message_count: int = 0
    reused: bool = False
    partial: bool = False

    @property
    def header(self) -> str:
        note = "\n复用近期缓存，统计截止时间以上述范围为准。" if self.reused else ""
        if self.partial:
            note += "\n消息较多，本报仅统计接口返回且落在该时段内的记录。"
        return self.window.header + note


@dataclass(frozen=True)
class GroupReportBatch:
    status: str
    items: tuple[GroupReportItem, ...] = ()
    message: str = ""


def calendar_report_windows(
    now: datetime | None = None, *, date_text: str = "",
) -> tuple[ReportWindow, ...]:
    """默认昨日＋今日；指定日只返回该日，今天截到上一完整分钟。"""
    current = now or datetime.now(SHANGHAI)
    if current.tzinfo is None:
        current = current.replace(tzinfo=SHANGHAI)
    local = current.astimezone(SHANGHAI)
    midnight = local.replace(hour=0, minute=0, second=0, microsecond=0)
    start = int(midnight.timestamp())
    cutoff = math.floor(local.timestamp() / 60) * 60
    selector = str(date_text or "").strip()
    if selector:
        relative = {"今天": 0, "今日": 0, "昨天": 1, "昨日": 1, "前天": 2}
        if selector in relative:
            selected = midnight - timedelta(days=relative[selector])
        elif re.fullmatch(r"\d{4}-\d{2}-\d{2}", selector):
            try:
                selected = datetime.strptime(selector, "%Y-%m-%d").replace(tzinfo=SHANGHAI)
            except ValueError:
                raise ValueError("日期无效，请用 群报 昨天 或 群报 2026-10-27。") from None
        else:
            raise ValueError("请指定今天、昨天、前天或 YYYY-MM-DD，例如 群报 昨天。")
        if selected > midnight or selected < midnight - timedelta(days=30):
            raise ValueError("群报可补发今天及过去30天内的单日；不能选择未来日期。")
        kind = "today" if selected == midnight else "yesterday" if selected == midnight - timedelta(days=1) else "daily"
        end = cutoff if kind == "today" else int((selected + timedelta(days=1)).timestamp())
        return (ReportWindow(kind, int(selected.timestamp()), end),)
    return (
        ReportWindow("yesterday", int((midnight - timedelta(days=1)).timestamp()), start),
        ReportWindow("today", start, cutoff),
    )


def _value(value: Any, key: str, default: Any = None) -> Any:
    return value.get(key, default) if isinstance(value, Mapping) else getattr(value, key, default)


def _timestamp(message: Any) -> int:
    raw = _value(message, "timestamp", _value(message, "time", 0))
    try:
        return int(raw) if not isinstance(raw, bool) else 0
    except (TypeError, ValueError, OverflowError):
        return 0


def _history_anchor(event: Any) -> str | None:
    raw = _value(_value(event, "message_obj"), "message_id")
    if raw is None or isinstance(raw, bool):
        return None
    value = str(raw).strip()
    return value if re.fullmatch(r"-?[0-9]{1,20}", value) and int(value) else None


def messages_in_window(messages: list, window: ReportWindow, group_id: str) -> list:
    """再次严格过滤半开区间和所属群；不信任后端只遵守 days。"""
    return sorted(
        (
            message for message in messages
            if str(_value(message, "group_id", "")) == group_id
            and window.start_ts <= _timestamp(message) < window.end_ts
        ),
        key=_timestamp,
    )


def _identity(event: Any) -> tuple[str, str, str] | None:
    try:
        group = str(event.get_group_id() or "").strip()
        sender = str(event.get_sender_id() or "").strip()
        self_id = str(event.get_self_id() or "").strip()
        origin = str(getattr(event, "unified_msg_origin", "") or "")
        parts = origin.split(":", 2)
        if (
            group not in ALLOWED_REPORT_GROUPS or not sender or sender == self_id
            or len(parts) != 3 or parts[1:] != ["GroupMessage", group]
        ):
            return None
        platform = str(event.get_platform_id() or "").strip()
        if not platform or platform != parts[0]:
            return None
        return group, platform, self_id
    except (AttributeError, TypeError, ValueError):
        return None


class _Step:
    def set_payload(self, **_kwargs: Any) -> None:
        pass

    def mark_warning(self, _message: str) -> None:
        pass

    def mark_failed(self, _message: str) -> None:
        pass


class _ReadOnlyPipeline:
    """复用原分析阶段契约，不写当日 checkpoint 或改变原定时任务。"""

    @asynccontextmanager
    async def step(self, *_args: Any, **_kwargs: Any):
        yield _Step()


class _WindowTemplates:
    """只覆盖这次渲染的日期，不修改共享模板或全局 datetime。"""

    def __init__(self, original: Any, window: ReportWindow):
        self.original = original
        self.window = window

    def __getattr__(self, name: str) -> Any:
        return getattr(self.original, name)

    def render_template(self, name: str, *args: Any, **kwargs: Any) -> str:
        if name == "image_template.html":
            kwargs["current_date"] = self.window.date
            last = max(self.window.start_ts, self.window.end_ts - 1)
            kwargs["current_datetime"] = datetime.fromtimestamp(last, SHANGHAI).strftime(
                "%Y-%m-%d %H:%M:%S"
            )
        return self.original.render_template(name, *args, **kwargs)


def _valid_image(data: bytes) -> bool:
    if not data or len(data) > _MAX_IMAGE_BYTES:
        return False
    try:
        with Image.open(BytesIO(data)) as image:
            if image.format not in {"PNG", "JPEG"} or image.width * image.height > 40_000_000:
                return False
            image.verify()
        return True
    except (OSError, ValueError, Image.DecompressionBombError):
        return False


class GroupReportService:
    """一份已加载日报服务的日历群报视图，完全不包含发送/漫画操作。"""

    def __init__(
        self, analysis_service: Any, *, cache_dir: Path | None = None,
        clock: Any = None, message_limit: int = 5000, report_timeout: float = 90,
    ) -> None:
        self.service = analysis_service
        self.cache_dir = Path(cache_dir) if cache_dir is not None else _CACHE_ROOT
        self.clock = clock or (lambda: datetime.now(SHANGHAI))
        self.message_limit = max(100, min(int(message_limit), 10_000))
        self.report_timeout = report_timeout
        self._active: set[str] = set()

    def _base(self, group: str, platform: str, window: ReportWindow) -> Path:
        namespace = hashlib.sha256(platform.encode()).hexdigest()[:16]
        cutoff = f"-{window.end_ts}" if window.kind == "today" else ""
        return self.cache_dir / namespace / group / f"{window.kind}-{window.date}{cutoff}"

    def _cached(
        self, group: str, platform: str, requested: ReportWindow, now_ts: int,
    ) -> GroupReportItem | None:
        base = self._base(group, platform, requested)
        try:
            path = base.with_suffix(".json")
            if path.is_symlink() or not path.is_file() or path.stat().st_size > 200_000:
                return None
            data = json.loads(path.read_text(encoding="utf-8"))
            if (
                data.get("version") != _CACHE_VERSION or data.get("group") != group
                or data.get("platform") != platform or data.get("kind") != requested.kind
                or data.get("start_ts") != requested.start_ts
                or not isinstance(data.get("end_ts"), int)
                or data["end_ts"] != requested.end_ts
                or data.get("status") not in {"ready", "text", "no_messages", "error"}
                or not isinstance(data.get("created_ts"), int)
                or now_ts < data["created_ts"]
            ):
                return None
            permanent = requested.kind != "today" and data["status"] != "error"
            if permanent and data["end_ts"] != requested.end_ts:
                return None
            if not permanent and now_ts - data["created_ts"] >= _CACHE_SECONDS:
                return None
            image_path = None
            if data["status"] == "ready":
                image = base.with_suffix(".png")
                if image.is_symlink() or image.stat().st_size > _MAX_IMAGE_BYTES:
                    return None
                if not _valid_image(image.read_bytes()):
                    return None
                image_path = str(image.resolve())
            return GroupReportItem(
                ReportWindow(requested.kind, requested.start_ts, data["end_ts"]),
                data["status"], text=str(data.get("text", ""))[:60_000],
                image_path=image_path, message_count=int(data.get("message_count", 0)),
                partial=bool(data.get("partial", False)), reused=True,
            )
        except (OSError, ValueError, TypeError, OverflowError):
            return None

    def _save(
        self, group: str, platform: str, item: GroupReportItem, now_ts: int,
        image: bytes | None = None,
    ) -> GroupReportItem:
        base = self._base(group, platform, item.window)
        base.parent.mkdir(parents=True, exist_ok=True)
        image_path = None
        if image:
            destination = base.with_suffix(".png")
            _atomic_write(destination, image)
            image_path = str(destination.resolve())
        data = {
            "version": _CACHE_VERSION, "group": group, "platform": platform, "kind": item.window.kind,
            "start_ts": item.window.start_ts, "end_ts": item.window.end_ts,
            "created_ts": now_ts, "status": item.status, "text": item.text[:60_000],
            "message_count": item.message_count, "partial": item.partial,
        }
        _atomic_write(base.with_suffix(".json"), json.dumps(data, ensure_ascii=False).encode())
        return replace(item, image_path=image_path)

    async def get_reports(self, event: Any, *, date_text: str = "") -> GroupReportBatch:
        identity = _identity(event)
        if identity is None:
            return GroupReportBatch("denied", message="请在已启用群报的本群中请求群报。")
        group, platform, self_id = identity
        if group in self._active or self.service.is_group_running(group, "daily"):
            return GroupReportBatch("busy", message="本群群报正在生成，请稍后再试。")
        self._active.add(group)
        try:
            current = self.clock()
            try:
                windows = calendar_report_windows(current, date_text=date_text)
            except ValueError as error:
                return GroupReportBatch("invalid_date", message=str(error))
            now_ts = int(current.replace(tzinfo=SHANGHAI).timestamp()) if (
                current.tzinfo is None
            ) else int(current.timestamp())
            cached = [self._cached(group, platform, window, now_ts) for window in windows]
            if all(cached):
                return GroupReportBatch("ready", tuple(item for item in cached if item))
            manager = self.service.bot_manager
            manager.update_from_event(event)
            adapter = manager.get_adapter(platform)
            if adapter is None:
                return GroupReportBatch("unavailable", message="本群的群报消息接口暂不可用。")
            async with self.service.group_lock(group, "daily"):
                if await adapter.is_group_muted(group):
                    return GroupReportBatch("muted", message="本群当前禁言，暂不生成群报。")
                earliest = min(w.start_ts for w, item in zip(windows, cached) if item is None)
                async with asyncio.timeout(30):
                    messages = await adapter.fetch_messages(
                        group_id=group, days=max(2, math.ceil((now_ts - earliest) / 86400)),
                        max_count=self.message_limit,
                        # NapCat's unanchored first page may be a sparse QQ view
                        # cache. Start from this real request to read contiguous history.
                        since_ts=earliest, before_id=_history_anchor(event),
                    )
                partial = len(messages) >= self.message_limit - 1
                items = []
                for window, existing in zip(windows, cached):
                    if existing is not None:
                        items.append(existing)
                        continue
                    try:
                        async with asyncio.timeout(self.report_timeout):
                            item, image = await self._build(
                                adapter, group, platform, self_id, window, messages, partial,
                            )
                    except Exception as error:  # noqa: BLE001 -- 第三方阶段失败只返回短提示，取消仍上抛。
                        _LOG.warning("calendar group report failed: %s", type(error).__name__)
                        item, image = GroupReportItem(
                            window, "error", text="这份群报暂时生成失败，稍后可重试。",
                        ), None
                    items.append(self._save(group, platform, item, now_ts, image))
                return GroupReportBatch("ready", tuple(items))
        except Exception as error:  # noqa: BLE001 -- 第三方接口异常不能泄漏配置、正文或凭据。
            _LOG.warning("calendar group report unavailable: %s", type(error).__name__)
            return GroupReportBatch("unavailable", message="本群群报暂时无法读取，请稍后再试。")
        finally:
            self._active.discard(group)

    async def _build(
        self, adapter: Any, group: str, platform: str, self_id: str,
        window: ReportWindow, messages: list, partial: bool,
    ) -> tuple[GroupReportItem, bytes | None]:
        filtered = messages_in_window(messages, window, group)
        bots = {str(value) for value in getattr(adapter, "bot_self_ids", []) if value}
        bots.update(str(value) for value in self.service.config_manager.get_bot_self_ids() or [])
        if self_id:
            bots.add(self_id)
        filtered = [message for message in filtered if str(_value(message, "sender_id", "")) not in bots]
        if not filtered:
            return GroupReportItem(
                window, "no_messages", text="这个时段没有可用于生成群报的聊天记录。",
                partial=partial,
            ), None
        pipeline = _ReadOnlyPipeline()
        cleaned = await self.service._clean_daily_messages(
            pipeline, None, adapter, filtered, group, platform,
        )
        if not cleaned.messages:
            return GroupReportItem(
                window, "no_messages", text="这个时段没有可用于生成群报的有效聊天记录。",
                partial=partial,
            ), None
        stats, activity = await self.service._calculate_daily_statistics(
            pipeline, cleaned.messages, list(bots),
        )
        if not any(str(_value(message, "text_content", "") or "").strip()
                   for message in cleaned.messages):
            return GroupReportItem(
                window, "text",
                text=(f"共 {stats.message_count} 条非文字消息，{stats.participant_count} 人参与。"
                      "这个时段没有可用于总结的文字，暂不生成话题、金句、成员画像和聊天质量评价。"),
                message_count=len(cleaned.messages), partial=partial,
            ), None
        top_users = self.service.analysis_domain_service.get_top_users(
            activity, limit=self.service.config_manager.get_max_user_titles(),
        )
        legacy = self.service.statistics_service._convert_to_legacy_dict(cleaned.messages)
        result = await self.service._run_llm_analysis_stage(
            pipeline, group, "calendar_group_report", legacy, activity, top_users,
            f"{platform}:GroupMessage:{group}",
            self.service._resolve_daily_analysis_sections(None), None,
        )
        if result.failed:
            return GroupReportItem(
                window, "error", text="这份群报的分析暂时未成功，没有编造内容。",
                message_count=len(cleaned.messages), partial=partial,
            ), None
        stats.golden_quotes = result.golden_quotes
        stats.token_usage = result.token_usage
        analysis = {
            "statistics": stats, "topics": result.topics, "user_titles": result.user_titles,
            "user_analysis": activity, "chat_quality_review": result.chat_quality_review,
        }
        image = await self._render(analysis, adapter, group, platform, window)
        text = ""
        if image is None:
            text = self.service.report_generator.generate_text_report(analysis).strip()
            text = re.sub(r"(?m)^📅 \d{4}年\d{2}月\d{2}日\s*$", "", text, count=1)
        return GroupReportItem(
            window, "ready" if image else "text", text=text, message_count=len(cleaned.messages),
            partial=partial,
        ), image

    async def _render(
        self, analysis: dict, adapter: Any, group: str, platform: str, window: ReportWindow,
    ) -> bytes | None:
        generator = copy.copy(self.service.report_generator)
        if getattr(generator, "html_templates", None) is not None:
            generator.html_templates = _WindowTemplates(generator.html_templates, window)
        rendered_paths: set[Path] = set()

        async def local_render(content: str, _data: Any, return_url: bool, options: Any):
            if return_url:
                raise ValueError("calendar reports require local image files")
            banner = (
                '<div style="font:18px/1.6 Microsoft YaHei,sans-serif;padding:18px 24px;'
                'background:#fff;color:#172330;border-bottom:2px solid #cadce8">'
                + html.escape(window.header).replace("\n", "<br>") + "</div>"
            )
            content, count = re.subn(
                r"(<body\b[^>]*>)", lambda match: match.group(1) + banner,
                content, count=1, flags=re.IGNORECASE,
            )
            if not count:
                content = banner + content
            safe_options = dict(options or {})
            safe_options.update(type="png")
            safe_options.pop("quality", None)
            path = Path(await report_renderer.render_to_file(
                content, safe_options, self.cache_dir / "render-work",
            )).resolve()
            rendered_paths.add(path)
            return str(path)

        async def avatar(user_id: str, size: int | None = None):
            return await adapter.get_user_avatar_url(user_id, size=size or 40)

        async def nickname(user_id: str):
            member = await adapter.get_member_info(group, user_id)
            return (getattr(member, "card", "") or getattr(member, "nickname", "")) if member else None

        try:
            path, _content = await generator.generate_image_report(
                analysis, group, local_render, avatar_url_getter=avatar,
                nickname_getter=nickname, avatar_cache_namespace=platform,
                template_theme=self.service.config_manager.get_report_template(),
            )
            # Only paths actually returned by our local renderer may be read.
            candidate = Path(path).resolve() if isinstance(path, str) else None
            if candidate in rendered_paths and candidate.stat().st_size <= _MAX_IMAGE_BYTES:
                data = candidate.read_bytes()
                if _valid_image(data):
                    return data
        except Exception as error:  # noqa: BLE001 -- 本地图片失败后仅回退已完成的文字报告。
            _LOG.warning("calendar report local render failed: %s", type(error).__name__)
        return None


def _atomic_write(path: Path, data: bytes) -> None:
    temporary = path.with_name(path.name + ".tmp-" + uuid.uuid4().hex)
    try:
        temporary.write_bytes(data)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


async def request_group_reports(
    context: Any, event: Any, *, date_text: str = "",
) -> GroupReportBatch:
    """任何普通群成员均可请求本群双日报，或指定单日的群报。"""
    if _identity(event) is None:
        return GroupReportBatch("denied", message="请在已启用群报的本群中请求群报。")
    loaded = _loaded_plugin(context, _PLUGIN, event)
    if loaded is None or getattr(loaded[0], "analysis_service", None) is None:
        return GroupReportBatch("unavailable", message="本群的日报插件尚未准备好。")
    plugin = loaded[0]
    reports = getattr(plugin, "_qqchat_calendar_reports", None)
    if not isinstance(reports, GroupReportService) or reports.service is not plugin.analysis_service:
        reports = GroupReportService(plugin.analysis_service)
        plugin._qqchat_calendar_reports = reports
    started = time.monotonic()
    batch = await reports.get_reports(event, date_text=date_text)
    _LOG.info(
        "calendar group reports: status=%s items=%s duration=%.2fs",
        batch.status, len(batch.items), time.monotonic() - started,
    )
    return batch
