from functools import lru_cache
from typing import Literal

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # pydantic-settings 会自动从环境变量和 .env 读取配置；extra=ignore 允许本地保留无关变量。
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", populate_by_name=True)

    app_name: str = "agentscope-router-intent"
    environment: Literal["local", "test", "staging", "production"] = "local"

    qwen_api_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices("OPENAI_API_KEY", "QWEN_API_KEY", "DASHSCOPE_API_KEY"),
    )
    # 兼容 OPENAI_*、QWEN_* 和 DASHSCOPE_* 命名；业务代码内部暂沿用 qwen_* 字段。
    qwen_base_http_api_url: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "OPENAI_BASE_URL",
            "QWEN_BASE_URL",
            "QWEN_BASE_HTTP_API_URL",
            "DASHSCOPE_BASE_URL",
            "DASHSCOPE_BASE_HTTP_API_URL",
        ),
    )
    qwen_model: str | None = Field(
        default=None,
        validation_alias=AliasChoices("OPENAI_MODEL", "QWEN_MODEL", "DASHSCOPE_MODEL"),
    )

    config_source_base_url: str | None = Field(
        default=None,
        validation_alias="CONFIG_SOURCE_BASE_URL",
    )
    config_request_timeout_seconds: float = Field(default=3.0)

    store_backend: Literal["memory", "redis"] = Field(default="memory")
    redis_url: str = Field(default="redis://localhost:6379/0")
    session_ttl_seconds: int = Field(default=30 * 60)
    redis_lock_ttl_ms: int = Field(default=10_000)

    llm_retry_count: int = Field(default=1)
    handoff_request_timeout_seconds: float = Field(default=5.0)
    production_trace_redact_body: bool = Field(default=True)

    @field_validator("session_ttl_seconds")
    @classmethod
    def validate_session_ttl(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("session_ttl_seconds must be positive")
        return value

    @property
    def ready_model_configured(self) -> bool:
        # readyz 只检查必要模型配置是否存在，不在健康检查阶段实际调用外部模型。
        return bool(self.qwen_api_key and self.qwen_model)

    @property
    def ready_config_source_configured(self) -> bool:
        return bool(self.config_source_base_url)


@lru_cache
def get_settings() -> Settings:
    return Settings()
