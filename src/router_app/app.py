from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import ORJSONResponse

from router_app.api.routes import router
from router_app.config_source import HTTPConfigSource
from router_app.core.service import RouterService
from router_app.modeling import AgentScopePlanner
from router_app.settings import Settings, get_settings
from router_app.store import InMemorySessionStore, RedisSessionStore, SessionStore


def create_app(
    *,
    settings: Settings | None = None,
    service: RouterService | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    app = FastAPI(
        title=settings.app_name,
        default_response_class=ORJSONResponse,
    )
    if service is None:
        service = create_service(settings)
    app.state.router_service = service
    app.include_router(router)
    return app


def create_service(settings: Settings) -> RouterService:
    config_source = HTTPConfigSource(
        settings.config_source_base_url or "http://127.0.0.1:18080",
        timeout_seconds=settings.config_request_timeout_seconds,
    )
    store: SessionStore
    if settings.store_backend == "redis":
        store = RedisSessionStore(settings.redis_url, lock_ttl_ms=settings.redis_lock_ttl_ms)
    else:
        store = InMemorySessionStore()
    # RouterService 只依赖 Planner 抽象；这里注入 AgentScope OpenAI-compatible 模型适配实现。
    planner = AgentScopePlanner(
        model_name=settings.qwen_model or "qwen-plus",
        api_key=settings.qwen_api_key or "",
        base_http_api_url=settings.qwen_base_http_api_url,
        retry_count=settings.llm_retry_count,
    )
    return RouterService(settings=settings, config_source=config_source, store=store, planner=planner)
