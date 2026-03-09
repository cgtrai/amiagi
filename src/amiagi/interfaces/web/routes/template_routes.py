"""Routes for task templates — CRUD, execute, import/export YAML.

Faza 14.1 — Task Templates & Workflows.
"""

from __future__ import annotations

import uuid
from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route


def _get_user_id(request: Request) -> str:
    user = getattr(request.state, "user", None)
    if user is None:
        return "anonymous"
    if hasattr(user, "user_id"):
        return str(user.user_id)
    if isinstance(user, dict):
        return str(user.get("user_id") or user.get("sub") or "anonymous")
    return str(user)


async def _load_template_preferences(request: Request) -> dict[str, Any]:
    repo = getattr(request.app.state, "user_settings_repo", None)
    if repo is None:
        return {"pinned_ids": []}
    settings = await repo.get_for_user(_get_user_id(request))
    prefs = settings.get("template_preferences") if isinstance(settings, dict) else None
    if not isinstance(prefs, dict):
        return {"pinned_ids": []}
    pinned_ids = prefs.get("pinned_ids")
    if not isinstance(pinned_ids, list):
        pinned_ids = []
    return {
        "pinned_ids": [str(item) for item in pinned_ids if item],
    }


async def _save_template_preferences(request: Request, pinned_ids: list[str]) -> dict[str, Any] | None:
    repo = getattr(request.app.state, "user_settings_repo", None)
    if repo is None:
        return None
    user_id = _get_user_id(request)
    settings = await repo.get_for_user(user_id)
    settings["template_preferences"] = {"pinned_ids": sorted({str(item) for item in pinned_ids if item})}
    return await repo.save_for_user(user_id, settings)


def _avg_completion_seconds(tasks: list[Any]) -> float | None:
    durations: list[float] = []
    for task in tasks:
        completed_at = getattr(task, "completed_at", None)
        started_at = getattr(task, "started_at", None) or getattr(task, "created_at", None)
        if completed_at is None or started_at is None:
            continue
        try:
            durations.append((completed_at - started_at).total_seconds())
        except Exception:
            continue
    if not durations:
        return None
    return round(sum(durations) / len(durations), 2)


def _format_duration(seconds: float | None) -> str | None:
    if seconds is None:
        return None
    if seconds < 60:
        return f"{int(seconds)}s"
    if seconds < 3600:
        return f"{int(seconds // 60)}m"
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    return f"{hours}h {minutes}m"


def _list_task_objects(task_queue: Any) -> list[Any]:
    if task_queue is None:
        return []
    if hasattr(task_queue, "list_all"):
        return task_queue.list_all()
    if hasattr(task_queue, "all_tasks"):
        return task_queue.all_tasks()
    if hasattr(task_queue, "_tasks"):
        return list(task_queue._tasks.values()) if isinstance(task_queue._tasks, dict) else list(task_queue._tasks)
    return []


def _build_template_stats_map(request: Request, templates: list[Any]) -> dict[str, dict[str, Any]]:
    template_ids = {str(t.id) for t in templates}
    task_queue = getattr(request.app.state, "task_queue", None)
    tasks = _list_task_objects(task_queue)
    grouped: dict[str, list[Any]] = {template_id: [] for template_id in template_ids}

    for task in tasks:
        metadata = getattr(task, "metadata", None) or {}
        template_id = str(metadata.get("template_id") or "").strip()
        if template_id in grouped:
            grouped[template_id].append(task)

    stats_map: dict[str, dict[str, Any]] = {}
    for tpl in templates:
        template_tasks = grouped.get(str(tpl.id), [])
        avg_seconds = _avg_completion_seconds(template_tasks)
        execution_ids = {
            str((getattr(task, "metadata", None) or {}).get("template_execution_id"))
            for task in template_tasks
            if (getattr(task, "metadata", None) or {}).get("template_execution_id")
        }
        completed_count = sum(
            1
            for task in template_tasks
            if str(getattr(getattr(task, "status", None), "value", getattr(task, "status", ""))) == "done"
        )
        stats_map[str(tpl.id)] = {
            "use_count": int(getattr(tpl, "use_count", 0) or 0),
            "avg_completion_time_seconds": avg_seconds,
            "avg_completion_time_label": _format_duration(avg_seconds),
            "queued_steps": len(template_tasks),
            "completed_steps": completed_count,
            "execution_count": len(execution_ids),
            "step_count": len(getattr(tpl, "steps", []) or []),
            "parameter_count": len(getattr(tpl, "parameters", []) or []),
        }
    return stats_map


def _template_payload(tpl: Any, *, pinned: bool = False, stats: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = tpl.to_dict()
    payload["pinned"] = bool(pinned)
    payload["stats"] = stats or {
        "use_count": int(payload.get("use_count", 0) or 0),
        "avg_completion_time_seconds": None,
        "avg_completion_time_label": None,
        "queued_steps": 0,
        "completed_steps": 0,
        "execution_count": 0,
        "step_count": len(payload.get("steps") or []),
        "parameter_count": len(payload.get("parameters") or []),
    }
    return payload


async def list_templates(request: Request) -> JSONResponse:
    repo = request.app.state.template_repository
    public_only = request.query_params.get("public") == "true"
    templates = await repo.list_templates(public_only=public_only)
    preferences = await _load_template_preferences(request)
    pinned_ids = set(preferences.get("pinned_ids") or [])
    stats_map = _build_template_stats_map(request, templates)
    payload = [_template_payload(t, pinned=str(t.id) in pinned_ids, stats=stats_map.get(str(t.id))) for t in templates]
    payload.sort(key=lambda item: (not item.get("pinned", False), -(item.get("use_count", 0) or 0), item.get("name", "")))
    return JSONResponse(payload)


async def get_template_stats(request: Request) -> JSONResponse:
    repo = request.app.state.template_repository
    public_only = request.query_params.get("public") == "true"
    templates = await repo.list_templates(public_only=public_only)
    preferences = await _load_template_preferences(request)
    pinned_ids = set(preferences.get("pinned_ids") or [])
    stats_map = _build_template_stats_map(request, templates)
    return JSONResponse({
        "templates": [
            {
                "template_id": str(t.id),
                "name": t.name,
                "pinned": str(t.id) in pinned_ids,
                **(stats_map.get(str(t.id)) or {}),
            }
            for t in templates
        ],
    })


async def get_template(request: Request) -> JSONResponse:
    repo = request.app.state.template_repository
    tpl = await repo.get(request.path_params["id"])
    if not tpl:
        return JSONResponse({"error": "not found"}, 404)
    preferences = await _load_template_preferences(request)
    stats_map = _build_template_stats_map(request, [tpl])
    return JSONResponse(_template_payload(
        tpl,
        pinned=str(tpl.id) in set(preferences.get("pinned_ids") or []),
        stats=stats_map.get(str(tpl.id)),
    ))


async def create_template(request: Request) -> JSONResponse:
    repo = request.app.state.template_repository
    body = await request.json()
    user_id = _get_user_id(request)
    try:
        tpl = await repo.create(
            name=body.get("name", "Untitled"),
            yaml_content=body.get("yaml_content", ""),
            description=body.get("description", ""),
            tags=body.get("tags", []),
            author_id=user_id if user_id else None,
            is_public=body.get("is_public", False),
        )
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, 400)
    return JSONResponse(tpl.to_dict(), status_code=201)


async def delete_template(request: Request) -> JSONResponse:
    repo = request.app.state.template_repository
    ok = await repo.delete(request.path_params["id"])
    return JSONResponse({"ok": ok})


async def update_template_pin(request: Request) -> JSONResponse:
    repo = getattr(request.app.state, "user_settings_repo", None)
    if repo is None:
        return JSONResponse({"error": "user_settings_repo unavailable"}, status_code=503)

    template_id = str(request.path_params["id"])
    try:
        body = await request.json()
    except Exception:
        body = {}

    preferences = await _load_template_preferences(request)
    pinned_ids = set(preferences.get("pinned_ids") or [])
    requested = body.get("pinned") if isinstance(body, dict) else None
    target = (template_id not in pinned_ids) if requested is None else bool(requested)

    if target:
        pinned_ids.add(template_id)
    else:
        pinned_ids.discard(template_id)

    await _save_template_preferences(request, sorted(pinned_ids))

    from amiagi.interfaces.web.audit.log_helpers import log_action
    await log_action(request, "templates.pin.updated", {
        "template_id": template_id,
        "pinned": target,
    })
    return JSONResponse({"ok": True, "template_id": template_id, "pinned": target, "pinned_ids": sorted(pinned_ids)})


async def preview_template(request: Request) -> JSONResponse:
    """Render a template with provided parameter values without executing it."""
    repo = request.app.state.template_repository
    tpl = await repo.get(request.path_params["id"])
    if not tpl:
        return JSONResponse({"error": "not found"}, 404)
    body = await request.json() if request.method != "GET" else {}
    values = body.get("values", {})
    rendered_steps = tpl.render_steps(values)
    return JSONResponse({
        "template_id": tpl.id,
        "name": tpl.name,
        "rendered_steps": rendered_steps,
        "status": "preview",
    })


async def execute_template(request: Request) -> JSONResponse:
    """Execute a template with provided parameter values."""
    repo = request.app.state.template_repository
    tpl = await repo.get(request.path_params["id"])
    if not tpl:
        return JSONResponse({"error": "not found"}, 404)
    body = await request.json()
    values = body.get("values", {})
    rendered_steps = tpl.render_steps(values)
    created_task_ids: list[str] = []
    execution_id = uuid.uuid4().hex[:12]

    task_queue = getattr(request.app.state, "task_queue", None)
    if task_queue is not None and hasattr(task_queue, "enqueue"):
        from amiagi.domain.task import Task, TaskPriority

        previous_task_id: str | None = None
        for index, step in enumerate(rendered_steps, start=1):
            metadata = {
                "origin": "template",
                "template_id": tpl.id,
                "template_name": tpl.name,
                "template_execution_id": execution_id,
                "template_step_index": index,
                "template_step_total": len(rendered_steps),
                "template_values": values,
            }
            required_skills = step.get("required_skills")
            if isinstance(required_skills, list):
                metadata["required_skills"] = [str(item) for item in required_skills]

            task = Task(
                task_id=Task.generate_id(),
                title=str(step.get("title") or step.get("name") or f"{tpl.name} — step {index}"),
                description=str(step.get("prompt") or step.get("description") or ""),
                priority=TaskPriority.NORMAL,
                assigned_agent_id=step.get("agent"),
                dependencies=[previous_task_id] if previous_task_id else [],
                metadata=metadata,
            )
            task_queue.enqueue(task)
            created_task_ids.append(task.task_id)
            previous_task_id = task.task_id

    await repo.increment_use_count(tpl.id)
    from amiagi.interfaces.web.audit.log_helpers import log_action
    await log_action(request, "task.create", {
        "template_id": tpl.id,
        "template_name": tpl.name,
        "origin": "template",
        "execution_id": execution_id,
        "created_task_ids": created_task_ids,
    })
    return JSONResponse({
        "template_id": tpl.id,
        "name": tpl.name,
        "rendered_steps": rendered_steps,
        "status": "started",
        "execution_id": execution_id,
        "created_task_ids": created_task_ids,
    })


async def export_template_yaml(request: Request) -> Response:
    """Export template as raw YAML file."""
    repo = request.app.state.template_repository
    content = await repo.export_yaml(request.path_params["id"])
    if not content:
        return JSONResponse({"error": "not found"}, 404)
    return Response(
        content=content,
        media_type="application/x-yaml",
        headers={"Content-Disposition": "attachment; filename=template.yaml"},
    )


async def import_template_yaml(request: Request) -> JSONResponse:
    """Import a template from YAML content in the request body."""
    repo = request.app.state.template_repository
    body = await request.json()
    yaml_content = body.get("yaml_content", "")
    user_id = _get_user_id(request)
    try:
        from amiagi.interfaces.web.task_templates.template_repository import validate_yaml
        import yaml
        ok, err = validate_yaml(yaml_content)
        if not ok:
            return JSONResponse({"error": err}, 400)
        parsed = yaml.safe_load(yaml_content)
        tpl = await repo.create(
            name=parsed.get("name", "Imported"),
            yaml_content=yaml_content,
            description=parsed.get("description", ""),
            tags=body.get("tags", []),
            author_id=user_id if user_id else None,
            is_public=body.get("is_public", False),
        )
        return JSONResponse(tpl.to_dict(), status_code=201)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, 400)


template_routes = [
    Route("/templates/stats", get_template_stats, methods=["GET"]),
    Route("/templates", list_templates, methods=["GET"]),
    Route("/templates", create_template, methods=["POST"]),
    Route("/templates/import", import_template_yaml, methods=["POST"]),
    Route("/templates/{id}", get_template, methods=["GET"]),
    Route("/templates/{id}", delete_template, methods=["DELETE"]),
    Route("/templates/{id}/pin", update_template_pin, methods=["PUT"]),
    Route("/templates/{id}/preview", preview_template, methods=["POST"]),
    Route("/templates/{id}/execute", execute_template, methods=["POST"]),
    Route("/templates/{id}/export", export_template_yaml, methods=["GET"]),
]
