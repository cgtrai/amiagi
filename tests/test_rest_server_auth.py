"""Tests for REST server — API keys, scopes, rate limiting, SSE routes."""

from __future__ import annotations

from typing import Any

from amiagi.infrastructure.rest_server import APIKey, RESTServer


# ====================================================================
# APIKey dataclass
# ====================================================================


def test_api_key_allows_scope_unrestricted() -> None:
    ak = APIKey(key="k1", scopes=[])
    assert ak.allows_scope("agents") is True
    assert ak.allows_scope("tasks") is True


def test_api_key_allows_scope_restricted() -> None:
    ak = APIKey(key="k1", scopes=["agents", "metrics"])
    assert ak.allows_scope("agents") is True
    assert ak.allows_scope("tasks") is False
    assert ak.allows_scope("metrics") is True


def test_api_key_rate_limit_within() -> None:
    ak = APIKey(key="k1", max_requests_per_minute=5)
    for _ in range(5):
        assert ak.check_rate_limit() is True
        ak.record_request()


def test_api_key_rate_limit_exceeded() -> None:
    ak = APIKey(key="k1", max_requests_per_minute=2)
    ak.record_request()
    ak.record_request()
    assert ak.check_rate_limit() is False


def test_api_key_rate_limit_unlimited() -> None:
    ak = APIKey(key="k1", max_requests_per_minute=0)
    for _ in range(100):
        assert ak.check_rate_limit() is True
        ak.record_request()


# ====================================================================
# RESTServer key management
# ====================================================================


def test_add_and_list_api_keys() -> None:
    srv = RESTServer(bearer_token="tok")
    srv.add_api_key(APIKey(key="k1", name="test-key", scopes=["agents"]))
    keys = srv.list_api_keys()
    assert len(keys) == 1
    assert keys[0]["name"] == "test-key"
    assert keys[0]["scopes"] == ["agents"]


def test_remove_api_key() -> None:
    srv = RESTServer()
    srv.add_api_key(APIKey(key="k1", name="removable"))
    assert srv.remove_api_key("k1") is True
    assert srv.remove_api_key("k1") is False  # already removed


def test_rotate_api_key() -> None:
    srv = RESTServer()
    srv.add_api_key(APIKey(key="old", name="rotatable", scopes=["agents"]))
    assert srv.rotate_api_key("old", "new") is True
    assert srv.remove_api_key("old") is False
    keys = srv.list_api_keys()
    assert len(keys) == 1
    assert keys[0]["name"] == "rotatable"


def test_rotate_nonexistent_key() -> None:
    srv = RESTServer()
    assert srv.rotate_api_key("no-exist", "new") is False


# ====================================================================
# Auth checks
# ====================================================================


def test_check_auth_open_access() -> None:
    srv = RESTServer()  # no token, no keys
    ok, ak = srv._check_auth(None)
    assert ok is True
    assert ak is None


def test_check_auth_bearer_token() -> None:
    srv = RESTServer(bearer_token="secret")
    ok, _ = srv._check_auth("Bearer secret")
    assert ok is True
    ok2, _ = srv._check_auth("Bearer wrong")
    assert ok2 is False


def test_check_auth_api_key() -> None:
    srv = RESTServer(bearer_token="secret")
    srv.add_api_key(APIKey(key="mykey", name="test"))
    ok, ak = srv._check_auth("Bearer mykey")
    assert ok is True
    assert ak is not None
    assert ak.name == "test"


def test_check_auth_no_header() -> None:
    srv = RESTServer(bearer_token="secret")
    ok, _ = srv._check_auth(None)
    assert ok is False


# ====================================================================
# Scope and rate-limit checks
# ====================================================================


def test_scope_check_passes_for_bearer() -> None:
    srv = RESTServer()
    ok, err = srv._check_scope_and_rate(None, "/agents")
    assert ok is True
    assert err == ""


def test_scope_check_blocked() -> None:
    srv = RESTServer()
    ak = APIKey(key="k1", name="limited", scopes=["metrics"])
    ok, err = srv._check_scope_and_rate(ak, "/agents")
    assert ok is False
    assert "scope" in err.lower()


def test_rate_limit_check_blocked() -> None:
    srv = RESTServer()
    ak = APIKey(key="k1", name="limited", max_requests_per_minute=1)
    ak.record_request()
    ok, err = srv._check_scope_and_rate(ak, "/agents")
    assert ok is False
    assert "rate limit" in err.lower()


# ====================================================================
# SSE route registration
# ====================================================================


def test_add_sse_route() -> None:
    srv = RESTServer()
    called = [False]

    def _handler(body: dict[str, Any]) -> Any:
        called[0] = True
        yield {"event": "test"}

    srv.add_sse_route("/events/stream", _handler)
    assert "/events/stream" in srv._sse_routes


# ====================================================================
# wire_domain_routes includes SSE
# ====================================================================


def test_wire_domain_routes_adds_sse() -> None:
    srv = RESTServer()
    count = srv.wire_domain_routes()
    # Should include /events (JSON polling) and /events/stream (SSE)
    assert count >= 2
    assert "/events/stream" in srv._sse_routes


def test_push_event_buffered() -> None:
    srv = RESTServer()
    srv.wire_domain_routes()
    srv.push_event({"type": "test", "data": 1})
    srv.push_event({"type": "test", "data": 2})
    buf = getattr(srv, "_events_buffer", None)
    assert buf is not None
    assert len(buf) == 2
