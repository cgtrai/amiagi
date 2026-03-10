"""Admin skill, project-skill & trait management routes.

Routes:
- GET    /admin/skills           — list skills (filter by category, role)
- POST   /admin/skills           — create skill
- GET    /admin/skills/{id}      — get skill detail
- PUT    /admin/skills/{id}      — update skill
- DELETE /admin/skills/{id}      — delete skill
- GET    /admin/skills/{id}/stats — usage stats
- GET    /admin/traits           — list traits (filter by type, role)
- POST   /admin/traits           — create trait
- GET    /admin/traits/{id}      — get trait detail
- PUT    /admin/traits/{id}      — update trait
- DELETE /admin/traits/{id}      — delete trait
"""

from __future__ import annotations

import logging
from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from amiagi.interfaces.web.rbac.middleware import require_permission

logger = logging.getLogger(__name__)


def _get_skill_repo(request: Request):
    return getattr(request.app.state, "skill_repository", None)


def _get_runtime_skill_provider(request: Request):
    return getattr(request.app.state, "runtime_skill_provider", None)


def _get_project_skill_repo(request: Request):
    return getattr(request.app.state, "project_skill_repository", None)


async def _refresh_runtime_skill_provider(request: Request) -> None:
    repo = _get_skill_repo(request)
    project_repo = _get_project_skill_repo(request)
    provider = _get_runtime_skill_provider(request)
    if repo is None or provider is None:
        return
    await provider.refresh(repo, project_repo)


async def _skill_preview_payload(repo, skill_id: str) -> dict[str, Any] | None:
    skill = await repo.get_skill(skill_id)
    if skill is None:
        return None

    usage_map = await repo.skill_usage_map() if hasattr(repo, "skill_usage_map") else []
    stats = await repo.skill_usage_stats(skill_id) if hasattr(repo, "skill_usage_stats") else {}
    usage_entry = next((item for item in usage_map if item.get("skill_id") == skill_id), None)
    linked_agents = usage_entry.get("agents", []) if usage_entry else []
    preview_sections = [
        f"# Skill: {skill.display_name or skill.name}",
        skill.description or skill.content or "",
    ]
    if skill.compatible_roles:
        preview_sections.append("Compatible roles: " + ", ".join(skill.compatible_roles))
    if skill.trigger_keywords:
        preview_sections.append("Trigger keywords: " + ", ".join(skill.trigger_keywords))
    if skill.compatible_tools:
        preview_sections.append("Tools: " + ", ".join(skill.compatible_tools))

    return {
        "skill": skill.to_dict(),
        "prompt_preview": "\n\n".join(section for section in preview_sections if section),
        "linked_agents": linked_agents,
        "stats": stats,
    }


def _parse_import_payload(body: bytes, content_type: str) -> list[dict]:
    """Parse YAML or JSON import body into a list of skill dicts."""
    text = body.decode("utf-8")
    if "yaml" in content_type or "x-yaml" in content_type:
        try:
            import yaml
        except ImportError:
            raise ValueError("PyYAML not installed — cannot parse YAML")
        data = yaml.safe_load(text)
    else:
        data = __import__("json").loads(text)

    if isinstance(data, dict) and "skills" in data:
        data = data["skills"]
    if not isinstance(data, list):
        raise ValueError("Expected a JSON/YAML array of skill objects")
    return data


def _parse_project_skill_payload(body: dict[str, Any]) -> dict[str, Any]:
    role = str(body.get("role", "") or "").strip()
    name = str(body.get("name", "") or "").strip()
    content = str(body.get("content", "") or "")
    if not role or not name or not content.strip():
        raise ValueError("role_name_and_content_required")
    return {
        "role": role,
        "name": name,
        "display_name": str(body.get("display_name", "") or "").strip(),
        "description": str(body.get("description", "") or "").strip(),
        "content": content,
        "trigger_keywords": list(body.get("trigger_keywords", []) or []),
        "compatible_tools": list(body.get("compatible_tools", []) or []),
        "compatible_roles": list(body.get("compatible_roles", []) or []),
        "priority": int(body.get("priority", 50) or 50),
    }


# ── Skills ─────────────────────────────────────────────────────

@require_permission("admin.settings")
async def admin_list_skills(request: Request) -> Response:
    # Browser navigation → render HTML template
    accept = request.headers.get("accept", "")
    templates = getattr(request.app.state, "templates", None)
    if templates is not None and "text/html" in accept:
        return templates.TemplateResponse(
            request,
            "admin/skills.html",
            {"user": request.state.user},
        )

    # JS fetch / API → return JSON
    repo = _get_skill_repo(request)
    if repo is None:
        return JSONResponse({"error": "skill_repository_not_available"}, status_code=503)

    category = request.query_params.get("category")
    role = request.query_params.get("role")
    active_only = request.query_params.get("active", "true").lower() == "true"

    skills = await repo.list_skills(category=category, role=role, active_only=active_only)
    return JSONResponse({"skills": [s.to_dict() for s in skills]})


@require_permission("admin.settings")
async def admin_skill_edit_page(request: Request) -> Response:
    templates = getattr(request.app.state, "templates", None)
    if templates is None:
        return JSONResponse({"error": "templates_not_available"}, status_code=503)
    return templates.TemplateResponse(
        request,
        "admin/skill_edit.html",
        {"user": request.state.user},
    )


@require_permission("admin.settings")
async def admin_create_skill(request: Request) -> Response:
    repo = _get_skill_repo(request)
    if repo is None:
        return JSONResponse({"error": "skill_repository_not_available"}, status_code=503)

    body = await request.json()
    if not body.get("name") or not body.get("content"):
        return JSONResponse({"error": "name_and_content_required"}, status_code=400)

    skill = await repo.create_skill(**body)
    await _refresh_runtime_skill_provider(request)
    return JSONResponse(skill.to_dict(), status_code=201)


@require_permission("admin.settings")
async def admin_get_skill(request: Request) -> Response:
    repo = _get_skill_repo(request)
    if repo is None:
        return JSONResponse({"error": "skill_repository_not_available"}, status_code=503)

    skill = await repo.get_skill(request.path_params["id"])
    if skill is None:
        return JSONResponse({"error": "not_found"}, status_code=404)
    return JSONResponse(skill.to_dict())


@require_permission("admin.settings")
async def admin_update_skill(request: Request) -> Response:
    repo = _get_skill_repo(request)
    if repo is None:
        return JSONResponse({"error": "skill_repository_not_available"}, status_code=503)

    body = await request.json()
    skill = await repo.update_skill(request.path_params["id"], **body)
    if skill is None:
        return JSONResponse({"error": "not_found"}, status_code=404)
    await _refresh_runtime_skill_provider(request)
    return JSONResponse(skill.to_dict())


@require_permission("admin.settings")
async def admin_delete_skill(request: Request) -> Response:
    repo = _get_skill_repo(request)
    if repo is None:
        return JSONResponse({"error": "skill_repository_not_available"}, status_code=503)

    ok = await repo.delete_skill(request.path_params["id"])
    if not ok:
        return JSONResponse({"error": "not_found"}, status_code=404)
    await _refresh_runtime_skill_provider(request)
    return JSONResponse({"status": "deleted"})


@require_permission("admin.settings")
async def admin_skill_stats(request: Request) -> Response:
    repo = _get_skill_repo(request)
    if repo is None:
        return JSONResponse({"error": "skill_repository_not_available"}, status_code=503)

    stats = await repo.skill_usage_stats(request.path_params["id"])
    return JSONResponse(stats)


@require_permission("admin.settings")
async def admin_skill_preview(request: Request) -> Response:
    repo = _get_skill_repo(request)
    if repo is None:
        return JSONResponse({"error": "skill_repository_not_available"}, status_code=503)

    payload = await _skill_preview_payload(repo, request.path_params["id"])
    if payload is None:
        return JSONResponse({"error": "not_found"}, status_code=404)
    return JSONResponse(payload)


@require_permission("admin.settings")
async def admin_skill_usage_map(request: Request) -> Response:
    repo = _get_skill_repo(request)
    if repo is None:
        return JSONResponse({"error": "skill_repository_not_available"}, status_code=503)

    usage = await repo.skill_usage_map()
    return JSONResponse({"skills": usage})


# ── Project Skills (file-based) ───────────────────────────────

@require_permission("admin.settings")
async def admin_list_project_skills(request: Request) -> Response:
    accept = request.headers.get("accept", "")
    templates = getattr(request.app.state, "templates", None)
    if templates is not None and "text/html" in accept:
        return templates.TemplateResponse(
            request,
            "admin/project_skills.html",
            {"user": request.state.user},
        )

    repo = _get_project_skill_repo(request)
    if repo is None:
        return JSONResponse({"error": "project_skill_repository_not_available"}, status_code=503)

    role = request.query_params.get("role")
    skills = repo.list_skills(role=role)
    return JSONResponse({"skills": [skill.to_dict() for skill in skills]})


@require_permission("admin.settings")
async def admin_create_project_skill(request: Request) -> Response:
    repo = _get_project_skill_repo(request)
    if repo is None:
        return JSONResponse({"error": "project_skill_repository_not_available"}, status_code=503)
    try:
        payload = _parse_project_skill_payload(await request.json())
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    skill = repo.upsert_skill(**payload)
    await _refresh_runtime_skill_provider(request)
    return JSONResponse(skill.to_dict(), status_code=201)


@require_permission("admin.settings")
async def admin_get_project_skill(request: Request) -> Response:
    repo = _get_project_skill_repo(request)
    if repo is None:
        return JSONResponse({"error": "project_skill_repository_not_available"}, status_code=503)
    role = request.path_params["role"]
    name = request.path_params["name"]
    skill = repo.get_skill(role, name)
    if skill is None:
        return JSONResponse({"error": "not_found"}, status_code=404)
    return JSONResponse(skill.to_dict())


@require_permission("admin.settings")
async def admin_update_project_skill(request: Request) -> Response:
    repo = _get_project_skill_repo(request)
    if repo is None:
        return JSONResponse({"error": "project_skill_repository_not_available"}, status_code=503)
    role = request.path_params["role"]
    name = request.path_params["name"]
    existing = repo.get_skill(role, name)
    if existing is None:
        return JSONResponse({"error": "not_found"}, status_code=404)
    body = await request.json()
    payload = {
        "role": role,
        "name": str(body.get("name", existing.name) or existing.name),
        "display_name": str(body.get("display_name", existing.display_name) or existing.display_name),
        "description": str(body.get("description", existing.description) or existing.description),
        "content": str(body.get("content", existing.content) or existing.content),
        "trigger_keywords": list(body.get("trigger_keywords", existing.trigger_keywords) or existing.trigger_keywords),
        "compatible_tools": list(body.get("compatible_tools", existing.compatible_tools) or existing.compatible_tools),
        "compatible_roles": list(body.get("compatible_roles", existing.compatible_roles) or existing.compatible_roles),
        "priority": int(body.get("priority", existing.priority) or existing.priority),
    }
    if payload["name"] != existing.name:
        repo.delete_skill(role, existing.name)
    skill = repo.upsert_skill(**payload)
    await _refresh_runtime_skill_provider(request)
    return JSONResponse(skill.to_dict())


@require_permission("admin.settings")
async def admin_delete_project_skill(request: Request) -> Response:
    repo = _get_project_skill_repo(request)
    if repo is None:
        return JSONResponse({"error": "project_skill_repository_not_available"}, status_code=503)
    role = request.path_params["role"]
    name = request.path_params["name"]
    ok = repo.delete_skill(role, name)
    if not ok:
        return JSONResponse({"error": "not_found"}, status_code=404)
    await _refresh_runtime_skill_provider(request)
    return JSONResponse({"status": "deleted"})


# ── Traits ─────────────────────────────────────────────────────

@require_permission("admin.settings")
async def admin_list_traits(request: Request) -> Response:
    # Browser navigation → render HTML template
    accept = request.headers.get("accept", "")
    templates = getattr(request.app.state, "templates", None)
    if templates is not None and "text/html" in accept:
        return templates.TemplateResponse(
            request,
            "admin/traits.html",
            {"user": request.state.user},
        )

    # JS fetch / API → return JSON
    repo = _get_skill_repo(request)
    if repo is None:
        return JSONResponse({"error": "skill_repository_not_available"}, status_code=503)

    trait_type = request.query_params.get("type")
    agent_role = request.query_params.get("role")
    active_only = request.query_params.get("active", "true").lower() == "true"
    traits = await repo.list_traits(trait_type=trait_type, agent_role=agent_role, active_only=active_only)
    return JSONResponse({"traits": [t.to_dict() for t in traits]})


@require_permission("admin.settings")
async def admin_create_trait(request: Request) -> Response:
    repo = _get_skill_repo(request)
    if repo is None:
        return JSONResponse({"error": "skill_repository_not_available"}, status_code=503)

    body = await request.json()
    required = ("trait_type", "agent_role", "name", "content")
    if not all(body.get(k) for k in required):
        return JSONResponse({"error": "missing_required_fields"}, status_code=400)

    trait = await repo.create_trait(**body)
    await _refresh_runtime_skill_provider(request)
    return JSONResponse(trait.to_dict(), status_code=201)


@require_permission("admin.settings")
async def admin_get_trait(request: Request) -> Response:
    repo = _get_skill_repo(request)
    if repo is None:
        return JSONResponse({"error": "skill_repository_not_available"}, status_code=503)

    trait = await repo.get_trait(request.path_params["id"])
    if trait is None:
        return JSONResponse({"error": "not_found"}, status_code=404)
    return JSONResponse(trait.to_dict())


@require_permission("admin.settings")
async def admin_update_trait(request: Request) -> Response:
    repo = _get_skill_repo(request)
    if repo is None:
        return JSONResponse({"error": "skill_repository_not_available"}, status_code=503)

    body = await request.json()
    trait = await repo.update_trait(request.path_params["id"], **body)
    if trait is None:
        return JSONResponse({"error": "not_found"}, status_code=404)
    await _refresh_runtime_skill_provider(request)
    return JSONResponse(trait.to_dict())


@require_permission("admin.settings")
async def admin_delete_trait(request: Request) -> Response:
    repo = _get_skill_repo(request)
    if repo is None:
        return JSONResponse({"error": "skill_repository_not_available"}, status_code=503)

    ok = await repo.delete_trait(request.path_params["id"])
    if not ok:
        return JSONResponse({"error": "not_found"}, status_code=404)
    await _refresh_runtime_skill_provider(request)
    return JSONResponse({"status": "deleted"})


# ── Import ─────────────────────────────────────────────────────

@require_permission("admin.settings")
async def admin_import_skills(request: Request) -> Response:
    """``POST /admin/skills/import`` — bulk import skills from YAML or JSON."""
    repo = _get_skill_repo(request)
    if repo is None:
        return JSONResponse({"error": "skill_repository_not_available"}, status_code=503)

    body = await request.body()
    ct = request.headers.get("content-type", "application/json")

    try:
        items = _parse_import_payload(body, ct)
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)

    created = []
    errors = []
    for idx, item in enumerate(items):
        if not item.get("name") or not item.get("content"):
            errors.append({"index": idx, "error": "name_and_content_required"})
            continue
        try:
            skill = await repo.create_skill(**item)
            created.append(skill.to_dict())
        except Exception as exc:
            errors.append({"index": idx, "error": str(exc)})

    if created:
        await _refresh_runtime_skill_provider(request)

    return JSONResponse(
        {"imported": len(created), "errors": errors, "skills": created},
        status_code=201 if created else 400,
    )


# ── Route list ─────────────────────────────────────────────────

skill_admin_routes = [
    Route("/admin/skills", admin_list_skills, methods=["GET"]),
    Route("/admin/project-skills", admin_list_project_skills, methods=["GET"]),
    Route("/admin/skills/{id}/edit", admin_skill_edit_page, methods=["GET"]),
    Route("/admin/skills", admin_create_skill, methods=["POST"]),
    Route("/admin/project-skills", admin_create_project_skill, methods=["POST"]),
    Route("/admin/skills/import", admin_import_skills, methods=["POST"]),
    Route("/admin/skills/usage-map", admin_skill_usage_map, methods=["GET"]),
    Route("/admin/skills/{id}", admin_get_skill, methods=["GET"]),
    Route("/admin/skills/{id}", admin_update_skill, methods=["PUT"]),
    Route("/admin/skills/{id}", admin_delete_skill, methods=["DELETE"]),
    Route("/admin/project-skills/{role}/{name}", admin_get_project_skill, methods=["GET"]),
    Route("/admin/project-skills/{role}/{name}", admin_update_project_skill, methods=["PUT"]),
    Route("/admin/project-skills/{role}/{name}", admin_delete_project_skill, methods=["DELETE"]),
    Route("/admin/skills/{id}/stats", admin_skill_stats, methods=["GET"]),
    Route("/admin/skills/{id}/preview", admin_skill_preview, methods=["GET"]),
    Route("/admin/traits", admin_list_traits, methods=["GET"]),
    Route("/admin/traits", admin_create_trait, methods=["POST"]),
    Route("/admin/traits/{id}", admin_get_trait, methods=["GET"]),
    Route("/admin/traits/{id}", admin_update_trait, methods=["PUT"]),
    Route("/admin/traits/{id}", admin_delete_trait, methods=["DELETE"]),
]
