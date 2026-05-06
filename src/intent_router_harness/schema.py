from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class SkillBinding(BaseModel):
    """Rule that promotes one skill from metadata-only to full body context."""

    skill: str
    surfaces: list[str] = Field(default_factory=list)
    intent_codes: list[str] = Field(default_factory=list)
    domain_codes: list[str] = Field(default_factory=list)
    capabilities: list[str] = Field(default_factory=list)
    load: Literal["metadata", "body"] = "body"


class SurfaceSpec(BaseModel):
    """Prompt surface owned by the harness spec."""

    system: str = ""
    human: str = ""
    include_skill_index: bool = True
    inline_skills: list[str] = Field(default_factory=list)
    max_skill_body_chars: int | None = Field(default=None, gt=0)


class HarnessSpec(BaseModel):
    """Top-level spec for a standalone intent router harness."""

    name: str = "intent-router-harness"
    version: str = "0.1.0"
    description: str = ""
    enabled: bool = True
    agent_paths: list[str] = Field(default_factory=list)
    skill_roots: list[str] = Field(default_factory=list)
    max_skill_body_chars: int = Field(default=6000, gt=0)
    max_reference_body_chars: int = Field(default=6000, gt=0)
    max_reference_count: int = Field(default=4, gt=0)
    surfaces: dict[str, SurfaceSpec] = Field(default_factory=dict)
    bindings: list[SkillBinding] = Field(default_factory=list)


class HarnessContext(BaseModel):
    """Runtime context used to select skills deterministically."""

    surface: str
    intent_codes: tuple[str, ...] = ()
    domain_codes: tuple[str, ...] = ()
    capabilities: tuple[str, ...] = ()


class EvalCase(BaseModel):
    """Portable eval case for harness-driven experiments."""

    id: str
    surface: str
    variables: dict[str, Any] = Field(default_factory=dict)
    intent_codes: list[str] = Field(default_factory=list)
    domain_codes: list[str] = Field(default_factory=list)
    capabilities: list[str] = Field(default_factory=list)
    expected: dict[str, Any] | None = None
    tags: list[str] = Field(default_factory=list)


class Variant(BaseModel):
    """One named spec variant in a harness experiment."""

    name: str
    spec_file: str


class ExperimentSpec(BaseModel):
    """Minimal experiment manifest for comparing harness variants."""

    name: str
    variants: list[Variant] = Field(default_factory=list)
    cases: list[EvalCase] = Field(default_factory=list)
