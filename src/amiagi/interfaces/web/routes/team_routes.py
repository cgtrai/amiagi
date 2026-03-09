"""Teams API routes — CRUD foundations and org view."""

from __future__ import annotations

import inspect
import logging
import uuid
from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from amiagi.domain.team_definition import AgentDescriptor, TeamDefinition

logger = logging.getLogger(__name__)


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


def _serialize_recommendation(suggestion: Any) -> dict[str, Any]:
    if hasattr(suggestion, "to_dict"):
        data = suggestion.to_dict()
    elif isinstance(suggestion, dict):
        data = dict(suggestion)
    else:
        data = {"value": suggestion}
    data.setdefault("recommended_roles", data.get("roles", []))
    data.setdefault("team_size", len(data.get("recommended_roles", [])))
    data.setdefault("reasoning", "")
    data.setdefault("confidence", 0.0)
    data.setdefault("metadata", {})
    return data


def _serialize_team(team: TeamDefinition) -> dict[str, Any]:
    metadata = dict(team.metadata or {})
    return {
        "team_id": team.team_id,
        "name": team.name,
        "description": metadata.get("description", ""),
        "lead_agent_id": team.lead_agent_id,
        "workflow": team.workflow,
        "project_context": team.project_context,
        "members": [m.to_dict() for m in team.members],
        "metadata": metadata,
        "size": team.size,
        "member_roles": [m.role for m in team.members],
        "status": metadata.get("status", "draft"),
    }


def _validate_members(members_raw: list[Any]) -> tuple[list[AgentDescriptor], list[str]]:
    members: list[AgentDescriptor] = []
    errors: list[str] = []
    for idx, member_raw in enumerate(members_raw):
        if not isinstance(member_raw, dict):
            errors.append(f"members[{idx}] must be an object")
            continue
        role = str(member_raw.get("role", "")).strip()
        if not role:
            errors.append(f"members[{idx}].role is required")
            continue
        members.append(AgentDescriptor.from_dict(member_raw))
    return members, errors


async def api_teams(request: Request) -> Response:
    """GET /api/teams — team summary from TeamDashboard."""
    team_dashboard = getattr(request.app.state, "team_dashboard", None)
    if team_dashboard is None:
        return JSONResponse({"teams": [], "error": "team_dashboard_not_available"})

    teams: list[dict[str, Any]] = []
    if hasattr(team_dashboard, "list_teams"):
        try:
            listed = team_dashboard.list_teams()
            teams = [_serialize_team(team) for team in listed]
        except Exception:
            teams = []
    if not teams and hasattr(team_dashboard, "summary"):
        try:
            summary = team_dashboard.summary() or {}
            teams = list(summary.get("teams", []))
        except Exception:
            teams = []
    return JSONResponse({"teams": teams, "total_teams": len(teams)})


async def api_teams_templates(request: Request) -> Response:
    """GET /api/teams/templates — available team templates."""
    team_composer = getattr(request.app.state, "team_composer", None)
    if team_composer is None:
        return JSONResponse({"templates": []})

    items = []
    for template_id in team_composer.list_templates():
        team = team_composer.get_template(template_id)
        if team is None:
            continue
        items.append({
            "template_id": template_id,
            "name": team.name or template_id,
            "members": [m.to_dict() for m in team.members],
            "lead_agent_id": team.lead_agent_id,
        })
    return JSONResponse({"templates": items, "total": len(items)})


async def api_team_create(request: Request) -> Response:
    """POST /api/teams — create a team from body, template, or description."""
    team_dashboard = getattr(request.app.state, "team_dashboard", None)
    if team_dashboard is None:
        return JSONResponse({"error": "team_dashboard_not_available"}, status_code=503)

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON body"}, status_code=400)
    if not isinstance(body, dict):
        return JSONResponse({"error": "invalid JSON body"}, status_code=400)

    team_composer = getattr(request.app.state, "team_composer", None)
    template_id = str(body.get("template_id", "")).strip()
    project_context = str(body.get("project_context", "")).strip()

    team: TeamDefinition | None = None
    if template_id:
        if team_composer is None:
            return JSONResponse({"error": "team_composer_not_available"}, status_code=503)
        team = team_composer.from_template(template_id, project_context=project_context)
        if team is None:
            return JSONResponse({"error": "template_not_found"}, status_code=404)
    elif project_context and not body.get("members"):
        if team_composer is None:
            return JSONResponse({"error": "team_composer_not_available"}, status_code=503)
        team = team_composer.build_team(project_context)

    if team is None:
        members, errors = _validate_members(body.get("members", []))
        if errors:
            return JSONResponse({"error": "validation failed", "details": errors}, status_code=400)
        if not members:
            return JSONResponse({"error": "at least one member is required"}, status_code=400)
        team = TeamDefinition(
            team_id=str(body.get("team_id") or f"team-{uuid.uuid4().hex[:8]}"),
            name=str(body.get("name", "")).strip() or "New Team",
            members=members,
            lead_agent_id=str(body.get("lead_agent_id", "")).strip() or members[0].role,
            workflow=str(body.get("workflow", "")).strip(),
            project_context=project_context,
            metadata=dict(body.get("metadata") or {}),
        )

    if body.get("name"):
        team.name = str(body.get("name")).strip() or team.name
    if body.get("lead_agent_id"):
        team.lead_agent_id = str(body.get("lead_agent_id")).strip() or team.lead_agent_id
    if body.get("workflow"):
        team.workflow = str(body.get("workflow")).strip()
    team.metadata = dict(team.metadata or {})
    if body.get("description"):
        team.metadata["description"] = str(body.get("description")).strip()

    team_dashboard.register_team(team)
    return JSONResponse({"ok": True, "team": _serialize_team(team)}, status_code=201)


async def api_team_update(request: Request) -> Response:
    """PUT /api/teams/{team_id} — update an existing team."""
    team_dashboard = getattr(request.app.state, "team_dashboard", None)
    if team_dashboard is None:
        return JSONResponse({"error": "team_dashboard_not_available"}, status_code=503)

    team_id = request.path_params["team_id"]
    team = team_dashboard.get_team(team_id)
    if team is None:
        return JSONResponse({"error": "team_not_found"}, status_code=404)

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON body"}, status_code=400)
    if not isinstance(body, dict):
        return JSONResponse({"error": "invalid JSON body"}, status_code=400)

    if "members" in body:
        members, errors = _validate_members(body.get("members", []))
        if errors:
            return JSONResponse({"error": "validation failed", "details": errors}, status_code=400)
        if not members:
            return JSONResponse({"error": "at least one member is required"}, status_code=400)
        team.members = members

    if "name" in body:
        team.name = str(body.get("name") or "").strip() or team.name
    if "lead_agent_id" in body:
        lead = str(body.get("lead_agent_id") or "").strip()
        if lead:
            team.lead_agent_id = lead
    if "workflow" in body:
        team.workflow = str(body.get("workflow") or "").strip()
    if "project_context" in body:
        team.project_context = str(body.get("project_context") or "").strip()
    if "description" in body:
        team.metadata = dict(team.metadata or {})
        team.metadata["description"] = str(body.get("description") or "").strip()
    if isinstance(body.get("metadata"), dict):
        merged = dict(team.metadata or {})
        merged.update(body["metadata"])
        team.metadata = merged

    team_dashboard.register_team(team)
    return JSONResponse({"ok": True, "team": _serialize_team(team)})


async def api_team_delete(request: Request) -> Response:
    """DELETE /api/teams/{team_id} — delete team."""
    team_dashboard = getattr(request.app.state, "team_dashboard", None)
    if team_dashboard is None:
        return JSONResponse({"error": "team_dashboard_not_available"}, status_code=503)

    team_id = request.path_params["team_id"]
    if not team_dashboard.unregister_team(team_id):
        return JSONResponse({"error": "team_not_found"}, status_code=404)
    return JSONResponse({"ok": True, "team_id": team_id})


async def api_team_org(request: Request) -> Response:
    """GET /api/teams/{team_id}/org — org chart for a single team."""
    team_dashboard = getattr(request.app.state, "team_dashboard", None)
    if team_dashboard is None:
        return JSONResponse({"error": "team_dashboard_not_available"}, status_code=503)

    team_id = request.path_params["team_id"]
    chart = team_dashboard.org_chart(team_id)
    if not chart:
        return JSONResponse({"error": "team_not_found"}, status_code=404)

    return JSONResponse(chart)


async def team_recommend(request: Request) -> Response:
    """GET /api/teams/recommend?task_type=research — AI recommends team composition."""
    team_composer = getattr(request.app.state, "team_composer", None)
    if team_composer is None:
        return JSONResponse({"error": "team_composer_not_available"}, status_code=503)

    project_context = str(
        request.query_params.get("project_context")
        or request.query_params.get("task_type")
        or "general"
    ).strip()
    try:
        recommend_fn = None
        for attr in ("smart_recommend", "recommend_with_llm", "recommend"):
            candidate = getattr(team_composer, attr, None)
            if callable(candidate):
                recommend_fn = candidate
                break
        if recommend_fn is None:
            suggestion = {"project_context": project_context, "note": "recommend not implemented"}
        else:
            suggestion = await _maybe_await(recommend_fn(project_context))
    except Exception as exc:
        logger.warning("team_recommend failed: %s", exc)
        return JSONResponse({"error": str(exc)}, status_code=500)

    preview_team = None
    build_team = getattr(team_composer, "build_team", None)
    if callable(build_team):
        try:
            preview_team = build_team(project_context)
        except Exception:
            preview_team = None

    return JSONResponse({
        "ok": True,
        "project_context": project_context,
        "recommendation": _serialize_recommendation(suggestion),
        "preview_team": _serialize_team(preview_team) if isinstance(preview_team, TeamDefinition) else None,
    })


async def team_deploy(request: Request) -> Response:
    """POST /api/teams/{team_id}/deploy — activate team (spawn agents if needed)."""
    team_dashboard = getattr(request.app.state, "team_dashboard", None)
    if team_dashboard is None:
        return JSONResponse({"error": "team_dashboard_not_available"}, status_code=503)

    team_id = request.path_params["team_id"]
    team = team_dashboard.get_team(team_id)
    if team is None:
        return JSONResponse({"error": "team_not_found"}, status_code=404)

    deploy_fn = getattr(team_dashboard, "deploy", None)
    if not callable(deploy_fn):
        team.metadata = dict(team.metadata or {})
        team.metadata["status"] = "saved_only"
        team.metadata["deploy_note"] = "runtime deploy not available"
        team_dashboard.register_team(team)
        return JSONResponse(
            {
                "ok": False,
                "error": "deploy_not_supported",
                "result": {
                    "team_id": team_id,
                    "status": "saved_only",
                    "note": "runtime deploy not available",
                },
                "team": _serialize_team(team),
            },
            status_code=409,
        )

    try:
        result = await _maybe_await(deploy_fn(team_id))
    except Exception as exc:
        logger.warning("team_deploy failed: %s", exc)
        return JSONResponse({"error": str(exc)}, status_code=500)

    if isinstance(result, TeamDefinition):
        payload = _serialize_team(result)
    elif isinstance(result, dict):
        payload = dict(result)
    else:
        payload = {"value": result}
    payload.setdefault("team_id", team_id)
    payload.setdefault("status", payload.get("status", "deployed"))

    team.metadata = dict(team.metadata or {})
    team.metadata["status"] = payload.get("status", "deployed")
    if payload.get("note"):
        team.metadata["deploy_note"] = payload["note"]
    team_dashboard.register_team(team)

    return JSONResponse({"ok": True, "result": payload, "team": _serialize_team(team)})


team_routes = [
    Route("/api/teams/recommend", team_recommend, methods=["GET"]),
    Route("/api/teams", api_teams, methods=["GET"]),
    Route("/api/teams", api_team_create, methods=["POST"]),
    Route("/api/teams/templates", api_teams_templates, methods=["GET"]),
    Route("/api/teams/{team_id}/deploy", team_deploy, methods=["POST"]),
    Route("/api/teams/{team_id}/org", api_team_org, methods=["GET"]),
    Route("/api/teams/{team_id}", api_team_update, methods=["PUT"]),
    Route("/api/teams/{team_id}", api_team_delete, methods=["DELETE"]),
]
