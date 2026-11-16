from __future__ import annotations

import asyncio
import base64
import importlib.util
import json
import logging
import sys
import types
from contextlib import nullcontext
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app import astrbot_comic as comic
from app.astrbot_vision import prepare_group_image_context

TOPICS = [
    {
        "topic": "游戏爆率讨论",
        "detail": "[123456789] 发现小号开出的奖励比大号好，大家讨论随机运气。",
    },
    {"topic": "语音翻唱服务", "detail": "群友请求助手用角色音色唱歌，并讨论翻唱效果。"},
    {"topic": "炉石卡组讨论", "detail": "机器人推荐中混进了狂野牌，群友指出标准模式不该这样用。"},
]
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a2ioAAAAASUVORK5CYII="
)
VENDOR = (
    Path(__file__).resolve().parents[1]
    / "data/astrbot/instance/data/plugins/astrbot_plugin_qq_group_daily_analysis/src"
)


class Event:
    unified_msg_origin = "qq:GroupMessage:42"

    def __init__(self, parts=(), *, group="42", self_id="bot"):
        self.group = group
        self.self_id = self_id
        self.message_obj = SimpleNamespace(message=list(parts), self_id=self_id)
        self.extras = {}

    def get_group_id(self):
        return self.group

    def get_self_id(self):
        return self.self_id

    def get_platform_id(self):
        return "qq"

    def get_extra(self, key, default=None):
        return self.extras.get(key, default)

    def set_extra(self, key, value):
        self.extras[key] = value


def _request():
    return SimpleNamespace(system_prompt="persona", extra_user_content_parts=[], image_urls=[])


def _saved(tmp_path, *, group="42", name="one", topics=TOPICS):
    image = tmp_path / f"comic_{group}_{name}.png"
    image.write_bytes(PNG)
    record = comic.write_generated_comic_sidecar(
        image, group, topics, trace_id=name, platform_id="qq"
    )
    return image, record


def _reply(*, sender="bot", text="", images=()):
    return SimpleNamespace(
        type="Reply", sender_id=sender, id="message-1", message_str=text, chain=list(images)
    )


def test_bad_title_only_storyboard_gets_real_interactive_three_panel_fallback():
    plan = comic.build_comic_plan(TOPICS, "抽卡爆率高？\n翻唱服务好\n卡组推荐狂野？", "丛雨")
    prompt = plan["drawing_prompt"]
    assert plan["scene_fallback"]
    assert all(f"第{i}格：" in prompt for i in (1, 2, 3))
    assert "第4格" not in prompt
    assert "麦克风" in prompt and "礼物盒" in prompt and "重新分开" in prompt
    assert "小号" in plan["source_summary"] and "狂野牌" in plan["source_summary"]
    assert "123456789" not in json.dumps(plan, ensure_ascii=False)
    assert len(prompt) < 1024
    assert "不出现任何文字" in prompt and "不画手机电脑屏幕" in prompt


def test_text_heavy_scene_is_not_forwarded_to_free_drawing_model():
    scene = "三格漫画。" + "第1格：角色举手微笑聊天，气泡写指定中文标题和台词。" * 3
    plan = comic.build_comic_plan(TOPICS, scene)
    assert plan["scene_fallback"]
    assert "指定中文标题" not in plan["drawing_prompt"]


def test_proper_three_panel_actions_can_be_kept_without_another_model_call():
    scene = "\n".join(
        f"第{i}格：助手和两位朋友围坐在木桌边，朋友举手分享，助手认真倾听后笑着递出小礼物，人物动作清楚。"
        for i in (1, 2, 3)
    )
    assert not comic.build_comic_plan(TOPICS, scene)["scene_fallback"]


def test_caption_and_matching_sidecars_keep_intent_separate_from_actual_pixels(tmp_path):
    image, record = _saved(tmp_path)
    assert image.with_suffix(".json").is_file() and image.with_suffix(".txt").is_file()
    assert "群聊摘要" in record["caption"] and "创作意图" in record["caption"]
    assert "不能据此认定图片已准确画出" in record["caption"]
    assert record["render_quality"] == "unverified" and not record["delivery_confirmed"]
    comic.mark_generated_comic_delivered(image, "42")
    assert json.loads(image.with_suffix(".json").read_text(encoding="utf-8"))["delivery_confirmed"]


def test_same_group_actual_bot_reply_can_match_caption_marker(tmp_path):
    _, record = _saved(tmp_path)
    event = Event([_reply(text=record["caption"])])
    result = comic.lookup_generated_comic_context(event, _request(), "42", reports_dir=tmp_path)
    assert result["source_marker"] == record["source_marker"]
    assert result["matched_by"] == "caption_marker"


@pytest.mark.parametrize(
    "mode",
    ["other_sender", "other_group", "plain_user_marker", "wrong_group_argument", "other_platform"],
)
def test_untrusted_or_cross_group_reference_cannot_claim_comic_source(tmp_path, mode):
    _, record = _saved(tmp_path)
    event = Event([_reply(text=record["caption"])])
    group = "42"
    if mode == "other_sender":
        event = Event([_reply(sender="human", text=record["caption"])])
    elif mode == "other_group":
        event = Event([_reply(text=record["caption"])], group="43")
        group = "43"
    elif mode == "plain_user_marker":
        event = Event([SimpleNamespace(type="Plain", text=record["caption"])])
    elif mode == "wrong_group_argument":
        group = "43"
    elif mode == "other_platform":
        event.get_platform_id = lambda: "wechat"
    assert (
        comic.lookup_generated_comic_context(event, _request(), group, reports_dir=tmp_path) is None
    )


@pytest.mark.parametrize("form", ["path", "qq_md5"])
def test_real_quoted_bot_attachment_hash_matches_without_recent_image_guess(tmp_path, form):
    image, record = _saved(tmp_path)
    value = str(image) if form == "path" else record["image_md5"] + ".jpg"
    event = Event([_reply(images=[SimpleNamespace(type="Image", file=value)])])
    result = comic.lookup_generated_comic_context(event, _request(), "42", reports_dir=tmp_path)
    assert result["matched_by"] == "attachment_hash"


def test_ambiguous_same_image_hash_does_not_pick_latest_caption(tmp_path):
    _, first = _saved(tmp_path, name="first")
    _saved(tmp_path, name="second", topics=[{"topic": "不同话题", "detail": "来源不相同"}])
    event = Event(
        [_reply(images=[SimpleNamespace(type="Image", file=first["image_md5"] + ".jpg")])]
    )
    assert (
        comic.lookup_generated_comic_context(event, _request(), "42", reports_dir=tmp_path) is None
    )
    event.message_obj.message[0].message_str = first["caption"]
    assert (
        comic.lookup_generated_comic_context(event, _request(), "42", reports_dir=tmp_path)[
            "trace_id"
        ]
        == "first"
    )


def test_modified_image_or_sidecar_path_escape_is_rejected(tmp_path):
    image, record = _saved(tmp_path)
    event = Event([_reply(text=record["caption"])])
    image.write_bytes(PNG + b"changed")
    assert (
        comic.lookup_generated_comic_context(event, _request(), "42", reports_dir=tmp_path) is None
    )
    image.write_bytes(PNG)
    record["image_filename"] = "../outside.png"
    image.with_suffix(".json").write_text(json.dumps(record), encoding="utf-8")
    assert (
        comic.lookup_generated_comic_context(event, _request(), "42", reports_dir=tmp_path) is None
    )


def test_legacy_trace_recovery_records_source_and_admits_bad_render(tmp_path):
    image = tmp_path / "comic_42_legacy-trace.png"
    image.write_bytes(PNG)
    spans = {
        "COMIC_STORYBOARD": {
            "character_name": "助手",
            "prompts": {
                "comic_storyboards": {
                    "prompt": "材料\n1. 话题: 爆率讨论\n   详情: [123456789] 分享小号运气。\n2. 话题: 翻唱\n   详情: 群友请求唱歌。"
                }
            },
        },
        "COMIC_DRAWING": {"prompts": {"comic_drawing": {"prompt": "抽卡爆率高\n翻唱服务好"}}},
    }
    record = comic.recover_generated_comic_sidecar(image, "42", "legacy-trace", spans)
    assert record["render_quality"] == "misaligned"
    assert record["source_kind"] == "recovered_trace"
    assert record["drawing_prompt"] == "抽卡爆率高\n翻唱服务好"
    assert "没有准确表现群聊" in record["caption"]
    assert "123456789" not in record["caption"]
    assert "第1格" not in record["drawing_prompt"]


def test_verified_source_can_explain_intent_when_vision_is_unavailable(tmp_path, monkeypatch):
    _, record = _saved(tmp_path)
    monkeypatch.setattr(comic, "REPORTS_DIR", tmp_path)
    event = Event([_reply(text=record["caption"])])
    req = _request()
    req.image_urls = ["current-quote.jpg"]
    source_text = SimpleNamespace(type="text", text="来源文字")
    req.extra_user_content_parts = [SimpleNamespace(type="image_url"), source_text]
    evidence = prepare_group_image_context(event, req, text_only=True)
    assert evidence.status == "source_only"
    assert req.image_urls == [] and req.extra_user_content_parts == [source_text]
    assert event.get_extra(comic.COMIC_SOURCE_EXTRA)["source_summary"] == record["source_summary"]
    assert event.get_extra(comic.COMIC_SOURCE_EXTRA)["type"] == "verified_group_comic_source_v1"
    assert event.get_extra(comic.COMIC_SOURCE_EXTRA)["verified"] is True
    assert event.get_extra(comic.COMIC_SOURCE_EXTRA)["group_id"] == "42"
    assert "bot_sender_id" not in req.system_prompt
    assert "不是画面识别结果" in req.system_prompt
    assert "不必因为视觉未识别就拒绝解释" in req.system_prompt


def _load_vendor(monkeypatch):
    if not (VENDOR / "application/services/comic_application_service.py").is_file():
        pytest.skip("需要已安装的群分析插件以验证其漫画服务")
    prefix = "_test_comic_bridge"
    renderer = types.ModuleType("app.services.readable_group_comic")
    renderer.render_readable_comic = AsyncMock(
        side_effect=RuntimeError("local rendering unavailable in this isolated test")
    )
    monkeypatch.setitem(sys.modules, "app.services.readable_group_comic", renderer)
    for suffix in (
        "",
        ".application",
        ".application.services",
        ".application.handlers",
        ".domain",
        ".domain.value_objects",
        ".infrastructure",
        ".infrastructure.drawing",
        ".shared",
        ".utils",
    ):
        module = types.ModuleType(prefix + suffix)
        module.__path__ = []
        monkeypatch.setitem(sys.modules, prefix + suffix, module)

    @dataclass
    class Storyboard:
        scene: str

    @dataclass
    class Topic:
        topic: str
        detail: str

    class Trace:
        active = None

        def __init__(self):
            self.trace_id = "trace-test"
            self.metadata = {}
            self.status = "running"
            self.finishes = []

        @classmethod
        def current(cls):
            return cls.active

        @staticmethod
        def generate(**kwargs):
            return "trace-test"

        @classmethod
        def set(cls, **kwargs):
            cls.active = cls()
            return cls.active

        def span(self, stage):
            return nullcontext({})

        def finish(self, **kwargs):
            self.finishes.append(kwargs)
            self.status = "done"

    class DownloadError(Exception):
        fallback_url = "https://example.com/unavailable.png"

    class DuplicateError(Exception):
        pass

    definitions = {
        ".domain.value_objects": {"ComicStoryboard": Storyboard},
        ".domain.value_objects.analysis_results": {"SummaryTopic": Topic},
        ".domain.value_objects.task_event": {
            "TaskEvent": SimpleNamespace(
                FAIL="FAIL", WARN="WARN", ABORT="ABORT", SUCCEED="SUCCEED", CANCEL="CANCEL"
            )
        },
        ".infrastructure.drawing.drawing_client": {
            "DrawingClient": object,
            "ImageDownloadFailedError": DownloadError,
        },
        ".shared.constants": {
            "PLUGIN_NAME": "test-comic",
            "AnalysisStage": SimpleNamespace(
                COMIC_STORYBOARD="STORYBOARD", COMIC_DRAWING="DRAWING", FETCH_MESSAGES="FETCH"
            ),
        },
        ".shared.trace_context": {
            "TraceContext": Trace,
            "_current_trace": ContextVar("test-comic"),
        },
        ".utils.logger": {"logger": logging.getLogger("test-comic")},
        ".application.services.analysis_application_service": {
            "AnalysisApplicationService": object,
            "DuplicateGroupTaskError": DuplicateError,
        },
    }
    for suffix, values in definitions.items():
        module = sys.modules.get(prefix + suffix) or types.ModuleType(prefix + suffix)
        for name, value in values.items():
            setattr(module, name, value)
        monkeypatch.setitem(sys.modules, prefix + suffix, module)
    loaded = []
    for suffix, path in (
        (
            ".application.services.comic_application_service",
            "application/services/comic_application_service.py",
        ),
        (
            ".application.handlers.comic_command_handler",
            "application/handlers/comic_command_handler.py",
        ),
    ):
        spec = importlib.util.spec_from_file_location(prefix + suffix, VENDOR / path)
        module = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules, prefix + suffix, module)
        spec.loader.exec_module(module)
        loaded.append(module)
    return loaded[0], loaded[1], Trace


def _config():
    return SimpleNamespace(
        get_enable_daily_comic=lambda: True,
        get_selected_comic_character=lambda: {"name": "助手"},
        get_comic_character_persona_id=lambda _: "",
        get_comic_character_storyboard_prompt=lambda _: "old template",
        get_drawing_reference_images=list,
        get_drawing_backend=lambda: "builtin",
        get_drawing_provider_configs=lambda: [{"model": "cogview-3-flash"}],
        get_drawing_output_exception_retry_keywords=list,
        is_comic_group_allowed=lambda _: True,
    )


@pytest.mark.parametrize("storyboards", [[{"scene": "抽卡爆率高\n翻唱服务好"}], []])
async def test_vendor_drawing_uses_controlled_prompt_not_title_only_completion(
    tmp_path, monkeypatch, storyboards
):
    service, _, _ = _load_vendor(monkeypatch)
    analyzer = SimpleNamespace(
        analyze_comic_storyboards=AsyncMock(return_value=(storyboards, None))
    )
    drawing = SimpleNamespace(generate_image=AsyncMock(return_value=(PNG, None)))
    custom_config = _config()
    custom_config.get_drawing_provider_configs = lambda: [{"model": "custom-drawing"}]
    result = await service.ComicApplicationService(
        analyzer, drawing, custom_config, tmp_path
    ).generate_comic(TOPICS, "42")
    assert result == (PNG, None)
    assert drawing.generate_image.call_count == 1
    prompt = drawing.generate_image.call_args.args[0]
    assert "人物互动" in prompt and "麦克风" in prompt and "第3格" in prompt
    assert prompt != "抽卡爆率高\n翻唱服务好"


async def test_vendor_final_image_caption_and_sidecar_have_actual_topics(tmp_path, monkeypatch):
    _, handler_module, trace_class = _load_vendor(monkeypatch)
    trace = trace_class()
    trace.metadata["qqchat_comic_plan"] = comic.build_comic_plan(TOPICS)
    adapter = SimpleNamespace(send_image=AsyncMock(return_value=True))
    manager = SimpleNamespace(get_adapter=lambda _: adapter)
    service = SimpleNamespace(generate_comic=AsyncMock(return_value=(PNG, None)))
    handler = handler_module.ComicCommandHandler(
        _config(), manager, service, None, plugin_data_dir=tmp_path
    )
    handler._try_upload_image = AsyncMock()
    await handler._trigger_comic_generation(TOPICS, "42", "qq", "qq:GroupMessage:42", trace=trace)
    assert adapter.send_image.call_count == 1
    caption = adapter.send_image.call_args.kwargs["caption"]
    assert "群聊摘要" in caption and "狂野牌" in caption and "创作意图" in caption
    sidecar = next((tmp_path / "reports").glob("comic_42_*.json"))
    record = json.loads(sidecar.read_text(encoding="utf-8"))
    assert record["caption"] == caption and record["delivery_confirmed"]


async def test_vendor_has_one_initial_progress_and_no_second_task_started_message(
    tmp_path, monkeypatch
):
    _, handler_module, _ = _load_vendor(monkeypatch)
    adapter = SimpleNamespace(
        get_group_info=AsyncMock(return_value=SimpleNamespace(group_name="test"))
    )
    manager = SimpleNamespace(update_from_event=lambda _: None, get_adapter=lambda _: adapter)
    analysis = SimpleNamespace(
        execute_comic_topic_analysis=AsyncMock(return_value={"success": True, "topics": TOPICS})
    )
    handler = handler_module.ComicCommandHandler(
        _config(), manager, None, analysis, plugin_data_dir=tmp_path
    )
    handler.try_trigger_comic_generation = lambda *args, **kwargs: "started"
    event = Event()
    event.should_call_llm = lambda _: None
    event.plain_result = lambda text: text
    results = [result async for result in handler.handle_group_comic(event)]
    assert len(results) == 1 and "正在提取群聊话题" in results[0]


async def test_free_cogview_backend_uses_illustrated_story_instead_of_generic_cards(
    tmp_path, monkeypatch
):
    service, _, trace_class = _load_vendor(monkeypatch)
    trace = trace_class()
    trace_class.active = trace
    plan = comic.build_comic_plan(TOPICS)
    plan.update(render_mode="illustrated_story", panel_captions=["一", "二", "三"])
    render = AsyncMock(return_value=(PNG, plan))
    monkeypatch.setattr(
        "app.services.group_comic_generation.generate_illustrated_comic", render
    )
    analyzer = SimpleNamespace(
        analyze_comic_storyboards=AsyncMock(
            side_effect=AssertionError("no second storyboard model for a controlled story")
        )
    )
    drawing = SimpleNamespace(
        generate_image=AsyncMock(side_effect=AssertionError("drawing is managed by the story pipeline"))
    )
    result = await service.ComicApplicationService(
        analyzer, drawing, _config(), tmp_path
    ).generate_comic(TOPICS, "42")
    assert result == (PNG, None)
    assert not analyzer.analyze_comic_storyboards.called and not drawing.generate_image.called
    assert render.call_args.args[0] == TOPICS
    assert trace.metadata["qqchat_comic_plan"]["render_mode"] == "illustrated_story"


async def test_free_story_failure_does_not_fall_back_to_the_old_generic_card(tmp_path, monkeypatch):
    service, _, trace_class = _load_vendor(monkeypatch)
    trace = trace_class()
    trace_class.active = trace
    from app.services.group_comic_generation import ComicGenerationError

    monkeypatch.setattr("app.services.group_comic_generation.generate_illustrated_comic",
                        AsyncMock(side_effect=ComicGenerationError("第2格绘图未完成")))
    analyzer = SimpleNamespace(analyze_comic_storyboards=AsyncMock())
    drawing = SimpleNamespace(generate_image=AsyncMock())
    result = await service.ComicApplicationService(analyzer, drawing, _config(), tmp_path).generate_comic(TOPICS, "42")
    assert result == (None, None)
    assert trace.metadata["qqchat_comic_failure"] == "第2格绘图未完成"
    assert not analyzer.analyze_comic_storyboards.called and not drawing.generate_image.called


def test_custom_drawing_models_or_external_backends_are_not_replaced():
    config = _config()
    config.get_drawing_provider_configs = lambda: [{"model": "custom-image"}]
    assert not comic.prefers_readable_comic_backend(config)
    config = _config()
    config.get_drawing_backend = lambda: "general_plugin"
    assert not comic.prefers_readable_comic_backend(config)


def _lifecycle_handler(tmp_path, monkeypatch, *, sent=True, failure=None):
    _, module, trace_class = _load_vendor(monkeypatch)
    trace = trace_class()
    adapter = SimpleNamespace(
        send_image=AsyncMock(return_value=sent), send_text=AsyncMock(return_value=True)
    )
    manager = SimpleNamespace(get_adapter=lambda _: adapter)
    active = SimpleNamespace(update_stage=AsyncMock(), finish_task=AsyncMock())
    service = SimpleNamespace(
        generate_comic=AsyncMock(return_value=(PNG, None), side_effect=failure)
    )
    handler = module.ComicCommandHandler(
        _config(), manager, service, None, active_task_manager=active, plugin_data_dir=tmp_path
    )
    handler._try_upload_image = AsyncMock()
    return handler, trace, active, adapter


async def test_delivered_comic_finishes_trace_and_active_task_once(tmp_path, monkeypatch):
    handler, trace, active, adapter = _lifecycle_handler(tmp_path, monkeypatch)
    await handler._trigger_comic_generation(TOPICS, "42", "qq", "qq:GroupMessage:42", trace=trace)
    assert trace.finishes == [{"event": "SUCCEED", "error_message": None}]
    active.finish_task.assert_awaited_once_with("trace-test")
    assert adapter.send_image.call_count == 1


@pytest.mark.parametrize("mode", ["send_failed", "generation_error"])
async def test_comic_failure_finishes_task_without_claiming_delivery(tmp_path, monkeypatch, mode):
    handler, trace, active, _ = _lifecycle_handler(
        tmp_path,
        monkeypatch,
        sent=mode != "send_failed",
        failure=RuntimeError("generation failed") if mode == "generation_error" else None,
    )
    await handler._trigger_comic_generation(TOPICS, "42", "qq", "qq:GroupMessage:42", trace=trace)
    assert trace.finishes[0]["event"] == "FAIL"
    active.finish_task.assert_awaited_once()
    assert not handler._try_upload_image.called


async def test_cancelled_while_queued_finishes_and_releases_active_task(tmp_path, monkeypatch):
    handler, trace, active, adapter = _lifecycle_handler(tmp_path, monkeypatch)
    await handler._comic_semaphore.acquire()
    task = asyncio.create_task(
        handler._trigger_comic_generation(TOPICS, "42", "qq", "qq:GroupMessage:42", trace=trace)
    )
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    handler._comic_semaphore.release()
    assert trace.finishes[0]["event"] == "CANCEL"
    active.finish_task.assert_awaited_once()
    assert not adapter.send_image.called


async def test_terminal_cancel_state_is_not_overwritten_or_delivered_late(tmp_path, monkeypatch):
    handler, trace, active, adapter = _lifecycle_handler(tmp_path, monkeypatch)
    trace.status = "cancelled"
    await handler._trigger_comic_generation(TOPICS, "42", "qq", "qq:GroupMessage:42", trace=trace)
    assert trace.status == "cancelled" and not trace.finishes
    active.finish_task.assert_awaited_once()
    assert not adapter.send_image.called


async def test_album_failure_after_image_delivery_does_not_turn_success_into_failure(
    tmp_path, monkeypatch
):
    handler, trace, active, adapter = _lifecycle_handler(tmp_path, monkeypatch)
    handler._try_upload_image = AsyncMock(side_effect=RuntimeError("album unavailable"))
    await handler._trigger_comic_generation(TOPICS, "42", "qq", "qq:GroupMessage:42", trace=trace)
    assert trace.finishes[0]["event"] == "SUCCEED"
    active.finish_task.assert_awaited_once()
    assert adapter.send_image.call_count == 1
