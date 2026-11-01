"""AstrBot Responses adapter backed by the bot's own ChatGPT OAuth grant."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

from app.chatgpt_subscription import API_BASE, DEFAULT_STORE, ChatGPTSubscription, SubscriptionError

PROVIDER_TYPE = "qqchat_chatgpt_subscription"
TOOL_NAMESPACE = "qqchat"


def subscription_payload(payload: dict, extra: dict, tool_schema: list[dict]) -> dict:
    """Use the documented subscription contract, keeping history and tool IDs."""
    body = {key: copy.deepcopy(payload[key]) for key in (
        "model", "input", "instructions", "reasoning", "text", "include", "parallel_tool_calls",
    ) if key in payload}
    # Legacy max_tokens/temperature and server-side history are not accepted on
    # this route. Do not copy arbitrary provider options into subscription calls.
    for key in ("reasoning", "text"):
        if key in extra:
            body[key] = copy.deepcopy(extra[key])
    if not isinstance(body.get("input"), list):
        raise SubscriptionError("ChatGPT 订阅请求缺少完整上下文")
    for item in body["input"]:
        if isinstance(item, dict) and item.get("role") == "system":
            item["role"] = "developer"
        if isinstance(item, dict) and item.get("type") == "function_call":
            item["namespace"] = TOOL_NAMESPACE
    functions = []
    for tool in tool_schema:
        function = tool.get("function", {})
        if tool.get("type") != "function" or not function.get("name"):
            raise SubscriptionError("ChatGPT 订阅路径仅支持本机函数工具")
        functions.append({"type": "function", **copy.deepcopy(function), "strict": False})
    if functions:
        body["tools"] = [{
            "type": "namespace", "name": TOOL_NAMESPACE,
            "description": "QQ 机器人的本机工具，沿用各工具的权限和配置。", "tools": functions,
        }]
    body.update(store=False, stream=True)
    return body


def register_chatgpt_provider() -> type:
    from astrbot.core.provider.register import provider_cls_map, register_provider_adapter
    from astrbot.core.provider.sources.openai_responses_source import ProviderOpenAIResponses

    existing = provider_cls_map.get(PROVIDER_TYPE)
    if existing is not None:
        if getattr(existing.cls_type, "qqchat_subscription", False):
            return existing.cls_type
        raise RuntimeError(f"Provider adapter type already registered: {PROVIDER_TYPE}")

    @register_provider_adapter(
        PROVIDER_TYPE, "ChatGPT 订阅授权（QQChat）",
        provider_display_name="ChatGPT 订阅 · Responses",
    )
    class QQChatGPTProvider(ProviderOpenAIResponses):
        qqchat_subscription = True

        def __init__(self, provider_config: dict, provider_settings: dict):
            config = {**provider_config, "api_base": API_BASE, "key": ["oauth-managed-locally"]}
            super().__init__(config, provider_settings)
            directory = Path(config.get("chatgpt_credentials_dir") or DEFAULT_STORE)
            self.subscription = ChatGPTSubscription(directory, timeout=float(self.timeout))

        async def _query(
            self, payloads: dict, tools: Any, *, request_max_retries: int | None = None,
        ) -> Any:
            extra = self.provider_config.get("custom_extra_body") or {}
            body = subscription_payload(payloads, extra, tools.openai_schema() if tools else [])
            response = await self.subscription.respond(body)
            # AstrBot retains local function names. The namespace is restored
            # on the next request's function_call items by subscription_payload.
            for item in response.get("output", []):
                if item.get("type") == "function_call":
                    name = item.get("name", "")
                    if name.startswith(f"{TOOL_NAMESPACE}."):
                        item["name"] = name[len(TOOL_NAMESPACE) + 1:]
            return await self._parse_response(response, tools)

        async def _query_stream(
            self, payloads: dict, tools: Any, *, request_max_retries: int | None = None,
        ):
            # Publish only completed replies: late quota errors must not turn
            # partial output or partial tool arguments into a successful reply.
            yield await self._query(payloads, tools, request_max_retries=request_max_retries)

        async def get_models(self):
            return [item["slug"] for item in await self.subscription.list_models()]

        async def terminate(self):
            try:
                await self.subscription.aclose()
            finally:
                await super().terminate()

    return QQChatGPTProvider
