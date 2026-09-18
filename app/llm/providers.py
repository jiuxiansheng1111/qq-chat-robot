import httpx


class LLMError(RuntimeError):
    pass


def describe_llm_error(exc: Exception) -> str:
    """Classify an LLM failure so logs reveal the actual cause."""
    text = str(exc)
    if "circuit open" in text:
        return "circuit_open"
    if text == "llm_queue_full":
        return "queue_full"
    if text == "llm_queue_timeout":
        return "queue_timeout"
    if "API key 未配置" in text:
        return "missing_api_key"
    if "temporary error: 429" in text:
        return "rate_limited"
    if "invalid response" in text:
        return "invalid_response"
    if "empty response" in text:
        return "empty_response"
    if isinstance(exc, httpx.TimeoutException):
        return "timeout"
    if isinstance(exc, httpx.ConnectError):
        return "connect_error"
    if isinstance(exc, httpx.HTTPStatusError):
        return f"http_{exc.response.status_code}"
    if isinstance(exc, httpx.HTTPError):
        return "network_error"
    return "llm_error"


class OpenAICompatibleProvider:
    def __init__(self, name: str, base_url: str, api_key: str, model: str, timeout: float, max_tokens: int, temperature: float):
        self.name = name
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.client = httpx.AsyncClient(timeout=timeout)

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
