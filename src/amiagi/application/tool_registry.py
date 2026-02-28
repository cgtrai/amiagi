from __future__ import annotations

import json
from pathlib import Path

REGISTRY_RELATIVE_PATH = "state/tool_registry.json"


def registry_path(work_dir: Path) -> Path:
    return (work_dir / REGISTRY_RELATIVE_PATH).resolve()


def load_registry(work_dir: Path) -> dict:
    path = registry_path(work_dir)
    if not path.exists() or not path.is_file():
        return {"tools": {}}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"tools": {}}
    if not isinstance(payload, dict):
        return {"tools": {}}
    tools = payload.get("tools")
    if not isinstance(tools, dict):
        payload["tools"] = {}
    return payload


def list_registered_tools(work_dir: Path) -> set[str]:
    payload = load_registry(work_dir)
    tools = payload.get("tools", {})
    if not isinstance(tools, dict):
        return set()
    result: set[str] = set()
    for raw_name, raw_spec in tools.items():
        if not isinstance(raw_name, str):
            continue
        name = raw_name.strip()
        if not name:
            continue
        if isinstance(raw_spec, dict):
            script_path = raw_spec.get("script_path")
            if isinstance(script_path, str) and script_path.strip():
                result.add(name)
    return result


def resolve_registered_tool_script(work_dir: Path, tool_name: str) -> Path | None:
    payload = load_registry(work_dir)
    tools = payload.get("tools")
    if not isinstance(tools, dict):
        return None
    spec = tools.get(tool_name)
    if not isinstance(spec, dict):
        return None
    raw_path = spec.get("script_path")
    if not isinstance(raw_path, str) or not raw_path.strip():
        return None
    candidate = Path(raw_path.strip())
    if not candidate.is_absolute():
        candidate = (work_dir / candidate).resolve()
    return candidate
