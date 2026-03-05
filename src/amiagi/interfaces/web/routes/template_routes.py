"""Routes for task templates — CRUD, execute, import/export YAML.

Faza 14.1 — Task Templates & Workflows.
"""

from __future__ import annotations

from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route


async def list_templates(request: Request) -> JSONResponse:
    repo = request.app.state.template_repository
    public_only = request.query_params.get("public") == "true"
    templates = await repo.list_templates(public_only=public_only)
    return JSONResponse([t.to_dict() for t in templates])


async def get_template(request: Request) -> JSONResponse:
    repo = request.app.state.template_repository
    tpl = await repo.get(request.path_params["id"])
    if not tpl:
        return JSONResponse({"error": "not found"}, 404)
    return JSONResponse(tpl.to_dict())


async def create_template(request: Request) -> JSONResponse:
    repo = request.app.state.template_repository
    body = await request.json()
    user_id = str(request.state.user.get("sub", ""))
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


async def execute_template(request: Request) -> JSONResponse:
    """Execute a template with provided parameter values."""
    repo = request.app.state.template_repository
    tpl = await repo.get(request.path_params["id"])
    if not tpl:
        return JSONResponse({"error": "not found"}, 404)
    body = await request.json()
    values = body.get("values", {})
    rendered_steps = tpl.render_steps(values)
    await repo.increment_use_count(tpl.id)
    from amiagi.interfaces.web.audit.log_helpers import log_action
    await log_action(request, "task.create", {
        "template_id": tpl.id,
        "template_name": tpl.name,
        "origin": "template",
    })
    return JSONResponse({
        "template_id": tpl.id,
        "name": tpl.name,
        "rendered_steps": rendered_steps,
        "status": "started",
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
    user_id = str(request.state.user.get("sub", ""))
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
    Route("/templates", list_templates, methods=["GET"]),
    Route("/templates", create_template, methods=["POST"]),
    Route("/templates/import", import_template_yaml, methods=["POST"]),
    Route("/templates/{id}", get_template, methods=["GET"]),
    Route("/templates/{id}", delete_template, methods=["DELETE"]),
    Route("/templates/{id}/execute", execute_template, methods=["POST"]),
    Route("/templates/{id}/export", export_template_yaml, methods=["GET"]),
]
