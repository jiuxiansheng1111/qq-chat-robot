import json
from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_env: str = "development"
    log_level: str = "INFO"
    database_path: str = "./data/qqchat.db"
    redis_url: str = ""
    event_dedupe_ttl_seconds: int = 300
    user_rate_limit_per_minute: int = 5
    group_rate_limit_per_minute: int = 60
    plugin_modules: str = "app.plugins.hello"

    jwt_secret_key: str = Field(default="change-me", repr=False)
    jwt_issuer: str = "qqchat-robot"
    jwt_audience: str = "qqchat-admin"
    access_token_expire_minutes: int = 20
    refresh_token_expire_days: int = 14
    admin_username: str = "admin"
    admin_password: str = Field(default="change-me-now", repr=False)

    onebot_api_base: str = ""
    onebot_access_token: str = Field(default="", repr=False)
    onebot_webhook_token: str = Field(default="", repr=False)
    onebot_self_id: str = ""

    llm_provider: str = "zhipu"
    llm_model: str = "glm-4.7-flash"
    llm_base_url: str = "https://open.bigmodel.cn/api/paas/v4"
    llm_api_key: str = Field(default="", repr=False)
    llm_fallback_provider: str = "groq"
    groq_base_url: str = "https://api.groq.com/openai/v1"
    groq_model: str = "openai/gpt-oss-20b"
    groq_api_key: str = Field(default="", repr=False)
    llm_timeout_seconds: float = 45
    llm_max_output_tokens: int = 500
    llm_temperature: float = 0.7
    llm_max_concurrency: int = 10
    llm_queue_size: int = 30
    llm_queue_timeout_seconds: float = 8

    persona_name: str = "阿柚"
    persona_prompt_file: str = "prompts/persona.txt"
    persona_examples_file: str = "prompts/examples.jsonl"
    max_context_messages: int = 10
    media_max_bytes: int = 5 * 1024 * 1024
    media_timeout_seconds: float = 60
    media_retry_attempts: int = 2
    cat_api_url: str = "https://cataas.com/cat/gif"
    pig_api_url: str = "https://commons.wikimedia.org/w/api.php"

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    def persona_prompt(self) -> str:
        path = Path(self.persona_prompt_file)
        prompt = path.read_text(encoding="utf-8").strip() if path.exists() else f"你是{self.persona_name}，请用自然、简短、友善的中文回答。"
        examples_path = Path(self.persona_examples_file)
        if examples_path.exists():
            examples = []
            for line in examples_path.read_text(encoding="utf-8").splitlines():
                try:
                    item = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(item, dict) and isinstance(item.get("messages"), list):
                    examples.append(json.dumps(item["messages"], ensure_ascii=False))
            if examples:
                prompt += "\n\n以下是语气示例，只模仿表达方式，不要编造示例中的事实：\n" + "\n".join(examples[:8])
        return prompt

    def validate_security(self) -> None:
        if self.app_env.lower() != "production":
            return
        if len(self.jwt_secret_key) < 32 or self.jwt_secret_key in {"change-me", "replace-with-a-long-random-secret"}:
            raise RuntimeError("生产环境必须配置至少 32 字符的 JWT_SECRET_KEY")
        if self.admin_password in {"change-me-now", "password"} or len(self.admin_password) < 12:
            raise RuntimeError("生产环境必须配置至少 12 字符的 ADMIN_PASSWORD")


@lru_cache
def get_settings() -> Settings:
    return Settings()
