from __future__ import annotations

from dataclasses import dataclass
import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class SkillReference:
    """One private resource explicitly exposed by a skill."""

    id: str
    path: Path
    body: str
    purpose: str = ""


@dataclass(frozen=True, slots=True)
class SkillDocument:
    """Parsed skill file with metadata separated from its full body."""

    name: str
    description: str
    path: Path
    body: str
    surfaces: tuple[str, ...] = ()
    intent_codes: tuple[str, ...] = ()
    domain_codes: tuple[str, ...] = ()
    capabilities: tuple[str, ...] = ()
    references: tuple[SkillReference, ...] = ()


class SkillLibrary:
    """Filesystem-backed skill index with progressive body loading."""

    def __init__(self, skills: dict[str, SkillDocument]) -> None:
        self._skills = dict(skills)

    def __len__(self) -> int:
        return len(self._skills)

    def names(self) -> list[str]:
        """Return loaded skill names in deterministic order."""
        return sorted(self._skills)

    @classmethod
    def from_roots(cls, roots: list[str | Path]) -> "SkillLibrary":
        """Load one-level skill directories from the configured roots."""
        skills: dict[str, SkillDocument] = {}
        logger.info("loading skill library roots=%s", [str(Path(root).expanduser()) for root in roots])
        for root in roots:
            root_path = Path(root).expanduser()
            if not root_path.is_dir():
                logger.warning("skipping missing skill root path=%s", root_path)
                continue
            for skill_dir in sorted(item for item in root_path.iterdir() if item.is_dir()):
                skill_path = skill_dir / "SKILL.md"
                if not skill_path.is_file():
                    continue
                skill = load_skill_document(skill_path)
                skills[skill.name] = skill
                logger.info(
                    "loaded skill name=%s path=%s surfaces=%s intent_codes=%s domain_codes=%s capabilities=%s",
                    skill.name,
                    skill.path,
                    list(skill.surfaces),
                    list(skill.intent_codes),
                    list(skill.domain_codes),
                    list(skill.capabilities),
                )
        logger.info("loaded skill library skill_count=%d skills=%s", len(skills), sorted(skills))
        return cls(skills)

    def get(self, name: str) -> SkillDocument | None:
        """Return one skill by name."""
        return self._skills.get(name)

    def matching_metadata(
        self,
        *,
        surface: str,
        intent_codes: tuple[str, ...] = (),
        domain_codes: tuple[str, ...] = (),
        capabilities: tuple[str, ...] = (),
    ) -> list[SkillDocument]:
        """Return skills whose metadata applies to the current harness context."""
        return [
            skill
            for skill in self._skills.values()
            if skill_matches(
                skill,
                surface=surface,
                intent_codes=intent_codes,
                domain_codes=domain_codes,
                capabilities=capabilities,
            )
        ]


def load_skill_document(path: Path) -> SkillDocument:
    """Parse one `SKILL.md` file using a small frontmatter subset."""
    content = path.read_text(encoding="utf-8")
    metadata, body = split_frontmatter(content)
    name = str(metadata.get("name") or path.parent.name).strip()
    description = str(metadata.get("description") or f"Skill instructions for {name}").strip()
    return SkillDocument(
        name=name,
        description=description,
        path=path,
        body=body.strip(),
        surfaces=tuple(_string_list(metadata.get("surfaces"))),
        intent_codes=tuple(_string_list(metadata.get("intent_codes"))),
        domain_codes=tuple(_string_list(metadata.get("domain_codes"))),
        capabilities=tuple(_string_list(metadata.get("capabilities"))),
        references=tuple(_reference_list(metadata.get("references"), path)),
    )


def split_frontmatter(content: str) -> tuple[dict[str, Any], str]:
    """Split minimal YAML-like frontmatter from Markdown content."""
    lines = content.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, content

    end_index: int | None = None
    for index, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            end_index = index
            break
    if end_index is None:
        return {}, content

    metadata = parse_simple_frontmatter(lines[1:end_index])
    body = "\n".join(lines[end_index + 1 :])
    return metadata, body


def parse_simple_frontmatter(lines: list[str]) -> dict[str, Any]:
    """Parse simple `key: value` frontmatter without adding a YAML dependency."""
    parsed: dict[str, Any] = {}
    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        key, raw_value = line.split(":", 1)
        parsed[key.strip()] = _parse_scalar_or_list(raw_value.strip())
    return parsed


def skill_matches(
    skill: SkillDocument,
    *,
    surface: str,
    intent_codes: tuple[str, ...] = (),
    domain_codes: tuple[str, ...] = (),
    capabilities: tuple[str, ...] = (),
) -> bool:
    """Return whether skill metadata applies to the current harness context."""
    if skill.surfaces and surface not in skill.surfaces:
        return False
    if skill.intent_codes and not set(skill.intent_codes).intersection(intent_codes):
        return False
    if skill.domain_codes and not set(skill.domain_codes).intersection(domain_codes):
        return False
    if skill.capabilities and not set(skill.capabilities).intersection(capabilities):
        return False
    return True


def _parse_scalar_or_list(value: str) -> Any:
    if not value:
        return ""
    if value.lower() in {"true", "false"}:
        return value.lower() == "true"
    if value.startswith("[") or value.startswith('"') or value.startswith("'"):
        try:
            decoded = json.loads(value.replace("'", '"'))
        except json.JSONDecodeError:
            return value.strip("'\"")
        return decoded
    if "," in value:
        return [item.strip() for item in value.split(",") if item.strip()]
    return value.strip("'\"")


def _string_list(value: Any) -> list[str]:
    if value is None or value == "":
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return [str(value).strip()]


def _reference_list(value: Any, skill_path: Path) -> list[SkillReference]:
    if value is None or value == "":
        return []
    if not isinstance(value, list):
        logger.warning("ignoring malformed skill references path=%s value_type=%s", skill_path, type(value).__name__)
        return []

    root = skill_path.parent.resolve()
    seen: set[str] = set()
    references: list[SkillReference] = []
    for index, item in enumerate(value, start=1):
        ref_id: str
        raw_path: str
        purpose = ""
        if isinstance(item, dict):
            ref_id = str(item.get("id") or "").strip()
            raw_path = str(item.get("path") or "").strip()
            purpose = str(item.get("purpose") or "").strip()
        elif isinstance(item, str):
            ref_id = f"ref_{index:03d}"
            raw_path = item.strip()
        else:
            logger.warning(
                "ignoring malformed skill reference path=%s index=%d value_type=%s",
                skill_path,
                index,
                type(item).__name__,
            )
            continue

        if not ref_id or not raw_path:
            logger.warning("ignoring incomplete skill reference path=%s index=%d", skill_path, index)
            continue
        if ref_id in seen:
            logger.warning("ignoring duplicate skill reference path=%s reference_id=%s", skill_path, ref_id)
            continue
        seen.add(ref_id)

        reference_path = (root / raw_path).resolve()
        if not reference_path.is_relative_to(root):
            logger.warning(
                "ignoring reference outside skill directory skill_path=%s reference_id=%s reference_path=%s",
                skill_path,
                ref_id,
                reference_path,
            )
            continue
        if not reference_path.is_file():
            logger.warning(
                "ignoring missing skill reference skill_path=%s reference_id=%s reference_path=%s",
                skill_path,
                ref_id,
                reference_path,
            )
            continue

        reference_content = reference_path.read_text(encoding="utf-8")
        _, reference_body = split_frontmatter(reference_content)
        references.append(
            SkillReference(
                id=ref_id,
                path=reference_path,
                body=reference_body.strip(),
                purpose=purpose,
            )
        )
    return references
