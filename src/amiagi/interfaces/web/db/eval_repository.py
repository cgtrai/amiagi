"""Eval repository — CRUD for eval_runs, eval_run_scenarios, ab_campaigns.

All data survives application restarts — reads and writes go through
the async DB pool (asyncpg or SqlitePool).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, TYPE_CHECKING
from uuid import uuid4

if TYPE_CHECKING:
    import asyncpg

logger = logging.getLogger(__name__)


# ── Value objects ────────────────────────────────────────────


@dataclass
class EvalRunRecord:
    """Persistent representation of a single eval run."""

    id: str
    name: str
    status: str
    metrics: dict[str, Any] = field(default_factory=dict)
    created_at: datetime | None = None
    updated_at: datetime | None = None

    def to_dict(self) -> dict[str, Any]:
        d = dict(self.metrics) if self.metrics else {}
        d.update({
            "id": self.id,
            "name": self.name,
            "status": self.status,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        })
        return d


@dataclass
class EvalScenarioRecord:
    """Persistent representation of a scenario within an eval run."""

    id: str
    run_id: str
    scenario_name: str
    passed: bool
    details: dict[str, Any] = field(default_factory=dict)
    created_at: datetime | None = None


@dataclass
class ABCampaignRecord:
    """Persistent representation of an A/B campaign."""

    id: str
    name: str
    status: str
    variant_a_id: str
    variant_b_id: str
    results: dict[str, Any] = field(default_factory=dict)
    created_at: datetime | None = None
    updated_at: datetime | None = None

    def to_dict(self) -> dict[str, Any]:
        d = dict(self.results) if self.results else {}
        d.update({
            "id": self.id,
            "name": self.name,
            "status": self.status,
            "agent_a_id": self.variant_a_id,
            "agent_b_id": self.variant_b_id,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        })
        return d


# ── Repository ───────────────────────────────────────────────


class EvalRepository:
    """Async repository for eval_runs, eval_run_scenarios, ab_campaigns.

    Uses raw SQL via the shared DB pool (asyncpg or SqlitePool).
    """

    def __init__(self, pool: "asyncpg.Pool") -> None:
        self._pool = pool

    # ── Eval Runs ────────────────────────────────────────────

    async def list_eval_runs(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
        agent_id: str | None = None,
        suite: str | None = None,
    ) -> tuple[list[dict[str, Any]], int]:
        """Return (items, total_count) from dbo.eval_runs."""
        async with self._pool.acquire() as conn:
            total: int = await conn.fetchval(
                "SELECT count(*) FROM dbo.eval_runs",
            ) or 0

            rows = await conn.fetch(
                "SELECT id, name, status, metrics_json, created_at, updated_at "
                "FROM dbo.eval_runs ORDER BY created_at DESC LIMIT $1 OFFSET $2",
                limit,
                offset,
            )

        items: list[dict[str, Any]] = []
        for r in rows:
            entry = _parse_eval_row(r)
            # Apply in-Python filters (metrics_json holds agent_id/suite)
            if agent_id and entry.get("agent_id") != agent_id:
                continue
            if suite and entry.get("suite") != suite:
                continue
            items.append(entry)

        return items, total

    async def get_eval_run(self, run_id: str) -> dict[str, Any] | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT id, name, status, metrics_json, created_at, updated_at "
                "FROM dbo.eval_runs WHERE id = $1",
                run_id,
            )
        if row is None:
            return None
        return _parse_eval_row(row)

    async def upsert_eval_run(self, entry: dict[str, Any]) -> None:
        """Insert or update an eval run.  *entry* is the rich API dict."""
        run_id = entry["id"]
        name = entry.get("label") or entry.get("suite") or entry.get("agent_id", "")
        status = entry.get("status", "pending")
        metrics_blob = json.dumps(entry)

        async with self._pool.acquire() as conn:
            await conn.execute(
                """INSERT INTO dbo.eval_runs (id, name, status, metrics_json, created_at, updated_at)
                   VALUES ($1, $2, $3, $4::jsonb, now(), now())
                   ON CONFLICT (id) DO UPDATE SET
                       status = EXCLUDED.status,
                       metrics_json = EXCLUDED.metrics_json,
                       updated_at = now()""",
                run_id,
                name,
                status,
                metrics_blob,
            )

    async def upsert_scenarios(self, run_id: str, scenarios: list[dict]) -> None:
        """Persist per-scenario results for an eval run."""
        if not scenarios:
            return
        async with self._pool.acquire() as conn:
            for sc in scenarios:
                sc_id = sc.get("id") or str(uuid4())
                await conn.execute(
                    """INSERT INTO dbo.eval_run_scenarios
                           (id, run_id, scenario_name, passed, details_json, created_at)
                       VALUES ($1, $2, $3, $4, $5::jsonb, now())
                       ON CONFLICT (id) DO NOTHING""",
                    sc_id,
                    run_id,
                    sc.get("scenario_id") or sc.get("scenario_name", ""),
                    sc.get("passed", sc.get("aggregate", 0) >= 50),
                    json.dumps(sc),
                )

    async def get_scenarios(self, run_id: str) -> list[dict[str, Any]]:
        """Load per-scenario rows for a run."""
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT id, run_id, scenario_name, passed, details_json, created_at "
                "FROM dbo.eval_run_scenarios WHERE run_id = $1 ORDER BY created_at",
                run_id,
            )
        return [_parse_scenario_row(r) for r in rows]

    # ── A/B Campaigns ────────────────────────────────────────

    async def list_ab_campaigns(self) -> list[dict[str, Any]]:
        """Return all A/B campaigns ordered by creation time desc."""
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT id, name, status, variant_a_id, variant_b_id, "
                "results_json, created_at, updated_at "
                "FROM dbo.ab_campaigns ORDER BY created_at DESC",
            )
        return [_parse_ab_row(r) for r in rows]

    async def get_ab_campaign(self, campaign_id: str) -> dict[str, Any] | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT id, name, status, variant_a_id, variant_b_id, "
                "results_json, created_at, updated_at "
                "FROM dbo.ab_campaigns WHERE id = $1",
                campaign_id,
            )
        if row is None:
            return None
        return _parse_ab_row(row)

    async def upsert_ab_campaign(self, entry: dict[str, Any]) -> None:
        """Insert or update an A/B campaign."""
        cid = entry["id"]
        name = entry.get("label") or entry.get("name", "")
        status = entry.get("status", "pending")
        variant_a = entry.get("agent_a_id", "")
        variant_b = entry.get("agent_b_id", "")
        results_blob = json.dumps(entry)

        async with self._pool.acquire() as conn:
            await conn.execute(
                """INSERT INTO dbo.ab_campaigns
                       (id, name, status, variant_a_id, variant_b_id, results_json, created_at, updated_at)
                   VALUES ($1, $2, $3, $4, $5, $6::jsonb, now(), now())
                   ON CONFLICT (id) DO UPDATE SET
                       status = EXCLUDED.status,
                       results_json = EXCLUDED.results_json,
                       updated_at = now()""",
                cid,
                name,
                status,
                variant_a,
                variant_b,
                results_blob,
            )

    async def update_ab_status(self, campaign_id: str, status: str) -> dict[str, Any] | None:
        """Update just the status column and refresh results_json."""
        campaign = await self.get_ab_campaign(campaign_id)
        if campaign is None:
            return None
        campaign["status"] = status
        if status == "completed":
            import time
            campaign["finished_at"] = time.time()
        await self.upsert_ab_campaign(campaign)
        return campaign


# ── Row parsers ──────────────────────────────────────────────

def _parse_eval_row(row) -> dict[str, Any]:
    """Convert a DB row from eval_runs into the API dict format."""
    metrics_raw = row["metrics_json"]
    if isinstance(metrics_raw, str):
        metrics = json.loads(metrics_raw) if metrics_raw else {}
    elif isinstance(metrics_raw, dict):
        metrics = metrics_raw
    else:
        metrics = {}

    # The full API dict is stored in metrics_json — use it directly.
    entry = dict(metrics)
    # Ensure canonical fields from columns override
    entry["id"] = str(row["id"])
    entry["status"] = row["status"]
    entry.setdefault("name", row["name"])
    return entry


def _parse_scenario_row(row) -> dict[str, Any]:
    details_raw = row["details_json"]
    if isinstance(details_raw, str):
        details = json.loads(details_raw) if details_raw else {}
    elif isinstance(details_raw, dict):
        details = details_raw
    else:
        details = {}

    return {
        "id": str(row["id"]),
        "run_id": str(row["run_id"]),
        "scenario_name": row["scenario_name"],
        "passed": row["passed"],
        **details,
    }


def _parse_ab_row(row) -> dict[str, Any]:
    """Convert a DB row from ab_campaigns into the API dict format."""
    results_raw = row["results_json"]
    if isinstance(results_raw, str):
        results = json.loads(results_raw) if results_raw else {}
    elif isinstance(results_raw, dict):
        results = results_raw
    else:
        results = {}

    entry = dict(results)
    entry["id"] = str(row["id"])
    entry["status"] = row["status"]
    entry.setdefault("name", row["name"])
    entry.setdefault("agent_a_id", row["variant_a_id"])
    entry.setdefault("agent_b_id", row["variant_b_id"])
    return entry
