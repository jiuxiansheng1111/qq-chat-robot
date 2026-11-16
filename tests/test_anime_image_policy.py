from __future__ import annotations

import json
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from PIL import Image

from app.astrbot_plugin_images import (
    AnimeImageRequest,
    forward_anime_image,
    parse_anime_image_request,
)
from app.services import anime_image_policy as policy


@pytest.mark.parametrize("text,themes", [
    ("来份色图", ()), ("来分色图", ()), ("/来一份涩图吧", ()), ("给我整张福利图", ()),
    ("来张稍微性感一点的女仆图", ("女仆",)), ("我想看黑丝女仆图", ("丝袜", "女仆")),
    ("来份泳装图，不要R18", ("泳装",)), ("来张短裙插画", ("短裙",)),
])
def test_style_aliases(text, themes):
    request = parse_anime_image_request(text)
    assert request and request.styled and request.themes == themes and not request.blocked


@pytest.mark.parametrize("text", ["为什么来份色图", "不要发色图", "色图是什么", "泳装", "来份色图是什么意思", "来份色图这个指令有意思", "来张泳装图再踢掉他"])
def test_discussion_and_negation_do_not_send(text):
    assert parse_anime_image_request(text) is None


@pytest.mark.parametrize("text", ["来份萝莉丝袜图", "来张校服涩图", "来份R-18色图", "来张16岁女仆图"])
def test_disallowed_style_is_handled_without_search(text):
    request = parse_anime_image_request(text)
    assert request and request.blocked


@pytest.mark.parametrize("artwork", [
    {"x_restrict": 1}, {}, {"x_restrict": None}, {"x_restrict": 0, "tags": [{"name": "ロリ"}]},
    {"x_restrict": 0, "title": "高校生の水着"}, {"x_restrict": 0, "caption": "NSFW"},
    {"x_restrict": 0, "tags": [{"translated_name": "school swimsuit"}]},
])
def test_metadata_is_fail_closed(artwork):
    assert not policy.safe_image_metadata(artwork)


def test_ordinary_swimsuit_metadata_only_passes_first_gate():
    assert policy.safe_image_metadata({"x_restrict": 0, "tags": [{"name": "水着"}]})


GOOD = {"anime": True, "clearly_adult": True, "matches_requested_themes": True, "age_uncertain": False, "minor_or_youthful": False, "explicit": False, "private_parts_exposed": False}


def test_visual_check_requires_complete_explicit_boolean_decision():
    assert policy.parse_visual_check(json.dumps(GOOD))
    for key, value in GOOD.items():
        assert not policy.parse_visual_check(json.dumps({**GOOD, key: not value}))
        assert not policy.parse_visual_check(json.dumps({**GOOD, key: str(value)}))
    assert not policy.parse_visual_check("yes")
    assert not policy.parse_visual_check(json.dumps({k: v for k, v in GOOD.items() if k != "age_uncertain"}))


def setup_flow(tmp_path, monkeypatch, *, allowed=True):
    image = tmp_path / "download.png"
    Image.new("RGB", (100, 100), "white").save(image)
    event = SimpleNamespace(
        unified_msg_origin="qq:GroupMessage:123", get_group_id=lambda: "123", get_platform_name=lambda: "aiocqhttp",
        get_sender_id=lambda: "456", plain_result=lambda s: s, chain_result=lambda x: x, send=AsyncMock(),
    )
    artwork = {"id": "1234:0", "x_restrict": 0, "tags": [{"name": "水着 ミニスカート ストッキング メイド"}]}
    index = SimpleNamespace(release_usage=AsyncMock())
    plugin = SimpleNamespace(
        _event_scope=lambda _e: "group:123", _check_rate_limit=lambda _u: 0,
        _ensure_client_or_error=lambda _e: True, _fetch_source_candidates=AsyncMock(return_value=([artwork], 1, "lolicon:search:水着")),
        _filter_blacklisted_illusts=AsyncMock(side_effect=lambda x: x),
        _cfg_str=lambda _name, default: default,
        _pick_illusts=AsyncMock(return_value=[artwork]), _record_image_usage=AsyncMock(), image_index=index,
        downloader=SimpleNamespace(download_for_send=AsyncMock(return_value=(str(image), "large", image.stat().st_size))),
    )
    monkeypatch.setattr(policy, "_vision_provider", lambda *_a: object())
    checker = AsyncMock(return_value=allowed)
    monkeypatch.setattr(policy, "verify_image", checker)
    module = ModuleType("astrbot.api.all")
    module.Image = SimpleNamespace(fromFileSystem=lambda path: ("image", path))
    module.Plain = lambda text: ("text", text)
    monkeypatch.setitem(sys.modules, "astrbot.api.all", module)
    return event, plugin, checker, image


@pytest.mark.asyncio
async def test_verified_image_sent_once_and_temp_removed(tmp_path, monkeypatch):
    event, plugin, checker, image = setup_flow(tmp_path, monkeypatch)
    output = [item async for item in policy.send_styled_anime_image(event, object(), plugin, AnimeImageRequest(True, ("泳装",)))]
    assert output == []
    event.send.assert_awaited_once()
    checker.assert_awaited_once()
    plugin._record_image_usage.assert_awaited_once()
    plugin.image_index.release_usage.assert_not_awaited()
    assert not image.exists()


@pytest.mark.asyncio
async def test_youthful_or_uncertain_image_is_not_sent(tmp_path, monkeypatch):
    event, plugin, _checker, image = setup_flow(tmp_path, monkeypatch, allowed=False)
    output = [item async for item in policy.send_styled_anime_image(event, object(), plugin, AnimeImageRequest(True))]
    assert len(output) == 1 and "没找到" in output[0]
    event.send.assert_not_awaited()
    plugin._pick_illusts.assert_not_awaited()
    assert not image.exists()


@pytest.mark.asyncio
async def test_check_failure_does_not_fall_back_to_unreviewed_random_image(tmp_path, monkeypatch):
    event, plugin, checker, image = setup_flow(tmp_path, monkeypatch)
    checker.side_effect = TimeoutError()
    output = [item async for item in policy.send_styled_anime_image(event, object(), plugin, AnimeImageRequest(True))]
    assert len(output) == 1 and "暂时" in output[0]
    event.send.assert_not_awaited()
    assert not image.exists()


@pytest.mark.asyncio
async def test_busy_check_reports_busy_and_never_claims_sent(tmp_path, monkeypatch):
    event, plugin, checker, image = setup_flow(tmp_path, monkeypatch)
    checker.side_effect = policy.ImageCheckBusy()
    output = [item async for item in policy.send_styled_anime_image(event, object(), plugin, AnimeImageRequest(True))]
    assert output == ["图片检查服务繁忙，稍后再试试"]
    event.send.assert_not_awaited()
    assert not image.exists()


@pytest.mark.asyncio
async def test_send_timeout_does_not_duplicate_image_and_releases_pending(tmp_path, monkeypatch):
    event, plugin, _checker, image = setup_flow(tmp_path, monkeypatch)
    event.send.side_effect = TimeoutError()
    output = [item async for item in policy.send_styled_anime_image(event, object(), plugin, AnimeImageRequest(True))]
    assert len(output) == 1
    event.send.assert_awaited_once()
    plugin.image_index.release_usage.assert_awaited_once()
    plugin._record_image_usage.assert_not_awaited()
    assert not image.exists()


@pytest.mark.asyncio
async def test_group_inflight_is_not_reentered(tmp_path, monkeypatch):
    event, plugin, _checker, _image = setup_flow(tmp_path, monkeypatch)
    policy._INFLIGHT.add("group:123")
    try:
        output = [item async for item in policy.send_styled_anime_image(event, object(), plugin, AnimeImageRequest(True))]
        assert output == ["这张还在找，稍等一下"]
        plugin._fetch_source_candidates.assert_not_awaited()
    finally:
        policy._INFLIGHT.discard("group:123")


@pytest.mark.asyncio
async def test_style_does_not_bypass_disabled_command_permission(monkeypatch):
    event = SimpleNamespace(get_message_str=lambda: "来份色图")
    plugin = SimpleNamespace(cmd_p=lambda *_a, **_kw: None)
    context = SimpleNamespace(get_registered_star=lambda _name: SimpleNamespace(star_cls=plugin, activated=True))
    monkeypatch.setattr("app.astrbot_menu._active_commands", lambda *_a: [])
    assert await forward_anime_image(event, context) is None


@pytest.mark.parametrize("model", ["glm-4-plus", "glm-4", "gpt-4o"])
def test_visual_check_never_uses_paid_chat_model(model):
    context = SimpleNamespace(get_config=lambda **_kw: {"provider_settings": {"default_image_caption_provider_id": "v"}}, get_provider_by_id=lambda _id: SimpleNamespace(get_model=lambda: model))
    with pytest.raises(RuntimeError):
        policy._vision_provider(context, SimpleNamespace(unified_msg_origin="qq:GroupMessage:123"))


class VisionHTTPError(Exception):
    def __init__(self, status, code):
        self.status_code = status
        self.body = {"error": {"code": code}}


def vision_fixture(tmp_path, responses, *, official=True):
    policy._CHECK_CACHE.clear()
    policy._VISION_COOLDOWN.clear()
    path = tmp_path / "visual.png"
    Image.new("RGB", (32, 32), "white").save(path)
    create = AsyncMock(side_effect=responses)
    client = SimpleNamespace(
        base_url="https://open.bigmodel.cn/api/paas/v4/" if official else "https://other.example/v1/",
        chat=SimpleNamespace(completions=SimpleNamespace(create=create)),
    )
    client.with_options = lambda **_kwargs: client
    return SimpleNamespace(client=client, get_model=lambda: "glm-4.6v-flash"), str(path), create


def passed_visual_response():
    return SimpleNamespace(choices=[SimpleNamespace(finish_reason="stop", message=SimpleNamespace(content=json.dumps(GOOD)))])


@pytest.mark.asyncio
async def test_official_free_busy_falls_back_once_and_cools_down(tmp_path):
    provider, path, create = vision_fixture(tmp_path, [VisionHTTPError(429, "1305"), passed_visual_response(), passed_visual_response()])
    assert await policy.verify_image(provider, path, ("泳装",))
    assert [call.kwargs["model"] for call in create.await_args_list] == ["glm-4.6v-flash", "glm-4.1v-thinking-flash"]
    # Outfit preferences do not require another content-safety call.
    assert await policy.verify_image(provider, path, ("女仆",))
    assert create.await_count == 2


@pytest.mark.asyncio
async def test_upstream_content_rejection_is_not_retried(tmp_path):
    provider, path, create = vision_fixture(tmp_path, [VisionHTTPError(400, "1301")])
    assert not await policy.verify_image(provider, path, ("泳装",))
    create.assert_awaited_once()


@pytest.mark.asyncio
async def test_busy_does_not_use_unverified_third_party_fallback(tmp_path):
    provider, path, create = vision_fixture(tmp_path, [VisionHTTPError(429, "1305")], official=False)
    with pytest.raises(RuntimeError, match="暂忙"):
        await policy.verify_image(provider, path, ("泳装",))
    create.assert_awaited_once()


@pytest.mark.asyncio
async def test_other_api_failures_never_trigger_hidden_model_fallback(tmp_path):
    provider, path, create = vision_fixture(tmp_path, [VisionHTTPError(401, "invalid_key")])
    with pytest.raises(VisionHTTPError):
        await policy.verify_image(provider, path, ("泳装",))
    create.assert_awaited_once()


@pytest.mark.asyncio
async def test_combined_theme_search_uses_preference_or_and_keeps_r18_zero(monkeypatch):
    captured = {}
    class Response:
        def raise_for_status(self): pass
        def json(self): return {"data": [{"pid": 123}]}
    class Client:
        async def __aenter__(self): return self
        async def __aexit__(self, *_args): return False
        async def get(self,url,**kwargs):
            captured.update(url=url,**kwargs)
            return Response()
    monkeypatch.setattr('app.services.http_routing.outbound_http_client',lambda **kwargs: Client())
    source = SimpleNamespace(available=True, api_url="https://api.example/v2", _normalize=lambda row: {"id": str(row["pid"])})
    plugin = SimpleNamespace(lolicon_client=source, _fetch_source_candidates=AsyncMock())
    rows, count, _source_key = await policy.fetch_theme_candidates(plugin, object(), ("丝袜", "女仆"))
    assert count == 1 and rows == [{"id": "123"}]
    assert ("r18", "0") in captured["params"]
    tags = [value for key, value in captured["params"] if key == "tag"]
    assert len(tags) == 1 and "ストッキング" in tags[0] and "メイド" in tags[0]
    assert "御姐" not in tags[0]
    plugin._fetch_source_candidates.assert_not_awaited()


def test_theme_tags_do_not_accept_ordinary_costume_as_swimsuit():
    artwork = {"title": "リサ(原神)", "tags": [{"name": "黒スト", "translated_name": "黑丝"}]}
    assert policy.matches_theme_metadata(artwork, ("丝袜",))
    assert not policy.matches_theme_metadata(artwork, ("泳装",))
    assert not policy.matches_theme_metadata(artwork, ("丝袜", "女仆"))


def test_generic_request_does_not_invent_a_theme_but_keeps_all_safety_checks():
    values = {**GOOD, "matches_requested_themes": False}
    assert policy.parse_visual_check(json.dumps(values), require_themes=False)
    assert not policy.parse_visual_check(json.dumps(values), require_themes=True)
    for key in ("clearly_adult", "age_uncertain", "minor_or_youthful", "explicit", "private_parts_exposed"):
        assert not policy.parse_visual_check(json.dumps({**values, key: not values[key]}), require_themes=False)


def test_visual_response_can_include_explanation_but_not_ambiguous_decisions():
    text = '检查结果如下：\n```json\n' + json.dumps({**GOOD, 'reason': '普通动漫画风'}) + '\n```'
    assert policy.parse_visual_check(text)
    assert not policy.parse_visual_check(text + '\n' + json.dumps({**GOOD, 'clearly_adult': False}))
    assert not policy.parse_visual_check(json.dumps({**GOOD, 'clearly_adult': 'true'}))


def test_adult_uniform_is_not_confused_with_school_uniform():
    assert policy.safe_image_metadata({'x_restrict': 0, 'tags': [{'name': 'OL制服 メイド'}]})
    assert not policy.safe_image_metadata({'x_restrict': 0, 'tags': [{'name': '学生校服'}]})


@pytest.mark.asyncio
async def test_third_free_model_is_used_if_first_two_are_busy(tmp_path):
    provider, path, create = vision_fixture(tmp_path, [VisionHTTPError(429, '1305'), VisionHTTPError(429, '1302'), passed_visual_response()])
    assert await policy.verify_image(provider, path, ())
    assert [call.kwargs['model'] for call in create.await_args_list] == list(policy._FREE_VISION_MODELS)
    assert policy._VISION_COOLDOWN['glm-4.1v-thinking-flash'] > policy.time.monotonic()


@pytest.mark.asyncio
async def test_network_timeout_can_use_one_other_free_model(tmp_path):
    provider, path, create = vision_fixture(tmp_path, [TimeoutError(), passed_visual_response()])
    assert await policy.verify_image(provider, path, ())
    assert create.await_count == 2


@pytest.mark.asyncio
async def test_busy_models_are_not_repeatedly_requested(tmp_path):
    provider, path, create = vision_fixture(tmp_path, [VisionHTTPError(429, '1305')] * 3)
    with pytest.raises(policy.ImageCheckBusy):
        await policy.verify_image(provider, path, ())
    with pytest.raises(policy.ImageCheckBusy):
        await policy.verify_image(provider, path, ())
    assert create.await_count == 3


@pytest.mark.asyncio
async def test_gallery_get_retries_one_transient_connection_failure_before_pixiv_login(monkeypatch):
    import httpx

    from app.services import http_routing
    response=SimpleNamespace(raise_for_status=lambda:None,json=lambda:{'data':[{'id':'fixture'}]})
    get=AsyncMock(side_effect=[httpx.ConnectError('temporary connection'),response])
    class Client:
        async def __aenter__(self):return SimpleNamespace(get=get)
        async def __aexit__(self,*args):pass
    monkeypatch.setattr(http_routing,'outbound_http_client',lambda **kwargs:Client())
    monkeypatch.setattr(policy.asyncio,'sleep',AsyncMock())
    monkeypatch.setattr(policy,'_SOURCE_COOLDOWN',{})
    pixiv=SimpleNamespace(search=AsyncMock())
    plugin=SimpleNamespace(lolicon_client=SimpleNamespace(api_url='https://gallery.invalid',available=True,_normalize=lambda x:x),client=pixiv)
    rows,_,_=await policy.fetch_theme_candidates(plugin,object(),())
    assert rows==[{'id':'fixture'}] and get.await_count==2
    pixiv.search.assert_not_awaited()
    assert ('r18','0') in get.await_args.kwargs['params']


@pytest.mark.asyncio
async def test_generic_request_does_not_impose_random_outfit(tmp_path, monkeypatch):
    event, plugin, checker, _image = setup_flow(tmp_path, monkeypatch)
    plugin._fetch_source_candidates.return_value=([{'id':'1234:0','x_restrict':0,'tags':[]}],1,'pixiv:general')
    output=[x async for x in policy.send_styled_anime_image(event, object(), plugin, AnimeImageRequest(True))]
    assert output == []
    assert plugin._fetch_source_candidates.await_args.args[1] == ''
    assert checker.await_args.args[2] == ()
    event.send.assert_awaited_once()


@pytest.mark.asyncio
async def test_record_failure_after_delivery_does_not_claim_image_failed(tmp_path, monkeypatch):
    event, plugin, _checker, _image = setup_flow(tmp_path, monkeypatch)
    plugin._record_image_usage.side_effect=RuntimeError('database busy')
    output=[x async for x in policy.send_styled_anime_image(event, object(), plugin, AnimeImageRequest(True))]
    assert output == []
    event.send.assert_awaited_once()


@pytest.mark.asyncio
async def test_preference_mismatch_does_not_reject_a_safe_picture(tmp_path, monkeypatch):
    event, plugin, _checker, _image = setup_flow(tmp_path, monkeypatch)
    plugin._fetch_source_candidates.return_value=([{'id':'1234:0','x_restrict':0,'tags':[]}],1,'pixiv:general')
    output=[x async for x in policy.send_styled_anime_image(event, object(), plugin, AnimeImageRequest(True, ('泳装',)))]
    assert output == []
    event.send.assert_awaited_once()
