"""Tests for Sprint P1: Inbox service, inbox routes, agent lifecycle,
WorkflowEngine gate callback, command-rail updates, and i18n keys.

Focuses on verifying *behaviour* — correct SQL arguments, proper mock
interactions, edge-case handling — not just data-shape smoke-tests.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, call

import pytest


# ═══════════════════════════════════════════════════════════════
# InboxItem — data model
# ═══════════════════════════════════════════════════════════════

class TestInboxItem:
    def test_to_dict_basic(self):
        from amiagi.interfaces.web.monitoring.inbox_service import InboxItem
        now = datetime(2025, 7, 1, 12, 0, 0, tzinfo=timezone.utc)
        item = InboxItem(
            id="i1", item_type="gate_approval", title="Approve step",
            body="Please approve", source_type="workflow", source_id="run-1",
            node_id="gate-1", agent_id="a1", status="pending",
            priority=1, resolution=None, resolved_by=None,
            created_at=now, resolved_at=None, metadata={"key": "val"},
        )
        d = item.to_dict()
        assert d["id"] == "i1"
        assert d["item_type"] == "gate_approval"
        assert d["status"] == "pending"
        assert d["priority"] == 1
        assert d["metadata"] == {"key": "val"}
        assert d["created_at"] == "2025-07-01T12:00:00+00:00"
        assert d["resolved_at"] is None

    def test_to_dict_none_timestamps(self):
        from amiagi.interfaces.web.monitoring.inbox_service import InboxItem
        item = InboxItem(
            id="i2", item_type="ask_human", title="Question",
            body="", source_type="agent", source_id=None,
            node_id=None, agent_id=None, status="approved",
            priority=0, resolution="answered", resolved_by="op",
        )
        d = item.to_dict()
        assert d["created_at"] is None
        assert d["resolved_at"] is None
        assert d["resolution"] == "answered"

    def test_to_dict_roundtrip_all_fields(self):
        from amiagi.interfaces.web.monitoring.inbox_service import InboxItem
        now = datetime(2025, 8, 15, 10, 30, tzinfo=timezone.utc)
        item = InboxItem(
            id="x", item_type="gate_approval", title="T", body="B",
            source_type="workflow", source_id="r1", node_id="n1",
            agent_id="a1", status="rejected", priority=5,
            resolution="nope", resolved_by="admin",
            created_at=now, resolved_at=now, metadata={"a": 1},
        )
        d = item.to_dict()
        # Every field propagates
        assert set(d.keys()) == {
            "id", "item_type", "title", "body", "source_type", "source_id",
            "node_id", "agent_id", "status", "priority", "resolution",
            "resolved_by", "created_at", "resolved_at", "metadata",
        }
        assert d["source_id"] == "r1"
        assert d["resolved_by"] == "admin"

    def test_default_metadata_is_empty_dict(self):
        from amiagi.interfaces.web.monitoring.inbox_service import InboxItem
        item = InboxItem(
            id="m", item_type="gate_approval", title="", body="",
            source_type="agent", source_id=None, node_id=None,
            agent_id=None, status="pending", priority=0,
            resolution=None, resolved_by=None,
        )
        assert item.metadata == {}
        # Default factory should be unique per instance
        item2 = InboxItem(
            id="m2", item_type="gate_approval", title="", body="",
            source_type="agent", source_id=None, node_id=None,
            agent_id=None, status="pending", priority=0,
            resolution=None, resolved_by=None,
        )
        assert item.metadata is not item2.metadata


# ═══════════════════════════════════════════════════════════════
# _row_to_item — DB row parsing
# ═══════════════════════════════════════════════════════════════

class TestRowToItem:
    def test_basic_row(self):
        from amiagi.interfaces.web.monitoring.inbox_service import _row_to_item
        row = {
            "id": "abc", "item_type": "gate_approval", "title": "T",
            "body": "B", "source_type": "workflow", "source_id": "r1",
            "node_id": "g1", "agent_id": "a1", "status": "pending",
            "priority": 2, "resolution": None, "resolved_by": None,
            "created_at": None, "resolved_at": None, "metadata": '{"x": 1}',
        }
        item = _row_to_item(row)
        assert item.id == "abc"
        assert item.priority == 2
        assert item.metadata == {"x": 1}

    def test_json_metadata(self):
        from amiagi.interfaces.web.monitoring.inbox_service import _row_to_item
        row = {
            "id": "x", "item_type": "ask_human", "title": "",
            "source_type": "agent", "status": "pending",
            "metadata": json.dumps({"foo": "bar"}),
        }
        item = _row_to_item(row)
        assert item.metadata == {"foo": "bar"}

    def test_dict_metadata(self):
        from amiagi.interfaces.web.monitoring.inbox_service import _row_to_item
        row = {
            "id": "x", "item_type": "ask_human", "title": "",
            "source_type": "agent", "status": "pending",
            "metadata": {"already": "parsed"},
        }
        item = _row_to_item(row)
        assert item.metadata == {"already": "parsed"}

    def test_invalid_json_metadata(self):
        from amiagi.interfaces.web.monitoring.inbox_service import _row_to_item
        row = {
            "id": "x", "item_type": "ask_human", "title": "",
            "source_type": "agent", "status": "pending",
            "metadata": "NOT JSON {{{",
        }
        item = _row_to_item(row)
        assert item.metadata == {}

    def test_missing_optional_fields_default(self):
        """Columns absent from the row dict should fall back via .get()."""
        from amiagi.interfaces.web.monitoring.inbox_service import _row_to_item
        row = {
            "id": "z", "item_type": "gate_approval", "title": "T",
            "source_type": "workflow", "status": "pending",
        }
        item = _row_to_item(row)
        assert item.body == ""
        assert item.source_id is None
        assert item.node_id is None
        assert item.agent_id is None
        assert item.priority == 0
        assert item.resolution is None

    def test_none_metadata(self):
        from amiagi.interfaces.web.monitoring.inbox_service import _row_to_item
        row = {
            "id": "n", "item_type": "gate_approval", "title": "",
            "source_type": "agent", "status": "pending",
            "metadata": None,
        }
        item = _row_to_item(row)
        assert item.metadata == {}

    def test_empty_string_metadata(self):
        from amiagi.interfaces.web.monitoring.inbox_service import _row_to_item
        row = {
            "id": "e", "item_type": "gate_approval", "title": "",
            "source_type": "agent", "status": "pending",
            "metadata": "",
        }
        item = _row_to_item(row)
        assert item.metadata == {}

    def test_id_is_always_string(self):
        """DB may return int/UUID — _row_to_item should stringify."""
        from amiagi.interfaces.web.monitoring.inbox_service import _row_to_item
        row = {
            "id": 42, "item_type": "gate_approval", "title": "",
            "source_type": "agent", "status": "pending",
        }
        item = _row_to_item(row)
        assert item.id == "42"
        assert isinstance(item.id, str)


# ═══════════════════════════════════════════════════════════════
# InboxService — async CRUD (verify SQL + arguments)
# ═══════════════════════════════════════════════════════════════

def _make_pool():
    """Return a mock pool with standard call methods."""
    pool = AsyncMock()
    pool.fetchrow = AsyncMock()
    pool.fetch = AsyncMock()
    pool.execute = AsyncMock()
    return pool


def _make_row(**overrides):
    """Standard pending row — overridable for test variations."""
    row = {
        "id": "i1", "item_type": "gate_approval", "title": "T",
        "body": "", "source_type": "workflow", "source_id": None,
        "node_id": None, "agent_id": None, "status": "pending",
        "priority": 0, "resolution": None, "resolved_by": None,
        "created_at": None, "resolved_at": None, "metadata": "{}",
    }
    row.update(overrides)
    return row


class TestInboxServiceCreate:
    @pytest.mark.asyncio
    async def test_create_item_returns_parsed_item(self):
        from amiagi.interfaces.web.monitoring.inbox_service import InboxService
        pool = _make_pool()
        pool.fetchrow.return_value = _make_row(
            id="new-1", title="Gate", body="approve?", source_id="r1",
            node_id="g1",
        )
        svc = InboxService(pool)
        item = await svc.create(title="Gate", body="approve?",
                                source_type="workflow", source_id="r1",
                                node_id="g1")
        assert item.id == "new-1"
        assert item.item_type == "gate_approval"

    @pytest.mark.asyncio
    async def test_create_sends_correct_sql_params(self):
        """Verify the INSERT SQL has RETURNING * and correct arg count."""
        from amiagi.interfaces.web.monitoring.inbox_service import InboxService
        pool = _make_pool()
        pool.fetchrow.return_value = _make_row(id="c1")
        svc = InboxService(pool)
        await svc.create(
            title="Test", body="body", source_type="workflow",
            source_id="r1", node_id="g1", agent_id="a1",
            priority=3, metadata={"k": "v"},
        )
        pool.fetchrow.assert_awaited_once()
        sql_arg = pool.fetchrow.call_args[0][0]
        # SQL should contain INSERT and RETURNING
        assert "INSERT INTO" in sql_arg
        assert "RETURNING" in sql_arg
        # 9 positional args: item_type, title, body, source_type, source_id,
        #                     node_id, agent_id, priority, metadata_json
        positional_args = pool.fetchrow.call_args[0][1:]
        assert len(positional_args) == 9
        assert positional_args[0] == "gate_approval"   # default item_type
        assert positional_args[1] == "Test"             # title
        assert positional_args[2] == "body"             # body
        assert positional_args[3] == "workflow"         # source_type
        assert positional_args[4] == "r1"               # source_id
        assert positional_args[5] == "g1"               # node_id
        assert positional_args[6] == "a1"               # agent_id
        assert positional_args[7] == 3                  # priority
        assert json.loads(positional_args[8]) == {"k": "v"}  # metadata JSON

    @pytest.mark.asyncio
    async def test_create_default_item_type(self):
        """item_type defaults to 'gate_approval'."""
        from amiagi.interfaces.web.monitoring.inbox_service import InboxService
        pool = _make_pool()
        pool.fetchrow.return_value = _make_row()
        svc = InboxService(pool)
        await svc.create(title="X")
        positional_args = pool.fetchrow.call_args[0][1:]
        assert positional_args[0] == "gate_approval"

    @pytest.mark.asyncio
    async def test_create_custom_item_type(self):
        from amiagi.interfaces.web.monitoring.inbox_service import InboxService
        pool = _make_pool()
        pool.fetchrow.return_value = _make_row(item_type="ask_human")
        svc = InboxService(pool)
        await svc.create(title="Q", item_type="ask_human")
        positional_args = pool.fetchrow.call_args[0][1:]
        assert positional_args[0] == "ask_human"

    @pytest.mark.asyncio
    async def test_create_metadata_none_serialized_as_empty(self):
        from amiagi.interfaces.web.monitoring.inbox_service import InboxService
        pool = _make_pool()
        pool.fetchrow.return_value = _make_row()
        svc = InboxService(pool)
        await svc.create(title="X", metadata=None)
        positional_args = pool.fetchrow.call_args[0][1:]
        assert positional_args[8] == "{}"


class TestInboxServiceList:
    @pytest.mark.asyncio
    async def test_list_with_status_passes_filter_params(self):
        from amiagi.interfaces.web.monitoring.inbox_service import InboxService
        pool = _make_pool()
        pool.fetch.return_value = [_make_row()]
        svc = InboxService(pool)
        items = await svc.list_items(status="pending", limit=10, offset=5)
        assert len(items) == 1
        assert items[0].status == "pending"
        # Check SQL arguments (status, limit, offset)
        pool.fetch.assert_awaited_once()
        args = pool.fetch.call_args[0]
        sql = args[0]
        assert "WHERE status" in sql
        assert args[1] == "pending"
        assert args[2] == 10
        assert args[3] == 5

    @pytest.mark.asyncio
    async def test_list_without_filter_omits_where(self):
        from amiagi.interfaces.web.monitoring.inbox_service import InboxService
        pool = _make_pool()
        pool.fetch.return_value = []
        svc = InboxService(pool)
        items = await svc.list_items()
        assert items == []
        sql = pool.fetch.call_args[0][0]
        assert "WHERE status" not in sql

    @pytest.mark.asyncio
    async def test_list_default_limit_offset(self):
        from amiagi.interfaces.web.monitoring.inbox_service import InboxService
        pool = _make_pool()
        pool.fetch.return_value = []
        svc = InboxService(pool)
        await svc.list_items()  # no status filter
        args = pool.fetch.call_args[0]
        assert args[1] == 50  # default limit
        assert args[2] == 0   # default offset

    @pytest.mark.asyncio
    async def test_list_orders_by_priority_desc(self):
        from amiagi.interfaces.web.monitoring.inbox_service import InboxService
        pool = _make_pool()
        pool.fetch.return_value = []
        svc = InboxService(pool)
        await svc.list_items()
        sql = pool.fetch.call_args[0][0]
        assert "priority DESC" in sql

    @pytest.mark.asyncio
    async def test_list_multiple_rows(self):
        from amiagi.interfaces.web.monitoring.inbox_service import InboxService
        pool = _make_pool()
        pool.fetch.return_value = [
            _make_row(id="a"), _make_row(id="b"), _make_row(id="c"),
        ]
        svc = InboxService(pool)
        items = await svc.list_items()
        assert [i.id for i in items] == ["a", "b", "c"]


class TestInboxServiceGet:
    @pytest.mark.asyncio
    async def test_get_existing_passes_id(self):
        from amiagi.interfaces.web.monitoring.inbox_service import InboxService
        pool = _make_pool()
        pool.fetchrow.return_value = _make_row(id="i1")
        svc = InboxService(pool)
        item = await svc.get("i1")
        assert item is not None
        assert item.id == "i1"
        # Verify correct item_id param
        assert pool.fetchrow.call_args[0][1] == "i1"

    @pytest.mark.asyncio
    async def test_get_missing(self):
        from amiagi.interfaces.web.monitoring.inbox_service import InboxService
        pool = _make_pool()
        pool.fetchrow.return_value = None
        svc = InboxService(pool)
        assert await svc.get("missing") is None

    @pytest.mark.asyncio
    async def test_get_sql_selects_by_id(self):
        from amiagi.interfaces.web.monitoring.inbox_service import InboxService
        pool = _make_pool()
        pool.fetchrow.return_value = _make_row()
        svc = InboxService(pool)
        await svc.get("target-id")
        sql = pool.fetchrow.call_args[0][0]
        assert "WHERE id" in sql


class TestInboxServicePendingCount:
    @pytest.mark.asyncio
    async def test_pending_count(self):
        from amiagi.interfaces.web.monitoring.inbox_service import InboxService
        pool = _make_pool()
        pool.fetchrow.return_value = {"cnt": 5}
        svc = InboxService(pool)
        assert await svc.pending_count() == 5

    @pytest.mark.asyncio
    async def test_pending_count_none_row(self):
        from amiagi.interfaces.web.monitoring.inbox_service import InboxService
        pool = _make_pool()
        pool.fetchrow.return_value = None
        svc = InboxService(pool)
        assert await svc.pending_count() == 0

    @pytest.mark.asyncio
    async def test_pending_count_sql_filters_pending(self):
        from amiagi.interfaces.web.monitoring.inbox_service import InboxService
        pool = _make_pool()
        pool.fetchrow.return_value = {"cnt": 0}
        svc = InboxService(pool)
        await svc.pending_count()
        sql = pool.fetchrow.call_args[0][0]
        assert "pending" in sql.lower()
        assert "count" in sql.lower()


class TestInboxServiceCountByStatus:
    @pytest.mark.asyncio
    async def test_count_by_status(self):
        from amiagi.interfaces.web.monitoring.inbox_service import InboxService
        pool = _make_pool()
        pool.fetch.return_value = [
            {"status": "pending", "cnt": 3},
            {"status": "approved", "cnt": 7},
        ]
        svc = InboxService(pool)
        counts = await svc.count_by_status()
        assert counts == {"pending": 3, "approved": 7}

    @pytest.mark.asyncio
    async def test_count_by_status_empty(self):
        from amiagi.interfaces.web.monitoring.inbox_service import InboxService
        pool = _make_pool()
        pool.fetch.return_value = []
        svc = InboxService(pool)
        assert await svc.count_by_status() == {}

    @pytest.mark.asyncio
    async def test_count_by_status_sql_groups(self):
        from amiagi.interfaces.web.monitoring.inbox_service import InboxService
        pool = _make_pool()
        pool.fetch.return_value = []
        svc = InboxService(pool)
        await svc.count_by_status()
        sql = pool.fetch.call_args[0][0]
        assert "GROUP BY" in sql


class TestInboxServiceResolve:
    @pytest.mark.asyncio
    async def test_approve_returns_resolved_item(self):
        from amiagi.interfaces.web.monitoring.inbox_service import InboxService
        now = datetime.now(tz=timezone.utc)
        pool = _make_pool()
        pool.fetchrow.return_value = _make_row(
            status="approved", resolution="approved", resolved_by="op",
            resolved_at=now,
        )
        svc = InboxService(pool)
        item = await svc.approve("i1", resolved_by="op")
        assert item is not None
        assert item.status == "approved"

    @pytest.mark.asyncio
    async def test_approve_sql_has_pending_guard(self):
        """approve() should only update WHERE status = 'pending'."""
        from amiagi.interfaces.web.monitoring.inbox_service import InboxService
        pool = _make_pool()
        pool.fetchrow.return_value = _make_row(status="approved")
        svc = InboxService(pool)
        await svc.approve("i1")
        sql = pool.fetchrow.call_args[0][0]
        assert "pending" in sql.lower()
        assert "UPDATE" in sql
        assert "RETURNING" in sql

    @pytest.mark.asyncio
    async def test_approve_passes_correct_args(self):
        from amiagi.interfaces.web.monitoring.inbox_service import InboxService
        pool = _make_pool()
        pool.fetchrow.return_value = _make_row(status="approved")
        svc = InboxService(pool)
        await svc.approve("item-42", resolved_by="admin")
        args = pool.fetchrow.call_args[0]
        # First positional arg after SQL is item_id
        assert args[1] == "item-42"

    @pytest.mark.asyncio
    async def test_reject_returns_resolved_item(self):
        from amiagi.interfaces.web.monitoring.inbox_service import InboxService
        now = datetime.now(tz=timezone.utc)
        pool = _make_pool()
        pool.fetchrow.return_value = _make_row(
            status="rejected", resolution="not needed", resolved_by="op",
            resolved_at=now,
        )
        svc = InboxService(pool)
        item = await svc.reject("i1", resolved_by="op", reason="not needed")
        assert item is not None
        assert item.status == "rejected"

    @pytest.mark.asyncio
    async def test_reject_passes_reason_as_resolution(self):
        from amiagi.interfaces.web.monitoring.inbox_service import InboxService
        pool = _make_pool()
        pool.fetchrow.return_value = _make_row(status="rejected")
        svc = InboxService(pool)
        await svc.reject("i1", reason="bad request")
        args = pool.fetchrow.call_args[0]
        # resolution arg should contain the reason
        assert "bad request" in args

    @pytest.mark.asyncio
    async def test_resolve_not_found_returns_none(self):
        from amiagi.interfaces.web.monitoring.inbox_service import InboxService
        pool = _make_pool()
        pool.fetchrow.return_value = None
        svc = InboxService(pool)
        assert await svc.approve("missing") is None

    @pytest.mark.asyncio
    async def test_reject_not_found_returns_none(self):
        from amiagi.interfaces.web.monitoring.inbox_service import InboxService
        pool = _make_pool()
        pool.fetchrow.return_value = None
        svc = InboxService(pool)
        assert await svc.reject("missing") is None


class TestInboxServiceExpire:
    @pytest.mark.asyncio
    async def test_expire_parses_update_count(self):
        from amiagi.interfaces.web.monitoring.inbox_service import InboxService
        pool = _make_pool()
        pool.execute.return_value = "UPDATE 3"
        svc = InboxService(pool)
        count = await svc.expire_old(hours=48)
        assert count == 3

    @pytest.mark.asyncio
    async def test_expire_passes_hours_param(self):
        from amiagi.interfaces.web.monitoring.inbox_service import InboxService
        pool = _make_pool()
        pool.execute.return_value = "UPDATE 0"
        svc = InboxService(pool)
        await svc.expire_old(hours=24)
        args = pool.execute.call_args[0]
        assert args[1] == 24

    @pytest.mark.asyncio
    async def test_expire_zero_rows(self):
        from amiagi.interfaces.web.monitoring.inbox_service import InboxService
        pool = _make_pool()
        pool.execute.return_value = "UPDATE 0"
        svc = InboxService(pool)
        assert await svc.expire_old() == 0

    @pytest.mark.asyncio
    async def test_expire_malformed_response(self):
        from amiagi.interfaces.web.monitoring.inbox_service import InboxService
        pool = _make_pool()
        pool.execute.return_value = "UNEXPECTED"
        svc = InboxService(pool)
        assert await svc.expire_old() == 0

    @pytest.mark.asyncio
    async def test_expire_sql_targets_pending_only(self):
        from amiagi.interfaces.web.monitoring.inbox_service import InboxService
        pool = _make_pool()
        pool.execute.return_value = "UPDATE 0"
        svc = InboxService(pool)
        await svc.expire_old()
        sql = pool.execute.call_args[0][0]
        assert "pending" in sql.lower()
        assert "expired" in sql.lower()

    @pytest.mark.asyncio
    async def test_expire_default_hours(self):
        """Default should be 72 hours."""
        from amiagi.interfaces.web.monitoring.inbox_service import InboxService
        pool = _make_pool()
        pool.execute.return_value = "UPDATE 0"
        svc = InboxService(pool)
        await svc.expire_old()
        args = pool.execute.call_args[0]
        assert args[1] == 72


# ═══════════════════════════════════════════════════════════════
# Inbox API routes — structural tests
# ═══════════════════════════════════════════════════════════════

class TestInboxRoutes:
    def test_all_inbox_routes_exist(self):
        from amiagi.interfaces.web.routes.inbox_routes import inbox_routes
        paths = [r.path for r in inbox_routes]
        expected = [
            "/api/inbox", "/api/inbox/count",
            "/api/inbox/{item_id}", "/api/inbox/{item_id}/approve",
            "/api/inbox/{item_id}/reject", "/api/inbox/{item_id}/reply",
        ]
        for p in expected:
            assert p in paths, f"Missing inbox route: {p}"

    def test_lifecycle_routes_exist(self):
        from amiagi.interfaces.web.routes.inbox_routes import inbox_routes
        paths = [r.path for r in inbox_routes]
        assert "/api/agents/{agent_id}/pause" in paths
        assert "/api/agents/{agent_id}/resume" in paths
        assert "/api/agents/{agent_id}/terminate" in paths

    def test_all_route_methods_correct(self):
        from amiagi.interfaces.web.routes.inbox_routes import inbox_routes
        method_map = {r.path: (r.methods or set()) for r in inbox_routes}
        # GET endpoints
        assert "GET" in method_map["/api/inbox"]
        assert "GET" in method_map["/api/inbox/count"]
        assert "GET" in method_map["/api/inbox/{item_id}"]
        # POST endpoints
        assert "POST" in method_map["/api/inbox/{item_id}/approve"]
        assert "POST" in method_map["/api/inbox/{item_id}/reject"]
        assert "POST" in method_map["/api/inbox/{item_id}/reply"]
        # Lifecycle — all POST
        assert "POST" in method_map["/api/agents/{agent_id}/pause"]
        assert "POST" in method_map["/api/agents/{agent_id}/resume"]
        assert "POST" in method_map["/api/agents/{agent_id}/terminate"]

    def test_route_count(self):
        from amiagi.interfaces.web.routes.inbox_routes import inbox_routes
        assert len(inbox_routes) == 9


# ═══════════════════════════════════════════════════════════════
# WorkflowEngine — on_gate_waiting callback
# ═══════════════════════════════════════════════════════════════

class TestGateWaitingCallback:
    def test_callback_fires_on_gate(self):
        from amiagi.application.workflow_engine import WorkflowEngine
        from amiagi.domain.workflow import (
            NodeStatus, NodeType, WorkflowDefinition, WorkflowNode,
        )
        calls = []

        def gate_cb(node, run):
            calls.append((node.node_id, run.run_id))

        wf = WorkflowDefinition(
            name="cb-test",
            nodes=[
                WorkflowNode(node_id="a", node_type=NodeType.EXECUTE),
                WorkflowNode(node_id="gate", node_type=NodeType.GATE, depends_on=["a"]),
                WorkflowNode(node_id="b", node_type=NodeType.EXECUTE, depends_on=["gate"]),
            ],
        )
        engine = WorkflowEngine(on_gate_waiting=gate_cb)
        run = engine.start(wf, run_id="r1")
        assert len(calls) == 1
        assert calls[0] == ("gate", "r1")
        assert run.workflow.node_map()["gate"].status == NodeStatus.WAITING_APPROVAL

    def test_callback_receives_correct_node(self):
        """The callback receives the actual gate node object, not a copy."""
        from amiagi.application.workflow_engine import WorkflowEngine
        from amiagi.domain.workflow import (
            NodeStatus, NodeType, WorkflowDefinition, WorkflowNode,
        )
        captured_nodes = []

        def gate_cb(node, run):
            captured_nodes.append(node)

        wf = WorkflowDefinition(
            name="ref-test",
            nodes=[
                WorkflowNode(node_id="a", node_type=NodeType.EXECUTE),
                WorkflowNode(node_id="gate", node_type=NodeType.GATE, depends_on=["a"]),
            ],
        )
        engine = WorkflowEngine(on_gate_waiting=gate_cb)
        run = engine.start(wf, run_id="r2")
        # The captured node should be the actual node in the workflow
        assert captured_nodes[0] is run.workflow.node_map()["gate"]
        assert captured_nodes[0].node_type == NodeType.GATE

    def test_callback_not_fired_on_auto_approve(self):
        from amiagi.application.workflow_engine import WorkflowEngine
        from amiagi.domain.workflow import (
            NodeType, WorkflowDefinition, WorkflowNode,
        )
        calls = []

        def gate_cb(node, run):
            calls.append(node.node_id)

        wf = WorkflowDefinition(
            name="auto",
            nodes=[
                WorkflowNode(node_id="a", node_type=NodeType.EXECUTE),
                WorkflowNode(node_id="gate", node_type=NodeType.GATE, depends_on=["a"]),
                WorkflowNode(node_id="b", node_type=NodeType.EXECUTE, depends_on=["gate"]),
            ],
        )
        engine = WorkflowEngine(
            gate_handler=lambda _: True,
            on_gate_waiting=gate_cb,
        )
        run = engine.start(wf, run_id="r1")
        assert calls == []  # Auto-approved, so callback should NOT fire
        # Gate should be completed, not waiting
        gate = run.workflow.node_map()["gate"]
        assert gate.result == "auto-approved"

    def test_callback_error_does_not_crash(self):
        from amiagi.application.workflow_engine import WorkflowEngine
        from amiagi.domain.workflow import (
            NodeStatus, NodeType, WorkflowDefinition, WorkflowNode,
        )

        def broken_cb(node, run):
            raise RuntimeError("oops")

        wf = WorkflowDefinition(
            name="err",
            nodes=[
                WorkflowNode(node_id="a", node_type=NodeType.EXECUTE),
                WorkflowNode(node_id="gate", node_type=NodeType.GATE, depends_on=["a"]),
            ],
        )
        engine = WorkflowEngine(on_gate_waiting=broken_cb)
        run = engine.start(wf, run_id="r1")
        # Should not crash — gate still enters waiting
        gate = run.workflow.node_map()["gate"]
        assert gate.status == NodeStatus.WAITING_APPROVAL

    def test_no_callback_no_error(self):
        """Engine without on_gate_waiting should work fine."""
        from amiagi.application.workflow_engine import WorkflowEngine
        from amiagi.domain.workflow import (
            NodeStatus, NodeType, WorkflowDefinition, WorkflowNode,
        )
        wf = WorkflowDefinition(
            name="no-cb",
            nodes=[
                WorkflowNode(node_id="a", node_type=NodeType.EXECUTE),
                WorkflowNode(node_id="gate", node_type=NodeType.GATE, depends_on=["a"]),
            ],
        )
        engine = WorkflowEngine()
        run = engine.start(wf, run_id="r3")
        gate = run.workflow.node_map()["gate"]
        assert gate.status == NodeStatus.WAITING_APPROVAL

    def test_run_status_paused_at_gate(self):
        """A run with an unapproved gate should NOT be 'completed'."""
        from amiagi.application.workflow_engine import WorkflowEngine
        from amiagi.domain.workflow import (
            NodeType, WorkflowDefinition, WorkflowNode,
        )
        wf = WorkflowDefinition(
            name="paused",
            nodes=[
                WorkflowNode(node_id="a", node_type=NodeType.EXECUTE),
                WorkflowNode(node_id="gate", node_type=NodeType.GATE, depends_on=["a"]),
                WorkflowNode(node_id="b", node_type=NodeType.EXECUTE, depends_on=["gate"]),
            ],
        )
        engine = WorkflowEngine()
        run = engine.start(wf, run_id="r4")
        # Run should NOT be completed — gate is waiting
        assert run.status != "completed"


# ═══════════════════════════════════════════════════════════════
# Dashboard routes — structural tests
# ═══════════════════════════════════════════════════════════════

class TestDashboardRoutes:
    def test_supervisor_route_exists(self):
        from amiagi.interfaces.web.routes.dashboard_routes import dashboard_routes
        paths = [r.path for r in dashboard_routes]
        assert "/supervisor" in paths

    def test_inbox_route_exists(self):
        from amiagi.interfaces.web.routes.dashboard_routes import dashboard_routes
        paths = [r.path for r in dashboard_routes]
        assert "/inbox" in paths

    def test_supervisor_and_inbox_are_get(self):
        from amiagi.interfaces.web.routes.dashboard_routes import dashboard_routes
        method_map = {r.path: (r.methods or set()) for r in dashboard_routes}
        assert "GET" in method_map["/supervisor"]
        assert "GET" in method_map["/inbox"]


# ═══════════════════════════════════════════════════════════════
# Command rail — template structure
# ═══════════════════════════════════════════════════════════════

class TestCommandRail:
    @pytest.fixture()
    def rail_html(self):
        p = Path(__file__).resolve().parent.parent / \
            "src/amiagi/interfaces/web/templates/partials/command_rail.html"
        return p.read_text(encoding="utf-8")

    def test_supervisor_link(self, rail_html):
        assert 'href="/supervisor"' in rail_html

    def test_inbox_link(self, rail_html):
        assert 'href="/inbox"' in rail_html

    def test_supervisor_tooltip(self, rail_html):
        assert "nav.supervisor" in rail_html

    def test_inbox_tooltip(self, rail_html):
        assert "nav.inbox" in rail_html


# ═══════════════════════════════════════════════════════════════
# i18n — new keys present in both locales
# ═══════════════════════════════════════════════════════════════

class TestI18nKeys:
    @pytest.fixture(params=["web_pl.json", "web_en.json"])
    def locale_data(self, request):
        p = Path(__file__).resolve().parent.parent / \
            f"src/amiagi/i18n/locales/{request.param}"
        return json.loads(p.read_text(encoding="utf-8"))

    @pytest.mark.parametrize("key", [
        "inbox.title", "inbox.pending", "inbox.approved", "inbox.rejected",
        "inbox.all", "inbox.approve", "inbox.reject", "inbox.empty",
        "inbox.reply_placeholder", "inbox.send_reply",
        "inbox.delegate",
        "nav.inbox", "nav.supervisor",
        "supervisor.title", "supervisor.active_agents", "supervisor.active_tasks",
        "supervisor.pending_approvals", "supervisor.uptime",
        "supervisor.agents_heading", "supervisor.live", "supervisor.live_stream",
        "supervisor.loading", "supervisor.refresh", "supervisor.waiting",
        "supervisor.operator_input", "supervisor.input_placeholder",
        "supervisor.input_target_all", "supervisor.send",
        "supervisor.spawn_agent", "supervisor.spawn_name", "supervisor.spawn",
    ])
    def test_key_exists(self, locale_data, key):
        assert key in locale_data, f"Missing i18n key: {key}"
        assert locale_data[key], f"Empty value for i18n key: {key}"


# ═══════════════════════════════════════════════════════════════
# Templates — existence check
# ═══════════════════════════════════════════════════════════════

class TestTemplatesExist:
    _TPL_DIR = Path(__file__).resolve().parent.parent / \
        "src/amiagi/interfaces/web/templates"

    def test_supervisor_html(self):
        assert (self._TPL_DIR / "supervisor.html").exists()

    def test_inbox_html(self):
        assert (self._TPL_DIR / "inbox.html").exists()


# ═══════════════════════════════════════════════════════════════
# Static assets — existence check
# ═══════════════════════════════════════════════════════════════

class TestStaticAssets:
    _STATIC = Path(__file__).resolve().parent.parent / \
        "src/amiagi/interfaces/web/static"

    def test_supervisor_css(self):
        assert (self._STATIC / "css/supervisor.css").exists()

    def test_inbox_css(self):
        assert (self._STATIC / "css/inbox.css").exists()

    def test_supervisor_js(self):
        assert (self._STATIC / "js/supervisor.js").exists()

    def test_inbox_js(self):
        assert (self._STATIC / "js/inbox.js").exists()


# ═══════════════════════════════════════════════════════════════
# DB migration — SQL file presence and structure
# ═══════════════════════════════════════════════════════════════

class TestMigration008:
    @pytest.fixture()
    def pg_sql(self):
        p = Path(__file__).resolve().parent.parent / \
            "src/amiagi/interfaces/web/db/migrations/008_inbox.sql"
        return p.read_text(encoding="utf-8")

    @pytest.fixture()
    def sqlite_sql(self):
        p = Path(__file__).resolve().parent.parent / \
            "src/amiagi/interfaces/web/db/migrations_sqlite/008_inbox.sql"
        return p.read_text(encoding="utf-8")

    def test_pg_creates_table(self, pg_sql):
        assert "inbox_items" in pg_sql
        assert "IF NOT EXISTS" in pg_sql

    def test_pg_has_indexes(self, pg_sql):
        assert "idx_inbox_status" in pg_sql
        assert "idx_inbox_source" in pg_sql

    def test_sqlite_creates_table(self, sqlite_sql):
        assert "inbox_items" in sqlite_sql
        assert "IF NOT EXISTS" in sqlite_sql

    def test_sqlite_has_indexes(self, sqlite_sql):
        assert "idx_inbox_status" in sqlite_sql

    def test_pg_uses_dbo_schema(self, pg_sql):
        assert "dbo.inbox_items" in pg_sql

    def test_sqlite_no_dbo(self, sqlite_sql):
        assert "dbo." not in sqlite_sql

    def test_pg_has_required_columns(self, pg_sql):
        for col in ["item_type", "title", "status", "priority",
                     "source_type", "created_at", "resolved_at", "metadata"]:
            assert col in pg_sql, f"Missing PG column: {col}"

    def test_sqlite_has_required_columns(self, sqlite_sql):
        for col in ["item_type", "title", "status", "priority",
                     "source_type", "created_at", "resolved_at", "metadata"]:
            assert col in sqlite_sql, f"Missing SQLite column: {col}"


# ═══════════════════════════════════════════════════════════════
# App wiring — inbox_service on app.state
# ═══════════════════════════════════════════════════════════════

class TestAppWiring:
    def test_inbox_service_import_in_app(self):
        app_path = Path(__file__).resolve().parent.parent / \
            "src/amiagi/interfaces/web/app.py"
        src = app_path.read_text(encoding="utf-8")
        assert "inbox_service" in src
        assert "InboxService" in src

    def test_inbox_routes_import_in_app(self):
        app_path = Path(__file__).resolve().parent.parent / \
            "src/amiagi/interfaces/web/app.py"
        src = app_path.read_text(encoding="utf-8")
        assert "inbox_routes" in src

    def test_system_routes_import_in_app(self):
        app_path = Path(__file__).resolve().parent.parent / \
            "src/amiagi/interfaces/web/app.py"
        src = app_path.read_text(encoding="utf-8")
        assert "system_routes" in src

    def test_workflow_engine_wiring_in_app(self):
        app_path = Path(__file__).resolve().parent.parent / \
            "src/amiagi/interfaces/web/app.py"
        src = app_path.read_text(encoding="utf-8")
        assert "_gate_to_inbox" in src
        assert "_on_gate_waiting" in src


# ═══════════════════════════════════════════════════════════════
# System routes — structural tests
# ═══════════════════════════════════════════════════════════════

class TestSystemRoutes:
    def test_all_system_routes_exist(self):
        from amiagi.interfaces.web.routes.system_routes import system_routes
        paths = [r.path for r in system_routes]
        expected = [
            "/api/system/state", "/api/system/input",
            "/api/inbox/{item_id}/delegate", "/api/agents/spawn",
        ]
        for p in expected:
            assert p in paths, f"Missing system route: {p}"

    def test_route_count(self):
        from amiagi.interfaces.web.routes.system_routes import system_routes
        assert len(system_routes) == 4

    def test_methods_correct(self):
        from amiagi.interfaces.web.routes.system_routes import system_routes
        method_map = {r.path: (r.methods or set()) for r in system_routes}
        assert "GET" in method_map["/api/system/state"]
        assert "POST" in method_map["/api/system/input"]
        assert "POST" in method_map["/api/inbox/{item_id}/delegate"]
        assert "POST" in method_map["/api/agents/spawn"]

    def test_handlers_are_async(self):
        import asyncio
        from amiagi.interfaces.web.routes.system_routes import (
            system_state, system_input, inbox_delegate, agent_spawn,
        )
        assert asyncio.iscoroutinefunction(system_state)
        assert asyncio.iscoroutinefunction(system_input)
        assert asyncio.iscoroutinefunction(inbox_delegate)
        assert asyncio.iscoroutinefunction(agent_spawn)


# ═══════════════════════════════════════════════════════════════
# System state endpoint — behaviour tests
# ═══════════════════════════════════════════════════════════════

class TestSystemStateEndpoint:
    def _make_request(self, **state_attrs):
        """Build a mock Request whose .app.state carries given attrs."""
        req = MagicMock(spec=["app"])
        # Use a simple namespace so getattr returns None for missing attrs
        ns = type("NS", (), {"__getattr__": lambda self, name: None})()
        for k, v in state_attrs.items():
            setattr(ns, k, v)
        req.app.state = ns
        return req

    @pytest.mark.asyncio
    async def test_returns_json(self):
        from amiagi.interfaces.web.routes.system_routes import system_state
        req = self._make_request()
        resp = await system_state(req)
        assert resp.status_code == 200
        body = json.loads(bytes(resp.body))
        assert "agents" in body
        assert "inbox" in body

    @pytest.mark.asyncio
    async def test_agents_with_registry(self):
        from amiagi.interfaces.web.routes.system_routes import system_state
        agent_mock = MagicMock()
        agent_mock.state = MagicMock(value="running")
        registry = MagicMock()
        registry.list_all.return_value = [agent_mock, agent_mock]
        req = self._make_request(agent_registry=registry)
        resp = await system_state(req)
        body = json.loads(bytes(resp.body))
        assert body["agents"]["total"] == 2

    @pytest.mark.asyncio
    async def test_inbox_with_service(self):
        from amiagi.interfaces.web.routes.system_routes import system_state
        inbox_svc = AsyncMock()
        inbox_svc.count_by_status.return_value = {"pending": 7, "approved": 3}
        req = self._make_request(inbox_service=inbox_svc)
        resp = await system_state(req)
        body = json.loads(bytes(resp.body))
        assert body["inbox"]["pending"] == 7

    @pytest.mark.asyncio
    async def test_uptime_present(self):
        from amiagi.interfaces.web.routes.system_routes import system_state
        import time
        req = self._make_request(_startup_time=time.time() - 120)
        resp = await system_state(req)
        body = json.loads(bytes(resp.body))
        assert "uptime_seconds" in body
        assert body["uptime_seconds"] >= 119


# ═══════════════════════════════════════════════════════════════
# Web Components — file existence checks
# ═══════════════════════════════════════════════════════════════

class TestWebComponentFiles:
    _COMP_DIR = Path(__file__).resolve().parent.parent / \
        "src/amiagi/interfaces/web/static/js/components"

    def test_live_stream_js(self):
        assert (self._COMP_DIR / "live-stream.js").exists()

    def test_inbox_badge_js(self):
        assert (self._COMP_DIR / "inbox-badge.js").exists()

    def test_approval_card_js(self):
        assert (self._COMP_DIR / "approval-card.js").exists()

    def test_live_stream_defines_element(self):
        src = (self._COMP_DIR / "live-stream.js").read_text()
        assert "customElements.define" in src
        assert "live-stream" in src

    def test_inbox_badge_defines_element(self):
        src = (self._COMP_DIR / "inbox-badge.js").read_text()
        assert "customElements.define" in src
        assert "inbox-badge" in src

    def test_approval_card_defines_element(self):
        src = (self._COMP_DIR / "approval-card.js").read_text()
        assert "customElements.define" in src
        assert "approval-card" in src

    def test_live_stream_uses_shadow_dom(self):
        src = (self._COMP_DIR / "live-stream.js").read_text()
        assert "attachShadow" in src

    def test_inbox_badge_uses_shadow_dom(self):
        src = (self._COMP_DIR / "inbox-badge.js").read_text()
        assert "attachShadow" in src

    def test_approval_card_uses_shadow_dom(self):
        src = (self._COMP_DIR / "approval-card.js").read_text()
        assert "attachShadow" in src


# ═══════════════════════════════════════════════════════════════
# Templates — Web Component integration
# ═══════════════════════════════════════════════════════════════

class TestTemplateIntegration:
    _TPL_DIR = Path(__file__).resolve().parent.parent / \
        "src/amiagi/interfaces/web/templates"

    def test_supervisor_imports_live_stream(self):
        src = (self._TPL_DIR / "supervisor.html").read_text()
        assert "live-stream.js" in src

    def test_supervisor_imports_inbox_badge(self):
        src = (self._TPL_DIR / "supervisor.html").read_text()
        assert "inbox-badge.js" in src

    def test_supervisor_uses_live_stream_element(self):
        src = (self._TPL_DIR / "supervisor.html").read_text()
        assert "<live-stream" in src

    def test_supervisor_uses_inbox_badge_element(self):
        src = (self._TPL_DIR / "supervisor.html").read_text()
        assert "<inbox-badge" in src

    def test_supervisor_has_operator_input_form(self):
        src = (self._TPL_DIR / "supervisor.html").read_text()
        assert "operator-input-form" in src

    def test_supervisor_has_spawn_form(self):
        src = (self._TPL_DIR / "supervisor.html").read_text()
        assert "spawn-agent-form" in src

    def test_inbox_imports_approval_card(self):
        src = (self._TPL_DIR / "inbox.html").read_text()
        assert "approval-card.js" in src

    def test_inbox_uses_inbox_badge(self):
        src = (self._TPL_DIR / "inbox.html").read_text()
        assert "<inbox-badge" in src
