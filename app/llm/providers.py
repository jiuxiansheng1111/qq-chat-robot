import httpx

from app.services.http_routing import outbound_http_client


class LLMError(RuntimeError):
    pass


def describe_llm_error(exc: Exception) -> str:
    """对 LLM 故障分类，方便日志显示实际原因。"""
    text = str(exc)
    if "circuit open" in text:
        return "circuit_open"
    if text == "llm_queue_full":
        return "queue_full"
    if text == "llm_queue_timeout":
        return "queue_timeout"
    if "API key 未配置" in text:
        return "missing_api_key"
    if "temporary error: 429" in text or "HTTP 429" in text:
        return "rate_limited"
    if "invalid response" in text:
        return "invalid_response"
    if "empty response" in text:
        return "empty_response"
    if isinstance(exc, httpx.TimeoutException) or "APITimeoutError" in text:
        return "timeout"
    if isinstance(exc, httpx.ConnectError) or "APIConnectionError" in text:
        return "connect_error"
    if isinstance(exc, httpx.HTTPStatusError):
        return f"http_{exc.response.status_code}"
    if isinstance(exc, httpx.HTTPError):
        return "network_error"
    return "llm_error"


def llm_failure_reply(exc: Exception) -> str:
    reason = describe_llm_error(exc)
    if reason in {"queue_full", "queue_timeout", "rate_limited"}:
        return "苟修金，聊天请求有点多，请稍后再试一下吧。"
    if reason in {"connect_error", "network_error"}:
        return "苟修金，吾辈暂时连不上聊天模型，请稍后再试一下吧。"
    if reason == "timeout":
        return "苟修金，聊天模型回复超时了，请稍后再试一下吧。"
    if reason == "circuit_open":
        return "苟修金，聊天模型连续调用失败，吾辈会在稍后重新尝试。"
    return "苟修金，聊天模型暂时出错了，请稍后再试一下吧。"


class OpenAICompatibleProvider:
    def __init__(self, name: str, base_url: str, api_key: str, model: str, timeout: float, max_tokens: int, temperature: float):
        self.name = name
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.client = outbound_http_client(timeout=timeout)

    async def chat(self, messages: list[dict]) -> str:
        if not self.api_key:
            raise LLMError(f"{self.name} API key 未配置")
        response = await self.client.post(
            f"{self.base_url}/chat/completions",
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
            json={"model": self.model, "messages": messages, "max_tokens": self.max_tokens, "temperature": self.temperature},
        )
        if response.status_code in (429, 500, 502, 503, 504):
            raise LLMError(f"{self.name} temporary error: {response.status_code}")
        if response.status_code >= 400:
            raise LLMError(f"{self.name} error: {response.status_code}")
        try:
            content = response.json()["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError(f"{self.name} invalid response") from exc
        if not isinstance(content, str) or not content.strip():
            raise LLMError(f"{self.name} empty response")
        return content

    async def aclose(self) -> None:
        await self.client.aclose()
