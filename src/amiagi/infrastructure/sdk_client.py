"""Phase 10 — Python SDK client (infrastructure).

Provides a high-level ``AmiagiClient`` for interacting with the REST API
from external scripts or services.

Usage::

    from amiagi.infrastructure.sdk_client import AmiagiClient

    client = AmiagiClient("http://127.0.0.1:8090", token="secret")
    agents = client.list_agents()
"""

from __future__ import annotations

import json
import urllib.request
import urllib.error
from typing import Any


class SDKError(Exception):
    """Raised when an SDK request fails."""

    def __init__(self, status: int, message: str) -> None:
        self.status = status
        super().__init__(f"HTTP {status}: {message}")


class AmiagiClient:
    """Lightweight Python SDK for the amiagi REST API."""

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:8090",
        *,
        token: str = "",
        timeout: int = 30,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._token = token
        self._timeout = timeout

    # ---- low-level ----

    def _request(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        url = f"{self._base_url}{path}"
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"

        data: bytes | None = None
        if body is not None:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")

        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                raw = resp.read().decode("utf-8")
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as exc:
            raw_body = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
            try:
                detail = json.loads(raw_body).get("error", raw_body)
            except (json.JSONDecodeError, AttributeError):
                detail = raw_body
            raise SDKError(exc.code, detail) from exc
        except urllib.error.URLError as exc:
            raise SDKError(0, str(exc.reason)) from exc

    def get(self, path: str) -> dict[str, Any]:
        return self._request("GET", path)

    def post(self, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        return self._request("POST", path, body)

    def delete(self, path: str) -> dict[str, Any]:
        return self._request("DELETE", path)

    # ---- high-level helpers ----

    def list_agents(self) -> list[dict[str, Any]]:
        resp = self.get("/agents")
        return resp.get("agents", [])

    def create_agent(self, **kwargs: Any) -> dict[str, Any]:
        return self.post("/agents", kwargs)

    def list_tasks(self) -> list[dict[str, Any]]:
        resp = self.get("/tasks")
        return resp.get("tasks", [])

    def create_task(self, **kwargs: Any) -> dict[str, Any]:
        return self.post("/tasks", kwargs)

    def run_workflow(self, workflow_id: str, **kwargs: Any) -> dict[str, Any]:
        payload = {"workflow_id": workflow_id, **kwargs}
        return self.post("/workflows/run", payload)

    def get_metrics(self) -> dict[str, Any]:
        return self.get("/metrics")

    def task_status(self, task_id: str) -> dict[str, Any]:
        """Get status of a specific task by ID."""
        return self.get(f"/tasks/{task_id}")

    def events(self, last_n: int = 50) -> list[dict[str, Any]]:
        """Poll the events endpoint for recent events."""
        resp = self.get("/events")
        return resp.get("events", [])[:last_n]

    def get_budget(self) -> dict[str, Any]:
        """Get budget status."""
        return self.get("/budget")

    def ping(self) -> bool:
        try:
            self.get("/metrics")
            return True
        except Exception:  # noqa: BLE001
            return False

    def __repr__(self) -> str:
        return f"AmiagiClient(base_url={self._base_url!r})"
