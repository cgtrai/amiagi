"""Routes: Cron / scheduled tasks — CRUD for recurring jobs."""

from __future__ import annotations

import json

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route


async def list_cron_jobs(request: Request) -> JSONResponse:
    """``GET /api/cron`` — list all scheduled jobs."""
    from amiagi.interfaces.web.scheduling.cron_scheduler import cron_to_human

    scheduler = getattr(request.app.state, "cron_scheduler", None)
    if scheduler is None:
        return JSONResponse([])
    return JSONResponse([
        {
            **j.to_dict(),
            "human_readable": cron_to_human(j.cron_expr),
        }
        for j in scheduler.list_jobs()
    ])


async def create_cron_job(request: Request) -> JSONResponse:
    """``POST /api/cron`` — create a new scheduled job."""
    from amiagi.interfaces.web.scheduling.cron_scheduler import CronJob, build_cron_expression, cron_to_human

    scheduler = getattr(request.app.state, "cron_scheduler", None)
    if scheduler is None:
        return JSONResponse({"error": "scheduler unavailable"}, status_code=503)

    body = await request.json()
    schedule = body.get("schedule") or body.get("schedule_builder")
    cron_expr = body.get("cron_expr") or body.get("cron_expression")
    if schedule:
        try:
            cron_expr = build_cron_expression(schedule)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
    cron_expr = cron_expr or "0 * * * *"
    job = CronJob(
        name=body.get("name", ""),
        cron_expr=cron_expr,
        task_title=body.get("task_title", ""),
        task_description=body.get("task_description", ""),
        enabled=body.get("enabled", True),
    )

    try:
        created = await scheduler.create_job(job)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)

    return JSONResponse({
        **created.to_dict(),
        "human_readable": cron_to_human(created.cron_expr),
    }, status_code=201)


async def delete_cron_job(request: Request) -> JSONResponse:
    """``DELETE /api/cron/{id}`` — remove a scheduled job."""
    scheduler = getattr(request.app.state, "cron_scheduler", None)
    if scheduler is None:
        return JSONResponse({"error": "scheduler unavailable"}, status_code=503)

    ok = await scheduler.delete_job(request.path_params["id"])
    return JSONResponse({"ok": ok})


async def toggle_cron_job(request: Request) -> JSONResponse:
    """``PUT /api/cron/{id}/toggle`` — enable or disable a job."""
    scheduler = getattr(request.app.state, "cron_scheduler", None)
    if scheduler is None:
        return JSONResponse({"error": "scheduler unavailable"}, status_code=503)

    try:
        body = json.loads((await request.body() or b"{}").decode("utf-8"))
    except json.JSONDecodeError:
        body = {}
    enabled = body.get("enabled")
    if enabled is None:
        current = next((job for job in scheduler.list_jobs() if job.id == request.path_params["id"]), None)
        enabled = not current.enabled if current is not None else True
    ok = await scheduler.toggle_job(request.path_params["id"], enabled)
    return JSONResponse({"ok": ok})


async def preview_cron_job(request: Request) -> JSONResponse:
    """``GET /api/cron/preview`` — validate expression and return next run."""
    from amiagi.interfaces.web.scheduling.cron_scheduler import build_cron_expression, cron_to_human, next_cron_trigger

    body: dict[str, object] = {}
    if request.method == "POST":
        try:
            body = await request.json()
        except Exception:
            body = {}
    schedule = body.get("schedule") or body.get("schedule_builder")
    expr = (
        body.get("cron_expr")
        or body.get("cron_expression")
        or request.query_params.get("cron_expr")
        or request.query_params.get("cron_expression")
        or ""
    )
    if schedule:
        try:
            expr = build_cron_expression(schedule if isinstance(schedule, dict) else {})
        except ValueError as exc:
            return JSONResponse({"valid": False, "error": str(exc)}, status_code=400)
    if not expr.strip():
        return JSONResponse({"valid": False, "error": "cron expression is required"}, status_code=400)
    try:
        next_run = next_cron_trigger(expr).isoformat()
    except ValueError as exc:
        return JSONResponse({"valid": False, "error": str(exc)}, status_code=400)
    return JSONResponse({
        "valid": True,
        "cron_expr": expr,
        "human_readable": cron_to_human(expr),
        "next_run": next_run,
    })


async def cron_history(request: Request) -> JSONResponse:
    """``GET /api/cron/history`` — recent execution history."""
    scheduler = getattr(request.app.state, "cron_scheduler", None)
    if scheduler is None or not hasattr(scheduler, "list_history"):
        return JSONResponse([])

    limit_raw = request.query_params.get("limit")
    try:
        limit = max(1, min(int(limit_raw or "20"), 100))
    except ValueError:
        limit = 20
    job_id = request.query_params.get("job_id")
    return JSONResponse([record.to_dict() for record in scheduler.list_history(job_id=job_id, limit=limit)])


async def cron_job_history(request: Request) -> JSONResponse:
    """GET /api/cron/{job_id}/history — execution history for a single job (CR2)."""
    scheduler = getattr(request.app.state, "cron_scheduler", None)
    if scheduler is None or not hasattr(scheduler, "get_history"):
        return JSONResponse({"runs": []})
    job_id = request.path_params["job_id"]
    limit_raw = request.query_params.get("limit")
    try:
        limit = max(1, min(int(limit_raw or "50"), 200))
    except ValueError:
        limit = 50
    if hasattr(scheduler, "get_history"):
        runs = scheduler.get_history(job_id, limit=limit)
    else:
        runs = [r for r in scheduler.list_history(limit=limit) if getattr(r, "job_id", None) == job_id]
    return JSONResponse({"runs": [r.to_dict() if hasattr(r, "to_dict") else r for r in runs]})


async def cron_page(request: Request):
    """GET /cron — dedicated cron management page (CR4)."""
    from starlette.responses import HTMLResponse
    templates = request.app.state.templates
    return templates.TemplateResponse("cron.html", {"request": request})


cron_routes: list[Route] = [
    Route("/cron", cron_page, methods=["GET"]),
    Route("/api/cron/preview", preview_cron_job, methods=["GET", "POST"]),
    Route("/api/cron/history", cron_history, methods=["GET"]),
    Route("/api/cron/{job_id}/history", cron_job_history, methods=["GET"]),
    Route("/api/cron", list_cron_jobs, methods=["GET"]),
    Route("/api/cron", create_cron_job, methods=["POST"]),
    Route("/api/cron/{id}", delete_cron_job, methods=["DELETE"]),
    Route("/api/cron/{id}/toggle", toggle_cron_job, methods=["PUT"]),
]
