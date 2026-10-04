"""复用 AstrBot 的模型，角色和聊天记录仍由原来的功能管理。"""

from __future__ import annotations

import copy
import logging
from typing import Any

from app.llm.providers import LLMError
from app.llm.resilience import CircuitBreaker, ProviderStats

logger = logging.getLogger(__name__)


def _model_config(context: Any) -> dict:
    config = context.get_config()
    runner = config.get("agent_runner", {})
    if not isinstance(runner, dict):
        raise LLMError("AstrBot 聊天配置格式无效")
    if runner.get("runner_type") != "local":
        return {}
    runner_config = runner.get("config", {})
    if not isinstance(runner_config, dict):
        raise LLMError("AstrBot 聊天配置格式无效")
    model = runner_config.get("model", {})
    if not isinstance(model, dict):
        raise LLMError("AstrBot 模型配置格式无效")
    return model


class AstrBotChatProvider:
    """每次读取面板里的主模型和备用模型，不另存一份密钥。"""

    def __init__(self, context: Any):
        self.context = context

    async def chat(self, messages: list[dict]) -> str:
        config = _model_config(self.context)
        primary = config.get("provider_id", "")
        if not isinstance(primary, str) or not primary.strip():
            raise LLMError("AstrBot 默认聊天模型未配置")
        fallbacks = config.get("fallback_provider_ids", []) or []
        if not isinstance(fallbacks, (list, tuple)) or any(
            not isinstance(item, str) or not item.strip() for item in fallbacks
        ):
            raise LLMError("AstrBot 备用模型配置无效")
        ids = list(dict.fromkeys([primary, *fallbacks]))
        try:
            retries = max(1, int(config.get("request_max_retries", 1)))
        except (TypeError, ValueError) as exc:
            raise LLMError("AstrBot 模型重试配置无效") from exc
        last_error: LLMError | None = None
        for provider_id in ids:
            provider = self.context.get_provider_by_id(provider_id)
            if not callable(getattr(provider, "text_chat", None)):
                last_error = LLMError("AstrBot 聊天模型未启用或未加载")
                continue
            try:
                # 原来的 system prompt、每用户记忆和角色选择直接传给模型。
                result = await provider.text_chat(
                    contexts=copy.deepcopy(messages), request_max_retries=retries,
                )
                content = result.completion_text
                if not isinstance(content, str) or not content.strip():
                    raise LLMError("AstrBot empty response")
                return content
            except Exception as exc:  # noqa: BLE001
                # 不把上游响应正文或密钥拼进错误消息。
                status = getattr(exc, "status_code", None)
                category = f"HTTP {status}" if isinstance(status, int) else type(exc).__name__
                last_error = LLMError(f"AstrBot 模型调用失败：{category}")
        raise last_error or LLMError("AstrBot 聊天模型不可用")

    async def aclose(self) -> None:
        """模型连接由 AstrBot 统一关闭。"""


async def bind_astrbot_models(manager: Any, context: Any) -> bool:
    """换掉模型连接，沿用原来的排队、并发限制和服务引用。"""
    if not _model_config(context).get("provider_id"):
        return False
    original = {id(manager.provider): manager.provider, id(manager.fallback): manager.fallback}
    adapter = AstrBotChatProvider(context)
    manager.provider = manager.fallback = adapter
    manager.provider_name = manager.fallback_name = "astrbot"
    manager.breakers = {"astrbot": CircuitBreaker()}
    manager.stats = {"astrbot": ProviderStats()}
    for provider in original.values():
        close = getattr(provider, "aclose", None)
        if close:
            try:
                await close()
            except Exception as exc:  # noqa: BLE001
                # 清理一个旧客户端失败时仍关闭其他客户端，避免启动后半绑定状态。
                logger.warning("关闭旧模型客户端失败：%s", type(exc).__name__)
    logger.info("角色聊天已接入 AstrBot 模型配置。")
    return True
