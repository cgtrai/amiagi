"""Admin skill & trait management routes.

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
    return JSONResponse(skill.to_dict())


@require_permission("admin.settings")
async def admin_delete_skill(request: Request) -> Response:
    repo = _get_skill_repo(request)
    if repo is None:
        return JSONResponse({"error": "skill_repository_not_available"}, status_code=503)

    ok = await repo.delete_skill(request.path_params["id"])
    if not ok:
        return JSONResponse({"error": "not_found"}, status_code=404)
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
    return JSONResponse(trait.to_dict())


@require_permission("admin.settings")
async def admin_delete_trait(request: Request) -> Response:
    repo = _get_skill_repo(request)
    if repo is None:
        return JSONResponse({"error": "skill_repository_not_available"}, status_code=503)

    ok = await repo.delete_trait(request.path_params["id"])
    if not ok:
        return JSONResponse({"error": "not_found"}, status_code=404)
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

    return JSONResponse(
        {"imported": len(created), "errors": errors, "skills": created},
        status_code=201 if created else 400,
    )


# ── Route list ─────────────────────────────────────────────────

skill_admin_routes = [
    Route("/admin/skills", admin_list_skills, methods=["GET"]),
    Route("/admin/skills/{id}/edit", admin_skill_edit_page, methods=["GET"]),
    Route("/admin/skills", admin_create_skill, methods=["POST"]),
    Route("/admin/skills/import", admin_import_skills, methods=["POST"]),
    Route("/admin/skills/usage-map", admin_skill_usage_map, methods=["GET"]),
    Route("/admin/skills/{id}", admin_get_skill, methods=["GET"]),
    Route("/admin/skills/{id}", admin_update_skill, methods=["PUT"]),
    Route("/admin/skills/{id}", admin_delete_skill, methods=["DELETE"]),
    Route("/admin/skills/{id}/stats", admin_skill_stats, methods=["GET"]),
    Route("/admin/skills/{id}/preview", admin_skill_preview, methods=["GET"]),
    Route("/admin/traits", admin_list_traits, methods=["GET"]),
    Route("/admin/traits", admin_create_trait, methods=["POST"]),
    Route("/admin/traits/{id}", admin_get_trait, methods=["GET"]),
    Route("/admin/traits/{id}", admin_update_trait, methods=["PUT"]),
    Route("/admin/traits/{id}", admin_delete_trait, methods=["DELETE"]),
]
