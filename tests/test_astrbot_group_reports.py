from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from PIL import Image

from app import astrbot_group_reports as reports
from app.astrbot_group_reports import (
    SHANGHAI,
    GroupReportService,
    calendar_report_windows,
    messages_in_window,
    request_group_reports,
)

ALLOWED_REPORT_GROUPS = frozenset({"1000000001", "1000000002", "1000000003"})


@pytest.fixture(autouse=True)
def report_groups_for_tests(monkeypatch):
    monkeypatch.setattr(reports, "ALLOWED_REPORT_GROUPS", ALLOWED_REPORT_GROUPS)


def test_report_groups_load_local_allowlist_and_fail_closed(tmp_path):
    path = tmp_path / "groups.json"
    assert reports._load_report_groups(path) == frozenset()
    path.write_text("not json", encoding="utf-8")
    assert reports._load_report_groups(path) == frozenset()
    path.write_text(json.dumps(["1000000001", "invalid", 123]), encoding="utf-8")
    assert reports._load_report_groups(path) == frozenset({"1000000001"})


GROUP = "1000000001"
NOW = datetime(2026, 10, 26, 12, 35, 47, tzinfo=SHANGHAI)


def event(group=GROUP, *, sender="ordinary-member", platform="local-qq"):
    return SimpleNamespace(
        get_group_id=lambda: group, get_sender_id=lambda: sender, get_self_id=lambda: "bot",
        get_platform_id=lambda: platform, unified_msg_origin=f"{platform}:GroupMessage:{group}",
        message_str="给我看看昨天和今天的群报", role="member",
    )


def message(timestamp, text="真实讨论", *, group=GROUP, sender="member", mid=None):
    return SimpleNamespace(
        timestamp=timestamp, group_id=group, sender_id=sender, text_content=text,
        message_id=mid or f"{timestamp}-{text}",
    )


def source_messages():
    yesterday, today = calendar_report_windows(NOW)
    return [
        message(yesterday.start_ts - 1, "前天不能入报"),
        message(yesterday.start_ts, "昨日零点"),
        message(yesterday.end_ts - 1, "昨日最后一秒"),
        message(today.start_ts, "今天零点"),
        message(today.end_ts - 1, "最新完整分钟"),
        message(today.end_ts, "当前分钟不能入报"),
        message(today.end_ts + 5, "当前分钟另条"),
        message(today.end_ts - 2, "别群秘密", group="1000000003"),
        message(today.end_ts - 3, "机器人不能灌入", sender="bot"),
        message(today.end_ts - 4, "/管理命令"),
    ]


class FakeTemplates:
    def __init__(self, calls):
        self.calls = calls

    def render_template(self, name, **kwargs):
        self.calls.append((name, kwargs))
        return f"<html><body>{kwargs['current_date']} {kwargs['current_datetime']}</body></html>"


class FakeGenerator:
    def __init__(self):
        self.calls = []
        self.html_templates = FakeTemplates(self.calls)

    async def generate_image_report(self, analysis, group, html_render_func, **kwargs):
        content = self.html_templates.render_template(
            "image_template.html", current_date="WRONG DATE", current_datetime="WRONG TIME",
        )
        self.calls.append((group, analysis, kwargs))
        result = await html_render_func(content, {}, False, {"type": "jpeg", "quality": 80})
        return result, content

    def generate_text_report(self, analysis):
        return "🎯 群聊日常分析报告\n📅 2026年10月26日\n话题：" + analysis["topics"][0]


class FakeService:
    def __init__(self, rows=None):
        self.fetch = AsyncMock(return_value=rows if rows is not None else source_messages())
        self.adapter = SimpleNamespace(
            bot_self_ids=["bot"], is_group_muted=AsyncMock(return_value=False),
            fetch_messages=self.fetch, get_user_avatar_url=AsyncMock(return_value=None),
            get_member_info=AsyncMock(return_value=None),
        )
        self.bot_manager = SimpleNamespace(
            update_from_event=lambda _event: None,
            get_adapter=lambda platform: self.adapter if platform == "local-qq" else None,
        )
        self.config_manager = SimpleNamespace(
            get_bot_self_ids=lambda: ["bot"], get_max_user_titles=lambda: 8,
            get_report_template=lambda: "HatsuneMiku",
        )
        self.analysis_domain_service = SimpleNamespace(get_top_users=lambda *args, **kwargs: [])
        self.statistics_service = SimpleNamespace(
            _convert_to_legacy_dict=lambda rows: [
                {"time": row.timestamp, "text": row.text_content} for row in rows
            ],
        )
        self.report_generator = FakeGenerator()
        self.cleaned = []
        self.analyzed = []
        self.running = False
        self.fail_analysis = False
        self.entered = None
        self.release = None
        # If any of these are used, the tests fail: never use sending/history/comic entry points.
        self.execute_daily_analysis = AsyncMock(side_effect=AssertionError("daily persistence"))
        self.history_manager = SimpleNamespace(save_analysis=AsyncMock(side_effect=AssertionError))
        self.comic_handler = SimpleNamespace(
            try_trigger_comic_generation=AsyncMock(side_effect=AssertionError),
        )
        self.adapter.send_image = AsyncMock(side_effect=AssertionError("QQ sending"))
        self.html_render = AsyncMock(side_effect=AssertionError("remote render"))

    def is_group_running(self, group, task_type):
        return self.running

    @asynccontextmanager
    async def group_lock(self, group, task_type):
        assert group in ALLOWED_REPORT_GROUPS and task_type == "daily"
        yield

    async def _clean_daily_messages(self, pipeline, trace, adapter, rows, group, platform):
        self.cleaned.append(rows)
        return SimpleNamespace(
            messages=[row for row in rows if not row.text_content.startswith("/")],
        )

    async def _calculate_daily_statistics(self, pipeline, rows, bots):
        return SimpleNamespace(message_count=len(rows), participant_count=len({row.sender_id for row in rows})), {}

    def _resolve_daily_analysis_sections(self, requested):
        assert requested is None
        return "native-configured-sections"

    async def _run_llm_analysis_stage(self, pipeline, group, stage, rows, *args):
        assert stage == "calendar_group_report"
        self.analyzed.append(rows)
        if self.entered:
            self.entered.set()
            await self.release.wait()
        return SimpleNamespace(
            failed=self.fail_analysis, topics=["真实话题"], user_titles=[], golden_quotes=[],
            token_usage=SimpleNamespace(total_tokens=1), chat_quality_review=None,
        )


@pytest.fixture
def render(monkeypatch, tmp_path):
    calls = []
    buffer = BytesIO()
    Image.new("RGB", (16, 16), "white").save(buffer, format="PNG")
    image = buffer.getvalue()

    async def local(content, options, directory):
        calls.append((content, options))
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"fake-render-{len(calls)}.png"
        path.write_bytes(image)
        return str(path)

    monkeypatch.setattr(reports.report_renderer, "render_to_file", local)
    return calls


def service(tmp_path, rows=None, *, clock=None, **kwargs):
    native = FakeService(rows)
    report = GroupReportService(
        native, cache_dir=tmp_path / "cache", clock=clock or (lambda: NOW), **kwargs,
    )
    return native, report


def test_calendar_boundaries_are_shanghai_dates_and_previous_complete_minute():
    yesterday, today = calendar_report_windows(NOW.astimezone(UTC))
    assert yesterday.date == "2026-10-25" and today.date == "2026-10-26"
    assert yesterday.end_ts == today.start_ts
    assert yesterday.end_ts - yesterday.start_ts == 86_400
    assert datetime.fromtimestamp(today.end_ts, SHANGHAI).strftime("%H:%M:%S") == "12:35:00"
    assert "12:34:59" in today.header and "23:59:59" in yesterday.header
    first, second = calendar_report_windows(
        datetime(2026, 1, 1, 0, 0, 42, tzinfo=SHANGHAI).replace(tzinfo=None),
    )
    assert first.date == "2025-12-31" and second.start_ts == second.end_ts


def test_selected_day_is_exact_shanghai_day_after_midnight():
    now = datetime(2026, 10, 28, 0, 2, 30, tzinfo=SHANGHAI)
    yesterday, = calendar_report_windows(now, date_text="昨天")
    exact, = calendar_report_windows(now.astimezone(UTC), date_text="2026-10-27")
    assert yesterday == exact
    assert exact.date == "2026-10-27" and exact.end_ts - exact.start_ts == 86400
    assert "23:59:59" in exact.header
    today, = calendar_report_windows(now, date_text="今天")
    assert today.date == "2026-10-28" and "00:01:59" in today.header


@pytest.mark.parametrize("selector", ["2026-10-29", "2026-09-01", "2026-02-30", "全群", "2026-1-1"])
def test_bad_or_unbounded_report_dates_are_rejected(selector):
    with pytest.raises(ValueError):
        calendar_report_windows(NOW, date_text=selector)


async def test_selected_day_only_builds_its_own_report_and_reuses_cache(tmp_path, render):
    native, report = service(tmp_path)
    batch = await report.get_reports(event(), date_text="昨天")
    assert len(batch.items) == 1 and batch.items[0].window.date == "2026-10-25"
    assert [[row["text"] for row in rows] for rows in native.analyzed] == [["昨日零点", "昨日最后一秒"]]
    again = await report.get_reports(event(), date_text="2026-10-25")
    assert again.items[0].reused and native.fetch.await_count == 1
    assert native.adapter.send_image.await_count == 0


async def test_older_single_day_fetch_uses_selected_boundary(tmp_path, render):
    window, = calendar_report_windows(NOW, date_text="2026-10-21")
    native, report = service(tmp_path, [message(window.start_ts, "那天的讨论")])
    batch = await report.get_reports(event(), date_text="2026-10-21")
    assert len(batch.items) == 1 and batch.items[0].message_count == 1
    assert batch.items[0].window.kind == "daily"
    assert native.fetch.await_args.kwargs["days"] == 6
    assert native.fetch.await_args.kwargs["since_ts"] == window.start_ts
    assert "今日群报" not in batch.items[0].header


async def test_invalid_selected_date_never_fetches_or_generates(tmp_path):
    native, report = service(tmp_path)
    batch = await report.get_reports(event(), date_text="2099-01-01")
    assert batch.status == "invalid_date" and not batch.items
    native.fetch.assert_not_awaited()


async def test_nontext_only_report_keeps_real_counts_without_llm_or_render(tmp_path, render):
    window, = calendar_report_windows(NOW, date_text="昨天")
    native, report = service(tmp_path, [
        message(window.start_ts + 1, "", sender="member-a"),
        message(window.start_ts + 2, "  ", sender="member-b"),
        message(window.start_ts + 3, "", sender="member-a"),
    ])
    native._run_llm_analysis_stage = AsyncMock(side_effect=AssertionError("no text for LLM"))
    native.analysis_domain_service.get_top_users = lambda *args, **kwargs: pytest.fail("no member profiles without text")
    batch = await report.get_reports(event(), date_text="昨天")
    item, = batch.items
    assert item.status == "text" and item.message_count == 3 and item.image_path is None
    assert "3 条非文字消息" in item.text and "2 人参与" in item.text
    assert "没有可用于总结的文字" in item.text
    native._run_llm_analysis_stage.assert_not_awaited()
    assert render == []
    cached = await report.get_reports(event(), date_text="昨天")
    assert cached.items[0].reused and cached.items[0].text == item.text
    assert native.fetch.await_count == 1


async def test_old_report_cache_is_rebuilt_after_zero_text_fix(tmp_path, render):
    window, = calendar_report_windows(NOW, date_text="昨天")
    native, report = service(tmp_path, [message(window.start_ts + 1, "")])
    batch = await report.get_reports(event(), date_text="昨天")
    path = report._base(GROUP, "local-qq", window).with_suffix(".json")
    old = json.loads(path.read_text(encoding="utf-8"))
    old.update(version=1, text="旧缓存的无依据评价")
    path.write_text(json.dumps(old, ensure_ascii=False), encoding="utf-8")
    renewed = await report.get_reports(event(), date_text="昨天")
    assert not renewed.items[0].reused and renewed.items[0].text == batch.items[0].text
    assert native.fetch.await_count == 2
    assert json.loads(path.read_text(encoding="utf-8"))["version"] == reports._CACHE_VERSION


@pytest.mark.parametrize("mid", ["2134567890", 2134567890, "-1234567"])
async def test_report_history_starts_at_real_request_and_excludes_current_minute(tmp_path, render, mid):
    native, report = service(tmp_path)
    request = event()
    request.message_obj = SimpleNamespace(message_id=mid)
    batch = await report.get_reports(request)
    assert native.fetch.await_args.kwargs["before_id"] == str(mid)
    assert [item.message_count for item in batch.items] == [2, 2]
    assert not any("当前分钟" in row["text"] for rows in native.analyzed for row in rows)


@pytest.mark.parametrize("mid", [None, "", "abc", "1;DROP", "0", 0, True, "9" * 40])
async def test_invalid_request_message_id_uses_compatible_history_fallback(tmp_path, render, mid):
    native, report = service(tmp_path)
    request = event()
    request.message_obj = SimpleNamespace(message_id=mid)
    await report.get_reports(request, date_text="昨天")
    assert native.fetch.await_args.kwargs["before_id"] is None


def test_half_open_filter_rejects_other_group_and_accepts_legacy_time():
    yesterday, today = calendar_report_windows(NOW)
    raw = source_messages() + [{"time": today.end_ts - 1, "group_id": GROUP}]
    rows = messages_in_window(raw, yesterday, GROUP)
    assert [row.text_content for row in rows] == ["昨日零点", "昨日最后一秒"]
    rows = messages_in_window(raw, today, GROUP)
    assert raw[-1] in rows
    assert all(reports._timestamp(row) < today.end_ts for row in rows)
    assert not any(getattr(row, "text_content", "") == "别群秘密" for row in rows)


async def test_ordinary_member_gets_two_strict_windows_native_template_local_images(tmp_path, render):
    native, report = service(tmp_path)
    batch = await report.get_reports(event())
    assert batch.status == "ready" and len(batch.items) == 2
    assert [item.status for item in batch.items] == ["ready", "ready"]
    assert [item.message_count for item in batch.items] == [2, 2]
    assert [[row["text"] for row in rows] for rows in native.analyzed] == [
        ["昨日零点", "昨日最后一秒"], ["今天零点", "最新完整分钟"],
    ]
    params = native.fetch.await_args.kwargs
    assert params["group_id"] == GROUP and params["days"] == 2
    assert params["since_ts"] == batch.items[0].window.start_ts and params["before_id"] is None
    assert all(Path(item.image_path).is_file() for item in batch.items)
    assert "昨日群报" in render[0][0] and "2026-10-25" in render[0][0]
    assert "12:34:59" in render[1][0] and "WRONG DATE" not in render[1][0]
    assert render[0][1]["type"] == "png" and "quality" not in render[0][1]
    assert native.report_generator.html_templates.__class__ is FakeTemplates
    assert native.execute_daily_analysis.await_count == 0
    assert native.adapter.send_image.await_count == 0
    assert native.html_render.await_count == 0
    assert native.history_manager.save_analysis.await_count == 0
    assert native.comic_handler.try_trigger_comic_generation.await_count == 0
    cache_text = " ".join(path.read_text(encoding="utf-8") for path in tmp_path.rglob("*.json"))
    assert "昨日零点" not in cache_text and "ordinary-member" not in cache_text


async def test_same_minute_and_service_restart_reuse_both_images(tmp_path, render):
    native, report = service(tmp_path)
    original = await report.get_reports(event())
    again = await report.get_reports(event())
    assert all(item.reused for item in again.items)
    assert [item.image_path for item in original.items] == [item.image_path for item in again.items]
    restarted = GroupReportService(native, cache_dir=report.cache_dir, clock=lambda: NOW)
    assert all(item.reused for item in (await restarted.get_reports(event())).items)
    assert native.fetch.await_count == 1 and len(native.analyzed) == 2


async def test_next_minute_never_returns_an_old_cutoff_but_reuses_yesterday(tmp_path, render):
    clock = [NOW]
    native, report = service(tmp_path, clock=lambda: clock[0])
    first = await report.get_reports(event())
    clock[0] += timedelta(minutes=1)
    latest = await report.get_reports(event())
    assert latest.items[0].reused and not latest.items[1].reused
    assert latest.items[1].window.end_ts == first.items[1].window.end_ts + 60
    assert "12:35:59" in latest.items[1].header
    assert native.fetch.await_count == 2 and len(native.analyzed) == 3
    assert native.fetch.await_args.kwargs["since_ts"] == latest.items[1].window.start_ts
    assert latest.items[1].image_path != first.items[1].image_path


async def test_new_calendar_day_rebuilds_yesterday_as_full_day_not_old_partial_today(tmp_path, render):
    clock = [NOW]
    _native, report = service(tmp_path, clock=lambda: clock[0])
    await report.get_reports(event())
    clock[0] = NOW + timedelta(days=1)
    new = await report.get_reports(event())
    assert not new.items[0].reused
    assert new.items[0].window.date == "2026-10-26"
    assert "23:59:59" in new.items[0].header


@pytest.mark.parametrize("group", sorted(ALLOWED_REPORT_GROUPS))
async def test_only_three_fixed_groups_are_supported(tmp_path, render, group):
    yesterday, today = calendar_report_windows(NOW)
    native, report = service(tmp_path, [
        message(yesterday.start_ts, group=group), message(today.start_ts, group=group),
    ])
    assert (await report.get_reports(event(group))).status == "ready"
    assert native.fetch.await_args.kwargs["group_id"] == group


@pytest.mark.parametrize("change", ["other-group", "private", "origin", "bot", "platform", "missing-sender"])
async def test_untrusted_or_cross_group_event_cannot_fetch_any_reports(tmp_path, change):
    native, report = service(tmp_path)
    request = event()
    if change == "other-group":
        request = event("999999999")
    elif change == "private":
        request.get_group_id = lambda: ""
    elif change == "origin":
        request.unified_msg_origin = "local-qq:GroupMessage:1000000003"
    elif change == "bot":
        request.get_sender_id = lambda: "bot"
    elif change == "missing-sender":
        request.get_sender_id = lambda: ""
    else:
        request.get_platform_id = lambda: "other-bot"
    assert (await report.get_reports(request)).status == "denied"
    assert native.fetch.await_count == 0 and not native.analyzed


async def test_empty_periods_never_call_analysis_or_invent_reports(tmp_path, render):
    native, report = service(tmp_path, [])
    batch = await report.get_reports(event())
    assert [item.status for item in batch.items] == ["no_messages", "no_messages"]
    assert all(item.image_path is None and "没有" in item.text for item in batch.items)
    assert not native.analyzed and not render


async def test_only_today_messages_produce_only_one_analysis(tmp_path, render):
    today = calendar_report_windows(NOW)[1]
    native, report = service(tmp_path, [message(today.start_ts, "仅今日真人")])
    batch = await report.get_reports(event())
    assert [item.status for item in batch.items] == ["no_messages", "ready"]
    assert len(native.analyzed) == 1


async def test_concurrent_duplicate_is_busy_and_does_not_duplicate_model_requests(tmp_path, render):
    native, report = service(tmp_path)
    native.entered, native.release = asyncio.Event(), asyncio.Event()
    task = asyncio.create_task(report.get_reports(event()))
    await native.entered.wait()
    duplicate = await report.get_reports(event())
    assert duplicate.status == "busy" and native.fetch.await_count == 1
    native.release.set()
    assert (await task).status == "ready"
    assert len(native.analyzed) == 2 and not report._active


async def test_native_scheduled_daily_already_running_blocks_reports(tmp_path):
    native, report = service(tmp_path)
    native.running = True
    assert (await report.get_reports(event())).status == "busy"
    assert not native.fetch.called


async def test_local_renderer_failure_falls_back_to_text_with_correct_external_date(tmp_path, monkeypatch):
    async def fail(*args, **kwargs):
        raise TimeoutError("local only")

    monkeypatch.setattr(reports.report_renderer, "render_to_file", fail)
    native, report = service(tmp_path)
    batch = await report.get_reports(event())
    assert all(item.status == "text" for item in batch.items)
    assert "2026-10-25" in batch.items[0].header
    assert "2026年10月26日" not in batch.items[0].text
    assert native.html_render.await_count == 0


async def test_failed_analysis_is_cached_briefly_and_never_rendered(tmp_path, render):
    native, report = service(tmp_path)
    native.fail_analysis = True
    first = await report.get_reports(event())
    assert all(item.status == "error" for item in first.items) and not render
    again = await report.get_reports(event())
    assert all(item.reused for item in again.items) and len(native.analyzed) == 2


async def test_cancelled_analysis_releases_group_without_caching_or_sending(tmp_path, render):
    native, report = service(tmp_path)
    native.entered, native.release = asyncio.Event(), asyncio.Event()
    task = asyncio.create_task(report.get_reports(event()))
    await native.entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not report._active and not list(tmp_path.rglob("*.json"))


async def test_unrelated_generated_path_cannot_be_read_or_cached(tmp_path, render, monkeypatch):
    secret = tmp_path / "outside.png"
    secret.write_bytes(b"not an image")

    async def malicious(self, *args, **kwargs):
        return str(secret), ""

    monkeypatch.setattr(FakeGenerator, "generate_image_report", malicious)
    _, report = service(tmp_path)
    assert all(item.status == "text" for item in (await report.get_reports(event())).items)


async def test_corrupt_image_cache_is_rebuilt_instead_of_sent(tmp_path, render):
    native, report = service(tmp_path)
    first = await report.get_reports(event())
    Path(first.items[0].image_path).write_bytes(b"corrupted")
    second = await report.get_reports(event())
    assert not second.items[0].reused and second.items[1].reused
    assert len(native.analyzed) == 3


async def test_loaded_plugin_helper_uses_activated_allowed_plugin_without_admin_check(tmp_path, render):
    native, instance = service(tmp_path)
    plugin = SimpleNamespace(analysis_service=native, _qqchat_calendar_reports=instance)
    context = SimpleNamespace(get_all_stars=lambda: [
        SimpleNamespace(name=reports._PLUGIN, activated=True, star_cls=plugin),
    ])
    batch = await request_group_reports(context, event())
    assert len(batch.items) == 2 and plugin._qqchat_calendar_reports is instance
    assert (await request_group_reports(context, event())).items[0].reused


async def test_unloaded_or_event_disabled_plugin_is_not_used(tmp_path):
    native, _instance = service(tmp_path)
    context = SimpleNamespace(get_all_stars=lambda: [
        SimpleNamespace(name=reports._PLUGIN, activated=False,
                        star_cls=SimpleNamespace(analysis_service=native)),
    ])
    assert (await request_group_reports(context, event())).status == "unavailable"
    request = event()
    request.plugins_name = ["some-other-plugin"]
    assert (await request_group_reports(context, request)).status == "unavailable"
    assert not native.fetch.called


def test_cache_manifest_never_can_change_its_group_or_cutoff(tmp_path, render):
    _, report = service(tmp_path)
    window = calendar_report_windows(NOW)[1]
    base = report._base(GROUP, "local-qq", window)
    base.parent.mkdir(parents=True)
    data = {
        "version": 1, "group": "1000000003", "platform": "local-qq", "kind": "today",
        "start_ts": window.start_ts, "end_ts": window.end_ts, "created_ts": int(NOW.timestamp()),
        "status": "no_messages",
    }
    base.with_suffix(".json").write_text(json.dumps(data))
    assert report._cached(GROUP, "local-qq", window, int(NOW.timestamp())) is None
    data.update(group=GROUP, end_ts=window.end_ts - 60)
    base.with_suffix(".json").write_text(json.dumps(data))
    assert report._cached(GROUP, "local-qq", window, int(NOW.timestamp())) is None


async def test_installed_daily_service_contract_with_real_unified_messages_offline(
    monkeypatch, tmp_path, render,
):
    import importlib
    import sys
    from types import ModuleType

    from test_astrbot_native_onebot import _load_astrbot_types

    root = Path(__file__).resolve().parents[1]
    plugin_root = root / "data/astrbot/instance/data/plugins" / reports._PLUGIN
    if not plugin_root.is_dir():
        pytest.skip("installed daily plugin unavailable")
    monkeypatch.setenv("ASTRBOT_ROOT", str(tmp_path / "isolated-astrbot"))
    monkeypatch.chdir(tmp_path)
    _load_astrbot_types(root, monkeypatch)
    prefix = "_calendar_reports_contract"
    package = ModuleType(prefix)
    package.__path__ = [str(plugin_root)]
    monkeypatch.setitem(sys.modules, prefix, package)
    def load(name):
        return importlib.import_module(prefix + ".src." + name)
    application = load("application.services.analysis_application_service")
    values = load("domain.value_objects")
    messages = load("domain.value_objects.unified_message")
    statistics = load("domain.services.statistics_service")
    domain = load("domain.services.analysis_domain_service")
    guard = load("application.services.task_guard")
    config = load("infrastructure.config.config_manager")
    generators = load("infrastructure.reporting.generators")
    fake = FakeService()
    native = application.AnalysisApplicationService.__new__(application.AnalysisApplicationService)
    native.config_manager = SimpleNamespace(
        get_bot_self_ids=lambda: ["bot"], get_filter_bot_messages=lambda: True,
        get_max_user_titles=lambda: 8, get_report_template=lambda: "HatsuneMiku",
        get_topic_analysis_enabled=lambda: True, get_user_title_analysis_enabled=lambda: False,
        get_golden_quote_analysis_enabled=lambda: False, get_chat_quality_analysis_enabled=lambda: False,
    )
    native._task_guard = guard.TaskGuard(1)
    native.bot_manager = fake.bot_manager
    native.statistics_service = statistics.StatisticsService(SimpleNamespace(
        generate_activity_visualization=lambda rows: values.ActivityVisualization(),
    ))
    native.analysis_domain_service = domain.AnalysisDomainService()
    template_config = config.ConfigManager.__new__(config.ConfigManager)
    template_config.config = {"basic": {"report_template": "HatsuneMiku"}}
    native.report_generator = generators.ReportGenerator(template_config, tmp_path / "reports")
    native.llm_analyzer = SimpleNamespace(analyze_all_concurrent=AsyncMock(return_value=(
        [values.SummaryTopic(topic="真实话题", contributors=[], detail="离线测试")], [], [],
        values.TokenUsage(), None,
    )))
    converted = [
        messages.UnifiedMessage(
            message_id=row.message_id, sender_id=row.sender_id, sender_name="测试群友",
            group_id=row.group_id, text_content=row.text_content, timestamp=row.timestamp,
            contents=(messages.MessageContent(type=messages.MessageContentType.TEXT,
                                              text=row.text_content),),
        ) for row in source_messages()
    ]
    fake.fetch.return_value = converted
    report = GroupReportService(native, cache_dir=tmp_path / "cache", clock=lambda: NOW)
    try:
        batch = await report.get_reports(event())
    finally:
        await native.report_generator.close()
    assert [item.status for item in batch.items] == ["ready", "ready"]
    assert [item.message_count for item in batch.items] == [2, 2]
    calls = native.llm_analyzer.analyze_all_concurrent.await_args_list
    assert len(calls) == 2
    yesterday, today = calendar_report_windows(NOW)
    assert all(yesterday.start_ts <= row["time"] < yesterday.end_ts for row in calls[0].args[0])
    assert all(today.start_ts <= row["time"] < today.end_ts for row in calls[1].args[0])
    assert fake.adapter.send_image.await_count == 0
    assert "昨日群报" in render[0][0] and "2026-10-25" in render[0][0]
    assert "12:34:59" in render[1][0]
