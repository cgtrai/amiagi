"""Agent configuration routes — persona, skills, prompt preview.

Routes:
- GET  /agents/{agent_id}/config   — current agent configuration
- PUT  /agents/{agent_id}/config   — update persona, skills
- GET  /agents/{agent_id}/preview  — preview assembled system prompt
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

logger = logging.getLogger(__name__)

_MODEL_CONFIG_PATH = Path("data/model_config.json")


def _resolve_model_config_path(request: Request | None = None) -> Path:
    if request is not None:
        settings = getattr(request.app.state, "settings", None)
        configured = getattr(settings, "model_config_path", None)
        if isinstance(configured, (str, Path)) and configured:
            return Path(configured)
    return _MODEL_CONFIG_PATH


def _apply_live_model(request: Request, agent_id: str, model_name: str) -> None:
    """Hot-swap the running OllamaClient model so the change is immediate."""
    if agent_id != "polluks":
        return  # only executor agent uses the shared OllamaClient
    adapter = getattr(request.app.state, "web_adapter", None)
    if adapter is None:
        return
    try:
        from amiagi.interfaces.shared_cli_helpers import _set_executor_model

        chat_svc = adapter.router_engine.chat_service
        ok, _prev = _set_executor_model(chat_svc, model_name)
        if ok:
            logger.info("Live model updated to '%s' for %s", model_name, agent_id)
        else:
            logger.warning("Failed to live-update model for %s", agent_id)
    except Exception as exc:
        logger.warning("_apply_live_model error: %s", exc)


def _persist_model_config(request: Request | None, agent_id: str, model_name: str, model_backend: str) -> None:
    """Write agent model changes to model_config.json for persistence."""
    try:
        path = _resolve_model_config_path(request)
        config: dict = {}
        if path.exists():
            config = json.loads(path.read_text(encoding="utf-8"))
        config[f"{agent_id}_model"] = model_name
        config[f"{agent_id}_source"] = model_backend or "ollama"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(config, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
    except Exception as exc:
        logger.warning("Failed to persist model config: %s", exc)


async def get_agent_config(request: Request) -> Response:
    """GET /agents/{agent_id}/config — full agent configuration."""
    agent_id = request.path_params["agent_id"]
    registry = getattr(request.app.state, "agent_registry", None)
    if registry is None:
        return JSONResponse({"error": "agent_registry_not_available"}, status_code=503)

    agent = registry.get(agent_id)
    if agent is None:
        return JSONResponse({"error": "agent_not_found"}, status_code=404)

    return JSONResponse({
        "agent_id": agent.agent_id,
        "name": agent.name,
        "role": agent.role.value if hasattr(agent.role, "value") else str(agent.role),
        "model_name": agent.model_name,
        "model_backend": agent.model_backend,
        "persona_prompt": agent.persona_prompt,
        "skills": list(agent.skills) if agent.skills else [],
        "tools": list(agent.tools) if agent.tools else [],
        "metadata": agent.metadata or {},
    })


async def update_agent_config(request: Request) -> Response:
    """PUT /agents/{agent_id}/config — update persona_prompt and/or skills."""
    agent_id = request.path_params["agent_id"]
    registry = getattr(request.app.state, "agent_registry", None)
    if registry is None:
        return JSONResponse({"error": "agent_registry_not_available"}, status_code=503)

    agent = registry.get(agent_id)
    if agent is None:
        return JSONResponse({"error": "agent_not_found"}, status_code=404)

    body = await request.json()
    changed: list[str] = []

    if "persona_prompt" in body:
        agent.persona_prompt = body["persona_prompt"]
        changed.append("persona_prompt")

    if "skills" in body:
        agent.skills = list(body["skills"])
        changed.append("skills")

    if "model_name" in body:
        model_backend = body.get("model_backend", agent.model_backend)
        try:
            registry.update_model(agent_id, body["model_name"], model_backend=model_backend)
        except Exception as exc:
            logger.exception("registry.update_model failed for %s", agent_id)
            return JSONResponse({"error": str(exc)}, status_code=500)
        # Persist to model_config.json so change survives restart
        _persist_model_config(request, agent_id, body["model_name"], model_backend)
        # Update the live OllamaClient so the change takes effect immediately
        _apply_live_model(request, agent_id, body["model_name"])
        changed.append("model")

    if not changed:
        return JSONResponse({"error": "no_changes"}, status_code=400)

    # Log activity
    activity_logger = getattr(request.app.state, "activity_logger", None)
    if activity_logger is not None:
        try:
            user = getattr(request.state, "user", None)
            user_id = str(user.user_id) if user else "anonymous"
            await activity_logger.log(
                user_id=user_id,
                action="agent.config_updated",
                detail={"agent_id": agent_id, "changed_fields": changed},
            )
        except Exception:
            pass

    return JSONResponse({"status": "ok", "changed": changed})


async def preview_agent_prompt(request: Request) -> Response:
    """GET /agents/{agent_id}/preview — assembled system prompt preview."""
    agent_id = request.path_params["agent_id"]
    registry = getattr(request.app.state, "agent_registry", None)
    if registry is None:
        return JSONResponse({"error": "agent_registry_not_available"}, status_code=503)

    agent = registry.get(agent_id)
    if agent is None:
        return JSONResponse({"error": "agent_not_found"}, status_code=404)

    # Build a preview of what the agent "sees"
    sections: list[str] = []

    if agent.persona_prompt:
        sections.append(f"## Persona\n{agent.persona_prompt}")

    if agent.skills:
        skill_list = "\n".join(f"- {s}" for s in agent.skills)
        sections.append(f"## Skills\n{skill_list}")

    if agent.tools:
        tool_list = "\n".join(f"- {t}" for t in agent.tools)
        sections.append(f"## Tools\n{tool_list}")

    prompt_preview = "\n\n".join(sections) if sections else "(no prompt configured)"

    return JSONResponse({
        "agent_id": agent_id,
        "prompt_preview": prompt_preview,
        "sections": {
            "persona": agent.persona_prompt or "",
            "skills": list(agent.skills) if agent.skills else [],
            "tools": list(agent.tools) if agent.tools else [],
        },
    })


def _serialize_activity_entry(row: dict[str, Any]) -> dict[str, Any]:
    detail = row.get("detail") or {}
    if isinstance(detail, str):
        try:
            detail = json.loads(detail)
        except Exception:
            detail = {"message": detail}
    return {
        "id": str(row.get("id", "")),
        "action": row.get("action", ""),
        "detail": detail,
        "created_at": row.get("created_at").isoformat() if row.get("created_at") else None,
    }


def _activity_matches_agent(detail: dict[str, Any], agent_id: str) -> bool:
    candidates = [
        detail.get("agent_id"),
        detail.get("source_agent"),
        detail.get("assigned_agent_id"),
        detail.get("lead_agent_id"),
    ]
    if agent_id in candidates:
        return True
    values = [str(v) for v in detail.values() if isinstance(v, (str, int, float))]
    return any(agent_id == value for value in values)


async def get_agent_permissions(request: Request) -> Response:
    """GET /api/agents/{agent_id}/permissions — effective permissions/capabilities."""
    agent_id = request.path_params["agent_id"]
    registry = getattr(request.app.state, "agent_registry", None)
    if registry is None:
        return JSONResponse({"error": "agent_registry_not_available"}, status_code=503)

    agent = registry.get(agent_id)
    if agent is None:
        return JSONResponse({"error": "agent_not_found"}, status_code=404)

    metadata = agent.metadata or {}
    allowed = list(metadata.get("allowed_permissions", metadata.get("permissions", [])) or [])
    blocked = list(metadata.get("blocked_permissions", []) or [])
    return JSONResponse({
        "agent_id": agent_id,
        "permissions": allowed,
        "allowed": allowed,
        "blocked": blocked,
        "tools": list(agent.tools) if agent.tools else [],
        "skills": list(agent.skills) if agent.skills else [],
        "workspace": metadata.get("workspace"),
        "sandbox_mode": metadata.get("sandbox_mode"),
        "model_backend": agent.model_backend,
        "model_name": agent.model_name,
        "metadata": metadata,
    })


async def update_agent_skills(request: Request) -> Response:
    """PUT /api/agents/{agent_id}/skills — replace assigned skills for an agent."""
    agent_id = request.path_params["agent_id"]
    registry = getattr(request.app.state, "agent_registry", None)
    if registry is None:
        return JSONResponse({"error": "agent_registry_not_available"}, status_code=503)

    agent = registry.get(agent_id)
    if agent is None:
        return JSONResponse({"error": "agent_not_found"}, status_code=404)

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON body"}, status_code=400)

    skills = body.get("skills", [])
    if not isinstance(skills, list):
        return JSONResponse({"error": "skills must be a list"}, status_code=400)

    agent.skills = [str(skill).strip() for skill in skills if str(skill).strip()]
    return JSONResponse({"ok": True, "agent_id": agent_id, "skills": list(agent.skills)})


def _normalise_permission_list(values: list[Any]) -> list[str]:
    seen: set[str] = set()
    items: list[str] = []
    for value in values:
        item = str(value).strip()
        if not item or item in seen:
            continue
        seen.add(item)
        items.append(item)
    return items


async def update_agent_permissions(request: Request) -> Response:
    """PUT /api/agents/{agent_id}/permissions — replace or add allowed/blocked permissions."""
    agent_id = request.path_params["agent_id"]
    registry = getattr(request.app.state, "agent_registry", None)
    if registry is None:
        return JSONResponse({"error": "agent_registry_not_available"}, status_code=503)

    agent = registry.get(agent_id)
    if agent is None:
        return JSONResponse({"error": "agent_not_found"}, status_code=404)

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON body"}, status_code=400)

    metadata = agent.metadata or {}
    allowed = _normalise_permission_list(list(metadata.get("allowed_permissions", metadata.get("permissions", [])) or []))
    blocked = _normalise_permission_list(list(metadata.get("blocked_permissions", []) or []))

    if isinstance(body.get("allowed"), list) or isinstance(body.get("blocked"), list):
        if isinstance(body.get("allowed"), list):
            allowed = _normalise_permission_list(body.get("allowed", []))
        if isinstance(body.get("blocked"), list):
            blocked = _normalise_permission_list(body.get("blocked", []))
    else:
        permission = str(body.get("permission", "")).strip()
        section = str(body.get("section", "allowed")).strip().lower() or "allowed"
        if not permission:
            return JSONResponse({"error": "permission is required"}, status_code=400)
        if section not in {"allowed", "blocked"}:
            return JSONResponse({"error": "section must be allowed or blocked"}, status_code=400)
        target = allowed if section == "allowed" else blocked
        opposite = blocked if section == "allowed" else allowed
        if permission not in target:
            target.append(permission)
        opposite[:] = [item for item in opposite if item != permission]

    metadata["allowed_permissions"] = allowed
    metadata["blocked_permissions"] = blocked
    metadata["permissions"] = allowed
    agent.metadata = metadata
    return JSONResponse({"ok": True, "agent_id": agent_id, "allowed": allowed, "blocked": blocked})


async def delete_agent_permission(request: Request) -> Response:
    """DELETE /api/agents/{agent_id}/permissions/{permission} — remove a permission from allowed/blocked."""
    agent_id = request.path_params["agent_id"]
    permission = request.path_params["permission"]
    registry = getattr(request.app.state, "agent_registry", None)
    if registry is None:
        return JSONResponse({"error": "agent_registry_not_available"}, status_code=503)

    agent = registry.get(agent_id)
    if agent is None:
        return JSONResponse({"error": "agent_not_found"}, status_code=404)

    body: dict[str, Any] = {}
    try:
        body = await request.json()
    except Exception:
        body = {}

    section = str(body.get("section", "allowed")).strip().lower() or "allowed"
    if section not in {"allowed", "blocked"}:
        return JSONResponse({"error": "section must be allowed or blocked"}, status_code=400)

    metadata = agent.metadata or {}
    allowed = _normalise_permission_list(list(metadata.get("allowed_permissions", metadata.get("permissions", [])) or []))
    blocked = _normalise_permission_list(list(metadata.get("blocked_permissions", []) or []))
    target = allowed if section == "allowed" else blocked
    updated = [item for item in target if item != permission]
    if section == "allowed":
        allowed = updated
    else:
        blocked = updated
    metadata["allowed_permissions"] = allowed
    metadata["blocked_permissions"] = blocked
    metadata["permissions"] = allowed
    agent.metadata = metadata
    return JSONResponse({"ok": True, "agent_id": agent_id, "allowed": allowed, "blocked": blocked})


async def get_agent_history(request: Request) -> Response:
    """GET /api/agents/{agent_id}/history — recent audit history for an agent."""
    agent_id = request.path_params["agent_id"]
    registry = getattr(request.app.state, "agent_registry", None)
    if registry is None:
        return JSONResponse({"error": "agent_registry_not_available"}, status_code=503)
    if registry.get(agent_id) is None:
        return JSONResponse({"error": "agent_not_found"}, status_code=404)

    activity_logger = getattr(request.app.state, "activity_logger", None)
    history: list[dict[str, Any]] = []
    if activity_logger is not None and hasattr(activity_logger, "query"):
        rows = await activity_logger.query(limit=100)
        for row in rows:
            detail = row.get("detail") or {}
            if isinstance(detail, str):
                try:
                    detail = json.loads(detail)
                except Exception:
                    detail = {"message": detail}
            if isinstance(detail, dict) and _activity_matches_agent(detail, agent_id):
                history.append(_serialize_activity_entry({**row, "detail": detail}))

    return JSONResponse({"agent_id": agent_id, "history": history[:25], "total": len(history)})


async def get_agent_benchmarks(request: Request) -> Response:
    """GET /api/agents/{agent_id}/benchmarks — evaluation history for an agent."""
    agent_id = request.path_params["agent_id"]
    registry = getattr(request.app.state, "agent_registry", None)
    if registry is None:
        return JSONResponse({"error": "agent_registry_not_available"}, status_code=503)
    if registry.get(agent_id) is None:
        return JSONResponse({"error": "agent_not_found"}, status_code=404)

    eval_runner = getattr(request.app.state, "eval_runner", None)
    results: list[dict[str, Any]] = []
    if eval_runner is not None and hasattr(eval_runner, "history"):
        history = eval_runner.history(agent_id)
        for item in history:
            if hasattr(item, "to_dict"):
                results.append(item.to_dict())
            elif isinstance(item, dict):
                results.append(item)

    return JSONResponse({"agent_id": agent_id, "benchmarks": results, "total": len(results)})


agent_config_routes = [
    Route("/agents/{agent_id}/config", get_agent_config, methods=["GET"]),
    Route("/agents/{agent_id}/config", update_agent_config, methods=["PUT"]),
    Route("/agents/{agent_id}/preview", preview_agent_prompt, methods=["GET"]),
    Route("/api/agents/{agent_id}/permissions", get_agent_permissions, methods=["GET"]),
    Route("/api/agents/{agent_id}/permissions", update_agent_permissions, methods=["PUT"]),
    Route("/api/agents/{agent_id}/permissions/{permission}", delete_agent_permission, methods=["DELETE"]),
    Route("/api/agents/{agent_id}/skills", update_agent_skills, methods=["PUT"]),
    Route("/api/agents/{agent_id}/history", get_agent_history, methods=["GET"]),
    Route("/api/agents/{agent_id}/benchmarks", get_agent_benchmarks, methods=["GET"]),
]
