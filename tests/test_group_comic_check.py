from __future__ import annotations

import base64
import io
import json

import httpx
import pytest
from PIL import Image

from app.services import group_comic_check as check

FREE = {
    "api_url": "https://open.bigmodel.cn/api/paas/v4/images/generations",
    "model": "cogview-3-flash",
    "api_protocol": "images",
    "api_key": "dummy-api-key-002",
}
GOOD = {
    "all_comic": True,
    "on_topic": True,
    "consistent_character": True,
    "unrelated_photo": False,
    "text_heavy": False,
}


def panels():
    result = []
    for color in ("green", "blue", "orange"):
        data = io.BytesIO()
        Image.new("RGB", (256, 256), color).save(data, format="PNG")
        result.append(data.getvalue())
    return result


def install(monkeypatch, handler):
    calls = []

    def transport(**kwargs):
        calls.append(kwargs)
        return httpx.MockTransport(handler)

    monkeypatch.setattr(check.httpx, "AsyncHTTPTransport", transport)
    return calls


async def test_single_official_free_vision_post_contains_one_contact_sheet_and_strict_booleans(
    monkeypatch,
):
    requests = []

    def respond(request):
        requests.append(request)
        assert request.method == "POST" and str(request.url) == check._CHECK_ENDPOINT
        assert request.headers["authorization"] == "Bearer dummy-api-key-002"
        body = json.loads(request.content)
        assert body["model"] == "glm-4v-flash" and body["stream"] is False
        assert "thinking" not in body
        parts = body["messages"][1]["content"]
        assert len(parts) == 2
        assert parts[1]["image_url"]["url"].startswith("data:image/png;base64,")
        with Image.open(
            io.BytesIO(base64.b64decode(parts[1]["image_url"]["url"].split(",", 1)[1]))
        ) as sheet:
            assert sheet.size == (1020, 340)
            assert [sheet.getpixel((x, 170)) for x in (170, 510, 850)] == [
                (0, 128, 0),
                (0, 0, 255),
                (255, 165, 0),
            ]
        assert json.loads(parts[0]["text"])["expected_scenes"] == ["开端", "变化", "收尾"]
        assert "水印不算文字过多" in body["messages"][0]["content"]
        return httpx.Response(
            200,
            json={"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(GOOD)}}]},
        )

    kwargs = install(monkeypatch, respond)
    result = await check.verify_group_comic_panels(
        panels(), ["开端", "变化", "收尾"], FREE, proxy="http://127.0.0.1:7897"
    )
    assert result["passed"] is True and result["checks"] == GOOD
    assert len(requests) == 1 and kwargs == [{"proxy": "http://127.0.0.1:7897", "retries": 0}]
    assert "dummy-api-key-002" not in json.dumps(result)


@pytest.mark.parametrize(
    "field,value",
    [
        ("model", "cogview-4"),
        ("model", "glm-4.6v"),
        ("api_url", "https://example.org/api/paas/v4/images/generations"),
        ("api_url", "https://open.bigmodel.cn/api/paas/v4/images/generations?other=1"),
        ("api_url", "http://open.bigmodel.cn/api/paas/v4/images/generations"),
        ("api_protocol", "chat"),
        ("api_key", ""),
    ],
)
async def test_paid_or_nonofficial_provider_is_rejected_before_any_request(
    monkeypatch, field, value
):
    def forbidden(**kwargs):
        pytest.fail("未验证的免费配置不能构建网络传输")

    monkeypatch.setattr(check.httpx, "AsyncHTTPTransport", forbidden)
    result = await check.verify_group_comic_panels(
        panels(), ["一", "二", "三"], {**FREE, field: value}
    )
    assert result["passed"] is False


@pytest.mark.parametrize(
    "status,body",
    [
        (429, {"error": "dummy-api-key-002 upstream text"}),
        (503, {"error": "dummy-api-key-002 upstream text"}),
        (200, {"error": {"code": "1305", "message": "dummy-api-key-002 upstream text"}}),
        (200, {"choices": [{"message": {"content": "dummy-api-key-002 not-json"}}]}),
        (302, {}),
    ],
)
async def test_busy_redirect_or_bad_response_fails_without_retry_or_upstream_text(
    monkeypatch, status, body
):
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(
            status, json=body, headers={"location": "https://paid.example/redirect"}
        )

    install(monkeypatch, respond)
    result = await check.verify_group_comic_panels(panels(), ["一", "二", "三"], FREE)
    assert result["passed"] is False and len(requests) == 1
    assert all(
        value not in json.dumps(result) for value in ("dummy-api-key-002", "upstream", "paid.example")
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"all_comic": False},
        {"on_topic": False},
        {"consistent_character": False},
        {"unrelated_photo": True},
        {"text_heavy": True},
        {"all_comic": "true"},
        {"all_comic": 1},
        {"extra": "dummy-api-key-002"},
    ],
)
def test_quality_or_schema_mismatch_cannot_pass(changes):
    result = check._parse_result(
        {"choices": [{"message": {"content": json.dumps({**GOOD, **changes})}}]}
    )
    assert result["passed"] is False
    assert "dummy-api-key-002" not in json.dumps(result)


async def test_connection_exception_is_single_attempt_and_does_not_disclose_credentials(
    monkeypatch,
):
    requests = []

    def failed(request):
        requests.append(request)
        raise httpx.ConnectError("dummy-api-key-002 endpoint private error")

    install(monkeypatch, failed)
    result = await check.verify_group_comic_panels(panels(), ["一", "二", "三"], FREE)
    assert result["passed"] is False and len(requests) == 1
    assert "dummy-api-key-002" not in json.dumps(result)


async def test_invalid_images_or_scene_count_never_send(monkeypatch):
    def forbidden(**kwargs):
        pytest.fail("无效图片不能构建传输")

    monkeypatch.setattr(check.httpx, "AsyncHTTPTransport", forbidden)
    assert not (await check.verify_group_comic_panels([b"invalid"] * 3, ["一", "二", "三"], FREE))[
        "passed"
    ]
    assert not (await check.verify_group_comic_panels(panels(), ["一"], FREE))["passed"]
