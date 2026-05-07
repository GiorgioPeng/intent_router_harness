from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import logging
from pathlib import Path
import tomllib
from typing import Any

from intent_router_harness.schema import HarnessContext, HarnessSpec, SkillBinding, SurfaceSpec
from intent_router_harness.skills import SkillDocument, SkillLibrary, SkillReference

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class AgentContext:
    """Root-level agent instruction file loaded for every prompt."""

    path: Path
    body: str


@dataclass(frozen=True, slots=True)
class RenderedPrompt:
    """Framework-neutral prompt rendered by the harness."""

    surface: str
    system: str
    human: str
    agent_contexts: tuple[str, ...]
    metadata_skills: tuple[str, ...]
    loaded_skills: tuple[str, ...]
    loaded_references: tuple[str, ...]
    trace_events: tuple[dict[str, Any], ...] = ()

    def messages(self) -> list[dict[str, str]]:
        """Return OpenAI-style chat messages."""
        return [
            {"role": "system", "content": self.system},
            {"role": "user", "content": self.human},
        ]


class PromptHarness:
    """Spec-driven prompt harness with progressive skill disclosure."""

    def __init__(
        self,
        *,
        spec: HarnessSpec,
        skills: SkillLibrary,
        agent_contexts: tuple[AgentContext, ...] = (),
    ) -> None:
        self.spec = spec
        self.skills = skills
        self.agent_contexts = agent_contexts

    def render(
        self,
        *,
        surface: str,
        variables: Mapping[str, Any] | None = None,
        intent_codes: tuple[str, ...] = (),
        domain_codes: tuple[str, ...] = (),
        capabilities: tuple[str, ...] = (),
        loaded_skill_names: tuple[str, ...] = (),
        requested_reference_ids: tuple[str, ...] = (),
    ) -> RenderedPrompt:
        """Render one prompt surface with matched skill context."""
        if surface not in self.spec.surfaces:
            raise KeyError(f"Unknown harness surface: {surface}")

        logger.info(
            "spec.render.start harness=%s@%s surface=%s variables=%s intent_codes=%s domain_codes=%s capabilities=%s",
            self.spec.name,
            self.spec.version,
            surface,
            sorted((variables or {}).keys()),
            list(intent_codes),
            list(domain_codes),
            list(capabilities),
        )
        surface_spec = self.spec.surfaces[surface]
        context = HarnessContext(
            surface=surface,
            intent_codes=tuple(sorted(set(intent_codes))),
            domain_codes=tuple(sorted(set(domain_codes))),
            capabilities=tuple(sorted(set(capabilities))),
        )
        rendered_variables = dict(variables or {})
        agent_context, agent_trace_events = self._agent_context(surface)
        skill_context, metadata_skills, loaded_skills, loaded_references, trace_events = self._skill_context(
            context,
            surface_spec,
            loaded_skill_names=loaded_skill_names,
            requested_reference_ids=requested_reference_ids,
        )
        system_parts = [
            _render_template(surface_spec.system, rendered_variables),
            agent_context,
            skill_context,
        ]
        human = _render_template(surface_spec.human, rendered_variables)
        rendered = RenderedPrompt(
            surface=surface,
            system="\n\n".join(part.strip() for part in system_parts if part and part.strip()),
            human=human.strip(),
            agent_contexts=tuple(str(context.path) for context in self.agent_contexts),
            metadata_skills=tuple(skill.name for skill in metadata_skills),
            loaded_skills=tuple(skill.name for skill in loaded_skills),
            loaded_references=tuple(loaded_references),
            trace_events=tuple([*agent_trace_events, *trace_events]),
        )
        logger.info(
            "spec.render.done harness=%s@%s surface=%s agent_contexts=%s metadata_skills=%s loaded_skills=%s loaded_references=%s system_chars=%d human_chars=%d",
            self.spec.name,
            self.spec.version,
            surface,
            list(rendered.agent_contexts),
            list(rendered.metadata_skills),
            list(rendered.loaded_skills),
            list(rendered.loaded_references),
            len(rendered.system),
            len(rendered.human),
        )
        return rendered

    def _agent_context(self, surface: str) -> tuple[str, list[dict[str, Any]]]:
        if not self.agent_contexts:
            return "", []

        logger.info(
            "agent.context.loaded surface=%s agent_paths=%s",
            surface,
            [str(context.path) for context in self.agent_contexts],
        )
        logger.info(
            "core.trace step=agent_context_loaded surface=%s agent_paths=%s",
            surface,
            [str(context.path) for context in self.agent_contexts],
        )
        lines = ["## Agent 根指令"]
        event_data: list[dict[str, Any]] = []
        for context in self.agent_contexts:
            lines.extend([f"### {context.path.name}", context.body])
            event_data.append(
                {
                    "path": str(context.path),
                    "body_chars": len(context.body),
                    "body": context.body,
                }
            )
        return "\n".join(lines), [
            {
                "stage": "agent_context_loaded",
                "title": "Agent根指令加载",
                "summary": f"加载 {len(self.agent_contexts)} 个 agent context",
                "data": {
                    "surface": surface,
                    "agent_contexts": event_data,
                },
            }
        ]

    def _skill_context(
        self,
        context: HarnessContext,
        surface_spec: SurfaceSpec,
        *,
        loaded_skill_names: tuple[str, ...],
        requested_reference_ids: tuple[str, ...],
    ) -> tuple[str, list[SkillDocument], list[SkillDocument], list[str], list[dict[str, Any]]]:
        metadata_skills = (
            self.skills.matching_metadata(
                surface=context.surface,
                intent_codes=context.intent_codes,
                domain_codes=context.domain_codes,
                capabilities=context.capabilities,
            )
            if surface_spec.include_skill_index
            else []
        )
        body_skills = self._body_skills(context, surface_spec, loaded_skill_names)
        available_references = _allowed_references(body_skills)
        loaded_references = self._load_references(
            context=context,
            available_references=available_references,
            requested_reference_ids=requested_reference_ids,
        )
        logger.info(
            "spec.progressive_load surface=%s include_skill_index=%s metadata_skills=%s body_skills=%s available_references=%s loaded_references=%s inline_skills=%s max_skill_body_chars=%s max_reference_body_chars=%s",
            context.surface,
            surface_spec.include_skill_index,
            [skill.name for skill in metadata_skills],
            [skill.name for skill in body_skills],
            sorted(available_references),
            [reference_id for reference_id, _, _ in loaded_references],
            list(surface_spec.inline_skills),
            surface_spec.max_skill_body_chars or self.spec.max_skill_body_chars,
            self.spec.max_reference_body_chars,
        )
        logger.info(
            "skill.context surface=%s metadata_skills=%s loaded_skill_bodies=%s",
            context.surface,
            [skill.name for skill in metadata_skills],
            [skill.name for skill in body_skills],
        )
        logger.info(
            "core.trace step=spec_progressive_load surface=%s include_skill_index=%s metadata_skills=%s loaded_skill_bodies=%s available_references=%s loaded_references=%s",
            context.surface,
            surface_spec.include_skill_index,
            [skill.name for skill in metadata_skills],
            [skill.name for skill in body_skills],
            sorted(available_references),
            [reference_id for reference_id, _, _ in loaded_references],
        )
        trace_events: list[dict[str, Any]] = [
            {
                "stage": "spec_progressive_load",
                "title": "Spec渐进式加载",
                "summary": (
                    f"surface={context.surface}，metadata skills="
                    f"{[skill.name for skill in metadata_skills]}，loaded skill bodies="
                    f"{[skill.name for skill in body_skills]}"
                ),
                "data": {
                    "surface": context.surface,
                    "include_skill_index": surface_spec.include_skill_index,
                    "metadata_skills": [skill.name for skill in metadata_skills],
                    "loaded_skill_bodies": [skill.name for skill in body_skills],
                    "available_references": sorted(available_references),
                    "loaded_references": [reference_id for reference_id, _, _ in loaded_references],
                    "inline_skills": list(surface_spec.inline_skills),
                    "max_skill_body_chars": surface_spec.max_skill_body_chars
                    or self.spec.max_skill_body_chars,
                    "max_reference_body_chars": self.spec.max_reference_body_chars,
                },
            }
        ]

        lines = [
            "## Harness 上下文",
            f"- Harness：{self.spec.name}@{self.spec.version}",
            f"- Surface：{context.surface}",
            "- 契约：必须严格保持当前 surface 要求的输出 schema。",
        ]
        if self.spec.description:
            lines.append(f"- 描述：{self.spec.description}")

        if metadata_skills:
            lines.extend(["", "### 可用 Skill 摘要"])
            for skill in metadata_skills:
                lines.append(f"- {skill.name}: {skill.description}")

        if body_skills:
            lines.extend(["", "### 已加载 Skill 正文"])
            for skill in body_skills:
                logger.info(
                    "spec.loaded_skill_body surface=%s skill=%s path=%s body_chars=%d truncated_to=%d",
                    context.surface,
                    skill.name,
                    skill.path,
                    len(skill.body),
                    surface_spec.max_skill_body_chars or self.spec.max_skill_body_chars,
                )
                logger.info(
                    "skill.body.used surface=%s skill=%s path=%s body_chars=%d truncated_to=%d",
                    context.surface,
                    skill.name,
                    skill.path,
                    len(skill.body),
                    surface_spec.max_skill_body_chars or self.spec.max_skill_body_chars,
                )
                logger.info(
                    "core.trace step=skill_body_loaded surface=%s skill=%s path=%s body_chars=%d",
                    context.surface,
                    skill.name,
                    skill.path,
                    len(skill.body),
                )
                max_chars = surface_spec.max_skill_body_chars or self.spec.max_skill_body_chars
                rendered_body = _truncate(skill.body, max_chars)
                trace_events.append(
                    {
                        "stage": "skill_body_loaded",
                        "title": "Skill正文加载",
                        "summary": f"{skill.name} 已加载到 {context.surface} 的 system prompt",
                        "data": {
                            "surface": context.surface,
                            "skill": skill.name,
                            "description": skill.description,
                            "path": str(skill.path),
                            "body_chars": len(skill.body),
                            "truncated_to": max_chars,
                            "body": rendered_body,
                        },
                    }
                )
                lines.extend(
                    [
                        f"#### {skill.name}",
                        rendered_body,
                    ]
                )
        if available_references:
            trace_events.append(
                {
                    "stage": "references_available",
                    "title": "可用Reference列表",
                    "summary": f"当前 skill 暴露 {len(available_references)} 个 reference",
                    "data": {
                        "surface": context.surface,
                        "references": [
                            {
                                "id": reference_id,
                                "skill": skill.name,
                                "path": str(reference.path),
                                "purpose": reference.purpose,
                            }
                            for reference_id, (skill, reference) in sorted(available_references.items())
                        ],
                    },
                }
            )
            lines.extend(["", "### 可用 Reference 摘要"])
            for reference_id, (skill, reference) in sorted(available_references.items()):
                purpose = f": {reference.purpose}" if reference.purpose else ""
                lines.append(f"- {reference_id} ({skill.name}){purpose}")

        if loaded_references:
            lines.extend(["", "### 已加载 Reference 正文"])
            for reference_id, skill, reference in loaded_references:
                rendered_body = _truncate(reference.body, self.spec.max_reference_body_chars)
                logger.info(
                    "reference.body.used surface=%s reference_id=%s skill=%s path=%s body_chars=%d truncated_to=%d",
                    context.surface,
                    reference_id,
                    skill.name,
                    reference.path,
                    len(reference.body),
                    self.spec.max_reference_body_chars,
                )
                logger.info(
                    "core.trace step=reference_body_loaded surface=%s reference_id=%s skill=%s path=%s body_chars=%d",
                    context.surface,
                    reference_id,
                    skill.name,
                    reference.path,
                    len(reference.body),
                )
                trace_events.append(
                    {
                        "stage": "reference_body_loaded",
                        "title": "Reference正文加载",
                        "summary": f"{reference_id} 已加载到 {context.surface} 的 system prompt",
                        "data": {
                            "surface": context.surface,
                            "reference_id": reference_id,
                            "skill": skill.name,
                            "path": str(reference.path),
                            "body_chars": len(reference.body),
                            "truncated_to": self.spec.max_reference_body_chars,
                            "body": rendered_body,
                        },
                    }
                )
                lines.extend(
                    [
                        f"#### {reference_id}",
                        rendered_body,
                    ]
                )
        return (
            "\n".join(lines),
            metadata_skills,
            body_skills,
            [reference_id for reference_id, _, _ in loaded_references],
            trace_events,
        )

    def _body_skills(
        self,
        context: HarnessContext,
        surface_spec: SurfaceSpec,
        loaded_skill_names: tuple[str, ...],
    ) -> list[SkillDocument]:
        names: list[str] = [*surface_spec.inline_skills, *loaded_skill_names]
        for binding in self.spec.bindings:
            if not binding_matches(
                binding,
                surface=context.surface,
                intent_codes=context.intent_codes,
                domain_codes=context.domain_codes,
                capabilities=context.capabilities,
            ):
                continue
            logger.info(
                "spec.binding_matched surface=%s skill=%s load=%s intent_codes=%s domain_codes=%s capabilities=%s",
                context.surface,
                binding.skill,
                binding.load,
                list(binding.intent_codes),
                list(binding.domain_codes),
                list(binding.capabilities),
            )
            if binding.load == "body":
                names.append(binding.skill)

        seen: set[str] = set()
        result: list[SkillDocument] = []
        for name in names:
            if name in seen:
                continue
            seen.add(name)
            skill = self.skills.get(name)
            if skill is not None:
                result.append(skill)
        return result

    def _load_references(
        self,
        *,
        context: HarnessContext,
        available_references: dict[str, tuple[SkillDocument, SkillReference]],
        requested_reference_ids: tuple[str, ...],
    ) -> list[tuple[str, SkillDocument, SkillReference]]:
        if not requested_reference_ids:
            return []

        requested = _dedupe(requested_reference_ids)
        if len(requested) > self.spec.max_reference_count:
            raise ValueError(
                f"requested reference count exceeds max_reference_count={self.spec.max_reference_count}: {requested}"
            )
        missing = [reference_id for reference_id in requested if reference_id not in available_references]
        if missing:
            logger.warning(
                "reference.request.rejected surface=%s missing_references=%s available_references=%s",
                context.surface,
                missing,
                sorted(available_references),
            )
            raise ValueError(
                f"requested references are not exposed by loaded skills: {missing}"
            )

        return [
            (reference_id, *available_references[reference_id])
            for reference_id in requested
        ]


def load_prompt_harness(
    spec_path: str | Path,
    *,
    skill_roots: list[str] | None = None,
) -> PromptHarness | None:
    """Load a prompt harness from a TOML spec file."""
    resolved_spec_path = Path(spec_path).expanduser()
    logger.info("loading harness spec path=%s", resolved_spec_path)
    spec = load_harness_spec(resolved_spec_path)
    surface_names = sorted(spec.surfaces)
    logger.info(
        "loaded harness spec name=%s version=%s enabled=%s surfaces=%s agent_paths=%s skill_roots=%s bindings=%d",
        spec.name,
        spec.version,
        spec.enabled,
        surface_names,
        list(spec.agent_paths or ["agent.md"]),
        list(spec.skill_roots),
        len(spec.bindings),
    )
    if not spec.enabled:
        logger.warning("harness spec disabled path=%s name=%s version=%s", resolved_spec_path, spec.name, spec.version)
        return None
    roots = [
        str(_resolve_relative_path(resolved_spec_path.parent, root))
        for root in spec.skill_roots
    ]
    roots.extend(str(Path(root).expanduser()) for root in (skill_roots or []))
    logger.info("resolved harness skill roots path=%s roots=%s", resolved_spec_path, roots)
    skills = SkillLibrary.from_roots(roots)
    agent_paths = spec.agent_paths or ["agent.md"]
    agent_contexts = _load_agent_contexts(resolved_spec_path.parent, agent_paths)
    logger.info(
        "initialized prompt harness name=%s version=%s surfaces=%s agent_contexts=%s skill_count=%d skills=%s",
        spec.name,
        spec.version,
        surface_names,
        [str(context.path) for context in agent_contexts],
        len(skills),
        skills.names(),
    )
    return PromptHarness(spec=spec, skills=skills, agent_contexts=agent_contexts)


def load_harness_spec(path: str | Path) -> HarnessSpec:
    """Load and validate one harness spec from TOML."""
    spec_path = Path(path).expanduser()
    logger.info("parsing harness spec toml path=%s", spec_path)
    raw = tomllib.loads(spec_path.read_text(encoding="utf-8"))
    spec = HarnessSpec.model_validate(raw)
    logger.info("validated harness spec path=%s name=%s version=%s", spec_path, spec.name, spec.version)
    return spec


def binding_matches(
    binding: SkillBinding,
    *,
    surface: str,
    intent_codes: tuple[str, ...],
    domain_codes: tuple[str, ...],
    capabilities: tuple[str, ...],
) -> bool:
    """Return whether one spec binding applies to the current context."""
    if binding.surfaces and surface not in binding.surfaces:
        return False
    if binding.intent_codes and not set(binding.intent_codes).intersection(intent_codes):
        return False
    if binding.domain_codes and not set(binding.domain_codes).intersection(domain_codes):
        return False
    if binding.capabilities and not set(binding.capabilities).intersection(capabilities):
        return False
    return True


class _SafeFormatDict(dict[str, Any]):
    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def _render_template(template: str, variables: Mapping[str, Any]) -> str:
    return template.format_map(_SafeFormatDict({key: str(value) for key, value in variables.items()}))


def _resolve_relative_path(base_dir: Path, raw_path: str) -> Path:
    path = Path(raw_path).expanduser()
    if path.is_absolute():
        return path
    return base_dir / path


def _load_agent_contexts(base_dir: Path, agent_paths: list[str]) -> tuple[AgentContext, ...]:
    contexts: list[AgentContext] = []
    for raw_path in agent_paths:
        path = _resolve_relative_path(base_dir, raw_path).resolve()
        if not path.is_file():
            logger.warning("skipping missing agent context path=%s", path)
            continue
        body = path.read_text(encoding="utf-8").strip()
        contexts.append(AgentContext(path=path, body=body))
        logger.info("loaded agent context path=%s body_chars=%d", path, len(body))
    return tuple(contexts)


def _allowed_references(
    skills: list[SkillDocument],
) -> dict[str, tuple[SkillDocument, SkillReference]]:
    id_counts: dict[str, int] = {}
    for skill in skills:
        for reference in skill.references:
            id_counts[reference.id] = id_counts.get(reference.id, 0) + 1

    references: dict[str, tuple[SkillDocument, SkillReference]] = {}
    for skill in skills:
        for reference in skill.references:
            reference_id = (
                reference.id
                if id_counts.get(reference.id, 0) == 1
                else f"{skill.name}:{reference.id}"
            )
            references[reference_id] = (skill, reference)
    return references


def _dedupe(values: tuple[str, ...]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        normalized = str(value).strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        result.append(normalized)
    return result


def _truncate(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return f"{value[:limit].rstrip()}\n...[已由 intent-router harness 截断]"
