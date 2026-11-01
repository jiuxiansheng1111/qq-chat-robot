"""Flash兼容仅改变该型号的纯文本序列，不影响多模态与工具语义。"""

from __future__ import annotations

import copy
import json
import logging

import httpx
import pytest

from app.astrbot_flash_provider import (
    PROVIDER_TYPE,
    _last_user_shape,
    flatten_flash_text_parts,
    register_flash_provider,
)


@pytest.mark.parametrize("model", ["glm-4-flash", "glm-4-flash-250414", "GLM-4-FLASH-250414"])
def test_native_text_arrays_become_ordered_paragraphs(model):
    payload = {"model": model, "messages": [
        {"role": "system", "content": [{"type": "text", "text": "角色规则"}]},
        {"role": "user", "content": [{"type": "text", "text": "真正问题"},
                                     {"type": "text", "text": "本轮资料\n第二行"}]},
        {"role": "assistant", "content": [{"type": "text", "text": "旧回答"}]},
    ]}
    flatten_flash_text_parts(payload)
    assert [message["content"] for message in payload["messages"]] == [
        "角色规则", "真正问题\n\n本轮资料\n第二行", "旧回答",
    ]
    before = copy.deepcopy(payload)
    flatten_flash_text_parts(payload)
    assert payload == before


@pytest.mark.parametrize("message", [
    {"role": "user", "content": [{"type": "text", "text": "看图"},
                                 {"type": "image_url", "image_url": {"url": "https://example.com/image"}}]},
    {"role": "user", "content": [{"type": "audio_url", "audio_url": {"url": "audio-reference"}}]},
    {"role": "user", "content": [{"type": "video_url", "video_url": {"url": "video-reference"}}]},
    {"role": "assistant", "content": [{"type": "text", "text": "调用工具"}],
     "tool_calls": [{"id": "call_1", "type": "function", "function": {"name": "search", "arguments": "{}"}}]},
    {"role": "tool", "tool_call_id": "call_1", "content": [{"type": "text", "text": "工具结果"}]},
    {"role": "user", "content": []},
    {"role": "assistant", "content": None},
    {"role": "user", "content": "已有字符串"},
    {"role": "user", "content": [{"type": "text", "text": "带额外语义", "cache_control": {"type": "ephemeral"}}]},
    {"role": "user", "content": [{"type": "text", "text": 123}]},
    {"role": "user", "content": ["不规范的段"]},
])
def test_non_text_or_structured_messages_remain_identical(message):
    payload = {"model": "glm-4-flash-250414", "messages": [message],
               "tools": [{"type": "function", "function": {"name": "search"}}], "tool_choice": "auto"}
    before = copy.deepcopy(payload)
    flatten_flash_text_parts(payload)
    assert payload == before


@pytest.mark.parametrize("model", ["glm-4.5", "glm-4.6v-flash", "glm-4v-flash", "glm-4-flashx", "gpt-4o"])
def test_other_models_keep_original_content(model):
    payload = {"model": model, "messages": [{"role": "user", "content": [{"type": "text", "text": "问题"}]}]}
    before = copy.deepcopy(payload)
    flatten_flash_text_parts(payload)
    assert payload == before


@pytest.mark.parametrize("finish_reason", ["stop", "length"])
async def test_registered_adapter_works_through_real_native_context_llm_generate(
    monkeypatch, tmp_path, caplog, finish_reason,
):
    from test_astrbot_native_onebot import _find_project_root, _load_astrbot_types

    root = _find_project_root()
    monkeypatch.setenv("ASTRBOT_ROOT", str(tmp_path / "isolated-astrbot"))
    monkeypatch.chdir(tmp_path)
    _load_astrbot_types(root, monkeypatch)
    monkeypatch.syspath_prepend(str(root))
    from astrbot.core.agent.message import Message, TextPart
    from astrbot.core.provider.manager import ProviderManager
    from astrbot.core.star.context import Context
    from openai import AsyncOpenAI

    adapter = register_flash_provider()
    assert register_flash_provider() is adapter
    # 走原生加载器与Provider查找，验证自有type无需修改vendor的import switch。
    manager = object.__new__(ProviderManager)
    manager.provider_sources_config = []
    manager.provider_settings = {}
    manager.provider_insts = []
    manager.inst_map = {}
    manager.default_chat_provider_id = "isolated-flash"
    manager.curr_provider_inst = None
    await manager.load_provider({
        "id": "isolated-flash", "type": PROVIDER_TYPE, "provider_type": "chat_completion",
        "enable": True, "api_base": "https://example.invalid/v1", "key": ["dummy-api-key-002"],
        "model": "glm-4-flash-250414", "modalities": ["text", "tool_use"],
        "custom_extra_body": {"temperature": 0.7, "max_tokens": 1024},
    })
    provider = await manager.get_provider_by_id("isolated-flash")
    assert isinstance(provider, adapter)
    assert provider.meta().type == PROVIDER_TYPE
    sent = []
    caplog.set_level(logging.INFO, logger="app.astrbot_flash_provider")

    async def receive(request):
        sent.append(json.loads(request.content))
        return httpx.Response(200, json={
            "id": "isolated-completion", "object": "chat.completion", "created": 1,
            "model": "glm-4-flash-250414", "choices": [
                {"index": 0, "finish_reason": finish_reason, "message": {"role": "assistant", "content": "已理解当前问题"}},
            ], "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        })

    await provider.client.close()
    provider.client = AsyncOpenAI(
        api_key="dummy-api-key-002", base_url="https://example.invalid/v1", max_retries=0,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(receive)),
    )

    context = object.__new__(Context)
    context.provider_manager = manager
    try:
        response = await context.llm_generate(
            chat_provider_id="isolated-flash", system_prompt="人格原样保留",
            contexts=[Message(role="user", content=[TextPart(text="前文第一段"), TextPart(text="前文第二段")])],
            prompt="真正的当前问题", extra_user_content_parts=[TextPart(text="本轮已查证资料")],
            request_max_retries=1,
        )
    finally:
        await provider.client.close()
    assert response.completion_text == "已理解当前问题"
    assert len(sent) == 1  # finish_reason=length也只观察，不自动续写或多发请求。
    assert sent[0]["messages"] == [
        {"role": "system", "content": "人格原样保留"},
        {"role": "user", "content": "前文第一段\n\n前文第二段"},
        {"role": "user", "content": "真正的当前问题\n\n本轮已查证资料"},
    ]
    diagnostic = "\n".join(r.getMessage() for r in caplog.records if r.name == "app.astrbot_flash_provider")
    assert "last_user=list→str" in diagnostic
    assert f"finish_reason={finish_reason}" in diagnostic
    assert "output_tokens=5" in diagnostic
    assert f"visible_chars={len('已理解当前问题')}" in diagnostic
    assert all(private not in diagnostic for private in ["dummy-api-key-002", "真正的当前问题", "本轮已查证资料", "已理解当前问题"])


def test_request_shape_reports_only_structure_and_extra_key_names():
    payload = {"messages": [
        {"role": "user", "content": "previous secret text"},
        {"role": "user", "content": [
            {"type": "text", "text": "private current question", "_no_save": True},
            {"type": "text", "text": "private evidence", "cache_control": {"token": "sensitive-token"}},
            {"type": "image_url", "image_url": {"url": "private-image-url"}},
        ]},
        {"role": "assistant", "content": "private reply"},
    ]}
    shape = _last_user_shape(payload)
    assert shape == {"content_type": "list", "text_parts": 2, "image_parts": 1,
                     "text_extra_keys": ["_no_save", "cache_control"]}
    assert "private" not in str(shape) and "sensitive-token" not in str(shape)


@pytest.mark.parametrize("status", [200, 429])
async def test_flash_repair_is_one_native_request_without_sdk_or_outer_retries(monkeypatch, tmp_path, status):
    from test_astrbot_native_onebot import _find_project_root, _load_astrbot_types

    root = _find_project_root()
    monkeypatch.setenv("ASTRBOT_ROOT", str(tmp_path / "isolated-astrbot"))
    monkeypatch.chdir(tmp_path)
    _load_astrbot_types(root, monkeypatch)
    monkeypatch.syspath_prepend(str(root))
    from openai import AsyncOpenAI, RateLimitError

    adapter = register_flash_provider()
    provider = adapter({
        "id": "isolated-flash", "type": PROVIDER_TYPE, "key": ["dummy-api-key-002"],
        "api_base": "https://example.invalid/v1", "model": "glm-4-flash-250414",
    }, {})
    requests = []

    async def receive(request):
        requests.append(json.loads(request.content))
        if status == 429:
            return httpx.Response(429, json={"error": {"code": "1302", "message": "synthetic busy"}})
        return httpx.Response(200, json={
            "id": "one-repair", "object": "chat.completion", "created": 1,
            "model": "glm-4-flash-250414", "choices": [{"index": 0, "finish_reason": "stop",
                "message": {"role": "assistant", "content": "已接上当前问题"}}],
        })

    await provider.client.close()
    provider.client = AsyncOpenAI(
        api_key="dummy-api-key-002", base_url="https://example.invalid/v1", max_retries=5,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(receive)),
    )
    try:
        if status == 429:
            with pytest.raises(RateLimitError):
                await provider.qqchat_repair_once(prompt="当前问题", system_prompt="纠正规则")
            assert provider.qqchat_repair_busy()
            with pytest.raises(RuntimeError, match="cooling down"):
                await provider.qqchat_repair_once(prompt="当前问题", system_prompt="纠正规则")
        else:
            result = await provider.qqchat_repair_once(prompt="当前问题", system_prompt="纠正规则")
            assert result.completion_text == "已接上当前问题"
            assert not provider.qqchat_repair_busy()
        assert len(requests) == 1
        assert requests[0]["messages"] == [{"role": "system", "content": "纠正规则"},
                                            {"role": "user", "content": "当前问题"}]
        assert "tools" not in requests[0] and requests[0]["max_tokens"] == 1024
        assert provider.client.max_retries == 5  # 正常主请求的client和重试设置保持原值。
        provider.set_model("glm-4.5")
        with pytest.raises(ValueError, match="free Flash model"):
            await provider.qqchat_repair_once(prompt="当前问题", system_prompt="纠正规则")
        assert len(requests) == 1
    finally:
        await provider.client.close()
