from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse

app = FastAPI(title="mock-router-config-source")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

BASE_DIR = Path(__file__).resolve().parent
SKILL_DIR = BASE_DIR / "mock_skills"
INDEX_ETAG = "mock-skills-v1"
SUPPORTED_SKILL_FRONTMATTER = {"name", "description"}


@app.get("/v1/router/skills/index")
async def skills_index(response: Response, if_none_match: str | None = None):
    if if_none_match == INDEX_ETAG:
        response.status_code = 304
        return None
    response.headers["ETag"] = INDEX_ETAG
    return {"version": "mock-skills-v1", "skills": _skill_index()}


@app.get("/v1/router/skills/{skill_id}/body")
async def skill_body(skill_id: str, version: str):
    skill = _skill_by_id(skill_id)
    if skill is None or skill["metadata"]["version"] != version:
        raise HTTPException(status_code=404, detail="skill body not found")
    return skill["body"]


@app.get("/v1/router/skills/{skill_id}/raw", response_class=PlainTextResponse)
async def raw_skill(skill_id: str):
    skill = _skill_by_id(skill_id)
    if skill is None:
        raise HTTPException(status_code=404, detail="skill file not found")
    return skill["raw"]


@app.get("/v1/router/references/{reference_key:path}")
async def reference_body(reference_key: str, version: str):
    body = _reference_bodies().get((reference_key, version))
    if body is None:
        raise HTTPException(status_code=404, detail="reference not found")
    return body


def _skill_index() -> list[dict[str, Any]]:
    return [skill["metadata"] for skill in _load_skills() if skill["expose_in_index"]]


def _skill_by_id(skill_id: str) -> dict[str, Any] | None:
    for skill in _load_skills():
        if skill["metadata"]["skillId"] == skill_id:
            return skill
    return None


def _load_skills() -> list[dict[str, Any]]:
    skills = [_load_skill_file(path) for path in _skill_files()]
    if not skills:
        raise HTTPException(status_code=500, detail=f"no skill files found in {SKILL_DIR}")
    return sorted(skills, key=lambda skill: skill["metadata"]["priority"])


def _skill_files() -> list[Path]:
    # 所有 SKILL 平级放在 mock_skills/<skill_name>/SKILL.md，便于 os.listdir 直接检索。
    return sorted(path / "SKILL.md" for path in SKILL_DIR.iterdir() if path.is_dir() and (path / "SKILL.md").exists())


def _load_skill_file(path: Path) -> dict[str, Any]:
    raw = path.read_text(encoding="utf-8")
    frontmatter, body_md = _parse_frontmatter(raw, path)
    skill_name = path.parent.name
    skill_id = f"skill_{_safe_id(skill_name)}"
    version = "v1"
    intent_code = str(frontmatter["name"])
    description = str(frontmatter.get("description") or frontmatter.get("name") or skill_id)
    references_dir = path.parent / "references"
    slot_contract = _load_json_reference(references_dir / "slot_contract.json", default=[])
    handoff_contract = _load_json_reference(
        references_dir / "handoff_contract.json",
        default={"target": f"{intent_code}_assistant", "payloadSchema": {}},
    )
    allowed_reference_keys = _allowed_reference_keys(path)

    # mock 数据直接从标准 SKILL 文件派生；Router index 只暴露摘要，不暴露正文。
    metadata = {
        "skillId": skill_id,
        "intentCode": intent_code,
        "summary": description,
        "priority": _priority(path),
        "version": version,
        "bodyKey": f"body/{skill_name}",
        "allowedReferenceKeys": allowed_reference_keys,
    }
    skill_body = {
        "skillId": skill_id,
        "version": version,
        "rulesMd": body_md.strip(),
        "slotContract": slot_contract,
        "handoffContract": handoff_contract,
    }
    return {
        "metadata": metadata,
        "body": skill_body,
        "raw": raw,
        "expose_in_index": _is_intent_skill(path),
    }


def _is_intent_skill(path: Path) -> bool:
    return path.parent.name != "follow_up"


def _priority(path: Path) -> int:
    if not _is_intent_skill(path):
        return 10_000
    names = [skill_path.parent.name for skill_path in _skill_files() if _is_intent_skill(skill_path)]
    return names.index(path.parent.name) + 1 if path.parent.name in names else 100


def _load_json_reference(path: Path, *, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def _allowed_reference_keys(path: Path) -> list[str]:
    references_dir = path.parent / "references"
    if not references_dir.exists():
        return []
    keys = []
    for reference_path in sorted(references_dir.glob("*.md")):
        keys.append(f"{path.parent.name}/{reference_path.stem}")
    return keys


def _reference_bodies() -> dict[tuple[str, str], dict[str, str]]:
    bodies: dict[tuple[str, str], dict[str, str]] = {}
    for skill_path in _skill_files():
        references_dir = skill_path.parent / "references"
        if not references_dir.exists():
            continue
        for reference_path in sorted(references_dir.glob("*.md")):
            reference_key = f"{skill_path.parent.name}/{reference_path.stem}"
            bodies[(reference_key, "v1")] = {
                "referenceKey": reference_key,
                "version": "v1",
                "bodyMd": reference_path.read_text(encoding="utf-8").strip(),
            }
    return bodies


def _parse_frontmatter(raw: str, path: Path) -> tuple[dict[str, Any], str]:
    match = re.match(r"\A---\s*\n(.*?)\n---\s*\n?(.*)\Z", raw, flags=re.S)
    if not match:
        raise HTTPException(status_code=500, detail=f"{path.name} missing frontmatter")
    return _parse_simple_yaml(match.group(1), path), match.group(2)


def _parse_simple_yaml(text: str, path: Path) -> dict[str, Any]:
    data: dict[str, Any] = {}
    current_key: str | None = None
    current_lines: list[str] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        if line.startswith("  ") and current_key:
            current_lines.append(line.strip())
            continue
        if current_key:
            data[current_key] = _parse_value(" ".join(current_lines))
            current_key = None
            current_lines = []
        if ":" not in line:
            raise HTTPException(status_code=500, detail=f"invalid frontmatter line in {path.name}: {line}")
        key, value = line.split(":", 1)
        key = key.strip()
        value = value.strip()
        if value:
            data[key] = _parse_value(value)
        else:
            current_key = key
            current_lines = []
    if current_key:
        data[current_key] = _parse_value(" ".join(current_lines))
    if "name" not in data or "description" not in data:
        raise HTTPException(status_code=500, detail=f"{path.name} must define name and description")
    unsupported = sorted(set(data) - SUPPORTED_SKILL_FRONTMATTER)
    if unsupported:
        raise HTTPException(status_code=500, detail=f"{path.name} has unsupported frontmatter: {unsupported}")
    return data


def _parse_value(value: str) -> Any:
    if value.startswith(("'", '"')) and value.endswith(("'", '"')):
        return value[1:-1]
    if value.startswith(("[", "{")):
        return json.loads(value)
    if value.isdigit():
        return int(value)
    return value


def _safe_id(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_]+", "_", value).strip("_")
