"""Tests for WebhookDispatcher (Phase 10)."""

from __future__ import annotations

import json
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import Any

import pytest

from amiagi.infrastructure.webhook_dispatcher import (
    DeliveryResult,
    WebhookDispatcher,
    WebhookTarget,
)


@pytest.fixture()
def dispatcher() -> WebhookDispatcher:
    return WebhookDispatcher()


class _SinkHandler(BaseHTTPRequestHandler):
    """HTTP handler that always responds 200 and records payloads."""

    received: list[dict[str, Any]] = []

    def log_message(self, format: str, *args: Any) -> None:
        pass

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw)
        except Exception:
            payload = {}
        _SinkHandler.received.append(payload)
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'{"ok":true}')


@pytest.fixture()
def sink_server():
    _SinkHandler.received = []
    import socket
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server = HTTPServer(("127.0.0.1", port), _SinkHandler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{port}"
    server.shutdown()


class TestWebhookTarget:
    def test_roundtrip(self) -> None:
        t = WebhookTarget(url="http://x", events=["task_done"], secret="s")
        d = t.to_dict()
        t2 = WebhookTarget.from_dict(d)
        assert t2.url == t.url
        assert t2.events == t.events

    def test_defaults(self) -> None:
        t = WebhookTarget(url="http://y")
        assert t.max_retries == 3
        assert t.events == []


class TestWebhookDispatcher:
    def test_register_and_list(self, dispatcher: WebhookDispatcher) -> None:
        dispatcher.register(WebhookTarget(url="http://a"))
        assert len(dispatcher.list_targets()) == 1

    def test_unregister(self, dispatcher: WebhookDispatcher) -> None:
        dispatcher.register(WebhookTarget(url="http://a"))
        assert dispatcher.unregister("http://a") is True
        assert dispatcher.unregister("http://a") is False
        assert len(dispatcher.list_targets()) == 0

    def test_dispatch_success(self, dispatcher: WebhookDispatcher, sink_server: str) -> None:
        dispatcher.register(WebhookTarget(url=sink_server, max_retries=1))
        results = dispatcher.dispatch("test_event", {"k": "v"})
        assert len(results) == 1
        assert results[0].success is True
        assert results[0].event == "test_event"
        assert len(_SinkHandler.received) == 1
        assert _SinkHandler.received[0]["event"] == "test_event"

    def test_dispatch_filters_events(self, dispatcher: WebhookDispatcher, sink_server: str) -> None:
        dispatcher.register(WebhookTarget(url=sink_server, events=["only_this"]))
        results = dispatcher.dispatch("other_event", {})
        assert len(results) == 0

    def test_dispatch_all_events(self, dispatcher: WebhookDispatcher, sink_server: str) -> None:
        dispatcher.register(WebhookTarget(url=sink_server, events=[]))
        results = dispatcher.dispatch("any_event", {})
        assert len(results) == 1
        assert results[0].success is True

    def test_dispatch_failure_retry(self, dispatcher: WebhookDispatcher) -> None:
        dispatcher.register(
            WebhookTarget(url="http://127.0.0.1:1", max_retries=2, backoff_seconds=0.01)
        )
        results = dispatcher.dispatch("fail_event", {})
        assert len(results) == 1
        assert results[0].success is False
        assert results[0].attempts == 2

    def test_history(self, dispatcher: WebhookDispatcher, sink_server: str) -> None:
        dispatcher.register(WebhookTarget(url=sink_server, max_retries=1))
        dispatcher.dispatch("e1", {})
        dispatcher.dispatch("e2", {})
        h = dispatcher.history()
        assert len(h) == 2
        assert h[0].event == "e2"  # newest first

    def test_clear_history(self, dispatcher: WebhookDispatcher) -> None:
        dispatcher._history.append(
            DeliveryResult(url="x", event="e", success=True)
        )
        dispatcher.clear_history()
        assert len(dispatcher.history()) == 0

    def test_to_dict(self, dispatcher: WebhookDispatcher) -> None:
        dispatcher.register(WebhookTarget(url="http://a"))
        d = dispatcher.to_dict()
        assert len(d["targets"]) == 1
        assert d["history_count"] == 0

    def test_secret_header(self, dispatcher: WebhookDispatcher, sink_server: str) -> None:
        dispatcher.register(WebhookTarget(url=sink_server, secret="mysecret", max_retries=1))
        dispatcher.dispatch("ev", {})
        assert len(_SinkHandler.received) == 1
