from __future__ import annotations

from datetime import datetime
from pathlib import Path

from starlette.applications import Starlette
from starlette.testclient import TestClient

from amiagi.interfaces.web.routes.cron_routes import cron_routes
from amiagi.interfaces.web.scheduling.cron_scheduler import (
    CronExecutionRecord,
    CronJob,
    next_cron_trigger,
)


class _FakeCronScheduler:
    def __init__(self) -> None:
        self.jobs = [
            CronJob(
                id="job-1",
                name="Nightly report",
                cron_expr="0 2 * * *",
                task_title="Report",
                enabled=True,
                next_run="2026-03-07T02:00:00",
            )
        ]
        self.history = [
            CronExecutionRecord(
                job_id="job-1",
                job_name="Nightly report",
                triggered_at="2026-03-06T02:00:00",
                status="success",
                message="Created task #1",
            )
        ]

    def list_jobs(self) -> list[CronJob]:
        return list(self.jobs)

    async def create_job(self, job: CronJob) -> CronJob:
        job.next_run = "2026-03-07T03:00:00"
        self.jobs.append(job)
        return job

    async def delete_job(self, job_id: str) -> bool:
        self.jobs = [job for job in self.jobs if job.id != job_id]
        return True

    async def toggle_job(self, job_id: str, enabled: bool) -> bool:
        for job in self.jobs:
            if job.id == job_id:
                job.enabled = enabled
        return True

    def list_history(self, *, job_id: str | None = None, limit: int = 100) -> list[CronExecutionRecord]:
        records = self.history
        if job_id:
            records = [record for record in records if record.job_id == job_id]
        return records[:limit]


def _make_client() -> TestClient:
    app = Starlette(routes=list(cron_routes))
    app.state.cron_scheduler = _FakeCronScheduler()
    return TestClient(app, raise_server_exceptions=False)


def test_next_cron_trigger_returns_following_match() -> None:
    trigger = next_cron_trigger("15 3 * * *", after=datetime(2026, 3, 6, 3, 15))

    assert trigger == datetime(2026, 3, 7, 3, 15)


def test_cron_routes_include_preview_and_history() -> None:
    paths = {route.path for route in cron_routes}

    assert "/api/cron/preview" in paths
    assert "/api/cron/history" in paths


def test_preview_endpoint_returns_next_run() -> None:
    client = _make_client()

    response = client.get("/api/cron/preview?cron_expr=0%202%20*%20*%20*")

    assert response.status_code == 200
    payload = response.json()
    assert payload["valid"] is True
    assert payload["next_run"]
    assert payload["human_readable"] == "Every day at 02:00"


def test_create_job_accepts_legacy_cron_expression_field() -> None:
    client = _make_client()

    response = client.post(
        "/api/cron",
        json={"name": "Morning sync", "cron_expression": "0 3 * * *", "task_title": "Sync"},
    )

    assert response.status_code == 201
    payload = response.json()
    assert payload["cron_expr"] == "0 3 * * *"
    assert payload["human_readable"] == "Every day at 03:00"


def test_create_job_accepts_schedule_builder_payload() -> None:
    client = _make_client()

    response = client.post(
        "/api/cron",
        json={
            "name": "Weekly review",
            "task_title": "Review backlog",
            "schedule": {"mode": "weekly", "weekdays": [0, 2], "time": "09:30"},
        },
    )

    assert response.status_code == 201
    payload = response.json()
    assert payload["cron_expr"] == "30 9 * * 0,2"
    assert payload["human_readable"] == "Every Monday, Wednesday at 09:30"


def test_preview_endpoint_accepts_schedule_builder_payload() -> None:
    client = _make_client()

    response = client.post(
        "/api/cron/preview",
        json={"schedule": {"mode": "hourly", "interval_hours": 4, "minute": 15}},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["valid"] is True
    assert payload["cron_expr"] == "15 */4 * * *"
    assert payload["human_readable"] == "Every 4 hours at :15"


def test_toggle_route_inverts_state_without_request_body() -> None:
    client = _make_client()

    response = client.put("/api/cron/job-1/toggle")

    assert response.status_code == 200
    assert response.json() == {"ok": True}
    jobs = client.get("/api/cron").json()
    assert jobs[0]["enabled"] is False


def test_history_endpoint_returns_execution_log() -> None:
    client = _make_client()

    response = client.get("/api/cron/history?limit=10")

    assert response.status_code == 200
    payload = response.json()
    assert payload[0]["job_name"] == "Nightly report"
    assert payload[0]["status"] == "success"


def test_list_jobs_returns_human_readable_schedule() -> None:
    client = _make_client()

    response = client.get("/api/cron")

    assert response.status_code == 200
    payload = response.json()
    assert payload[0]["human_readable"] == "Every day at 02:00"


def test_settings_template_contains_cron_history_and_preview_hooks() -> None:
    template = Path("src/amiagi/interfaces/web/templates/settings.html").read_text(encoding="utf-8")

    assert "/api/cron/preview" in template
    assert "/api/cron/history" in template
    assert "cron-preview-status" in template
    assert "cron-history-list" in template
