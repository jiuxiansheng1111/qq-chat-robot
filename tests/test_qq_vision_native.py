import json
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import httpx
import pytest
from PIL import Image as PILImage


@pytest.mark.parametrize("mode", ["group", "quote", "private"])
async def test_real_native_attachments_survive_primary_overload(monkeypatch, tmp_path, mode):
    from test_astrbot_native_onebot import (
        _find_project_root,
        _load_astrbot_types,
        _make_group_event,
    )

    root = _find_project_root()
    monkeypatch.setenv("ASTRBOT_ROOT", str(tmp_path / "isolated-astrbot"))
    monkeypatch.chdir(tmp_path)
    types = _load_astrbot_types(root, monkeypatch)
    from astrbot.api.message_components import Image, Plain, Reply
    from astrbot.core.astr_main_agent import (
        MainAgentBuildConfig,
        _ensure_img_caption,
        collect_initial_request,
    )
    from astrbot.core.provider.manager import ProviderManager
    from openai import AsyncOpenAI

    from app.astrbot_vision import read_turn_image_evidence
    from app.astrbot_vision_provider import (
        BACKUP_MODEL,
        PRIMARY_MODEL,
        PROVIDER_TYPE,
        register_vision_provider,
    )

    adapter = register_vision_provider()
    assert register_vision_provider() is adapter
    manager = object.__new__(ProviderManager)
    manager.provider_sources_config = []
    manager.provider_settings = {}
    manager.provider_insts, manager.inst_map = [], {}
    manager.default_chat_provider_id, manager.curr_provider_inst = "vision-test", None
    await manager.load_provider(
        {
            "id": "vision-test",
            "type": PROVIDER_TYPE,
            "provider_type": "chat_completion",
            "enable": True,
            "model": PRIMARY_MODEL,
            "key": ["synthetic-key"],
            "api_base": "https://example.invalid/v1",
            "modalities": ["text", "image"],
        }
    )
    provider = await manager.get_provider_by_id("vision-test")
    calls = []

    async def respond(request):
        body = json.loads(request.content)
        calls.append(body)
        assert any(
            p.get("type") == "image_url" and p["image_url"]["url"].startswith("data:image/")
            for p in body["messages"][-1]["content"]
        )
        if body["model"] == PRIMARY_MODEL:
            return httpx.Response(
                429, json={"error": {"message": "model overloaded", "code": "1305"}}
            )
        return httpx.Response(
            200,
            json={
                "id": "test",
                "object": "chat.completion",
                "created": 1,
                "model": BACKUP_MODEL,
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {
                            "role": "assistant",
                            "content": "图片中的统计为223条消息，8人参与",
                        },
                    }
                ],
            },
        )

    await provider.client.close()
    provider.client = AsyncOpenAI(
        api_key="synthetic-key",
        base_url="https://example.invalid/v1",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(respond)),
    )
    image_path = tmp_path / "report.png"
    PILImage.new("RGB", (640, 480), "blue").save(image_path)
    event, api, _raw = _make_group_event(types)
    picture = Image.fromFileSystem(str(image_path))
    event.message_obj.message = [
        Plain("看看这张图"),
        Reply(id="123", chain=[picture]) if mode == "quote" else picture,
    ]
    if mode == "private":
        event.message_obj.type = types.MessageType.FRIEND_MESSAGE
        event.message_obj.group_id = ""
    context = NS(
        conversation_manager=NS(
            get_curr_conversation_id=AsyncMock(return_value="test-conv"),
            get_conversation=AsyncMock(return_value=NS(history="[]")),
        ),
        get_provider_by_id=lambda _: provider,
    )
    try:
        req, _ = await collect_initial_request(
            event, context, MainAgentBuildConfig(tool_call_timeout=10)
        )
        assert req.image_urls
        await _ensure_img_caption(
            event, req, {"image_caption_prompt": "读取实际图片"}, context, "vision-test"
        )
        evidence = read_turn_image_evidence(event, req)
        assert evidence.status == "ready" and "223" in evidence.caption
        assert [c["model"] for c in calls] == [PRIMARY_MODEL, BACKUP_MODEL]
        assert req.image_urls == [] and not api.calls
        # A recently overloaded primary is not hammered again on the next request.
        await provider.text_chat(prompt="读取实际图片", image_urls=[str(image_path)])
        assert [c["model"] for c in calls] == [PRIMARY_MODEL, BACKUP_MODEL, BACKUP_MODEL]
    finally:
        await provider.terminate()


async def test_authentication_failure_is_not_retried_as_overload(monkeypatch, tmp_path):
    from test_astrbot_native_onebot import _find_project_root, _load_astrbot_types

    root = _find_project_root()
    monkeypatch.setenv("ASTRBOT_ROOT", str(tmp_path / "isolated-astrbot"))
    monkeypatch.chdir(tmp_path)
    _load_astrbot_types(root, monkeypatch)
    from openai import AsyncOpenAI, AuthenticationError

    from app.astrbot_vision_provider import PRIMARY_MODEL, register_vision_provider

    provider = register_vision_provider()(
        {
            "id": "test",
            "key": ["synthetic-key"],
            "model": PRIMARY_MODEL,
            "api_base": "https://example.invalid/v1",
        },
        {},
    )
    image_path = tmp_path / "sample.png"
    PILImage.new("RGB", (32, 32), "blue").save(image_path)
    calls = []

    def reject(request):
        calls.append(request)
        return httpx.Response(401, json={"error": {"message": "invalid synthetic key"}})

    await provider.client.close()
    provider.client = AsyncOpenAI(
        api_key="synthetic-key",
        base_url="https://example.invalid/v1",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(reject)),
    )
    try:
        with pytest.raises(AuthenticationError):
            await provider.text_chat(prompt="读图", image_urls=[str(image_path)])
        assert len(calls) == 1
    finally:
        await provider.terminate()
