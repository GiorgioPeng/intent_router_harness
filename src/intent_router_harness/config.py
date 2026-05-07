from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class AppSettings(BaseSettings):
    """Runtime settings for deployable harness service."""

    model_config = SettingsConfigDict(
        env_prefix="INTENT_ROUTER_HARNESS_",
        env_file=".env",
        extra="ignore",
    )

    spec_path: Path = Path("examples/finance-router-harness.toml")
    regression_suite_path: Path | None = Path("regressions/assistant_protocol_v0_6.json")
    llm_env_file: Path | None = Path(".env.local")
    skill_roots: list[str] = Field(default_factory=list)
    expose_prompt_payloads: bool = True
    log_level: str = "INFO"


class ReadinessStatus(BaseModel):
    """Readiness response model."""

    ready: bool
    service: str = "intent_router_harness"
    llm_configured: bool
    regression_suite_loaded: bool
    surfaces: list[str]
