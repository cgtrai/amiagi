"""Routes: Cron / scheduled tasks — CRUD for recurring jobs."""

from __future__ import annotations

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route


async def list_cron_jobs(request: Request) -> JSONResponse:
    """``GET /api/cron`` — list all scheduled jobs."""
    scheduler = getattr(request.app.state, "cron_scheduler", None)
    if scheduler is None:
        return JSONResponse([])
    return JSONResponse([j.to_dict() for j in scheduler.list_jobs()])


async def create_cron_job(request: Request) -> JSONResponse:
    """``POST /api/cron`` — create a new scheduled job."""
    from amiagi.interfaces.web.scheduling.cron_scheduler import CronJob

    scheduler = getattr(request.app.state, "cron_scheduler", None)
    if scheduler is None:
        return JSONResponse({"error": "scheduler unavailable"}, status_code=503)

    body = await request.json()
    job = CronJob(
        name=body.get("name", ""),
        cron_expr=body.get("cron_expr", "0 * * * *"),
        task_title=body.get("task_title", ""),
        task_description=body.get("task_description", ""),
        enabled=body.get("enabled", True),
    )

    try:
        created = await scheduler.create_job(job)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)

    return JSONResponse(created.to_dict(), status_code=201)


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

    body = await request.json()
    ok = await scheduler.toggle_job(request.path_params["id"], body.get("enabled", True))
    return JSONResponse({"ok": ok})


cron_routes: list[Route] = [
    Route("/api/cron", list_cron_jobs, methods=["GET"]),
    Route("/api/cron", create_cron_job, methods=["POST"]),
    Route("/api/cron/{id}", delete_cron_job, methods=["DELETE"]),
    Route("/api/cron/{id}/toggle", toggle_cron_job, methods=["PUT"]),
]
