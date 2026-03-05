"""Agent configuration routes — persona, skills, prompt preview.

Routes:
- GET  /agents/{agent_id}/config   — current agent configuration
- PUT  /agents/{agent_id}/config   — update persona, skills
- GET  /agents/{agent_id}/preview  — preview assembled system prompt
"""

from __future__ import annotations

import logging
from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

logger = logging.getLogger(__name__)


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
        registry.update_model(agent_id, body["model_name"], model_backend=model_backend)
        changed.append("model")

    if not changed:
        return JSONResponse({"error": "no_changes"}, status_code=400)

    # Log activity
    activity_logger = getattr(request.app.state, "activity_logger", None)
    if activity_logger is not None:
        user = getattr(request.state, "user", None)
        user_id = str(user.id) if user else "anonymous"
        try:
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


agent_config_routes = [
    Route("/agents/{agent_id}/config", get_agent_config, methods=["GET"]),
    Route("/agents/{agent_id}/config", update_agent_config, methods=["PUT"]),
    Route("/agents/{agent_id}/preview", preview_agent_prompt, methods=["GET"]),
]
