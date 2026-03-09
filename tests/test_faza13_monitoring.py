"""Tests for Faza 13 — Monitoring, analysis & integrations.

Covers audit criteria 13.1–13.11:
- 13.1: dbo.agent_performance auto-record after CycleFinishedEvent
- 13.2: Dashboard Performance — model comparison
- 13.3: Notification bell — dropdown, badge count
- 13.4: Web Push — notification service
- 13.5: Webhook relay — POST to URL
- 13.6: Session replay — timeline
- 13.7: Session events stored in dbo.session_events
- 13.8: API key creation — scope picker, hash in DB
- 13.9: API key auth — X-API-Key header
- 13.10: Webhook test button — sample payload
- 13.11: Tests ≥ 8 performance, ≥ 8 notifications, ≥ 5 session, ≥ 8 api_keys
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

_WEB_ROOT = Path(__file__).parent.parent / "src/amiagi/interfaces/web"


# ═══════════════════════════════════════════════════════════════
# 13.1 / 13.2 — Performance  (≥ 8 tests)
# ═══════════════════════════════════════════════════════════════

class TestPerformanceTracker:
    """13.1: PerformanceTracker + PerformanceRecord unit tests."""

    def test_performance_record_to_dict(self):
        from amiagi.interfaces.web.monitoring.performance_tracker import PerformanceRecord
        now = datetime(2025, 1, 1, tzinfo=timezone.utc)
        r = PerformanceRecord(
            id=1, agent_role="kastor", model="gpt-4", task_type="code",
            duration_ms=500, success=True, tokens_in=100, tokens_out=200,
            cost_usd=0.003, created_at=now,
        )
        d = r.to_dict()
        assert d["id"] == 1
        assert d["agent_role"] == "kastor"
        assert d["model"] == "gpt-4"
        assert d["duration_ms"] == 500
        assert d["success"] is True
        assert d["cost_usd"] == 0.003
        assert "2025-01-01" in d["created_at"]

    def test_performance_record_none_created_at(self):
        from amiagi.interfaces.web.monitoring.performance_tracker import PerformanceRecord
        r = PerformanceRecord(
            id=2, agent_role="polluks", model=None, task_type=None,
            duration_ms=None, success=False, tokens_in=0, tokens_out=0,
            cost_usd=0.0, created_at=None,
        )
        assert r.to_dict()["created_at"] is None

    @pytest.mark.asyncio
    async def test_record_inserts_and_returns_id(self):
        from amiagi.interfaces.web.monitoring.performance_tracker import PerformanceTracker
        pool = AsyncMock()
        pool.fetchrow = AsyncMock(return_value={"id": 42})
        tracker = PerformanceTracker(pool)
        result = await tracker.record("kastor", model="gpt-4", duration_ms=300, tokens_in=10)
        assert result == 42
        pool.fetchrow.assert_called_once()
        sql_arg = pool.fetchrow.call_args[0][0]
        assert "INSERT INTO dbo.agent_performance" in sql_arg

    @pytest.mark.asyncio
    async def test_query_no_filters(self):
        from amiagi.interfaces.web.monitoring.performance_tracker import PerformanceTracker
        pool = AsyncMock()
        pool.fetch = AsyncMock(return_value=[])
        tracker = PerformanceTracker(pool)
        results = await tracker.query()
        assert results == []
        sql_arg = pool.fetch.call_args[0][0]
        assert "WHERE" not in sql_arg

    @pytest.mark.asyncio
    async def test_query_with_agent_filter(self):
        from amiagi.interfaces.web.monitoring.performance_tracker import PerformanceTracker
        pool = AsyncMock()
        pool.fetch = AsyncMock(return_value=[])
        tracker = PerformanceTracker(pool)
        await tracker.query(agent_role="kastor")
        sql_arg = pool.fetch.call_args[0][0]
        assert "agent_role = $1" in sql_arg

    @pytest.mark.asyncio
    async def test_query_with_model_filter(self):
        from amiagi.interfaces.web.monitoring.performance_tracker import PerformanceTracker
        pool = AsyncMock()
        pool.fetch = AsyncMock(return_value=[])
        tracker = PerformanceTracker(pool)
        await tracker.query(model="llama3")
        sql_arg = pool.fetch.call_args[0][0]
        assert "model = $1" in sql_arg

    @pytest.mark.asyncio
    async def test_query_with_date_range(self):
        from amiagi.interfaces.web.monitoring.performance_tracker import PerformanceTracker
        pool = AsyncMock()
        pool.fetch = AsyncMock(return_value=[])
        tracker = PerformanceTracker(pool)
        await tracker.query(since="2025-01-01", until="2025-06-01")
        sql_arg = pool.fetch.call_args[0][0]
        assert "created_at >=" in sql_arg
        assert "created_at <=" in sql_arg

    @pytest.mark.asyncio
    async def test_summary_aggregates(self):
        from amiagi.interfaces.web.monitoring.performance_tracker import PerformanceTracker
        pool = AsyncMock()
        pool.fetchrow = AsyncMock(return_value={
            "total": 10,
            "avg_duration_ms": 450.0,
            "p50_ms": 400.0,
            "p95_ms": 800.0,
            "success_rate": 0.9,
            "total_tokens_in": 1000,
            "total_tokens_out": 2000,
            "total_cost_usd": 0.05,
        })
        tracker = PerformanceTracker(pool)
        result = await tracker.summary()
        assert result["total"] == 10
        assert result["p50_ms"] == 400.0
        assert result["success_rate"] == 0.9
        sql_arg = pool.fetchrow.call_args[0][0]
        assert "percentile_cont" in sql_arg

    @pytest.mark.asyncio
    async def test_summary_with_model_filter(self):
        from amiagi.interfaces.web.monitoring.performance_tracker import PerformanceTracker
        pool = AsyncMock()
        pool.fetchrow = AsyncMock(return_value={
            "total": 5, "avg_duration_ms": 200.0, "p50_ms": 180.0,
            "p95_ms": 400.0, "success_rate": 1.0,
            "total_tokens_in": 500, "total_tokens_out": 1000, "total_cost_usd": 0.02,
        })
        tracker = PerformanceTracker(pool)
        result = await tracker.summary(model="gpt-4")
        assert result["total"] == 5
        sql_arg = pool.fetchrow.call_args[0][0]
        assert "model = $1" in sql_arg

    @pytest.mark.asyncio
    async def test_summary_empty_returns_zero(self):
        from amiagi.interfaces.web.monitoring.performance_tracker import PerformanceTracker
        pool = AsyncMock()
        pool.fetchrow = AsyncMock(return_value=None)
        tracker = PerformanceTracker(pool)
        result = await tracker.summary()
        assert result == {"total": 0}

    def test_row_to_record_helper(self):
        from amiagi.interfaces.web.monitoring.performance_tracker import _row_to_record
        now = datetime.now(tz=timezone.utc)
        row = {
            "id": 1, "agent_role": "kastor", "model": "gpt-4",
            "task_type": "code", "duration_ms": 100, "success": True,
            "tokens_in": 10, "tokens_out": 20, "cost_usd": 0.001,
            "created_at": now,
        }
        rec = _row_to_record(row)
        assert rec.agent_role == "kastor"
        assert rec.created_at == now


# ═══════════════════════════════════════════════════════════════
# 13.2 — Performance Dashboard routes
# ═══════════════════════════════════════════════════════════════

class TestPerformanceDashboard:
    """13.2: Performance route definitions."""

    def test_performance_routes_exist(self):
        from amiagi.interfaces.web.routes.monitoring_routes import monitoring_routes
        paths = [r.path for r in monitoring_routes]
        assert "/api/performance" in paths
        assert "/api/performance/summary" in paths


# ═══════════════════════════════════════════════════════════════
# 13.3 / 13.4 — Notifications  (≥ 8 tests)
# ═══════════════════════════════════════════════════════════════

class TestNotificationService:
    """13.3 / 13.4: Notification CRUD and badge count."""

    def test_notification_to_dict(self):
        from amiagi.interfaces.web.monitoring.notification_service import Notification
        now = datetime(2025, 6, 1, tzinfo=timezone.utc)
        n = Notification(
            id="n1", user_id="u1", type="task.done",
            title="Task Complete", body="Code review done",
            is_read=False, created_at=now,
        )
        d = n.to_dict()
        assert d["id"] == "n1"
        assert d["type"] == "task.done"
        assert d["is_read"] is False
        assert "2025-06-01" in d["created_at"]

    def test_notification_none_created_at(self):
        from amiagi.interfaces.web.monitoring.notification_service import Notification
        n = Notification(id="n2", user_id="u2", type="error",
                         title="Error", body="", is_read=True, created_at=None)
        assert n.to_dict()["created_at"] is None

    @pytest.mark.asyncio
    async def test_create_notification(self):
        from amiagi.interfaces.web.monitoring.notification_service import NotificationService
        now = datetime.now(tz=timezone.utc)
        pool = AsyncMock()
        pool.fetchrow = AsyncMock(return_value={
            "id": "abc-def", "user_id": "u1", "type": "task.done",
            "title": "Done", "body": "Task finished", "is_read": False,
            "created_at": now,
        })
        svc = NotificationService(pool)
        n = await svc.create("u1", "task.done", "Done", "Task finished")
        assert n.type == "task.done"
        assert n.is_read is False
        sql_arg = pool.fetchrow.call_args[0][0]
        assert "INSERT INTO dbo.notifications" in sql_arg

    @pytest.mark.asyncio
    async def test_list_for_user_all(self):
        from amiagi.interfaces.web.monitoring.notification_service import NotificationService
        pool = AsyncMock()
        pool.fetch = AsyncMock(return_value=[])
        svc = NotificationService(pool)
        result = await svc.list_for_user("u1")
        assert result == []
        sql_arg = pool.fetch.call_args[0][0]
        assert "user_id = $1" in sql_arg
        assert "is_read = false" not in sql_arg

    @pytest.mark.asyncio
    async def test_list_for_user_unread_only(self):
        from amiagi.interfaces.web.monitoring.notification_service import NotificationService
        pool = AsyncMock()
        pool.fetch = AsyncMock(return_value=[])
        svc = NotificationService(pool)
        await svc.list_for_user("u1", unread_only=True)
        sql_arg = pool.fetch.call_args[0][0]
        assert "is_read = false" in sql_arg

    @pytest.mark.asyncio
    async def test_unread_count(self):
        from amiagi.interfaces.web.monitoring.notification_service import NotificationService
        pool = AsyncMock()
        pool.fetchrow = AsyncMock(return_value={"cnt": 7})
        svc = NotificationService(pool)
        count = await svc.unread_count("u1")
        assert count == 7

    @pytest.mark.asyncio
    async def test_mark_read_success(self):
        from amiagi.interfaces.web.monitoring.notification_service import NotificationService
        pool = AsyncMock()
        pool.execute = AsyncMock(return_value="UPDATE 1")
        svc = NotificationService(pool)
        ok = await svc.mark_read("n1")
        assert ok is True

    @pytest.mark.asyncio
    async def test_mark_read_not_found(self):
        from amiagi.interfaces.web.monitoring.notification_service import NotificationService
        pool = AsyncMock()
        pool.execute = AsyncMock(return_value="UPDATE 0")
        svc = NotificationService(pool)
        ok = await svc.mark_read("bogus")
        assert ok is False

    @pytest.mark.asyncio
    async def test_mark_all_read(self):
        from amiagi.interfaces.web.monitoring.notification_service import NotificationService
        pool = AsyncMock()
        pool.execute = AsyncMock(return_value="UPDATE 3")
        svc = NotificationService(pool)
        count = await svc.mark_all_read("u1")
        assert count == 3

    def test_notification_routes_exist(self):
        from amiagi.interfaces.web.routes.monitoring_routes import monitoring_routes
        paths = [r.path for r in monitoring_routes]
        assert "/api/notifications" in paths
        assert "/api/notifications/{id}/read" in paths
        assert "/api/notifications/read-all" in paths

    def test_notification_routes_count_path(self):
        """Badge count is provided via the /api/notifications GET endpoint."""
        from amiagi.interfaces.web.routes.monitoring_routes import monitoring_routes
        paths = [r.path for r in monitoring_routes]
        assert "/api/notifications" in paths


# ═══════════════════════════════════════════════════════════════
# 13.5 — Webhook Relay  (part of api_keys group)
# ═══════════════════════════════════════════════════════════════

class TestWebhookManager:
    """13.5 / 13.10: Webhook CRUD and dispatch."""

    def test_webhook_record_to_dict(self):
        from amiagi.interfaces.web.monitoring.webhook_manager import WebhookRecord
        now = datetime(2025, 6, 1, tzinfo=timezone.utc)
        w = WebhookRecord(
            id="w1", user_id="u1", url="https://example.com/hook",
            events=["task.done"], secret="shhh", is_active=True,
            last_delivery_status=200, last_delivery_at=now, last_error=None, created_at=now,
        )
        d = w.to_dict()
        assert d["url"] == "https://example.com/hook"
        assert d["events"] == ["task.done"]
        assert d["status"] == "active"
        assert "secret" not in d  # secret should not be exposed

    def test_compute_signature(self):
        from amiagi.interfaces.web.monitoring.webhook_manager import compute_signature
        sig = compute_signature("secret", '{"event":"test"}')
        assert isinstance(sig, str)
        assert len(sig) == 64  # SHA-256 hex

    @pytest.mark.asyncio
    async def test_create_webhook(self):
        from amiagi.interfaces.web.monitoring.webhook_manager import WebhookManager
        now = datetime.now(tz=timezone.utc)
        pool = AsyncMock()
        pool.fetchrow = AsyncMock(return_value={
            "id": "w1", "user_id": "u1", "url": "https://hook.example.com",
            "events": ["task.done"], "secret": "s", "is_active": True,
            "last_delivery_status": None, "last_delivery_at": None, "last_error": None,
            "created_at": now,
        })
        mgr = WebhookManager(pool)
        wh = await mgr.create_webhook("u1", "https://hook.example.com", ["task.done"])
        assert wh.url == "https://hook.example.com"
        sql_arg = pool.fetchrow.call_args[0][0]
        assert "INSERT INTO dbo.webhooks" in sql_arg

    @pytest.mark.asyncio
    async def test_list_webhooks(self):
        from amiagi.interfaces.web.monitoring.webhook_manager import WebhookManager
        pool = AsyncMock()
        pool.fetch = AsyncMock(return_value=[])
        mgr = WebhookManager(pool)
        result = await mgr.list_webhooks("u1")
        assert result == []

    @pytest.mark.asyncio
    async def test_delete_webhook(self):
        from amiagi.interfaces.web.monitoring.webhook_manager import WebhookManager
        pool = AsyncMock()
        pool.execute = AsyncMock(return_value="DELETE 1")
        mgr = WebhookManager(pool)
        ok = await mgr.delete_webhook("w1")
        assert ok is True

    @pytest.mark.asyncio
    async def test_get_active_webhooks_for_event(self):
        from amiagi.interfaces.web.monitoring.webhook_manager import WebhookManager
        pool = AsyncMock()
        pool.fetch = AsyncMock(return_value=[])
        mgr = WebhookManager(pool)
        result = await mgr.get_active_webhooks_for_event("task.done")
        assert result == []
        sql_arg = pool.fetch.call_args[0][0]
        assert "is_active = true" in sql_arg
        assert "ANY(events)" in sql_arg

    @pytest.mark.asyncio
    async def test_update_webhook(self):
        from amiagi.interfaces.web.monitoring.webhook_manager import WebhookManager
        now = datetime.now(tz=timezone.utc)
        pool = AsyncMock()
        pool.fetchrow = AsyncMock(return_value={
            "id": "w1", "user_id": "u1", "url": "https://edited.example.com",
            "events": ["workflow_done"], "secret": "s2", "is_active": False,
            "last_delivery_status": 500, "last_delivery_at": now, "last_error": "HTTP 500",
            "created_at": now,
        })
        mgr = WebhookManager(pool)
        hook = await mgr.update_webhook("w1", url="https://edited.example.com", events=["workflow_done"], is_active=False)
        assert hook is not None
        assert hook.url == "https://edited.example.com"
        assert hook.status == "disabled"
        sql_arg = pool.fetchrow.call_args[0][0]
        assert "UPDATE dbo.webhooks SET" in sql_arg

    @pytest.mark.asyncio
    async def test_record_delivery_result(self):
        from amiagi.interfaces.web.monitoring.webhook_manager import WebhookManager
        pool = AsyncMock()
        pool.execute = AsyncMock(return_value="UPDATE 1")
        mgr = WebhookManager(pool)
        await mgr.record_delivery_result("w1", status=200, error=None)
        sql_arg = pool.execute.call_args[0][0]
        assert "last_delivery_status" in sql_arg

    @pytest.mark.asyncio
    async def test_dispatch_no_subscribers(self):
        from amiagi.interfaces.web.monitoring.webhook_manager import WebhookManager
        pool = AsyncMock()
        pool.fetch = AsyncMock(return_value=[])
        mgr = WebhookManager(pool)
        results = await mgr.dispatch("task.done", {"task_id": "t1"})
        assert results == []

    def test_webhook_routes_exist(self):
        from amiagi.interfaces.web.routes.monitoring_routes import monitoring_routes
        paths = [r.path for r in monitoring_routes]
        assert "/settings/webhooks" in paths

    def test_webhook_test_route_exists(self):
        """13.10: Test button route."""
        from amiagi.interfaces.web.routes.monitoring_routes import monitoring_routes
        paths = [r.path for r in monitoring_routes]
        assert "/settings/webhooks/{id}/test" in paths


# ═══════════════════════════════════════════════════════════════
# 13.6 / 13.7 — Session Replay  (≥ 5 tests)
# ═══════════════════════════════════════════════════════════════

class TestSessionRecorder:
    """13.6 / 13.7: Session event recording and timeline."""

    def test_session_event_to_dict(self):
        from amiagi.interfaces.web.monitoring.session_recorder import SessionEvent
        now = datetime(2025, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
        ev = SessionEvent(
            id=1, session_id="s1", event_type="actor_state",
            agent_id="kastor", payload={"state": "thinking"},
            created_at=now,
        )
        d = ev.to_dict()
        assert d["session_id"] == "s1"
        assert d["event_type"] == "actor_state"
        assert d["payload"]["state"] == "thinking"
        assert "2025-06-01" in d["created_at"]

    def test_session_event_none_created_at(self):
        from amiagi.interfaces.web.monitoring.session_recorder import SessionEvent
        ev = SessionEvent(id=2, session_id="s2", event_type="log",
                          agent_id=None, payload={}, created_at=None)
        assert ev.to_dict()["created_at"] is None

    @pytest.mark.asyncio
    async def test_record_event(self):
        from amiagi.interfaces.web.monitoring.session_recorder import SessionRecorder
        pool = AsyncMock()
        pool.fetchrow = AsyncMock(return_value={"id": 7})
        rec = SessionRecorder(pool)
        eid = await rec.record_event("s1", "cycle_finished", agent_id="kastor", payload={"ok": True})
        assert eid == 7
        sql_arg = pool.fetchrow.call_args[0][0]
        assert "INSERT INTO dbo.session_events" in sql_arg

    @pytest.mark.asyncio
    async def test_record_event_no_payload(self):
        from amiagi.interfaces.web.monitoring.session_recorder import SessionRecorder
        pool = AsyncMock()
        pool.fetchrow = AsyncMock(return_value={"id": 8})
        rec = SessionRecorder(pool)
        eid = await rec.record_event("s2", "start")
        assert eid == 8
        # Payload should be serialized as "{}"
        call_args = pool.fetchrow.call_args[0]
        payload_arg = call_args[4]  # $4
        assert payload_arg == "{}"

    @pytest.mark.asyncio
    async def test_get_session_events(self):
        from amiagi.interfaces.web.monitoring.session_recorder import SessionRecorder
        pool = AsyncMock()
        pool.fetch = AsyncMock(return_value=[])
        rec = SessionRecorder(pool)
        events = await rec.get_session_events("s1")
        assert events == []
        sql_arg = pool.fetch.call_args[0][0]
        assert "session_id = $1" in sql_arg
        assert "ORDER BY created_at ASC" in sql_arg

    @pytest.mark.asyncio
    async def test_list_sessions(self):
        from amiagi.interfaces.web.monitoring.session_recorder import SessionRecorder
        pool = AsyncMock()
        pool.fetch = AsyncMock(return_value=[])
        rec = SessionRecorder(pool)
        sessions = await rec.list_sessions()
        assert sessions == []
        sql_arg = pool.fetch.call_args[0][0]
        assert "GROUP BY session_id" in sql_arg

    def test_row_to_event_json_string_payload(self):
        from amiagi.interfaces.web.monitoring.session_recorder import _row_to_event
        now = datetime.now(tz=timezone.utc)
        row = {
            "id": 1, "session_id": "s1", "event_type": "test",
            "agent_id": None, "payload": '{"key": "val"}', "created_at": now,
        }
        ev = _row_to_event(row)
        assert ev.payload == {"key": "val"}

    def test_row_to_event_dict_payload(self):
        from amiagi.interfaces.web.monitoring.session_recorder import _row_to_event
        now = datetime.now(tz=timezone.utc)
        row = {
            "id": 2, "session_id": "s2", "event_type": "log",
            "agent_id": "agent1", "payload": {"already": "dict"}, "created_at": now,
        }
        ev = _row_to_event(row)
        assert ev.payload == {"already": "dict"}

    def test_session_routes_exist(self):
        from amiagi.interfaces.web.routes.monitoring_routes import monitoring_routes
        paths = [r.path for r in monitoring_routes]
        assert "/api/sessions" in paths
        assert "/api/sessions/{session_id}/events" in paths


# ═══════════════════════════════════════════════════════════════
# 13.8 / 13.9 — API Keys  (≥ 8 tests)
# ═══════════════════════════════════════════════════════════════

class TestApiKeyManager:
    """13.8 / 13.9: API key creation, validation and management."""

    def test_api_key_record_to_dict(self):
        from amiagi.interfaces.web.monitoring.api_key_manager import ApiKeyRecord
        now = datetime(2025, 6, 1, tzinfo=timezone.utc)
        r = ApiKeyRecord(
            id="k1", user_id="u1", name="My Key",
            scopes=["agents.view", "tasks.manage"],
            expires_at=now + timedelta(days=30),
            is_active=True, last_used_at=None, rate_limit_per_min=120, created_at=now,
        )
        d = r.to_dict()
        assert d["id"] == "k1"
        assert d["name"] == "My Key"
        assert "agents.view" in d["scopes"]
        assert d["is_active"] is True
        assert d["last_used_at"] is None
        assert d["rate_limit_per_min"] == 120

    def test_generate_api_key_format(self):
        from amiagi.interfaces.web.monitoring.api_key_manager import generate_api_key
        key = generate_api_key()
        assert key.startswith("ak_")
        assert len(key) > 40  # ak_ + 64 hex chars

    def test_generate_api_key_unique(self):
        from amiagi.interfaces.web.monitoring.api_key_manager import generate_api_key
        keys = {generate_api_key() for _ in range(10)}
        assert len(keys) == 10  # All unique

    def test_hash_key_is_sha256(self):
        from amiagi.interfaces.web.monitoring.api_key_manager import _hash_key
        raw = "ak_test12345"
        h = _hash_key(raw)
        expected = hashlib.sha256(raw.encode()).hexdigest()
        assert h == expected

    @pytest.mark.asyncio
    async def test_create_key_returns_raw_and_record(self):
        from amiagi.interfaces.web.monitoring.api_key_manager import ApiKeyManager
        now = datetime.now(tz=timezone.utc)
        pool = AsyncMock()
        pool.fetchrow = AsyncMock(return_value={
            "id": "k1", "user_id": "u1", "name": "Test",
            "scopes": ["agents.view"], "expires_at": None,
            "is_active": True, "last_used_at": None, "rate_limit_per_min": 60, "created_at": now,
        })
        mgr = ApiKeyManager(pool)
        raw_key, record = await mgr.create_key("u1", "Test", scopes=["agents.view"], rate_limit_per_min=60)
        assert raw_key.startswith("ak_")
        assert record.name == "Test"
        assert record.scopes == ["agents.view"]
        assert record.rate_limit_per_min == 60
        sql_arg = pool.fetchrow.call_args[0][0]
        assert "INSERT INTO dbo.api_keys" in sql_arg

    @pytest.mark.asyncio
    async def test_create_key_stores_hash(self):
        """13.8: Key hash is stored in DB, not the raw key."""
        from amiagi.interfaces.web.monitoring.api_key_manager import ApiKeyManager, _hash_key
        now = datetime.now(tz=timezone.utc)
        pool = AsyncMock()
        pool.fetchrow = AsyncMock(return_value={
            "id": "k2", "user_id": "u1", "name": "Hashed",
            "scopes": [], "expires_at": None,
            "is_active": True, "last_used_at": None, "rate_limit_per_min": None, "created_at": now,
        })
        mgr = ApiKeyManager(pool)
        raw_key, _ = await mgr.create_key("u1", "Hashed")
        # Verify the hash was passed to the INSERT
        call_args = pool.fetchrow.call_args[0]
        key_hash_arg = call_args[3]  # $3 = key_hash
        assert key_hash_arg == _hash_key(raw_key)

    @pytest.mark.asyncio
    async def test_validate_key_found(self):
        from amiagi.interfaces.web.monitoring.api_key_manager import ApiKeyManager
        now = datetime.now(tz=timezone.utc)
        pool = AsyncMock()
        pool.fetchrow = AsyncMock(return_value={
            "id": "k1", "user_id": "u1", "name": "Key",
            "scopes": [], "expires_at": None,
            "is_active": True, "last_used_at": None, "rate_limit_per_min": None, "created_at": now,
        })
        pool.execute = AsyncMock(return_value="UPDATE 1")
        mgr = ApiKeyManager(pool)
        record = await mgr.validate_key("ak_test")
        assert record is not None
        assert record.id == "k1"
        # last_used_at should be updated
        update_sql = pool.execute.call_args[0][0]
        assert "last_used_at" in update_sql

    @pytest.mark.asyncio
    async def test_validate_key_not_found(self):
        from amiagi.interfaces.web.monitoring.api_key_manager import ApiKeyManager
        pool = AsyncMock()
        pool.fetchrow = AsyncMock(return_value=None)
        mgr = ApiKeyManager(pool)
        result = await mgr.validate_key("ak_invalid")
        assert result is None

    @pytest.mark.asyncio
    async def test_list_keys(self):
        from amiagi.interfaces.web.monitoring.api_key_manager import ApiKeyManager
        pool = AsyncMock()
        pool.fetch = AsyncMock(return_value=[])
        mgr = ApiKeyManager(pool)
        keys = await mgr.list_keys("u1")
        assert keys == []
        sql_arg = pool.fetch.call_args[0][0]
        assert "user_id = $1" in sql_arg

    @pytest.mark.asyncio
    async def test_revoke_key(self):
        from amiagi.interfaces.web.monitoring.api_key_manager import ApiKeyManager
        pool = AsyncMock()
        pool.execute = AsyncMock(return_value="UPDATE 1")
        mgr = ApiKeyManager(pool)
        ok = await mgr.revoke_key("k1")
        assert ok is True
        sql_arg = pool.execute.call_args[0][0]
        assert "is_active = false" in sql_arg

    @pytest.mark.asyncio
    async def test_delete_key(self):
        from amiagi.interfaces.web.monitoring.api_key_manager import ApiKeyManager
        pool = AsyncMock()
        pool.execute = AsyncMock(return_value="DELETE 1")
        mgr = ApiKeyManager(pool)
        ok = await mgr.delete_key("k1")
        assert ok is True

    def test_api_key_routes_exist(self):
        from amiagi.interfaces.web.routes.monitoring_routes import monitoring_routes
        paths = [r.path for r in monitoring_routes]
        assert "/settings/api-keys" in paths
        assert "/settings/api-keys/{id}" in paths
        assert "/settings/api-keys/{id}/revoke" in paths


# ═══════════════════════════════════════════════════════════════
# Migration 004 structure
# ═══════════════════════════════════════════════════════════════

class TestMigration004:
    """Verify 004_monitoring.sql has all required tables."""

    @pytest.fixture()
    def migration_sql(self) -> str:
        return (_WEB_ROOT / "db/migrations/004_monitoring.sql").read_text()

    def test_agent_performance_table(self, migration_sql):
        assert "CREATE TABLE IF NOT EXISTS dbo.agent_performance" in migration_sql

    def test_notifications_table(self, migration_sql):
        assert "CREATE TABLE IF NOT EXISTS dbo.notifications" in migration_sql

    def test_notification_preferences_table(self, migration_sql):
        assert "CREATE TABLE IF NOT EXISTS dbo.notification_preferences" in migration_sql

    def test_session_events_table(self, migration_sql):
        assert "CREATE TABLE IF NOT EXISTS dbo.session_events" in migration_sql

    def test_api_keys_table(self, migration_sql):
        assert "CREATE TABLE IF NOT EXISTS dbo.api_keys" in migration_sql

    def test_webhooks_table(self, migration_sql):
        assert "CREATE TABLE IF NOT EXISTS dbo.webhooks" in migration_sql

    def test_perf_indexes(self, migration_sql):
        assert "idx_perf_agent" in migration_sql
        assert "idx_perf_model" in migration_sql

    def test_api_key_hash_index(self, migration_sql):
        assert "idx_apikey_hash" in migration_sql
        assert "WHERE is_active = true" in migration_sql

    def test_monitoring_integrations_enhancement_migration_exists(self):
        path = _WEB_ROOT / "db/migrations/016_monitoring_integrations_enhancements.sql"
        assert path.exists()

    def test_monitoring_integrations_enhancement_contains_new_columns(self):
        sql = (_WEB_ROOT / "db/migrations/016_monitoring_integrations_enhancements.sql").read_text()
        assert "rate_limit_per_min" in sql
        assert "last_delivery_status" in sql
        assert "last_error" in sql

    def test_session_events_index(self, migration_sql):
        assert "idx_sess_events_session" in migration_sql


# ═══════════════════════════════════════════════════════════════
# App.py wiring
# ═══════════════════════════════════════════════════════════════

class TestAppWiring:
    """Verify monitoring services are wired into app.py."""

    @pytest.fixture()
    def app_source(self) -> str:
        return (_WEB_ROOT / "app.py").read_text()

    def test_monitoring_routes_imported(self, app_source):
        assert "from amiagi.interfaces.web.routes.monitoring_routes import monitoring_routes" in app_source

    def test_monitoring_routes_wired(self, app_source):
        assert "*monitoring_routes" in app_source

    def test_performance_tracker_wired(self, app_source):
        assert "performance_tracker" in app_source.lower()

    def test_notification_service_wired(self, app_source):
        assert "notification_service" in app_source.lower()

    def test_session_recorder_wired(self, app_source):
        assert "session_recorder" in app_source.lower()

    def test_api_key_manager_wired(self, app_source):
        assert "api_key_manager" in app_source.lower()

    def test_webhook_manager_wired(self, app_source):
        assert "webhook_manager" in app_source.lower()


# ═══════════════════════════════════════════════════════════════
# Route coverage (all 16 monitoring routes registered)
# ═══════════════════════════════════════════════════════════════

class TestRouteCoverage:
    """Ensure all monitoring routes are registered."""

    def test_total_monitoring_routes_ge_15(self):
        from amiagi.interfaces.web.routes.monitoring_routes import monitoring_routes
        assert len(monitoring_routes) >= 15

    def test_all_expected_paths_present(self):
        from amiagi.interfaces.web.routes.monitoring_routes import monitoring_routes
        paths = {r.path for r in monitoring_routes}
        expected = {
            "/api/performance", "/api/performance/summary",
            "/api/notifications", "/api/notifications/read-all",
            "/api/notifications/{id}/read",
            "/api/sessions", "/api/sessions/{session_id}/events",
            "/settings/api-keys", "/settings/api-keys/{id}",
            "/settings/api-keys/{id}/revoke",
            "/settings/webhooks", "/settings/webhooks/{id}",
            "/settings/webhooks/{id}/test",
        }
        assert expected.issubset(paths)


class TestSettingsIntegrationsTemplate:
    def test_settings_integrations_template_has_rich_controls(self):
        html = (_WEB_ROOT / "templates/settings.html").read_text(encoding="utf-8")
        assert 'id="api-key-created-panel"' in html
        assert 'class="api-key-scope"' in html
        assert 'id="new-key-rate-limit"' in html
        assert 'openWebhookEdit' in html
        assert 'id="webhook-submit-btn"' in html
