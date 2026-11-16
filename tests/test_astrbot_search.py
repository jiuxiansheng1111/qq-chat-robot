import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from app.astrbot_search import (
    _FACT_BOUNDARY,
    _GROUP_REPLY_POLICY,
    _free_fallback_results,
    _normalize_public_query,
    _plan_group_search,
    _relevant_result,
    native_web_search,
    prepare_group_search_context,
)
from app.services.web_search import SearchResult


@pytest.fixture(autouse=True)
def isolate_secondary_public_search(monkeypatch):
    monkeypatch.setattr("app.astrbot_search._free_fallback_results", AsyncMock(return_value=[]))


class Event:
    unified_msg_origin = "test:GroupMessage:42"

    def __init__(self):
        self.extras = {}

    def get_extra(self, key, default=None):
        return self.extras.get(key, default)

    def set_extra(self, key, value):
        self.extras[key] = value


def context(host="https://open.bigmodel.cn/api/paas/v4", *, allow_paid_search=False):
    def get_config(umo):
        assert umo == Event.unified_msg_origin
        return {
            "provider_sources": [{"api_base": host, "key": ["dummy-api-key-002"], "enable": True}],
            "provider_settings": {"qqchat_allow_paid_search": allow_paid_search},
        }

    return SimpleNamespace(get_config=get_config)


def install_http(monkeypatch, handler):
    monkeypatch.setattr(
        "app.astrbot_search.outbound_http_client",
        lambda **kwargs: httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )


async def test_search_reuses_official_credentials_and_keeps_sources_dates_and_query(monkeypatch):
    calls = []

    async def handle(request):
        assert str(request.url) == "https://open.bigmodel.cn/api/paas/v4/web_search"
        assert request.headers["Authorization"] == "Bearer dummy-api-key-002"
        body = json.loads(request.content)
        calls.append(body)
        return httpx.Response(
            200,
            json={
                "search_result": [
                    {
                        "title": "Official announcement",
                        "link": "https://example.com/update",
                        "content": "Release details",
                        "media": "Publisher",
                        "publish_date": "2026-10-25",
                    },
                    {
                        "title": "Duplicate",
                        "link": "https://example.com/update",
                        "content": "duplicate",
                    },
                    {"title": "Not a web result", "link": "file:///private", "content": "ignore"},
                ]
            },
        )

    install_http(monkeypatch, handle)
    event = Event()
    answer = json.loads(
        await native_web_search(
            context(allow_paid_search=True), event, " latest   update ", "oneWeek"
        )
    )
    assert calls == [
        {
            "search_engine": "search_pro",
            "search_query": "latest update",
            "search_intent": False,
            "count": 5,
            "search_recency_filter": "oneWeek",
            "content_size": "high",
        }
    ]
    assert answer["status"] == "ok"
    assert answer["results"] == [
        {
            "title": "Official announcement",
            "url": "https://example.com/update",
            "content": "Release details",
            "source": "Publisher",
            "published_at": "2026-10-25",
        }
    ]
    assert answer["searched_at"].endswith("+08:00")
    assert "不可信" in answer["guidance"]
    assert "dummy-api-key-002" not in json.dumps(answer)


async def test_search_cache_and_per_turn_budget_do_not_leak_between_events(monkeypatch):
    calls = []

    async def search(query, **kwargs):
        calls.append(query)
        return []

    monkeypatch.setattr("app.astrbot_search.search_web", search)
    event = Event()
    one = await native_web_search(context(), event, "one")
    assert await native_web_search(context(), event, "one") == one
    await native_web_search(context(), event, "two")
    assert (
        json.loads(await native_web_search(context(), event, "three"))["status"] == "limit_reached"
    )
    await native_web_search(context(), Event(), "three")
    assert calls == ["one", "two", "three"]


async def test_opinion_search_keeps_source_limits_and_does_not_reuse_fact_guidance(monkeypatch):
    calls = []
    async def public(query, settings=None):
        calls.append(query)
        return [{"title": "公开评价", "url": "https://example.org/review", "content": "有长处也有短处", "published_at": ""}]
    monkeypatch.setattr("app.astrbot_search._public_results", public)
    e = Event()
    e.get_platform_name = lambda: "aiocqhttp"
    factual = json.loads(await native_web_search(context(), e, "某作品 评价"))
    opinion = json.loads(await native_web_search(context(), e, "某作品 评价", purpose="game_opinion"))
    assert len(calls) == 2
    assert "主观评价" not in factual["guidance"]
    assert "选择和具体理由" in opinion["guidance"] and "不能编查不到的比分或数据" in opinion["guidance"]
    assert opinion["current_facts_verified"] is False and opinion["results"][0]["published_at"] == ""


async def test_subscription_planner_uses_authorized_provider_instead_of_keyless_client():
    chat = AsyncMock(return_value=SimpleNamespace(completion_text=json.dumps({
        "search": True, "query": "NiKo donk 近期关键局 评价", "recency": "oneMonth", "purpose": "game_opinion"})))
    provider = SimpleNamespace(qqchat_subscription=True, get_model=lambda: "gpt-6-luna", text_chat=chat)
    ctx = SimpleNamespace(get_current_chat_provider_id=AsyncMock(return_value="luna"), get_provider_by_id=lambda _: provider)
    result = await _plan_group_search(ctx, GroupEvent(), SimpleNamespace(contexts=[]))
    assert result == {"query": "NiKo donk 近期 关键局 评价", "recency": "oneMonth", "purpose": "game_opinion"}
    chat.assert_awaited_once()
    assert chat.await_args.kwargs["contexts"] == [] and chat.await_args.kwargs["request_max_retries"] == 1
    assert "明确倾向" not in chat.await_args.kwargs["prompt"]  # user input remains data
    assert "不把印象二选一" in chat.await_args.kwargs["system_prompt"]


async def test_search_http_failure_does_not_disclose_upstream_body_or_credentials(monkeypatch):
    async def handle(request):
        return httpx.Response(401, json={"error": "dummy-api-key-002 upstream private text"})

    install_http(monkeypatch, handle)
    answer = await native_web_search(context(allow_paid_search=True), Event(), "query")
    assert json.loads(answer)["status"] == "search_failed"
    assert "dummy-api-key-002" not in answer and "upstream" not in answer


@pytest.mark.parametrize(
    ("query", "recency", "status"),
    [
        (" ", "noLimit", "invalid_query"),
        ("x" * 71, "noLimit", "invalid_query"),
        ("query", "invalid", "invalid_recency"),
    ],
)
async def test_invalid_search_never_calls_network(monkeypatch, query, recency, status):
    def forbidden(**kwargs):
        pytest.fail("invalid query must not make a request")

    monkeypatch.setattr("app.astrbot_search.outbound_http_client", forbidden)
    assert (
        json.loads(await native_web_search(context(), Event(), query, recency))["status"] == status
    )


async def test_missing_credentials_and_disabled_search_never_calls_network(monkeypatch):
    def forbidden(**kwargs):
        pytest.fail("disabled or unconfigured search must not make a request")

    monkeypatch.setattr("app.astrbot_search.outbound_http_client", forbidden)
    answer = await native_web_search(
        context("https://example.invalid", allow_paid_search=True), Event(), "query"
    )
    assert json.loads(answer)["status"] == "unavailable"
    answer = await native_web_search(
        context(),
        Event(),
        "query",
        settings=SimpleNamespace(auto_web_search_enabled=False),
    )
    assert json.loads(answer)["status"] == "disabled"


@pytest.mark.parametrize("flag", [False, None, "true", 1])
async def test_default_and_nonboolean_opt_in_use_free_search_without_paid_post(monkeypatch, flag):
    sent = []

    async def free_search(query, **kwargs):
        sent.append((query, kwargs))
        return [
            SearchResult(
                "Public source",
                "https://docs.python.org/3/library/asyncio.html",
                "Published 2026-10-26: this text is only a snippet",
            ),
            SearchResult(
                "Duplicate", "https://docs.python.org/3/library/asyncio.html", "duplicate"
            ),
            SearchResult("Private file", "file:///private", "skip"),
        ]

    def paid_forbidden(**kwargs):
        pytest.fail("default/free mode must never construct a paid API client")

    monkeypatch.setattr("app.astrbot_search.search_web", free_search)
    monkeypatch.setattr("app.astrbot_search.outbound_http_client", paid_forbidden)
    answer = json.loads(
        await native_web_search(
            context(allow_paid_search=flag), Event(), "Python asyncio", "oneDay"
        )
    )

    assert sent == [("Python asyncio", {"limit": 5, "timeout": 4})]
    assert answer["backend"] == "public_web"
    assert answer["requested_recency"] == "oneDay"
    assert answer["recency_filter_applied"] is False
    assert answer["results"][0]["published_at"] == ""
    assert answer["results"][0]["source"] == "docs.python.org"
    assert len(answer["results"]) == 1
    assert "不能编日期" in answer["guidance"]
    assert "dummy-api-key-002" not in json.dumps(answer)


async def test_free_search_works_without_any_model_api_key(monkeypatch):
    async def free_search(query, **kwargs):
        return [
            SearchResult("Public topic page", "https://example.org/page", "Public topic summary")
        ]

    monkeypatch.setattr("app.astrbot_search.search_web", free_search)
    no_credentials = SimpleNamespace(get_config=lambda _umo: {})
    answer = json.loads(await native_web_search(no_credentials, Event(), "public topic"))
    assert answer["status"] == "ok"
    assert answer["backend"] == "public_web"


async def test_failed_free_search_does_not_fall_back_to_paid_endpoint(monkeypatch):
    async def failed(*args, **kwargs):
        raise RuntimeError("upstream private response text")

    def paid_forbidden(**kwargs):
        pytest.fail("failed free search must not charge through another API")

    monkeypatch.setattr("app.astrbot_search.search_web", failed)
    monkeypatch.setattr("app.astrbot_search.outbound_http_client", paid_forbidden)
    event = Event()
    answer = await native_web_search(context(), event, "public topic")
    assert json.loads(answer)["status"] == "search_failed"
    assert "upstream" not in answer and "dummy-api-key-002" not in answer
    assert await native_web_search(context(), event, "public topic") == answer


@pytest.mark.parametrize(
    "query",
    [
        "QQ号 123456789",
        "群号1000000001",
        "联系13812345678",
        "邮箱user@example.com",
        "api_key=private-value",
    ],
)
async def test_search_privacy_blocks_identifiers_before_any_backend(monkeypatch, query):
    async def forbidden(*args, **kwargs):
        pytest.fail("private material must not reach a public search engine")

    monkeypatch.setattr("app.astrbot_search.search_web", forbidden)
    monkeypatch.setattr("app.astrbot_search._paid_results", forbidden)
    answer = json.loads(await native_web_search(context(), Event(), query))
    assert answer["status"] == "private_query"


async def test_current_group_identifier_cannot_be_searched_as_plain_digits(monkeypatch):
    async def forbidden(*args, **kwargs):
        pytest.fail("actual group ID must never leave the session")

    monkeypatch.setattr("app.astrbot_search.search_web", forbidden)
    event = Event()
    event.get_group_id = lambda: "1000000001"
    answer = json.loads(await native_web_search(context(), event, "1000000001 相关资料"))
    assert answer["status"] == "private_query"


class GroupEvent(Event):
    def get_group_id(self):
        return "42"

    def get_message_str(self):
        return "我打捞到电鳗发电机了"


async def test_group_statement_gets_topic_evidence_and_style_without_explicit_search_command(
    monkeypatch,
):
    calls = []

    async def plan(ctx, event, request):
        assert event.get_message_str() == "我打捞到电鳗发电机了"
        return {"query": "疯狂水世界 电鳗发电机 梗", "recency": "noLimit"}

    async def search(ctx, event, **kwargs):
        calls.append(kwargs)
        return '{"status":"ok","results":[{"title":"public game information"}]}'

    monkeypatch.setattr("app.astrbot_search._plan_group_search", plan)
    monkeypatch.setattr("app.astrbot_search.native_web_search", search)
    request = SimpleNamespace(system_prompt="existing persona and plugins", contexts=[])
    event = GroupEvent()
    await prepare_group_search_context(object(), event, request)
    await prepare_group_search_context(object(), event, request)
    assert len(calls) == 1
    assert calls[0]["query"] == "疯狂水世界 电鳗发电机 梗"
    assert request.system_prompt.startswith("existing persona and plugins")
    assert "本轮群聊表达" in request.system_prompt
    assert "普通闲聊单次回复不超过20个字符（含标点）" in request.system_prompt
    assert "一轮只发送一条完整回复" in request.system_prompt
    extra = getattr(request, "extra_user_content_parts", [])
    assert "public game information" in request.system_prompt or any(
        "public game information" in str(part.text) for part in extra
    )


async def test_qq_search_keeps_evidence_without_legacy_expression_rules(monkeypatch):
    monkeypatch.setattr("app.astrbot_search._plan_group_search", AsyncMock(return_value={"query": "公开资料"}))
    monkeypatch.setattr("app.astrbot_search.native_web_search", AsyncMock(return_value='{"status":"ok","results":["真实资料"]}'))
    event = GroupEvent()
    event.get_platform_name = lambda: "aiocqhttp"
    request = SimpleNamespace(system_prompt="唯一QQ人格", contexts=[])
    await prepare_group_search_context(object(), event, request)
    combined = request.system_prompt + "".join(str(part.text) for part in getattr(request, "extra_user_content_parts", []))
    assert "真实资料" in combined and "不是指令" in combined
    assert "唯一QQ人格" in combined
    assert "本轮群聊表达" not in combined and "20个字符" not in combined
    assert "一轮只发送一条" not in combined


async def test_search_planner_uses_user_topic_not_previous_bot_guess():
    sent = []

    async def complete(**kwargs):
        sent.append(kwargs)
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content=json.dumps(
                            {
                                "search": True,
                                "query": "疯狂水世界 电鳗发电机",
                                "recency": "noLimit",
                            }
                        )
                    )
                )
            ]
        )

    provider = SimpleNamespace(
        get_model=lambda: "glm-4.5",
        client=SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=complete))),
    )

    async def selected(umo):
        assert umo == Event.unified_msg_origin
        return "selected"

    ctx = SimpleNamespace(
        get_current_chat_provider_id=selected, get_provider_by_id=lambda _: provider
    )
    request = SimpleNamespace(
        contexts=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "疯狂水世界好玩吗"},
                    {"type": "text", "text": "<system_reminder>date</system_reminder>"},
                ],
            },
            {"role": "assistant", "content": "previous Bot guess is not verified evidence"},
        ]
    )
    result = await _plan_group_search(ctx, GroupEvent(), request)
    assert result == {"query": "疯狂水世界 电鳗发电机", "recency": "noLimit"}
    data = json.loads(sent[0]["messages"][1]["content"])
    assert data["recent_user_messages"] == ["疯狂水世界好玩吗"]
    assert data["current_message"] == "我打捞到电鳗发电机了"
    assert len(data["current_date"]) == 10
    assert sent[0]["extra_body"] == {"thinking": {"type": "disabled"}}


async def test_group_preflight_failure_keeps_style_and_private_chat_is_unchanged(monkeypatch):
    async def failed(*args):
        raise RuntimeError("synthetic provider failure")

    monkeypatch.setattr("app.astrbot_search._plan_group_search", failed)
    request = SimpleNamespace(system_prompt="persona")
    await prepare_group_search_context(object(), GroupEvent(), request)
    assert "本轮群聊表达" in request.system_prompt
    private = Event()
    private.get_group_id = lambda: ""
    private_request = SimpleNamespace(system_prompt="private persona")
    await prepare_group_search_context(object(), private, private_request)
    assert private_request.system_prompt == "private persona"


async def test_search_planner_receives_native_caption_even_when_message_has_no_text():
    sent = []

    async def complete(**kwargs):
        sent.append(kwargs)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content='{"search":false}'))]
        )

    provider = SimpleNamespace(
        get_model=lambda: "glm-4.5",
        client=SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=complete))),
    )

    async def selected(umo):
        return "selected"

    ctx = SimpleNamespace(
        get_current_chat_provider_id=selected, get_provider_by_id=lambda _: provider
    )
    event = GroupEvent()
    event.get_message_str = lambda: ""
    request = SimpleNamespace(
        contexts=[{"role": "user", "content": "疯狂水世界"}],
        extra_user_content_parts=[
            SimpleNamespace(
                text=(
                    "<image_caption>健康生活梗图：表面说调教，实际上是强制规律作息。</image_caption>"
                )
            )
        ],
    )
    assert await _plan_group_search(ctx, event, request) is None
    data = json.loads(sent[0]["messages"][1]["content"])
    assert data["current_message"] == ""
    assert data["current_image_caption"] == "健康生活梗图：表面说调教，实际上是强制规律作息。"
    assert "最近话题不能覆盖当前图片" in sent[0]["messages"][0]["content"]


@pytest.mark.parametrize(
    "text",
    [
        "说话",
        "你还在吗？",
        "丛雨，回我一下",
        "怎么不回复我！",
        "说你呢，吃白饭的",
        "谢谢",
    ],
)
async def test_local_check_in_skips_extra_model_call_but_keeps_reply_policy(monkeypatch, text):
    async def forbidden(*args, **kwargs):
        pytest.fail("a complete check-in must not occupy the group lock for a search precheck")

    monkeypatch.setattr("app.astrbot_search._plan_group_search", forbidden)
    monkeypatch.setattr("app.astrbot_search.native_web_search", forbidden)
    event = GroupEvent()
    event.get_message_str = lambda: text
    request = SimpleNamespace(
        system_prompt="persona", contexts=[{"role": "user", "content": "旧游戏话题"}]
    )
    await prepare_group_search_context(object(), event, request)
    assert "普通闲聊单次回复不超过20个字符（含标点）" in request.system_prompt
    assert "真实看法" in request.system_prompt


@pytest.mark.parametrize(
    "text",
    [
        "你在吗，今天有啥新闻",
        "在吗，电鳗发电机牛不牛逼",
        "吃白饭的这个梗谁发明的",
        "说话这个新模型什么时候发布",
        "小丛雨，鸣潮最新版本怎么样",
        "我打捞到电鳗发电机了",
    ],
)
async def test_fast_local_chat_path_does_not_suppress_facts_or_unfamiliar_entities(
    monkeypatch, text
):
    calls = []

    async def plan(*args):
        calls.append(True)

    monkeypatch.setattr("app.astrbot_search._plan_group_search", plan)
    event = GroupEvent()
    event.get_message_str = lambda: text
    await prepare_group_search_context(object(), event, SimpleNamespace(system_prompt="persona"))
    assert calls == [True]


async def test_local_chat_with_real_media_still_prepares_media_evidence(monkeypatch):
    from app.astrbot_vision import TurnImageEvidence

    calls = []

    async def plan(*args):
        calls.append(True)

    monkeypatch.setattr("app.astrbot_search._plan_group_search", plan)
    monkeypatch.setattr(
        "app.astrbot_search.prepare_group_image_context",
        lambda *_, **__: TurnImageEvidence(status="ready", caption="当前真实图片含未知新品名称"),
    )
    event = GroupEvent()
    event.get_message_str = lambda: "在吗"
    await prepare_group_search_context(object(), event, SimpleNamespace(system_prompt="persona"))
    assert calls == [True]


@pytest.mark.parametrize("text",["陪我听歌","大肥鱼陪我一起听歌","鲸鱼娘，陪我听歌呀","陪我听歌，小鲸鱼"])
async def test_short_listening_invitation_skips_unrelated_web_planning(monkeypatch,text):
    async def forbidden(*args,**kwargs):
        pytest.fail("a pure listening invitation needs the music tool, not another web-planning call")
    monkeypatch.setattr("app.astrbot_search._plan_group_search",forbidden)
    monkeypatch.setattr("app.astrbot_search.native_web_search",forbidden)
    e=GroupEvent()
    e.get_message_str=lambda:text
    await prepare_group_search_context(object(),e,SimpleNamespace(system_prompt="大肥鱼"))


def test_natural_game_request_keeps_entity_and_drops_recommendation_prefix():
    query = _normalize_public_query("请帮我推荐一套炉石传说标准模式卡组")
    assert "炉石传说" in query and "标准" in query and "卡组" in query
    assert "推荐" not in query and "一套" not in query
    assert not _relevant_result(
        SearchResult("推荐_百度百科", "https://baike.baidu.com/recommend", "介绍推荐一词的含义"),
        query,
    )
    assert not _relevant_result(
        SearchResult("人民日报推荐100本书", "https://example.org/books", "推荐书单"), query
    )
    assert not _relevant_result(
        SearchResult("炉石传说狂野卡组", "https://example.org/wild-decks", "狂野模式牌表"), query
    )
    assert _relevant_result(
        SearchResult("炉石传说标准卡组", "https://example.org/standard-decks", "标准模式牌表"),
        query,
    )


async def test_irrelevant_five_results_trigger_free_backup_and_cannot_be_success(monkeypatch):
    books = [
        SearchResult(f"推荐书单{index}", f"https://example.org/books/{index}", "推荐100本书")
        for index in range(5)
    ]
    primary = AsyncMock(return_value=books)
    backup = AsyncMock(return_value=[])
    monkeypatch.setattr("app.astrbot_search.search_web", primary)
    monkeypatch.setattr("app.astrbot_search._free_fallback_results", backup)
    event = Event()
    answer = await native_web_search(context(), event, "推荐一套炉石传说标准模式卡组")
    payload = json.loads(answer)

    assert payload["status"] == "insufficient_evidence"
    assert not payload["evidence_sufficient"] and payload["results"] == []
    assert backup.await_args.args[0] == primary.await_args.args[0]
    assert "推荐" not in primary.await_args.args[0]
    assert "不生成牌名列表" in payload["guidance"]
    assert await native_web_search(context(), event, "推荐一套炉石传说标准模式卡组") == answer
    assert primary.await_count == backup.await_count == 1
    assert event.extras["qqchat_native_web_search"]["count"] == 1


async def test_relevant_undated_deck_snippet_is_not_a_verified_current_list(monkeypatch):
    source = SearchResult(
        "炉石传说标准法师卡组", "https://example.org/deck", "标准模式法师牌表介绍，日期未知"
    )
    monkeypatch.setattr("app.astrbot_search.search_web", AsyncMock(return_value=[source]))
    payload = json.loads(
        await native_web_search(context(), Event(), "炉石传说 标准 法师 卡组", purpose="correction")
    )
    assert payload["status"] == "insufficient_evidence"
    assert len(payload["results"]) == 1
    assert payload["results"][0]["published_at"] == ""
    assert payload["current_facts_verified"] is False
    assert "没有当前模式有效的完整牌表/代码" in payload["guidance"]
    assert "发明新牌名" in payload["guidance"]


async def test_secondary_engine_uses_only_free_get_without_model_credentials(monkeypatch):
    calls = []

    async def respond(request):
        calls.append(request)
        assert request.method == "GET"
        assert request.url.host == "html.duckduckgo.com"
        assert "authorization" not in request.headers
        return httpx.Response(
            200,
            text='<a class="result__a" href="https://example.org/standard">炉石传说标准卡组</a><a class="result__snippet">标准模式牌表</a>',
        )

    install_http(monkeypatch, respond)
    result = await _free_fallback_results("炉石传说 标准 卡组")
    assert len(result) == 1
    assert calls[0].url.params["q"] == "炉石传说 标准 卡组"


def _planner_context(plan, sent):
    async def complete(**kwargs):
        sent.append(kwargs)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(plan)))]
        )

    async def selected(_umo):
        return "free-model"

    provider = SimpleNamespace(
        get_model=lambda: "glm-4-flash",
        client=SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=complete))),
    )
    return SimpleNamespace(
        get_current_chat_provider_id=selected, get_provider_by_id=lambda _: provider
    )


@pytest.mark.parametrize(
    "message,purpose",
    [
        ("推荐一套炉石传说标准模式卡组", "current_facts"),
        ("你逗我吗，寒冰屏障不是狂野的吗", "correction"),
    ],
)
async def test_current_mode_and_correction_force_verification_even_when_small_model_skips(
    message, purpose
):
    sent = []
    event = GroupEvent()
    event.get_message_str = lambda: message
    result = await _plan_group_search(
        _planner_context({"search": False}, sent), event, SimpleNamespace(contexts=[])
    )
    assert result["purpose"] == purpose
    assert result["recency"] != "noLimit"
    assert "官方" in result["query"]
    assert "旧Bot回复不是证据" in sent[0]["messages"][0]["content"]


async def test_opinion_lookup_preserves_the_entity_without_forcing_meme_results():
    sent = []
    event = GroupEvent()
    event.get_message_str = lambda: "原神牛逼吗"
    result = await _plan_group_search(
        _planner_context({"search": True, "query": "原神", "purpose": "game_opinion"}, sent),
        event,
        SimpleNamespace(contexts=[]),
    )
    assert result["purpose"] == "game_opinion"
    assert result["query"] == "原神"
    assert "普通闲聊单次回复不超过20个字符（含标点）" in _GROUP_REPLY_POLICY
    assert "明确提问、求助" in _GROUP_REPLY_POLICY
    assert "不为普通闲聊自行找理由超长" in _GROUP_REPLY_POLICY
    assert "不强制搞怪开场或三段式" in _GROUP_REPLY_POLICY
    assert "明确的真实看法与具体理由" in _GROUP_REPLY_POLICY
    assert _relevant_result(
        SearchResult("原神官方网站", "https://example.org/genshin", "开放世界游戏介绍"),
        result["query"],
    )


async def test_new_function_question_is_not_forced_to_search_old_card_topic():
    sent = []
    event = GroupEvent()
    event.get_message_str = lambda: "主动消息"
    request = SimpleNamespace(contexts=[{"role": "user", "content": "炉石传说标准模式卡组"}])
    result = await _plan_group_search(_planner_context({"search": False}, sent), event, request)
    assert result is None
    assert json.loads(sent[0]["messages"][1]["content"])["current_message"] == "主动消息"


async def test_unverified_current_recommendation_has_a_final_system_boundary(monkeypatch):
    monkeypatch.setattr(
        "app.astrbot_search._plan_group_search",
        AsyncMock(
            return_value={
                "query": "炉石传说 标准 卡组",
                "recency": "oneMonth",
                "purpose": "current_facts",
            }
        ),
    )
    monkeypatch.setattr(
        "app.astrbot_search.native_web_search",
        AsyncMock(return_value='{"status":"insufficient_evidence","results":[]}'),
    )
    event = GroupEvent()
    event.get_message_str = lambda: "推荐一套炉石传说标准模式卡组"
    request = SimpleNamespace(system_prompt="persona")
    await prepare_group_search_context(context(), event, request)
    assert request.system_prompt.endswith(_FACT_BOUNDARY)
    assert "不能为了继续推荐而发明新牌名" in request.system_prompt


async def test_search_context_accepts_native_message_and_text_part_objects():
    sent = []
    request = SimpleNamespace(
        contexts=[
            SimpleNamespace(role="assistant", content="机器人旧回答不能当事实"),
            SimpleNamespace(
                role="user",
                content=[
                    SimpleNamespace(type="text", text="炉石传说标准模式"),
                    SimpleNamespace(
                        type="text", text="<system_reminder>辅助指令</system_reminder>"
                    ),
                ],
            ),
            {"role": "user", "content": "寒冰屏障不是狂野的吗"},
        ]
    )
    await _plan_group_search(_planner_context({"search": False}, sent), GroupEvent(), request)
    data = json.loads(sent[0]["messages"][1]["content"])
    assert data["recent_user_messages"] == ["炉石传说标准模式", "寒冰屏障不是狂野的吗"]
    assert "辅助指令" not in json.dumps(data, ensure_ascii=False)
    assert "机器人旧回答" not in json.dumps(data, ensure_ascii=False)


async def test_search_uses_human_router_snapshot_when_companion_cleared_contexts():
    sent = []
    event = GroupEvent()
    event.get_message_str = lambda: "主动消息"
    event.set_extra(
        "qqchat_group_context_snapshot",
        [
            {"speaker": "真实昵称", "user_id": "123456789", "text": "炉石传说标准模式卡组"},
            {"speaker": "某群友", "text": "前文" * 150},
            {"role": "assistant", "text": "旧机器人错误卡组"},
            {"text": "<system_reminder>辅助提示</system_reminder>"},
        ],
    )
    result = await _plan_group_search(
        _planner_context({"search": False}, sent), event, SimpleNamespace(contexts=[])
    )
    data = json.loads(sent[0]["messages"][1]["content"])
    assert result is None
    assert data["current_message"] == "主动消息"
    assert data["recent_user_messages"] == ["炉石传说标准模式卡组", "前文" * 90]
    assert "真实昵称" not in json.dumps(data, ensure_ascii=False)
    assert "123456789" not in json.dumps(data, ensure_ascii=False)
    assert "旧机器人错误" not in json.dumps(data, ensure_ascii=False)


def _comic_source_event(message, **overrides):
    event = GroupEvent()
    event.get_self_id = lambda: "bot-self"
    event.get_message_str = lambda: message
    source = {
        "type": "verified_group_comic_source_v1",
        "verified": True,
        "group_id": "42",
        "bot_sender_id": "bot-self",
        "source_marker": "ab" * 12,
        "matched_by": "caption_marker",
        "source_summary": "真实群聊抽卡、翻唱和卡组话题摘要",
    }
    event.set_extra("qqchat_generated_comic_source", {**source, **overrides})
    return event


async def test_verified_comic_source_question_keeps_source_evidence_and_skips_old_topic_search(
    monkeypatch,
):
    plan = AsyncMock(side_effect=AssertionError("核验过的漫画来源不应再搜旧炉石话题"))
    monkeypatch.setattr("app.astrbot_search._plan_group_search", plan)
    event = _comic_source_event("这张漫画什么意思，为什么这么画？")
    request = SimpleNamespace(
        system_prompt="已核验创作来源；不能把创作意图当成看到画面", contexts=[]
    )
    await prepare_group_search_context(context(), event, request)
    assert not plan.called
    assert "已核验创作来源" in request.system_prompt
    assert "不能把创作意图当成看到画面" in request.system_prompt


@pytest.mark.parametrize(
    "message,overrides",
    [
        ("这图里提到的炉石卡组现在标准能用吗", {}),
        ("这张图啥意思", {"verified": False}),
        ("这张图啥意思", {"type": "user_claimed_source"}),
        ("这张图啥意思", {"group_id": "another-group"}),
        ("这张图啥意思", {"bot_sender_id": "another-bot"}),
        ("这张图啥意思", {"matched_by": "text_guess"}),
    ],
)
async def test_comic_source_does_not_bypass_external_facts_or_provenance_checks(
    monkeypatch, message, overrides
):
    plan = AsyncMock(return_value=None)
    monkeypatch.setattr("app.astrbot_search._plan_group_search", plan)
    await prepare_group_search_context(
        context(),
        _comic_source_event(message, **overrides),
        SimpleNamespace(system_prompt="persona", contexts=[]),
    )
    assert plan.await_count == 1


@pytest.mark.parametrize(
    "modalities,keeps_images",
    [
        (["text", "tool_use"], False),
        (["text", "image", "tool_use"], True),
        (None, True),
        ([], True),
    ],
)
async def test_current_provider_capabilities_control_failed_image_text_fallback(
    modalities, keeps_images
):
    selected = AsyncMock(return_value="current-provider")
    provider = SimpleNamespace(modalities=modalities)
    ctx = SimpleNamespace(
        get_current_chat_provider_id=selected,
        get_provider_by_id=lambda provider_id: (
            provider if provider_id == "current-provider" else None
        ),
    )
    event = GroupEvent()
    event.get_message_str = lambda: "[图片]"
    event.message_obj = SimpleNamespace(message=[{"type": "image"}])
    image = {"type": "image_url", "image_url": {"url": "current.jpg"}}
    failure = SimpleNamespace(type="text", text="[Image Captioning Failed]")
    source = SimpleNamespace(type="text", text="真实来源文字")
    request = SimpleNamespace(
        system_prompt="persona",
        contexts=[],
        image_urls=["current.jpg"],
        extra_user_content_parts=[failure, image, source],
    )

    await prepare_group_search_context(ctx, event, request, enabled=False)

    selected.assert_awaited_once_with(event.unified_msg_origin)
    assert bool(request.image_urls) is keeps_images
    assert (image in request.extra_user_content_parts) is keeps_images
    assert (
        failure in request.extra_user_content_parts and source in request.extra_user_content_parts
    )
    assert event.message_obj.message == [{"type": "image"}]


async def test_text_only_provider_keeps_images_after_successful_native_caption():
    selected = AsyncMock(return_value="text-provider")
    ctx = SimpleNamespace(
        get_current_chat_provider_id=selected,
        get_provider_by_id=lambda _: SimpleNamespace(modalities=["text"]),
    )
    event = GroupEvent()
    event.get_message_str = lambda: "[图片]"
    image = {"type": "image_url", "image_url": {"url": "current.jpg"}}
    caption = SimpleNamespace(
        type="text", text="<image_caption>本轮实际画面已成功识别</image_caption>"
    )
    request = SimpleNamespace(
        system_prompt="persona",
        contexts=[],
        image_urls=["current.jpg"],
        extra_user_content_parts=[caption, image],
    )

    await prepare_group_search_context(ctx, event, request, enabled=False)

    assert request.image_urls == ["current.jpg"]
    assert image in request.extra_user_content_parts
    assert "本轮已获得视觉摘要" in request.system_prompt

