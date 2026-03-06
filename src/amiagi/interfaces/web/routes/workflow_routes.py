"""Workflow Studio API routes.

Endpoints:
    GET    /workflows                         — Workflow Studio page
    GET    /api/workflows                     — list workflow definitions
    POST   /api/workflows                     — create workflow definition
    GET    /api/workflows/{id}                — get definition + DAG
    DELETE /api/workflows/{id}                — delete definition
    GET    /api/workflow-runs                  — list active runs
    GET    /api/workflow-runs/{run_id}         — run status with node statuses
    POST   /api/workflow-runs                  — start new run from definition
    POST   /api/workflow-runs/{run_id}/approve/{node_id} — approve a gate
    POST   /api/workflow-runs/{run_id}/pause   — pause a run
    POST   /api/workflow-runs/{run_id}/resume  — resume a paused run
    POST   /api/workflow-runs/{run_id}/abort   — abort a run
"""

from __future__ import annotations

import logging
import time
import uuid

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

logger = logging.getLogger(__name__)


# ── Helpers ──────────────────────────────────────────────────

def _get_engine(request: Request):
    """Return the WorkflowEngine from app.state, or None."""
    return getattr(request.app.state, "workflow_engine", None)


def _no_engine() -> JSONResponse:
    return JSONResponse({"error": "workflow_engine unavailable"}, status_code=503)


def _get_definitions(request: Request) -> dict:
    """Return the mutable dict of stored workflow definitions.

    Stored on ``app.state._workflow_definitions`` as ``{id: definition}``.
    """
    state = request.app.state
    if not hasattr(state, "_workflow_definitions"):
        state._workflow_definitions = {}
    return state._workflow_definitions


def _run_to_dict(run) -> dict:
    """Serialize a WorkflowRun to a JSON-friendly dict."""
    nodes = []
    for n in run.workflow.nodes:
        nodes.append({
            "node_id": n.node_id,
            "node_type": n.node_type.value if hasattr(n.node_type, "value") else str(n.node_type),
            "label": n.label,
            "description": n.description,
            "agent_role": n.agent_role,
            "depends_on": n.depends_on,
            "status": n.status.value if hasattr(n.status, "value") else str(n.status),
            "result": n.result or "",
        })
    return {
        "run_id": run.run_id,
        "workflow_name": run.workflow.name,
        "description": run.workflow.description,
        "status": run.status,
        "started_at": run.started_at,
        "finished_at": run.finished_at,
        "nodes": nodes,
        "is_terminal": run.is_terminal,
    }


def _definition_to_dict(def_id: str, defn) -> dict:
    """Serialize a WorkflowDefinition to a JSON-friendly dict."""
    nodes = []
    for n in defn.nodes:
        nodes.append({
            "node_id": n.node_id,
            "node_type": n.node_type.value if hasattr(n.node_type, "value") else str(n.node_type),
            "label": n.label,
            "description": n.description,
            "agent_role": n.agent_role,
            "depends_on": n.depends_on,
            "config": n.config,
        })
    return {
        "id": def_id,
        "name": defn.name,
        "description": defn.description,
        "nodes": nodes,
        "metadata": defn.metadata,
    }


# ── Page view ────────────────────────────────────────────────

async def workflow_page(request: Request) -> JSONResponse:
    """GET /workflows — render Workflow Studio page."""
    templates = getattr(request.app.state, "templates", None)
    if templates is None:
        return JSONResponse({"error": "templates unavailable"}, status_code=500)
    return templates.TemplateResponse(request, "workflows.html")


# ── Definition CRUD ──────────────────────────────────────────

async def list_definitions(request: Request) -> JSONResponse:
    """GET /api/workflows — list all stored workflow definitions."""
    defs = _get_definitions(request)
    items = [_definition_to_dict(did, d) for did, d in defs.items()]
    return JSONResponse({"definitions": items, "total": len(items)})


async def create_definition(request: Request) -> JSONResponse:
    """POST /api/workflows — create a workflow definition from JSON/YAML body."""
    from amiagi.domain.workflow import WorkflowDefinition

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON body"}, status_code=400)

    name = body.get("name", "").strip()
    if not name:
        return JSONResponse({"error": "name is required"}, status_code=400)

    nodes_raw = body.get("nodes", [])
    if not nodes_raw:
        return JSONResponse({"error": "at least one node is required"}, status_code=400)

    try:
        defn = WorkflowDefinition.from_dict(body)
        errors = defn.validate()
        if errors:
            return JSONResponse({"error": "validation failed", "details": errors}, status_code=400)
    except Exception as exc:
        return JSONResponse({"error": f"invalid definition: {exc}"}, status_code=400)

    def_id = str(uuid.uuid4())
    defs = _get_definitions(request)
    defs[def_id] = defn

    from amiagi.interfaces.web.audit.log_helpers import log_action
    await log_action(request, "workflow.definition.created", {"id": def_id, "name": name})

    return JSONResponse({"id": def_id, "definition": _definition_to_dict(def_id, defn)}, status_code=201)


async def get_definition(request: Request) -> JSONResponse:
    """GET /api/workflows/{id} — get definition with DAG data."""
    def_id = request.path_params["id"]
    defs = _get_definitions(request)
    defn = defs.get(def_id)
    if defn is None:
        return JSONResponse({"error": "definition not found"}, status_code=404)
    return JSONResponse({"definition": _definition_to_dict(def_id, defn)})


async def delete_definition(request: Request) -> JSONResponse:
    """DELETE /api/workflows/{id} — delete a workflow definition."""
    def_id = request.path_params["id"]
    defs = _get_definitions(request)
    defn = defs.pop(def_id, None)
    if defn is None:
        return JSONResponse({"error": "definition not found"}, status_code=404)

    from amiagi.interfaces.web.audit.log_helpers import log_action
    await log_action(request, "workflow.definition.deleted", {"id": def_id, "name": defn.name})

    return JSONResponse({"ok": True})


# ── Run management ───────────────────────────────────────────

async def list_runs(request: Request) -> JSONResponse:
    """GET /api/workflow-runs — list all runs."""
    engine = _get_engine(request)
    if engine is None:
        return _no_engine()

    runs = engine.list_runs()
    items = [_run_to_dict(r) for r in runs]
    return JSONResponse({"runs": items, "total": len(items)})


async def get_run(request: Request) -> JSONResponse:
    """GET /api/workflow-runs/{run_id} — run status with node statuses."""
    engine = _get_engine(request)
    if engine is None:
        return _no_engine()

    run_id = request.path_params["run_id"]
    run = engine.get_run(run_id)
    if run is None:
        return JSONResponse({"error": "run not found"}, status_code=404)

    return JSONResponse({"run": _run_to_dict(run)})


async def start_run(request: Request) -> JSONResponse:
    """POST /api/workflow-runs — start a new run from a definition.

    Body: { "definition_id": "..." } or inline definition.
    """
    engine = _get_engine(request)
    if engine is None:
        return _no_engine()

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON body"}, status_code=400)

    # Option A: reference a stored definition by ID
    def_id = body.get("definition_id")
    if def_id:
        defs = _get_definitions(request)
        defn = defs.get(def_id)
        if defn is None:
            return JSONResponse({"error": "definition not found"}, status_code=404)
    else:
        # Option B: inline definition
        from amiagi.domain.workflow import WorkflowDefinition
        try:
            defn = WorkflowDefinition.from_dict(body.get("definition", body))
            errors = defn.validate()
            if errors:
                return JSONResponse({"error": "validation failed", "details": errors}, status_code=400)
        except Exception as exc:
            return JSONResponse({"error": f"invalid definition: {exc}"}, status_code=400)

    run_id = body.get("run_id", str(uuid.uuid4()))

    try:
        run = engine.start(defn, run_id=run_id)
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)

    hub = getattr(request.app.state, "event_hub", None)
    if hub is not None:
        hub.broadcast("workflow.started", {"run_id": run.run_id, "name": defn.name})

    from amiagi.interfaces.web.audit.log_helpers import log_action
    await log_action(request, "workflow.run.started", {"run_id": run.run_id, "name": defn.name})

    return JSONResponse({"run": _run_to_dict(run)}, status_code=201)


async def approve_gate(request: Request) -> JSONResponse:
    """POST /api/workflow-runs/{run_id}/approve/{node_id} — approve gate."""
    engine = _get_engine(request)
    if engine is None:
        return _no_engine()

    run_id = request.path_params["run_id"]
    node_id = request.path_params["node_id"]

    ok = engine.approve_gate(run_id, node_id)
    if not ok:
        return JSONResponse({"error": "gate not found or not waiting"}, status_code=404)

    hub = getattr(request.app.state, "event_hub", None)
    if hub is not None:
        hub.broadcast("workflow.gate.approved", {"run_id": run_id, "node_id": node_id})

    from amiagi.interfaces.web.audit.log_helpers import log_action
    await log_action(request, "workflow.gate.approved", {"run_id": run_id, "node_id": node_id})

    run = engine.get_run(run_id)
    return JSONResponse({"ok": True, "run": _run_to_dict(run) if run else None})


async def pause_run(request: Request) -> JSONResponse:
    """POST /api/workflow-runs/{run_id}/pause"""
    engine = _get_engine(request)
    if engine is None:
        return _no_engine()

    run_id = request.path_params["run_id"]
    ok = engine.pause(run_id)
    if not ok:
        return JSONResponse({"error": "run not found or not pausable"}, status_code=404)
    return JSONResponse({"ok": True})


async def resume_run(request: Request) -> JSONResponse:
    """POST /api/workflow-runs/{run_id}/resume"""
    engine = _get_engine(request)
    if engine is None:
        return _no_engine()

    run_id = request.path_params["run_id"]
    ok = engine.resume(run_id)
    if not ok:
        return JSONResponse({"error": "run not found or not paused"}, status_code=404)
    return JSONResponse({"ok": True})


async def abort_run(request: Request) -> JSONResponse:
    """POST /api/workflow-runs/{run_id}/abort"""
    engine = _get_engine(request)
    if engine is None:
        return _no_engine()

    run_id = request.path_params["run_id"]
    run = engine.get_run(run_id)
    if run is None:
        return JSONResponse({"error": "run not found"}, status_code=404)

    run.status = "failed"
    run.finished_at = time.time()

    hub = getattr(request.app.state, "event_hub", None)
    if hub is not None:
        hub.broadcast("workflow.aborted", {"run_id": run_id})

    from amiagi.interfaces.web.audit.log_helpers import log_action
    await log_action(request, "workflow.run.aborted", {"run_id": run_id})

    return JSONResponse({"ok": True})


# ── Route table ──────────────────────────────────────────────

workflow_routes: list[Route] = [
    Route("/workflows", workflow_page),
    Route("/api/workflows", list_definitions, methods=["GET"]),
    Route("/api/workflows", create_definition, methods=["POST"]),
    Route("/api/workflows/{id}", get_definition, methods=["GET"]),
    Route("/api/workflows/{id}", delete_definition, methods=["DELETE"]),
    Route("/api/workflow-runs", list_runs, methods=["GET"]),
    Route("/api/workflow-runs", start_run, methods=["POST"]),
    Route("/api/workflow-runs/{run_id}/approve/{node_id}", approve_gate, methods=["POST"]),
    Route("/api/workflow-runs/{run_id}/pause", pause_run, methods=["POST"]),
    Route("/api/workflow-runs/{run_id}/resume", resume_run, methods=["POST"]),
    Route("/api/workflow-runs/{run_id}/abort", abort_run, methods=["POST"]),
    Route("/api/workflow-runs/{run_id}", get_run, methods=["GET"]),
]
