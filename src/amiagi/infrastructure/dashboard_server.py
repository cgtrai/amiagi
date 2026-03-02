"""DashboardServer — lightweight HTTP + SSE server for the monitoring dashboard."""

from __future__ import annotations

import json
import threading
import time
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from typing import Any, Callable

from amiagi.application.agent_registry import AgentRegistry
from amiagi.application.alert_manager import AlertManager
from amiagi.application.task_queue import TaskQueue
from amiagi.infrastructure.metrics_collector import MetricsCollector
from amiagi.infrastructure.session_replay import SessionReplay


class _DashboardHandler(BaseHTTPRequestHandler):
    """Handles HTTP requests for the dashboard API and serves static files."""

    # Injected by DashboardServer
    _registry: AgentRegistry | None = None
    _task_queue: TaskQueue | None = None
    _metrics: MetricsCollector | None = None
    _alert_manager: AlertManager | None = None
    _session_replay: SessionReplay | None = None
    _static_dir: Path | None = None
    _sse_subscribers: list[Any] = []
    _sse_lock: threading.Lock = threading.Lock()

    def log_message(self, format: str, *args: Any) -> None:
        """Suppress default stderr logging."""
        pass

    def do_GET(self) -> None:
        path = self.path.split("?")[0]

        # ---- API endpoints ----
        if path == "/api/agents":
            self._json_response(self._get_agents())
        elif path == "/api/tasks":
            self._json_response(self._get_tasks())
        elif path == "/api/metrics":
            self._json_response(self._get_metrics())
        elif path == "/api/alerts":
            self._json_response(self._get_alerts())
        elif path == "/api/events":
            self._handle_sse()
        elif path == "/api/replay":
            self._json_response(self._get_replay())
        elif path == "/api/status":
            self._json_response(self._get_status())
        # ---- static files ----
        elif path == "/" or path == "/index.html":
            self._serve_static("index.html", "text/html")
        elif path.startswith("/static/"):
            filename = path[len("/static/"):]
            content_type = "text/css" if filename.endswith(".css") else "application/javascript"
            self._serve_static(filename, content_type)
        else:
            self.send_error(404)

    # ---- API data providers ----

    def _get_agents(self) -> list[dict[str, Any]]:
        if self._registry is None:
            return []
        return [
            {
                "agent_id": a.agent_id,
                "name": a.name,
                "role": a.role.value,
                "state": a.state.value,
                "model_backend": a.model_backend,
                "model_name": a.model_name,
                "skills": a.skills,
                "created_at": a.created_at.isoformat(),
            }
            for a in self._registry.list_all()
        ]

    def _get_tasks(self) -> list[dict[str, Any]]:
        if self._task_queue is None:
            return []
        return [
            {
                "task_id": t.task_id,
                "title": t.title,
                "status": t.status.value,
                "priority": t.priority.value,
                "assigned_agent_id": t.assigned_agent_id,
                "parent_task_id": t.parent_task_id,
                "created_at": t.created_at.isoformat(),
            }
            for t in self._task_queue.list_all()
        ]

    def _get_metrics(self) -> dict[str, Any]:
        if self._metrics is None:
            return {"metrics": {}}
        return {"metrics": self._metrics.summary()}

    def _get_alerts(self) -> list[dict[str, Any]]:
        if self._alert_manager is None:
            return []
        return [
            {
                "rule_name": a.rule_name,
                "message": a.message,
                "severity": a.severity.value,
                "timestamp": a.timestamp,
            }
            for a in self._alert_manager.recent_alerts()
        ]

    def _get_replay(self) -> list[dict[str, Any]]:
        if self._session_replay is None:
            return []
        events = self._session_replay.load_session(limit=200)
        return [
            {
                "timestamp": e.timestamp,
                "source": e.source,
                "event_type": e.event_type,
            }
            for e in events
        ]

    def _get_status(self) -> dict[str, Any]:
        agent_count = len(self._registry) if self._registry else 0
        task_stats = self._task_queue.stats() if self._task_queue else {}
        return {
            "status": "running",
            "agents": agent_count,
            "tasks": task_stats,
        }

    # ---- SSE ----

    def _handle_sse(self) -> None:
        """Server-Sent Events endpoint for live event streaming."""
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()

        with self._sse_lock:
            self._sse_subscribers.append(self.wfile)

        try:
            # Keep connection alive — SSE heartbeat
            while True:
                time.sleep(15)
                try:
                    self.wfile.write(b": heartbeat\n\n")
                    self.wfile.flush()
                except Exception:
                    break
        finally:
            with self._sse_lock:
                if self.wfile in self._sse_subscribers:
                    self._sse_subscribers.remove(self.wfile)

    # ---- helpers ----

    def _json_response(self, data: Any) -> None:
        body = json.dumps(data, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_static(self, filename: str, content_type: str) -> None:
        if self._static_dir is None:
            self.send_error(404)
            return
        filepath = self._static_dir / filename
        if not filepath.exists():
            self.send_error(404)
            return
        body = filepath.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", f"{content_type}; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class DashboardServer:
    """Manages the lifecycle of the dashboard HTTP server.

    Usage::

        server = DashboardServer(registry=..., task_queue=..., ...)
        server.start(port=8080)
        # ... later ...
        server.stop()
    """

    def __init__(
        self,
        *,
        registry: AgentRegistry | None = None,
        task_queue: TaskQueue | None = None,
        metrics_collector: MetricsCollector | None = None,
        alert_manager: AlertManager | None = None,
        session_replay: SessionReplay | None = None,
        static_dir: Path | None = None,
    ) -> None:
        self._registry = registry
        self._task_queue = task_queue
        self._metrics = metrics_collector
        self._alert_manager = alert_manager
        self._session_replay = session_replay
        self._static_dir = static_dir
        self._server: HTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def running(self) -> bool:
        return self._server is not None

    @property
    def port(self) -> int | None:
        if self._server is not None:
            return self._server.server_address[1]
        return None

    def start(self, port: int = 8080) -> None:
        """Start the dashboard server in a background thread."""
        if self._server is not None:
            return

        # Inject dependencies into handler class
        _DashboardHandler._registry = self._registry
        _DashboardHandler._task_queue = self._task_queue
        _DashboardHandler._metrics = self._metrics
        _DashboardHandler._alert_manager = self._alert_manager
        _DashboardHandler._session_replay = self._session_replay
        _DashboardHandler._static_dir = self._static_dir

        self._server = HTTPServer(("0.0.0.0", port), _DashboardHandler)
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            name="dashboard-server",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        """Shutdown the dashboard server."""
        if self._server is not None:
            self._server.shutdown()
            self._server = None
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None

    def broadcast_event(self, event_type: str, data: dict[str, Any]) -> None:
        """Push an SSE event to all connected subscribers."""
        payload = f"event: {event_type}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"
        encoded = payload.encode("utf-8")
        with _DashboardHandler._sse_lock:
            dead: list[Any] = []
            for subscriber in _DashboardHandler._sse_subscribers:
                try:
                    subscriber.write(encoded)
                    subscriber.flush()
                except Exception:
                    dead.append(subscriber)
            for d in dead:
                _DashboardHandler._sse_subscribers.remove(d)
