"""Evaluation, Benchmark and A/B Test API routes.

Endpoints:
    GET    /evaluations                   — Evaluations dashboard page
    GET    /api/evaluations               — list eval run results
    POST   /api/evaluations/run           — run eval in background
    GET    /api/evaluations/{id}          — eval run details (per-scenario)
    GET    /api/evaluations/ab-tests      — list A/B campaigns
    POST   /api/evaluations/ab-tests      — start A/B campaign
    PUT    /api/evaluations/ab-tests/{id}/pause — pause campaign
    PUT    /api/evaluations/ab-tests/{id}/stop  — stop campaign
    GET    /api/evaluations/baselines     — list baselines
    POST   /api/evaluations/baselines     — save baseline
    DELETE /api/evaluations/baselines/{name} — delete baseline
    GET    /api/evaluations/regressions   — regression report
    GET    /api/evaluations/suites        — list benchmark suites
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from amiagi.interfaces.web.db.eval_repository import EvalRepository

logger = logging.getLogger(__name__)


# ── Helpers ──────────────────────────────────────────────────

def _get_eval_repo(request: Request) -> EvalRepository:
    """Return the DB-backed EvalRepository from app.state."""
    return request.app.state.eval_repo


def _get_eval_runner(request: Request):
    return getattr(request.app.state, "eval_runner", None)


def _get_ab_runner(request: Request):
    return getattr(request.app.state, "ab_test_runner", None)


def _get_regression_detector(request: Request):
    return getattr(request.app.state, "regression_detector", None)


def _get_benchmark_suite(request: Request):
    return getattr(request.app.state, "benchmark_suite", None)


# ── Page view ────────────────────────────────────────────────


def _resolve_agent_fn(request: Request, agent_id: str):
    """Try to build an ``AgentCallable`` for *agent_id*.

    Returns ``None`` when no runtime is available (stub/test environments).
    """
    adapter = getattr(request.app.state, "web_adapter", None)
    engine = getattr(adapter, "router_engine", None) if adapter else None
    if engine is None:
        return None
    runtimes = getattr(engine, "_runtimes", None) or getattr(engine, "runtimes", None)
    if not isinstance(runtimes, dict):
        return None
    runtime = runtimes.get(agent_id)
    if runtime is None:
        return None

    def _agent_fn(prompt: str) -> str:
        return runtime.ask(prompt)

    return _agent_fn


async def _run_eval_background(
    repo: EvalRepository,
    runner,
    run_id: str,
    agent_id: str,
    agent_fn,
    scenarios: list,
    *,
    suite_name: str = "",
    label: str = "",
    hub=None,
) -> None:
    """Background task — actually executes the eval via ``EvalRunner``."""
    try:
        # Mark as "running"
        await repo.upsert_eval_run({
            "id": run_id,
            "agent_id": agent_id,
            "status": "running",
            "suite": suite_name,
            "label": label,
            "started_at": time.time(),
        })

        result = await runner.run_async(
            agent_id,
            agent_fn,
            scenarios,
            metadata={"run_id": run_id, "suite": suite_name, "label": label},
        )

        completed = _eval_result_to_dict(result, run_id=run_id)
        completed["status"] = "completed"
        completed["suite"] = suite_name
        completed["label"] = label
        await repo.upsert_eval_run(completed)

        # Persist per-scenario rows
        scenario_rows = completed.get("results", [])
        if scenario_rows:
            await repo.upsert_scenarios(run_id, scenario_rows)

        if hub is not None:
            hub.broadcast("eval.completed", {"run_id": run_id, "agent_id": agent_id,
                                              "aggregate_score": result.aggregate_score})

        logger.info("Eval run %s completed — score %.2f", run_id, result.aggregate_score)

    except Exception:
        logger.exception("Eval run %s failed", run_id)
        try:
            await repo.upsert_eval_run({
                "id": run_id,
                "agent_id": agent_id,
                "status": "failed",
                "suite": suite_name,
                "label": label,
                "finished_at": time.time(),
            })
        except Exception:
            logger.exception("Failed to persist 'failed' status for run %s", run_id)

        if hub is not None:
            hub.broadcast("eval.failed", {"run_id": run_id, "agent_id": agent_id})


async def _run_ab_background(
    repo: EvalRepository,
    ab_runner,
    campaign_id: str,
    agent_a_id: str,
    agent_a_fn,
    agent_b_id: str,
    agent_b_fn,
    scenarios: list,
    *,
    suite: str = "",
    label: str = "",
    hub=None,
) -> None:
    """Background task — actually executes the A/B comparison."""
    try:
        await repo.upsert_ab_campaign({
            "id": campaign_id,
            "agent_a_id": agent_a_id,
            "agent_b_id": agent_b_id,
            "status": "running",
            "suite": suite,
            "label": label,
            "started_at": time.time(),
        })

        result = await ab_runner.compare_async(
            agent_a_id, agent_a_fn,
            agent_b_id, agent_b_fn,
            scenarios,
        )

        completed = _ab_result_to_dict(result, campaign_id=campaign_id)
        completed["status"] = "completed"
        completed["suite"] = suite
        completed["label"] = label
        await repo.upsert_ab_campaign(completed)

        if hub is not None:
            hub.broadcast("ab.completed", {
                "campaign_id": campaign_id,
                "a_wins": result.a_wins,
                "b_wins": result.b_wins,
                "ties": result.ties,
            })

        logger.info("A/B campaign %s completed — a_wins=%d b_wins=%d ties=%d",
                     campaign_id, result.a_wins, result.b_wins, result.ties)

    except Exception:
        logger.exception("A/B campaign %s failed", campaign_id)
        try:
            await repo.upsert_ab_campaign({
                "id": campaign_id,
                "agent_a_id": agent_a_id,
                "agent_b_id": agent_b_id,
                "status": "failed",
                "suite": suite,
                "label": label,
                "finished_at": time.time(),
            })
        except Exception:
            logger.exception("Failed to persist 'failed' status for campaign %s", campaign_id)

        if hub is not None:
            hub.broadcast("ab.failed", {"campaign_id": campaign_id})




async def evaluations_page(request: Request):
    """GET /evaluations — render Evaluations dashboard."""
    templates = getattr(request.app.state, "templates", None)
    if templates is None:
        return JSONResponse({"error": "templates unavailable"}, status_code=500)
    return templates.TemplateResponse(request, "evaluations.html")


# ── Eval Run CRUD ────────────────────────────────────────────

async def list_eval_runs(request: Request) -> JSONResponse:
    """GET /api/evaluations — list eval run results (from DB)."""
    repo = _get_eval_repo(request)

    limit = min(int(request.query_params.get("limit", "50")), 200)
    offset = int(request.query_params.get("offset", "0"))
    agent_id = request.query_params.get("agent_id")
    suite_filter = request.query_params.get("suite")

    items, total = await repo.list_eval_runs(
        limit=limit, offset=offset, agent_id=agent_id, suite=suite_filter,
    )

    # Sort by started_at descending (might already be sorted, ensure it)
    items.sort(key=lambda x: x.get("started_at", 0), reverse=True)

    return JSONResponse({"runs": items, "total": total})


def _eval_result_to_dict(result, run_id: str = "") -> dict:
    """Convert an EvalRunResult to a JSON-safe dict."""
    return {
        "id": run_id or result.metadata.get("run_id", str(uuid.uuid4())),
        "agent_id": result.agent_id,
        "rubric_name": result.rubric_name,
        "suite": result.metadata.get("suite", ""),
        "label": result.metadata.get("label", ""),
        "scorer": result.metadata.get("scorer", "keyword"),
        "aggregate_score": result.aggregate_score,
        "scenarios_count": result.scenarios_count,
        "passed": result.passed,
        "failed": result.failed,
        "started_at": result.started_at,
        "finished_at": result.finished_at,
        "status": "completed" if result.finished_at else "running",
        "results": [
            {
                "scenario_id": getattr(r, "scenario_id", getattr(r, "metadata", {}).get("scenario_id", "")),
                "aggregate": r.aggregate,
                "scores": r.scores,
                "notes": r.notes,
            }
            for r in result.results
        ],
    }


async def run_evaluation(request: Request) -> JSONResponse:
    """POST /api/evaluations/run — trigger an evaluation.

    Body: { "agent_id": "...", "suite": "...", "scorer": "keyword", "label": "..." }
    """
    runner = _get_eval_runner(request)
    if runner is None:
        return JSONResponse({"error": "eval_runner unavailable"}, status_code=503)

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON body"}, status_code=400)

    agent_id = body.get("agent_id", "").strip()
    suite_name = body.get("suite", "").strip()
    label = body.get("label", "").strip()

    if not agent_id:
        return JSONResponse({"error": "agent_id is required"}, status_code=400)

    # Fetch scenarios from benchmark suite
    bsuite = _get_benchmark_suite(request)
    scenarios = []
    if bsuite and suite_name:
        try:
            scenarios = bsuite.get_scenarios(suite_name)
        except Exception:
            pass

    if not scenarios:
        return JSONResponse(
            {"error": f"no scenarios found for suite '{suite_name}'"},
            status_code=400,
        )

    run_id = str(uuid.uuid4())

    # Create a placeholder agent function
    # In real use the agent_fn calls the actual agent; here we create a stub
    chat_service = getattr(request.app.state, "web_adapter", None)
    agent_registry = getattr(request.app.state, "agent_registry", None)

    # Persist as pending in DB immediately
    repo = _get_eval_repo(request)
    entry = {
        "id": run_id,
        "agent_id": agent_id,
        "suite": suite_name,
        "label": label,
        "status": "pending",
        "aggregate_score": 0,
        "scenarios_count": len(scenarios),
        "passed": 0,
        "failed": 0,
        "started_at": time.time(),
        "finished_at": None,
        "results": [],
    }

    hub = getattr(request.app.state, "event_hub", None)
    if hub is not None:
        hub.broadcast("eval.started", {"run_id": run_id, "agent_id": agent_id})

    await repo.upsert_eval_run(entry)

    # Launch actual evaluation in background
    agent_fn = _resolve_agent_fn(request, agent_id)
    if agent_fn is not None:
        asyncio.create_task(
            _run_eval_background(
                repo, runner, run_id, agent_id, agent_fn, scenarios,
                suite_name=suite_name, label=label, hub=hub,
            )
        )

    from amiagi.interfaces.web.audit.log_helpers import log_action
    await log_action(request, "eval.run.started", {"run_id": run_id, "agent_id": agent_id, "suite": suite_name})

    return JSONResponse({"id": run_id, "status": "pending"}, status_code=201)


async def get_eval_run(request: Request) -> JSONResponse:
    """GET /api/evaluations/{id} — get detailed eval run (from DB)."""
    run_id = request.path_params["id"]
    repo = _get_eval_repo(request)
    entry = await repo.get_eval_run(run_id)

    if entry is None:
        return JSONResponse({"error": "eval run not found"}, status_code=404)

    # Attach per-scenario rows if available
    scenarios = await repo.get_scenarios(run_id)
    if scenarios:
        entry["results"] = scenarios

    return JSONResponse({"run": entry})


# ── A/B Tests ────────────────────────────────────────────────

async def list_ab_tests(request: Request) -> JSONResponse:
    """GET /api/evaluations/ab-tests (from DB)."""
    repo = _get_eval_repo(request)
    items = await repo.list_ab_campaigns()
    items.sort(key=lambda x: x.get("started_at", 0), reverse=True)
    return JSONResponse({"campaigns": items, "total": len(items)})


def _ab_result_to_dict(result, campaign_id: str = "") -> dict:
    """Convert ABComparisonResult to dict."""
    return {
        "id": campaign_id or str(uuid.uuid4()),
        "agent_a_id": result.agent_a_id,
        "agent_b_id": result.agent_b_id,
        "rubric_name": result.rubric_name,
        "scenarios_count": result.scenarios_count,
        "a_wins": result.a_wins,
        "b_wins": result.b_wins,
        "ties": result.ties,
        "a_aggregate": result.a_aggregate,
        "b_aggregate": result.b_aggregate,
        "score_delta": result.score_delta,
        "started_at": result.started_at,
        "finished_at": result.finished_at,
        "status": "completed" if result.finished_at else "running",
        "per_scenario": result.per_scenario,
    }


async def create_ab_test(request: Request) -> JSONResponse:
    """POST /api/evaluations/ab-tests — start A/B campaign.

    Body: { "agent_a_id": "...", "agent_b_id": "...", "suite": "...", "label": "..." }
    """
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON body"}, status_code=400)

    agent_a = body.get("agent_a_id", "").strip()
    agent_b = body.get("agent_b_id", "").strip()
    suite = body.get("suite", "").strip()
    label = body.get("label", "")

    if not agent_a or not agent_b:
        return JSONResponse({"error": "both agent_a_id and agent_b_id are required"}, status_code=400)

    campaign_id = str(uuid.uuid4())
    repo = _get_eval_repo(request)
    entry = {
        "id": campaign_id,
        "agent_a_id": agent_a,
        "agent_b_id": agent_b,
        "suite": suite,
        "label": label,
        "status": "pending",
        "a_wins": 0,
        "b_wins": 0,
        "ties": 0,
        "a_aggregate": 0.0,
        "b_aggregate": 0.0,
        "score_delta": 0.0,
        "scenarios_count": 0,
        "started_at": time.time(),
        "finished_at": None,
        "per_scenario": [],
    }

    hub = getattr(request.app.state, "event_hub", None)
    if hub is not None:
        hub.broadcast("ab.started", {"campaign_id": campaign_id})

    await repo.upsert_ab_campaign(entry)

    # Launch actual A/B comparison in background
    ab_runner = _get_ab_runner(request)
    if ab_runner is not None:
        agent_a_fn = _resolve_agent_fn(request, agent_a)
        agent_b_fn = _resolve_agent_fn(request, agent_b)
        if agent_a_fn is not None and agent_b_fn is not None:
            bsuite = _get_benchmark_suite(request)
            ab_scenarios = []
            if bsuite and suite:
                try:
                    ab_scenarios = bsuite.get_scenarios(suite)
                except Exception:
                    pass
            if ab_scenarios:
                asyncio.create_task(
                    _run_ab_background(
                        repo, ab_runner, campaign_id,
                        agent_a, agent_a_fn, agent_b, agent_b_fn, ab_scenarios,
                        suite=suite, label=label, hub=hub,
                    )
                )

    from amiagi.interfaces.web.audit.log_helpers import log_action
    await log_action(request, "ab.campaign.started", {"campaign_id": campaign_id, "a": agent_a, "b": agent_b})

    return JSONResponse({"id": campaign_id, "status": "pending"}, status_code=201)


async def pause_ab_test(request: Request) -> JSONResponse:
    """PUT /api/evaluations/ab-tests/{id}/pause"""
    campaign_id = request.path_params["id"]
    repo = _get_eval_repo(request)
    campaign = await repo.update_ab_status(campaign_id, "paused")
    if campaign is None:
        return JSONResponse({"error": "campaign not found"}, status_code=404)
    return JSONResponse({"ok": True, "status": "paused"})


async def stop_ab_test(request: Request) -> JSONResponse:
    """PUT /api/evaluations/ab-tests/{id}/stop"""
    campaign_id = request.path_params["id"]
    repo = _get_eval_repo(request)
    campaign = await repo.update_ab_status(campaign_id, "completed")
    if campaign is None:
        return JSONResponse({"error": "campaign not found"}, status_code=404)
    return JSONResponse({"ok": True, "status": "completed"})


# ── Baselines ────────────────────────────────────────────────

async def list_baselines(request: Request) -> JSONResponse:
    """GET /api/evaluations/baselines"""
    detector = _get_regression_detector(request)
    if detector is None:
        return JSONResponse({"baselines": [], "total": 0})

    names = detector.list_baselines()
    baselines = []
    for name in names:
        bl = detector.load_baseline(name)
        if bl:
            baselines.append({
                "agent_id": name,
                "aggregate_score": bl.aggregate_score,
                "scenarios_count": bl.scenarios_count,
                "passed": bl.passed,
                "failed": bl.failed,
            })
    return JSONResponse({"baselines": baselines, "total": len(baselines)})


async def save_baseline(request: Request) -> JSONResponse:
    """POST /api/evaluations/baselines — save current run as baseline.

    Body: { "run_id": "..." }
    """
    detector = _get_regression_detector(request)
    if detector is None:
        return JSONResponse({"error": "regression_detector unavailable"}, status_code=503)

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON body"}, status_code=400)

    run_id = body.get("run_id", "").strip()
    if not run_id:
        return JSONResponse({"error": "run_id is required"}, status_code=400)

    # Look up run result from DB
    repo = _get_eval_repo(request)
    entry = await repo.get_eval_run(run_id)
    if entry is None:
        return JSONResponse({"error": "eval run not found"}, status_code=404)

    # Reconstruct EvalRunResult-like object for detector
    from amiagi.application.eval_runner import EvalRunResult
    from amiagi.domain.eval_rubric import EvalResult

    result = EvalRunResult(
        agent_id=entry["agent_id"],
        rubric_name=entry.get("rubric_name", "default"),
        results=[],
        scenarios_count=entry.get("scenarios_count", 0),
        passed=entry.get("passed", 0),
        failed=entry.get("failed", 0),
        aggregate_score=entry.get("aggregate_score", 0),
        started_at=entry.get("started_at", 0),
        finished_at=entry.get("finished_at"),
        metadata=entry.get("metadata", {}),
    )

    try:
        path = detector.save_baseline(result)
        from amiagi.interfaces.web.audit.log_helpers import log_action
        await log_action(request, "eval.baseline.saved", {"agent_id": entry["agent_id"]})
        return JSONResponse({"ok": True, "path": str(path)}, status_code=201)
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)


async def delete_baseline(request: Request) -> JSONResponse:
    """DELETE /api/evaluations/baselines/{name}"""
    detector = _get_regression_detector(request)
    if detector is None:
        return JSONResponse({"error": "regression_detector unavailable"}, status_code=503)

    name = request.path_params["name"]
    ok = detector.delete_baseline(name)
    if not ok:
        return JSONResponse({"error": "baseline not found"}, status_code=404)

    from amiagi.interfaces.web.audit.log_helpers import log_action
    await log_action(request, "eval.baseline.deleted", {"name": name})

    return JSONResponse({"ok": True})


# ── Regressions ──────────────────────────────────────────────

async def check_regressions(request: Request) -> JSONResponse:
    """GET /api/evaluations/regressions — regression report."""
    detector = _get_regression_detector(request)
    if detector is None:
        return JSONResponse({"regressions": [], "total": 0})

    repo = _get_eval_repo(request)
    all_runs, _ = await repo.list_eval_runs(limit=500, offset=0)
    reports = []

    for agent_id in detector.list_baselines():
        # Find latest completed run for this agent from DB
        latest = None
        for entry in all_runs:
            if entry.get("agent_id") == agent_id and entry.get("status") == "completed":
                if latest is None or entry.get("started_at", 0) > latest.get("started_at", 0):
                    latest = entry

        if latest is None:
            continue

        baseline = detector.load_baseline(agent_id)
        if baseline is None:
            continue

        delta = latest.get("aggregate_score", 0) - baseline.aggregate_score
        threshold = detector.threshold
        reports.append({
            "agent_id": agent_id,
            "baseline_score": baseline.aggregate_score,
            "current_score": latest.get("aggregate_score", 0),
            "delta": round(delta, 2),
            "regressed": delta < -threshold,
            "threshold": threshold,
        })

    return JSONResponse({"regressions": reports, "total": len(reports)})


# ── Benchmark Suites ─────────────────────────────────────────

async def list_suites(request: Request) -> JSONResponse:
    """GET /api/evaluations/suites — list available benchmark suites."""
    bsuite = _get_benchmark_suite(request)
    if bsuite is None:
        return JSONResponse({"suites": [], "total": 0})

    try:
        bsuite.load_all()
    except Exception:
        pass

    categories = bsuite.list_categories()
    suites = []
    for cat in categories:
        scenarios = bsuite.get_scenarios(cat)
        suites.append({
            "name": cat,
            "scenarios_count": len(scenarios),
        })

    return JSONResponse({"suites": suites, "total": len(suites)})


# ── Route table ──────────────────────────────────────────────

eval_routes: list[Route] = [
    Route("/evaluations", evaluations_page),
    # Specific paths first
    Route("/api/evaluations/ab-tests/{id}/pause", pause_ab_test, methods=["PUT"]),
    Route("/api/evaluations/ab-tests/{id}/stop", stop_ab_test, methods=["PUT"]),
    Route("/api/evaluations/ab-tests", list_ab_tests, methods=["GET"]),
    Route("/api/evaluations/ab-tests", create_ab_test, methods=["POST"]),
    Route("/api/evaluations/baselines/{name}", delete_baseline, methods=["DELETE"]),
    Route("/api/evaluations/baselines", list_baselines, methods=["GET"]),
    Route("/api/evaluations/baselines", save_baseline, methods=["POST"]),
    Route("/api/evaluations/regressions", check_regressions, methods=["GET"]),
    Route("/api/evaluations/suites", list_suites, methods=["GET"]),
    Route("/api/evaluations/run", run_evaluation, methods=["POST"]),
    Route("/api/evaluations/{id}", get_eval_run, methods=["GET"]),
    Route("/api/evaluations", list_eval_runs, methods=["GET"]),
]
