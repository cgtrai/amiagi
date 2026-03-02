"""Phase 10 — Lightweight REST API server (infrastructure).

Exposes agent, task and workflow operations over HTTP.
Designed to run in a background thread alongside the TUI.
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import Any, Callable


# ---- types ----

RouteHandler = Callable[[dict[str, Any]], tuple[int, dict[str, Any]]]


@dataclass
class Route:
    method: str  # GET / POST / DELETE
    path: str  # e.g. "/agents"
    handler: RouteHandler


# ---- request context ----

@dataclass
class RequestContext:
    method: str
    path: str
    body: dict[str, Any] = field(default_factory=dict)
    headers: dict[str, str] = field(default_factory=dict)


# ---- server ----

class RESTServer:
    """Minimal REST server with bearer-token auth and pluggable routes."""

    def __init__(
        self,
        *,
        host: str = "127.0.0.1",
        port: int = 8090,
        bearer_token: str = "",
    ) -> None:
        self._host = host
        self._port = port
        self._bearer_token = bearer_token
        self._routes: list[Route] = []
        self._server: HTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    # ---- route registration ----

    def add_route(self, method: str, path: str, handler: RouteHandler) -> None:
        self._routes.append(Route(method=method.upper(), path=path, handler=handler))

    def _find_route(self, method: str, path: str) -> Route | None:
        # Exact match first
        for route in self._routes:
            if route.method == method.upper() and route.path == path:
                return route
        # Parametric match (e.g. /tasks/{id})
        for route in self._routes:
            if route.method != method.upper():
                continue
            if "{" not in route.path:
                continue
            route_parts = route.path.strip("/").split("/")
            path_parts = path.strip("/").split("/")
            if len(route_parts) != len(path_parts):
                continue
            match = True
            for rp, pp in zip(route_parts, path_parts):
                if rp.startswith("{") and rp.endswith("}"):
                    continue  # parameter segment — matches anything
                if rp != pp:
                    match = False
                    break
            if match:
                return route
        return None

    def extract_path_params(self, route_path: str, actual_path: str) -> dict[str, str]:
        """Extract named parameters from a parametric route path."""
        params: dict[str, str] = {}
        route_parts = route_path.strip("/").split("/")
        path_parts = actual_path.strip("/").split("/")
        for rp, pp in zip(route_parts, path_parts):
            if rp.startswith("{") and rp.endswith("}"):
                params[rp[1:-1]] = pp
        return params

    # ---- auth ----

    def _check_auth(self, auth_header: str | None) -> bool:
        if not self._bearer_token:
            return True  # no token configured → open access
        if auth_header is None:
            return False
        return auth_header == f"Bearer {self._bearer_token}"

    # ---- lifecycle ----

    def start(self) -> None:
        with self._lock:
            if self._server is not None:
                return
            server_ref = self

            class _Handler(BaseHTTPRequestHandler):
                def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
                    pass  # suppress console spam

                def _dispatch(self) -> None:
                    if not server_ref._check_auth(self.headers.get("Authorization")):
                        self._respond(401, {"error": "Unauthorized"})
                        return

                    route = server_ref._find_route(self.command, self.path)
                    if route is None:
                        self._respond(404, {"error": "Not found"})
                        return

                    body: dict[str, Any] = {}
                    content_length = int(self.headers.get("Content-Length", 0))
                    if content_length > 0:
                        raw = self.rfile.read(content_length)
                        try:
                            body = json.loads(raw.decode("utf-8"))
                        except (json.JSONDecodeError, UnicodeDecodeError):
                            self._respond(400, {"error": "Invalid JSON body"})
                            return

                    # Inject path parameters into body
                    if "{" in route.path:
                        body["_path_params"] = server_ref.extract_path_params(
                            route.path, self.path
                        )
                    body["_path"] = self.path

                    try:
                        status, payload = route.handler(body)
                    except Exception as exc:  # noqa: BLE001
                        self._respond(500, {"error": str(exc)})
                        return

                    self._respond(status, payload)

                def _respond(self, status: int, payload: dict[str, Any]) -> None:
                    self.send_response(status)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps(payload, ensure_ascii=False).encode("utf-8"))

                def do_GET(self) -> None:  # noqa: N802
                    self._dispatch()

                def do_POST(self) -> None:  # noqa: N802
                    self._dispatch()

                def do_DELETE(self) -> None:  # noqa: N802
                    self._dispatch()

            self._server = HTTPServer((self._host, self._port), _Handler)
            self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
            self._thread.start()

    def stop(self) -> None:
        with self._lock:
            if self._server is not None:
                self._server.shutdown()
                self._server = None
                self._thread = None

    @property
    def is_running(self) -> bool:
        return self._server is not None

    @property
    def address(self) -> str:
        return f"http://{self._host}:{self._port}"

    def list_routes(self) -> list[dict[str, str]]:
        return [{"method": r.method, "path": r.path} for r in self._routes]

    def to_dict(self) -> dict[str, Any]:
        return {
            "host": self._host,
            "port": self._port,
            "is_running": self.is_running,
            "routes": self.list_routes(),
        }

    # ---- domain route wiring ----

    def wire_domain_routes(
        self,
        *,
        agent_registry: Any = None,
        task_queue: Any = None,
        workflow_engine: Any = None,
        metrics_collector: Any = None,
        budget_manager: Any = None,
    ) -> int:
        """Register standard domain routes. Returns the number of routes added."""
        count = 0

        if agent_registry is not None:
            def _list_agents(body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
                agents = [a.to_dict() for a in agent_registry.list_all()]
                return 200, {"agents": agents}

            def _create_agent(body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
                return 201, {"status": "created", "data": body}

            self.add_route("GET", "/agents", _list_agents)
            self.add_route("POST", "/agents", _create_agent)
            count += 2

        if task_queue is not None:
            def _list_tasks(body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
                stats = task_queue.stats()
                return 200, {"tasks": stats}

            def _create_task(body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
                return 201, {"status": "queued", "data": body}

            def _get_task(body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
                params = body.get("_path_params", {})
                task_id = params.get("id", "")
                task = task_queue.get(task_id)
                if task is None:
                    return 404, {"error": f"Task {task_id} not found"}
                return 200, {"task": task.to_dict() if hasattr(task, "to_dict") else {"id": task_id}}

            self.add_route("GET", "/tasks", _list_tasks)
            self.add_route("POST", "/tasks", _create_task)
            self.add_route("GET", "/tasks/{id}", _get_task)
            count += 3

        if workflow_engine is not None:
            def _run_workflow(body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
                wf_id = body.get("workflow_id", "")
                return 200, {"status": "started", "workflow_id": wf_id}

            self.add_route("POST", "/workflows/run", _run_workflow)
            count += 1

        if metrics_collector is not None:
            def _get_metrics(body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
                summary = metrics_collector.summary()
                return 200, summary

            self.add_route("GET", "/metrics", _get_metrics)
            count += 1

        if budget_manager is not None:
            def _get_budget(body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
                return 200, {"budgets": budget_manager.summary()}

            self.add_route("GET", "/budget", _get_budget)
            count += 1

        # SSE-like events polling endpoint (JSON, not true SSE)
        _events_buffer: list[dict[str, Any]] = []

        def _get_events(body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
            events = list(_events_buffer[-50:])
            return 200, {"events": events}

        self.add_route("GET", "/events", _get_events)
        self._events_buffer = _events_buffer
        count += 1

        return count

    def push_event(self, event: dict[str, Any]) -> None:
        """Push an event to the events buffer for polling."""
        buf = getattr(self, "_events_buffer", None)
        if buf is not None:
            buf.append(event)
            if len(buf) > 200:
                del buf[:100]
