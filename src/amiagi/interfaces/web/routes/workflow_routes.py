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

import ast
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


def _parse_scalar(value: str):
    text = value.strip()
    if not text:
        return ""
    lowered = text.lower()
    if lowered in {"true", "false"}:
        return lowered == "true"
    if lowered in {"null", "none"}:
        return None
    try:
        return ast.literal_eval(text)
    except (ValueError, SyntaxError):
        return text.strip('"\'')


def _fallback_parse_yaml_definition(yaml_body: str) -> dict:
    """Parse a limited YAML subset used by the workflow dialog.

    Supports top-level scalar fields and a ``nodes:`` list of mappings.
    This keeps workflow creation functional even without PyYAML.
    """
    result: dict = {}
    nodes: list[dict] = []
    current_node: dict | None = None

    for raw_line in yaml_body.splitlines():
        line = raw_line.rstrip()
        if not line.strip() or line.lstrip().startswith("#"):
            continue

        stripped = line.lstrip()
        indent = len(line) - len(stripped)

        if indent == 0:
            if stripped == "nodes:":
                result["nodes"] = nodes
                current_node = None
                continue
            if ":" not in stripped:
                continue
            key, value = stripped.split(":", 1)
            result[key.strip()] = _parse_scalar(value)
            current_node = None
            continue

        if stripped.startswith("-"):
            current_node = {}
            nodes.append(current_node)
            item_body = stripped[1:].strip()
            if item_body and ":" in item_body:
                key, value = item_body.split(":", 1)
                current_node[key.strip()] = _parse_scalar(value)
            continue

        if current_node is not None and ":" in stripped:
            key, value = stripped.split(":", 1)
            current_node[key.strip()] = _parse_scalar(value)

    if nodes and "nodes" not in result:
        result["nodes"] = nodes
    return result


def _coerce_definition_payload(body: dict) -> dict:
    """Normalize POST payload into a workflow definition dict."""
    yaml_body = body.get("yaml_body")
    if isinstance(yaml_body, str) and yaml_body.strip():
        try:
            import yaml  # type: ignore[import-untyped]

            parsed = yaml.safe_load(yaml_body) or {}
        except ImportError:
            parsed = _fallback_parse_yaml_definition(yaml_body)
        except Exception as exc:
            raise ValueError(f"invalid YAML definition: {exc}") from exc

        if not isinstance(parsed, dict):
            raise ValueError("invalid YAML definition: root object must be a mapping")

        merged = dict(parsed)
        if body.get("name"):
            merged["name"] = body["name"]
        if body.get("description"):
            merged["description"] = body["description"]
        return merged

    if isinstance(body.get("definition"), dict):
        merged = dict(body["definition"])
        if body.get("name"):
            merged["name"] = body["name"]
        if body.get("description"):
            merged["description"] = body["description"]
        return merged

    return body


def _node_progress(node) -> str:
    """Return a human-friendly node progress label when available."""
    raw_progress = getattr(node, "progress", "")
    if raw_progress not in (None, ""):
        return str(raw_progress)

    config = getattr(node, "config", None) or {}
    if not isinstance(config, dict):
        return ""

    direct_value = config.get("progress") or config.get("progress_label")
    if direct_value not in (None, ""):
        return str(direct_value)

    current = config.get("progress_current")
    total = config.get("progress_total")
    if current is not None and total not in (None, ""):
        return f"{current}/{total}"

    return ""


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
            "progress": _node_progress(n),
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
            "progress": _node_progress(n),
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

    try:
        body = _coerce_definition_payload(body)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)

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


async def update_definition(request: Request) -> JSONResponse:
    """PUT /api/workflows/{id} — update a workflow definition."""
    from amiagi.domain.workflow import WorkflowDefinition

    def_id = request.path_params["id"]
    defs = _get_definitions(request)
    existing = defs.get(def_id)
    if existing is None:
        return JSONResponse({"error": "definition not found"}, status_code=404)

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON body"}, status_code=400)

    try:
        merged_body = _coerce_definition_payload(body)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)

    if not isinstance(merged_body, dict):
        return JSONResponse({"error": "invalid definition payload"}, status_code=400)

    normalized = existing.to_dict()
    normalized.update(merged_body)
    normalized["name"] = str(normalized.get("name", "")).strip()
    if not normalized["name"]:
        return JSONResponse({"error": "name is required"}, status_code=400)
    if not normalized.get("nodes"):
        return JSONResponse({"error": "at least one node is required"}, status_code=400)

    try:
        defn = WorkflowDefinition.from_dict(normalized)
        errors = defn.validate()
        if errors:
            return JSONResponse({"error": "validation failed", "details": errors}, status_code=400)
    except Exception as exc:
        return JSONResponse({"error": f"invalid definition: {exc}"}, status_code=400)

    defs[def_id] = defn

    from amiagi.interfaces.web.audit.log_helpers import log_action
    await log_action(request, "workflow.definition.updated", {"id": def_id, "name": defn.name})

    return JSONResponse({"id": def_id, "definition": _definition_to_dict(def_id, defn)})


async def clone_definition(request: Request) -> JSONResponse:
    """POST /api/workflows/{id}/clone — clone a stored workflow definition."""
    from amiagi.domain.workflow import WorkflowDefinition

    source_id = request.path_params["id"]
    defs = _get_definitions(request)
    source = defs.get(source_id)
    if source is None:
        return JSONResponse({"error": "definition not found"}, status_code=404)

    try:
        body = await request.json()
    except Exception:
        body = {}

    base_payload = source.to_dict()
    base_payload["name"] = f"{source.name} (copy)"

    try:
        overrides = _coerce_definition_payload(body or {})
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)

    if isinstance(overrides, dict):
        base_payload.update({k: v for k, v in overrides.items() if v not in (None, "")})

    try:
        defn = WorkflowDefinition.from_dict(base_payload)
        errors = defn.validate()
        if errors:
            return JSONResponse({"error": "validation failed", "details": errors}, status_code=400)
    except Exception as exc:
        return JSONResponse({"error": f"invalid definition: {exc}"}, status_code=400)

    cloned_id = str(uuid.uuid4())
    defs[cloned_id] = defn

    from amiagi.interfaces.web.audit.log_helpers import log_action
    await log_action(
        request,
        "workflow.definition.cloned",
        {"id": cloned_id, "source_id": source_id, "name": defn.name},
    )

    return JSONResponse({"id": cloned_id, "definition": _definition_to_dict(cloned_id, defn)}, status_code=201)


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
        await hub.broadcast("workflow.started", {"run_id": run.run_id, "name": defn.name})

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
        await hub.broadcast("workflow.gate.approved", {"run_id": run_id, "node_id": node_id})

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

    run = engine.get_run(run_id)
    hub = getattr(request.app.state, "event_hub", None)
    if hub is not None:
        await hub.broadcast("workflow.paused", {"run_id": run_id})

    from amiagi.interfaces.web.audit.log_helpers import log_action
    await log_action(request, "workflow.run.paused", {"run_id": run_id})

    return JSONResponse({"ok": True, "run": _run_to_dict(run) if run else None})


async def resume_run(request: Request) -> JSONResponse:
    """POST /api/workflow-runs/{run_id}/resume"""
    engine = _get_engine(request)
    if engine is None:
        return _no_engine()

    run_id = request.path_params["run_id"]
    ok = engine.resume(run_id)
    if not ok:
        return JSONResponse({"error": "run not found or not paused"}, status_code=404)

    run = engine.get_run(run_id)
    hub = getattr(request.app.state, "event_hub", None)
    if hub is not None:
        await hub.broadcast("workflow.resumed", {"run_id": run_id})

    from amiagi.interfaces.web.audit.log_helpers import log_action
    await log_action(request, "workflow.run.resumed", {"run_id": run_id})

    return JSONResponse({"ok": True, "run": _run_to_dict(run) if run else None})


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
        await hub.broadcast("workflow.aborted", {"run_id": run_id})

    from amiagi.interfaces.web.audit.log_helpers import log_action
    await log_action(request, "workflow.run.aborted", {"run_id": run_id})

    return JSONResponse({"ok": True, "run": _run_to_dict(run)})


# ── Route table ──────────────────────────────────────────────

workflow_routes: list[Route] = [
    Route("/workflows", workflow_page),
    Route("/api/workflows", list_definitions, methods=["GET"]),
    Route("/api/workflows", create_definition, methods=["POST"]),
    Route("/api/workflows/{id}", get_definition, methods=["GET"]),
    Route("/api/workflows/{id}", update_definition, methods=["PUT"]),
    Route("/api/workflows/{id}", delete_definition, methods=["DELETE"]),
    Route("/api/workflows/{id}/clone", clone_definition, methods=["POST"]),
    Route("/api/workflow-runs", list_runs, methods=["GET"]),
    Route("/api/workflow-runs", start_run, methods=["POST"]),
    Route("/api/workflow-runs/{run_id}/approve/{node_id}", approve_gate, methods=["POST"]),
    Route("/api/workflow-runs/{run_id}/pause", pause_run, methods=["POST"]),
    Route("/api/workflow-runs/{run_id}/resume", resume_run, methods=["POST"]),
    Route("/api/workflow-runs/{run_id}/abort", abort_run, methods=["POST"]),
    Route("/api/workflow-runs/{run_id}", get_run, methods=["GET"]),
]
