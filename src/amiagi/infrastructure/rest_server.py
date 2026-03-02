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
        for route in self._routes:
            if route.method == method.upper() and route.path == path:
                return route
        return None

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
