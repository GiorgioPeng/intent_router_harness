from __future__ import annotations

from typing import Any
from urllib.parse import quote

import httpx
from pydantic import ValidationError

from router_app.config_source.base import ConfigSource, ConfigSourceError, ConfigValidationError
from router_app.core.schemas import (
    ReferenceBody,
    SkillBody,
    SkillIndex,
    TraceEvent,
    validate_reference_key,
    validate_skill_index_payload,
)


class HTTPConfigSource(ConfigSource):
    """HTTP JSON config source with ETag-based index refresh."""

    def __init__(
        self,
        base_url: str,
        *,
        timeout_seconds: float = 3.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout_seconds
        # 配置中心是服务端内部依赖；忽略 HTTP_PROXY/HTTPS_PROXY，避免本地 mock 或内网地址被代理转发成 502。
        self._client = client or httpx.AsyncClient(
            base_url=self._base_url,
            timeout=self._timeout,
            trust_env=False,
        )
        self._owns_client = client is None
        self._etag: str | None = None
        self._last_good_index: SkillIndex | None = None

    async def refresh_index(self, trace: list[TraceEvent] | None = None) -> SkillIndex | None:
        headers = {}
        if self._etag:
            headers["If-None-Match"] = self._etag
        emit(trace, "config.refresh_start", {"etag": self._etag})
        try:
            response = await self._client.get("/v1/router/skills/index", headers=headers)
        except httpx.HTTPError as exc:
            emit(trace, "config.refresh_failed", {"reason": type(exc).__name__})
            if self._last_good_index:
                return self._last_good_index
            raise ConfigSourceError("failed to refresh skill index") from exc

        if response.status_code == 304 and self._last_good_index:
            emit(
                trace,
                "config.refresh_not_modified",
                {"etag": self._etag, "version": self._last_good_index.version},
            )
            return self._last_good_index

        if response.status_code >= 400:
            body = response.text[:300]
            emit(trace, "config.refresh_failed", {"statusCode": response.status_code, "body": body})
            if self._last_good_index:
                return self._last_good_index
            raise ConfigSourceError(f"config index request failed: {response.status_code}: {body}")

        etag = response.headers.get("ETag")
        try:
            payload: Any = response.json()
            index = validate_skill_index_payload(payload, etag=etag)
        except (ValueError, ValidationError) as exc:
            emit(trace, "config.refresh_rejected", {"reason": str(exc)[:300]})
            if self._last_good_index:
                return self._last_good_index
            raise ConfigValidationError("invalid skill index payload") from exc

        self._etag = etag or index.etag or index.version
        self._last_good_index = index
        emit(
            trace,
            "config.refresh_loaded",
            {
                "etag": self._etag,
                "version": index.version,
                "skillCount": len(index.skills),
            },
        )
        return index

    async def get_last_good_index(self) -> SkillIndex | None:
        return self._last_good_index

    async def load_skill_body(self, skill_id: str, version: str, trace: list[TraceEvent] | None = None) -> SkillBody:
        emit(trace, "skill.body_load_start", {"skillId": skill_id, "version": version})
        try:
            response = await self._client.get(
                f"/v1/router/skills/{quote(skill_id, safe='')}/body",
                params={"version": version},
            )
            response.raise_for_status()
            body = SkillBody.model_validate(response.json())
        except (httpx.HTTPError, ValidationError, ValueError) as exc:
            emit(
                trace,
                "skill.body_load_failed",
                {"skillId": skill_id, "version": version, "reason": type(exc).__name__},
            )
            raise ConfigSourceError(f"failed to load skill body: {skill_id}@{version}") from exc

        if body.skill_id != skill_id or body.version != version:
            emit(
                trace,
                "skill.body_load_failed",
                {
                    "skillId": skill_id,
                    "version": version,
                    "reason": "body identity mismatch",
                },
            )
            raise ConfigValidationError("skill body identity mismatch")

        emit(
            trace,
            "skill.body_loaded",
            {
                "skillId": skill_id,
                "version": version,
                "slotCount": len(body.slot_contract),
                "rulesHash": hash_text(body.rules_md),
            },
        )
        return body

    async def load_skill_body_by_id(
        self,
        skill_id: str,
        *,
        trace: list[TraceEvent] | None = None,
    ) -> SkillBody:
        index = await self.refresh_index(trace)
        if index is not None:
            metadata = index.by_skill_id().get(skill_id)
            if metadata is not None:
                return await self.load_skill_body(skill_id, metadata.version, trace)
        return await self.load_skill_body(skill_id, "v1", trace)

    async def load_reference(
        self,
        reference_key: str,
        version: str,
        trace: list[TraceEvent] | None = None,
    ) -> ReferenceBody:
        validate_reference_key(reference_key)
        emit(trace, "reference.load_start", {"referenceKey": reference_key, "version": version})
        try:
            response = await self._client.get(
                f"/v1/router/references/{quote(reference_key, safe='')}",
                params={"version": version},
            )
            response.raise_for_status()
            reference = ReferenceBody.model_validate(response.json())
        except (httpx.HTTPError, ValidationError, ValueError) as exc:
            emit(
                trace,
                "reference.load_failed",
                {"referenceKey": reference_key, "version": version, "reason": type(exc).__name__},
            )
            raise ConfigSourceError(f"failed to load reference: {reference_key}@{version}") from exc

        emit(
            trace,
            "reference.loaded",
            {
                "referenceKey": reference_key,
                "version": version,
                "bodyHash": hash_text(reference.body_md),
            },
        )
        return reference

    async def healthcheck(self) -> bool:
        try:
            await self.refresh_index()
            return self._last_good_index is not None
        except ConfigSourceError:
            return False

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()


def emit(trace: list[TraceEvent] | None, stage: str, detail: dict[str, Any] | None = None) -> None:
    if trace is not None:
        trace.append(TraceEvent(stage=stage, detail=detail or {}))


def hash_text(value: str) -> str:
    import hashlib

    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]
