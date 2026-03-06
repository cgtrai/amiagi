"""Extended budget / cost center API routes.

Endpoints:
    GET  /api/budget/history   — budget usage history (time-series)
    PUT  /api/budget/quotas    — update per-role & session quotas
    POST /api/budget/reset     — reset agent or session budget counters
"""

from __future__ import annotations

import logging
from pathlib import Path

import yaml
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

logger = logging.getLogger(__name__)

_BUDGET_DEFAULTS_PATH = Path("config/budget_defaults.yaml")
_QUOTA_DEFAULTS_PATH = Path("config/quota_defaults.yaml")


# ── GET /api/budget/history ──────────────────────────────────

async def budget_history(request: Request) -> JSONResponse:
    """Return budget usage history.

    For now returns an in-memory snapshot from BudgetManager.
    In future, should query a persisted time-series table.
    """
    bm = getattr(request.app.state, "budget_manager", None)
    if bm is None:
        return JSONResponse({"error": "budget_manager_not_available"}, status_code=503)

    try:
        summary = bm.summary()
        session = bm.session_summary()

        # Build per-agent history entries
        agents: list[dict] = []
        for agent_id, rec in summary.items():
            agents.append({
                "agent_id": agent_id,
                "spent_usd": round(rec.get("spent_usd", rec.get("spent", 0)), 6),
                "limit_usd": round(rec.get("limit_usd", rec.get("limit", 0)), 2),
                "tokens": rec.get("tokens", rec.get("total_tokens", 0)),
                "requests": rec.get("requests", rec.get("total_requests", 0)),
                "utilization_pct": round(rec.get("utilization_pct", rec.get("utilization", 0)), 1),
            })

        return JSONResponse({
            "ok": True,
            "session": {
                "spent_usd": round(session.get("spent_usd", session.get("spent", 0)), 6),
                "limit_usd": round(session.get("limit_usd", session.get("limit", 0)), 2),
                "tokens": session.get("tokens", session.get("total_tokens", 0)),
                "requests": session.get("requests", session.get("total_requests", 0)),
            },
            "agents": agents,
        })

    except Exception as exc:
        logger.exception("budget.history failed")
        return JSONResponse({"error": str(exc)}, status_code=500)


# ── PUT /api/budget/quotas ───────────────────────────────────

async def budget_quotas_update(request: Request) -> JSONResponse:
    """Update budget quotas configuration.

    Body (partial update): {
        "session_limit_usd": 100.0,
        "warning_pct": 80,
        "agents": { "polluks": { "limit_usd": 20.0 } }
    }
    """
    body = await request.json()

    # Read current config
    config = _read_yaml(_BUDGET_DEFAULTS_PATH) or {
        "session": {"limit_usd": 50.0},
        "agents": {"default": {"limit_usd": 5.0}},
        "thresholds": {"warning_pct": 80, "blocked_pct": 100},
    }

    changed = False

    if "session_limit_usd" in body:
        config.setdefault("session", {})["limit_usd"] = float(body["session_limit_usd"])
        changed = True

    if "warning_pct" in body:
        config.setdefault("thresholds", {})["warning_pct"] = int(body["warning_pct"])
        changed = True

    if "blocked_pct" in body:
        config.setdefault("thresholds", {})["blocked_pct"] = int(body["blocked_pct"])
        changed = True

    if "agents" in body and isinstance(body["agents"], dict):
        config_agents = config.setdefault("agents", {})
        for agent_id, agent_cfg in body["agents"].items():
            if isinstance(agent_cfg, dict):
                config_agents.setdefault(agent_id, {}).update(agent_cfg)
                changed = True

    if changed:
        _write_yaml(_BUDGET_DEFAULTS_PATH, config)

        # Apply to running BudgetManager if available
        bm = getattr(request.app.state, "budget_manager", None)
        if bm is not None:
            try:
                session_limit = config.get("session", {}).get("limit_usd")
                if session_limit is not None and hasattr(bm, "set_session_budget"):
                    bm.set_session_budget(float(session_limit))
            except Exception:
                pass

        from amiagi.interfaces.web.audit.log_helpers import log_action
        await log_action(request, "budget.quotas_update", {
            "keys": list(body.keys()),
        })

    return JSONResponse({"ok": True, "config": config})


# ── POST /api/budget/reset ───────────────────────────────────

async def budget_reset(request: Request) -> JSONResponse:
    """Reset budget counters for a specific agent or the whole session.

    Body: { "agent_id": "polluks" }  — reset one agent
    Body: { "scope": "session" }     — reset entire session
    """
    body = await request.json()
    bm = getattr(request.app.state, "budget_manager", None)
    if bm is None:
        return JSONResponse({"error": "budget_manager_not_available"}, status_code=503)

    try:
        agent_id = body.get("agent_id")
        scope = body.get("scope", "")

        if scope == "session":
            bm.reset_all()
            target = "session"
        elif agent_id:
            bm.reset_agent(agent_id)
            target = agent_id
        else:
            return JSONResponse({"error": "agent_id_or_scope_required"}, status_code=400)

        from amiagi.interfaces.web.audit.log_helpers import log_action
        await log_action(request, "budget.reset", {"target": target})

        return JSONResponse({"ok": True, "target": target})

    except Exception as exc:
        logger.exception("budget.reset failed")
        return JSONResponse({"error": str(exc)}, status_code=500)


# ── Helpers ──────────────────────────────────────────────────

def _read_yaml(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _write_yaml(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.dump(data, default_flow_style=False, allow_unicode=True),
        encoding="utf-8",
    )


# ── Route table ──────────────────────────────────────────────

budget_routes: list[Route] = [
    Route("/api/budget/history", budget_history, methods=["GET"]),
    Route("/api/budget/quotas", budget_quotas_update, methods=["PUT"]),
    Route("/api/budget/reset", budget_reset, methods=["POST"]),
]
