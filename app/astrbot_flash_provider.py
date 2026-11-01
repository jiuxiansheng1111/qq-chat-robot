"""仅为 GLM-4-Flash 的纯文本请求兼容原生 AstrBot 多段文字。"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Mapping
from typing import Any

PROVIDER_TYPE = "qqchat_glm_flash"
_LOGGER = logging.getLogger(__name__)


def _busy_error(exc: Exception) -> bool:
    # 只判断，不记录可能包含请求数据的异常原文。
    return type(exc).__name__ == "RateLimitError" or "1302" in str(exc)


def _last_user_shape(payload: Mapping[str, Any]) -> dict[str, Any]:
    messages = payload.get("messages")
    messages = messages if isinstance(messages, list) else []
    message = next((m for m in reversed(messages) if isinstance(m, Mapping) and m.get("role") == "user"), {})
    content = message.get("content")
    parts = content if isinstance(content, list) else []
    text_parts = [p for p in parts if isinstance(p, Mapping) and p.get("type") == "text"]
    keys = {str(key)[:48] for part in text_parts for key in part if key not in {"type", "text"}}
    return {
        "content_type": type(content).__name__,
        "text_parts": len(text_parts),
        "image_parts": sum(isinstance(p, Mapping) and p.get("type") in {"image", "image_url"} for p in parts),
        "text_extra_keys": sorted(keys)[:16],
    }


def flatten_flash_text_parts(payload: dict[str, Any]) -> None:
    """保持文本顺序和段落；混合媒体、工具消息及其他型号不改写。"""
    model = str(payload.get("model", "")).strip().casefold()
    if model != "glm-4-flash" and not model.startswith("glm-4-flash-"):
        return
    messages = payload.get("messages")
    if not isinstance(messages, list):
        return
    for message in messages:
        if not isinstance(message, dict) or message.get("tool_calls") or message.get("role") == "tool":
            continue
        content = message.get("content")
        if not isinstance(content, list) or not content:
            continue
        if all(
            isinstance(part, Mapping)
            and part.get("type") == "text"
            and isinstance(part.get("text"), str)
            and set(part) <= {"type", "text"}
            for part in content
        ):
            message["content"] = "\n\n".join(part["text"] for part in content)


def register_flash_provider() -> type:
    """插件初始化时注册，早于 AstrBot 的 Provider 加载；重复加载幂等。"""
    from astrbot.core.provider.register import provider_cls_map, register_provider_adapter
    from astrbot.core.provider.sources.openai_source import ProviderOpenAIOfficial

    existing = provider_cls_map.get(PROVIDER_TYPE)
    if existing is not None:
        cls = existing.cls_type
        if cls is not None and getattr(cls, "qqchat_flash_text_compatibility", False):
            return cls
        raise RuntimeError(f"Provider adapter type already registered: {PROVIDER_TYPE}")

    @register_provider_adapter(
        PROVIDER_TYPE,
        "GLM-4-Flash 纯文本分段兼容（QQChat）",
        provider_display_name="GLM-4-Flash 文本兼容",
    )
    class QQChatFlashProvider(ProviderOpenAIOfficial):
        qqchat_flash_text_compatibility = True

        async def text_chat(self, *args: Any, **kwargs: Any) -> Any:
            try:
                return await super().text_chat(*args, **kwargs)
            except Exception as exc:
                if _busy_error(exc):
                    self._qqchat_repair_busy_until = time.monotonic() + 120
                raise

        def qqchat_repair_busy(self) -> bool:
            """主请求刚遇到限流时，额外纠正直接使用本地诚实提示。"""
            return time.monotonic() < getattr(self, "_qqchat_repair_busy_until", 0)

        async def qqchat_repair_once(
            self, *, prompt: str, system_prompt: str, deadline_seconds: float = 12,
        ) -> Any:
            """同一个免费Flash接口的一次纯文本纠正，不走native多Key重试或fallback。"""
            if str(self.get_model()).casefold() not in {"glm-4-flash", "glm-4-flash-250414"}:
                raise ValueError("One-shot repair is restricted to the configured free Flash model")
            if self.qqchat_repair_busy():
                raise RuntimeError("Free Flash provider is cooling down")
            deadline = min(12.0, max(0.01, float(deadline_seconds)))
            try:
                async with asyncio.timeout(deadline):
                    payload, _ = await self._prepare_chat_payload(
                        prompt=prompt, contexts=[], system_prompt=system_prompt,
                    )
                    payload.update(max_tokens=1024, temperature=0.7)
                    self._sanitize_assistant_messages(payload)
                    extra_body: dict[str, Any] = {}
                    self._apply_provider_specific_request_overrides(payload, extra_body)
                    client = self.client.with_options(max_retries=0, timeout=deadline)
                    completion = await client.chat.completions.create(
                        **payload, stream=False, extra_body=extra_body,
                    )
                    return await self._parse_openai_completion(completion, None)
            except Exception as exc:
                if _busy_error(exc):
                    self._qqchat_repair_busy_until = time.monotonic() + 120
                raise

        def _finally_convert_payload(self, payloads: dict) -> None:
            super()._finally_convert_payload(payloads)
            before = _last_user_shape(payloads)
            flatten_flash_text_parts(payloads)
            after = _last_user_shape(payloads)
            _LOGGER.info(
                "Flash请求结构：provider=%s model=%s last_user=%s→%s text_parts=%s image_parts=%s text_extra_keys=%s。",
                self.provider_config.get("id", ""), payloads.get("model", ""),
                before["content_type"], after["content_type"], before["text_parts"],
                before["image_parts"], before["text_extra_keys"],
            )

        async def _parse_openai_completion(self, completion: Any, tools: Any) -> Any:
            response = await super()._parse_openai_completion(completion, tools)
            choices = getattr(completion, "choices", None) or []
            finish = getattr(choices[0], "finish_reason", None) if choices else None
            usage = getattr(completion, "usage", None)
            _LOGGER.info(
                "Flash回复完成：provider=%s model=%s finish_reason=%s output_tokens=%s visible_chars=%s。",
                self.provider_config.get("id", ""), getattr(completion, "model", ""), finish,
                getattr(usage, "completion_tokens", None), len(response.completion_text or ""),
            )
            return response

    return QQChatFlashProvider
