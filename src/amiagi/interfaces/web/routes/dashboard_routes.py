"""Dashboard routes — serves the main dashboard and other page views."""

from __future__ import annotations

from starlette.requests import Request
from starlette.responses import RedirectResponse
from starlette.routing import Route


async def dashboard_page(request: Request) -> RedirectResponse:
    """Render the main dashboard.

    Requires authentication (ensured by AuthMiddleware).
    """
    templates = request.app.state.templates
    return templates.TemplateResponse(request, "dashboard.html")


# A bare ``/`` redirects to ``/dashboard``.
async def root_redirect(request: Request) -> RedirectResponse:
    return RedirectResponse(url="/dashboard", status_code=302)


# ── Agent pages ──────────────────────────────────────────────────

async def agents_page(request: Request):
    """GET /agents — agent list."""
    templates = request.app.state.templates
    return templates.TemplateResponse(request, "agents.html")


async def agent_detail_page(request: Request):
    """GET /agents/{agent_id} — single agent detail view."""
    templates = request.app.state.templates
    return templates.TemplateResponse(
        request,
        "agent_detail.html",
        {"agent_id": request.path_params["agent_id"]},
    )


# ── Task pages ───────────────────────────────────────────────────

async def tasks_page(request: Request):
    """GET /tasks — task board overview."""
    templates = request.app.state.templates
    return templates.TemplateResponse(request, "tasks.html")


async def task_wizard_page(request: Request):
    """GET /tasks/new — task creation wizard."""
    templates = request.app.state.templates
    return templates.TemplateResponse(request, "task_wizard.html")


# ── Metrics page ─────────────────────────────────────────────────

async def metrics_page(request: Request):
    """GET /metrics — performance metrics & system health."""
    templates = request.app.state.templates
    return templates.TemplateResponse(request, "metrics.html")


# ── Session Replay page ───────────────────────────────────────────

async def sessions_page(request: Request):
    """GET /sessions — session replay and timeline browser."""
    templates = request.app.state.templates
    return templates.TemplateResponse(request, "sessions.html")


# ── Productivity pages ──────────────────────────────────────────

async def prompt_library_page(request: Request):
    """GET /prompt-library — shared prompt library UI."""
    templates = request.app.state.templates
    return templates.TemplateResponse(request, "prompts.html")


async def snippets_page(request: Request):
    """GET /snippets-library — saved snippets UI."""
    templates = request.app.state.templates
    return templates.TemplateResponse(request, "snippets.html")


# ── Settings page ────────────────────────────────────────────────

async def settings_page(request: Request):
    """GET /settings — model config, API keys, webhooks, memory, cron."""
    templates = request.app.state.templates
    return templates.TemplateResponse(request, "settings.html")


# ── Files page ───────────────────────────────────────────────────

async def files_page(request: Request):
    """GET /files — file browser and upload."""
    templates = request.app.state.templates
    return templates.TemplateResponse(request, "files.html")


# ── Teams page ───────────────────────────────────────────────────

async def teams_page(request: Request):
    """GET /teams — teams overview."""
    templates = request.app.state.templates
    return templates.TemplateResponse(request, "teams.html")


# ── Supervisor page ──────────────────────────────────────────────

async def supervisor_page(request: Request):
    """GET /supervisor — Mission Control (live agent overview)."""
    templates = request.app.state.templates
    return templates.TemplateResponse(request, "supervisor.html")


# ── Inbox page ───────────────────────────────────────────────────

async def inbox_page(request: Request):
    """GET /inbox — Human-in-the-Loop inbox."""
    templates = request.app.state.templates
    return templates.TemplateResponse(request, "inbox.html")


# ── Model Hub page ───────────────────────────────────────────

async def model_hub_page(request: Request):
    """GET /model-hub — Model management hub."""
    templates = request.app.state.templates
    return templates.TemplateResponse(request, "model_hub.html")


# ── Budget / Cost Center page ────────────────────────────────

async def budget_page(request: Request):
    """GET /budget — Cost Center and budget dashboard."""
    templates = request.app.state.templates
    return templates.TemplateResponse(request, "budget.html")


# ── Vault page ───────────────────────────────────────────────

async def vault_page(request: Request):
    """GET /admin/vault — Credential vault management."""
    templates = request.app.state.templates
    return templates.TemplateResponse(request, "vault.html")


# ── Health page ──────────────────────────────────────────────

async def health_page(request: Request):
    """GET /health-dashboard — System Health dashboard."""
    templates = request.app.state.templates
    return templates.TemplateResponse(request, "health.html")


# ── Sandboxes admin page ─────────────────────────────────────

async def sandboxes_page(request: Request):
    """GET /admin/sandboxes — Sandbox & shell policy management."""
    templates = request.app.state.templates
    return templates.TemplateResponse(request, "admin/sandboxes.html")


dashboard_routes: list[Route] = [
    Route("/", root_redirect, methods=["GET"]),
    Route("/dashboard", dashboard_page, methods=["GET"]),
    Route("/agents", agents_page, methods=["GET"]),
    Route("/agents/{agent_id}", agent_detail_page, methods=["GET"]),
    Route("/tasks", tasks_page, methods=["GET"]),
    Route("/tasks/new", task_wizard_page, methods=["GET"]),
    Route("/metrics", metrics_page, methods=["GET"]),
    Route("/sessions", sessions_page, methods=["GET"]),
    Route("/prompt-library", prompt_library_page, methods=["GET"]),
    Route("/prompts-library", prompt_library_page, methods=["GET"]),
    Route("/snippets-library", snippets_page, methods=["GET"]),
    Route("/settings", settings_page, methods=["GET"]),
    Route("/files", files_page, methods=["GET"]),
    Route("/teams", teams_page, methods=["GET"]),
    Route("/supervisor", supervisor_page, methods=["GET"]),
    Route("/inbox", inbox_page, methods=["GET"]),
    Route("/model-hub", model_hub_page, methods=["GET"]),
    Route("/budget", budget_page, methods=["GET"]),
    Route("/admin/vault", vault_page, methods=["GET"]),
    Route("/health-dashboard", health_page, methods=["GET"]),
    Route("/admin/sandboxes", sandboxes_page, methods=["GET"]),
]
