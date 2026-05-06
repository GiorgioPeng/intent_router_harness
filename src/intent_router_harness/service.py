from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from intent_router_harness.assistant_protocol import (
    ProtocolAssertionError,
    parse_sse_text,
)
from intent_router_harness.assistant_service import AssistantProtocolService
from intent_router_harness.contracts import (
    AssistantServiceResult,
    RouterMessageRequest,
    TaskCompletionRequest,
)
from intent_router_harness.llm import LLMClient, LLMRequestError
from intent_router_harness.planner import LLMMessagePlanner, MessagePlanner
from intent_router_harness.regression import (
    RegressionCase,
    RegressionSuite,
    load_regression_suite,
    validate_case_transcripts,
    validate_step_transcript,
)
from intent_router_harness.runtime import PromptHarness, RenderedPrompt, load_prompt_harness

logger = logging.getLogger(__name__)


class ServiceConfigurationError(RuntimeError):
    """Raised when a harness service cannot be constructed."""


class HarnessHealth(BaseModel):
    """Static service metadata for health checks."""

    name: str
    version: str
    enabled: bool
    surfaces: list[str]


class SurfaceSummary(BaseModel):
    """Public summary of one configured prompt surface."""

    name: str
    include_skill_index: bool
    inline_skills: list[str] = Field(default_factory=list)
    max_skill_body_chars: int | None = None


class RegressionCaseSummary(BaseModel):
    """Public summary of one regression case."""

    id: str
    title: str
    status: str
    step_count: int
    tags: list[str] = Field(default_factory=list)


class RegressionSuiteSummary(BaseModel):
    """Public summary of a loaded regression suite."""

    version: str
    source_document: str
    primary_mode: str
    event_filter: list[str]
    cases: list[RegressionCaseSummary]


class RenderPromptRequest(BaseModel):
    """Service request for rendering one prompt surface."""

    surface: str
    stream: bool = False
    variables: dict[str, Any] = Field(default_factory=dict)
    intent_codes: list[str] = Field(default_factory=list)
    domain_codes: list[str] = Field(default_factory=list)
    capabilities: list[str] = Field(default_factory=list)
    loaded_skill_names: list[str] = Field(default_factory=list)
    requested_reference_ids: list[str] = Field(default_factory=list)


class RenderPromptResponse(BaseModel):
    """Service response containing the rendered prompt and skill decisions."""

    surface: str
    system: str
    human: str
    messages: list[dict[str, str]]
    agent_contexts: list[str]
    metadata_skills: list[str]
    loaded_skills: list[str]
    loaded_references: list[str]


class RenderLLMRequest(RenderPromptRequest):
    """Service request for rendering one prompt and invoking the configured LLM."""

    max_tokens: int | None = Field(default=None, gt=0)
    parse_json: bool = True


class RenderLLMResponse(BaseModel):
    """Rendered prompt plus the configured LLM response."""

    surface: str
    model: str
    content: str
    json_valid: bool
    parsed_json: Any | None = None
    json_error: str | None = None
    finish_reason: str | None = None
    usage: dict[str, Any] | None = None
    prompt: RenderPromptResponse


class RegressionValidationRequest(BaseModel):
    """Request for validating one step or one full case transcript."""

    case_id: str
    step_name: str | None = None
    sse_text: str | None = None
    transcripts: dict[str, str] = Field(default_factory=dict)


class RegressionValidationResponse(BaseModel):
    """Regression transcript validation result."""

    ok: bool
    case_id: str
    step_name: str | None = None
    errors: list[str] = Field(default_factory=list)


class IntentRouterHarnessService:
    """Application service boundary around prompt harness operations."""

    def __init__(
        self,
        harness: PromptHarness,
        *,
        regression_suite: RegressionSuite | None = None,
        llm_client: LLMClient | None = None,
        message_planner: MessagePlanner | None = None,
    ) -> None:
        self.harness = harness
        self.regression_suite = regression_suite
        self.llm_client = llm_client
        planner = message_planner
        if planner is None and llm_client is not None:
            planner = LLMMessagePlanner(harness=harness, llm_client=llm_client)
        self.assistant = (
            AssistantProtocolService(planner=planner)
            if planner is not None
            else None
        )

    @classmethod
    def from_spec(
        cls,
        spec_path: str | Path,
        *,
        skill_roots: list[str] | None = None,
        regression_suite_path: str | Path | None = None,
        llm_client: LLMClient | None = None,
        message_planner: MessagePlanner | None = None,
    ) -> "IntentRouterHarnessService":
        """Load a service from a harness spec file."""
        logger.info(
            "building harness service spec_path=%s regression_suite_path=%s skill_roots=%s llm_configured=%s message_planner_configured=%s",
            spec_path,
            regression_suite_path,
            skill_roots or [],
            llm_client is not None,
            message_planner is not None,
        )
        harness = load_prompt_harness(spec_path, skill_roots=skill_roots)
        if harness is None:
            raise ServiceConfigurationError(f"harness spec is disabled: {spec_path}")
        regression_suite = (
            load_regression_suite(regression_suite_path)
            if regression_suite_path is not None
            else None
        )
        if regression_suite is not None:
            logger.info(
                "loaded regression suite version=%s source_document=%s case_count=%d",
                regression_suite.version,
                regression_suite.source_document,
                len(regression_suite.cases),
            )
        service = cls(
            harness,
            regression_suite=regression_suite,
            llm_client=llm_client,
            message_planner=message_planner,
        )
        logger.info(
            "initialized harness service name=%s version=%s surfaces=%s llm_configured=%s assistant_configured=%s regression_suite_loaded=%s",
            service.harness.spec.name,
            service.harness.spec.version,
            sorted(service.harness.spec.surfaces),
            service.llm_client is not None,
            service.assistant is not None,
            service.regression_suite is not None,
        )
        return service

    def health(self) -> HarnessHealth:
        """Return deterministic metadata for liveness and startup checks."""
        return HarnessHealth(
            name=self.harness.spec.name,
            version=self.harness.spec.version,
            enabled=self.harness.spec.enabled,
            surfaces=sorted(self.harness.spec.surfaces),
        )

    def surfaces(self) -> list[SurfaceSummary]:
        """Return configured prompt surfaces without exposing prompt bodies."""
        return [
            SurfaceSummary(
                name=name,
                include_skill_index=surface.include_skill_index,
                inline_skills=list(surface.inline_skills),
                max_skill_body_chars=surface.max_skill_body_chars,
            )
            for name, surface in sorted(self.harness.spec.surfaces.items())
        ]

    def regression_summary(self) -> RegressionSuiteSummary:
        """Return a summary of the loaded regression suite."""
        suite = self._require_regression_suite()
        return RegressionSuiteSummary(
            version=suite.version,
            source_document=suite.source_document,
            primary_mode=suite.primary_mode,
            event_filter=list(suite.event_filter),
            cases=[
                RegressionCaseSummary(
                    id=case.id,
                    title=case.title,
                    status=case.status,
                    step_count=len(case.steps),
                    tags=list(case.tags),
                )
                for case in suite.cases
            ],
        )

    def regression_case(self, case_id: str) -> RegressionCase:
        """Return one loaded regression case."""
        return self._require_regression_suite().case_by_id(case_id)

    def validate_regression(
        self,
        request: RegressionValidationRequest,
    ) -> RegressionValidationResponse:
        """Validate a step or full case SSE transcript against the loaded suite."""
        try:
            case = self.regression_case(request.case_id)
            if request.step_name is not None:
                step = _step_by_name(case, request.step_name)
                if request.sse_text is None:
                    raise ProtocolAssertionError("sse_text is required for step validation")
                validate_step_transcript(step, parse_sse_text(request.sse_text))
                return RegressionValidationResponse(
                    ok=True,
                    case_id=request.case_id,
                    step_name=request.step_name,
                )

            if not request.transcripts:
                raise ProtocolAssertionError("transcripts is required for case validation")
            validate_case_transcripts(
                case,
                {
                    name: parse_sse_text(sse_text)
                    for name, sse_text in request.transcripts.items()
                },
            )
            return RegressionValidationResponse(ok=True, case_id=request.case_id)
        except (KeyError, ProtocolAssertionError) as exc:
            return RegressionValidationResponse(
                ok=False,
                case_id=request.case_id,
                step_name=request.step_name,
                errors=[str(exc)],
            )

    def render(self, request: RenderPromptRequest) -> RenderPromptResponse:
        """Render one prompt request through the underlying harness."""
        prompt = self.harness.render(
            surface=request.surface,
            variables=request.variables,
            intent_codes=tuple(request.intent_codes),
            domain_codes=tuple(request.domain_codes),
            capabilities=tuple(request.capabilities),
            loaded_skill_names=tuple(request.loaded_skill_names),
            requested_reference_ids=tuple(request.requested_reference_ids),
        )
        return _rendered_prompt_response(prompt)

    def render_llm(self, request: RenderLLMRequest) -> RenderLLMResponse:
        """Render one prompt and invoke the configured LLM."""
        if self.llm_client is None:
            raise ServiceConfigurationError("LLM client is not configured")

        prompt_response = self.render(request)
        raw_response = self.llm_client.chat(
            prompt_response.messages,
            max_tokens=request.max_tokens,
        )
        content, finish_reason = _chat_message_content(raw_response)
        parsed_json: Any | None = None
        json_error: str | None = None
        if request.parse_json:
            try:
                parsed_json = json.loads(content)
            except json.JSONDecodeError as exc:
                json_error = str(exc)

        return RenderLLMResponse(
            surface=prompt_response.surface,
            model=str(getattr(self.llm_client.settings, "model", "")),
            content=content,
            json_valid=(json_error is None if request.parse_json else False),
            parsed_json=parsed_json,
            json_error=json_error,
            finish_reason=finish_reason,
            usage=raw_response.get("usage") if isinstance(raw_response.get("usage"), dict) else None,
            prompt=prompt_response,
        )

    def handle_message(self, request: RouterMessageRequest) -> AssistantServiceResult:
        """Handle an assistant protocol message request."""
        if self.assistant is None:
            raise ServiceConfigurationError("assistant planner is not configured")
        return self.assistant.handle_message(request)

    def handle_task_completion(self, request: TaskCompletionRequest) -> AssistantServiceResult:
        """Handle an assistant protocol task completion request."""
        if self.assistant is None:
            raise ServiceConfigurationError("assistant planner is not configured")
        return self.assistant.handle_task_completion(request)

    def _require_regression_suite(self) -> RegressionSuite:
        if self.regression_suite is None:
            raise ServiceConfigurationError("regression suite is not loaded")
        return self.regression_suite


def _rendered_prompt_response(prompt: RenderedPrompt) -> RenderPromptResponse:
    return RenderPromptResponse(
        surface=prompt.surface,
        system=prompt.system,
        human=prompt.human,
        messages=prompt.messages(),
        agent_contexts=list(prompt.agent_contexts),
        metadata_skills=list(prompt.metadata_skills),
        loaded_skills=list(prompt.loaded_skills),
        loaded_references=list(prompt.loaded_references),
    )


def _step_by_name(case: RegressionCase, step_name: str):
    for step in case.steps:
        if step.name == step_name:
            return step
    raise KeyError(step_name)


def _chat_message_content(response: dict[str, Any]) -> tuple[str, str | None]:
    try:
        choice = response["choices"][0]
        message = choice["message"]
        content = str(message["content"]).strip()
        finish_reason = choice.get("finish_reason")
        return content, str(finish_reason) if finish_reason is not None else None
    except (KeyError, IndexError, TypeError) as exc:
        raise LLMRequestError("chat completion response did not contain choices[0].message.content") from exc
