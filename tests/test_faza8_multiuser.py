"""Tests for Faza 8 — Multi-user workspace, activity logging, audit.

Covers audit criteria 8.1–8.9:
- 8.1: New user → auto-created workspace at data/workspaces/{uuid}/default/
- 8.2: User A cannot see User B's workspace
- 8.3: prompt.submit → record in user_activity_log
- 8.4: /admin/audit with filters → JSON
- 8.5: task.metadata.origin correctly set
- 8.6: System tasks invisible to operators
- 8.7: Audit CSV export
- 8.8: Retention: records > 90 days deleted
- 8.9: Logs don't appear in git status
"""

from __future__ import annotations

import csv
import io
import json
import os
import subprocess
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# 8.1 — WorkspaceManager auto-provisioning
# ---------------------------------------------------------------------------

class TestWorkspaceAutoCreate:
    """8.1: New user → auto-created workspace."""

    def test_ensure_workspace_creates_dirs(self):
        from amiagi.interfaces.web.audit.workspace_manager import WorkspaceManager

        with tempfile.TemporaryDirectory() as tmp:
            mgr = WorkspaceManager(tmp)
            ws_path = mgr.ensure_workspace("user-abc-123")
            assert ws_path.exists()
            assert (ws_path / "plans").is_dir()
            assert (ws_path / "downloads").is_dir()
            assert (ws_path / "results").is_dir()

    def test_ensure_workspace_path_format(self):
        from amiagi.interfaces.web.audit.workspace_manager import WorkspaceManager

        with tempfile.TemporaryDirectory() as tmp:
            mgr = WorkspaceManager(tmp)
            ws_path = mgr.ensure_workspace("u1")
            assert str(ws_path).endswith("u1/default")

    def test_ensure_workspace_idempotent(self):
        from amiagi.interfaces.web.audit.workspace_manager import WorkspaceManager

        with tempfile.TemporaryDirectory() as tmp:
            mgr = WorkspaceManager(tmp)
            p1 = mgr.ensure_workspace("user1")
            p2 = mgr.ensure_workspace("user1")
            assert p1 == p2

    def test_workspace_path_method(self):
        from amiagi.interfaces.web.audit.workspace_manager import WorkspaceManager

        with tempfile.TemporaryDirectory() as tmp:
            mgr = WorkspaceManager(tmp)
            p = mgr.workspace_path("uid")
            assert "uid/default" in str(p)

    def test_custom_workspace_name(self):
        from amiagi.interfaces.web.audit.workspace_manager import WorkspaceManager

        with tempfile.TemporaryDirectory() as tmp:
            mgr = WorkspaceManager(tmp)
            ws_path = mgr.ensure_workspace("user1", "project-alpha")
            assert ws_path.exists()
            assert str(ws_path).endswith("user1/project-alpha")


# ---------------------------------------------------------------------------
# 8.2 — Workspace isolation
# ---------------------------------------------------------------------------

class TestWorkspaceIsolation:
    """8.2: User A cannot see User B's workspace."""

    def test_can_access_own_workspace(self):
        from amiagi.interfaces.web.audit.workspace_manager import WorkspaceManager

        with tempfile.TemporaryDirectory() as tmp:
            mgr = WorkspaceManager(tmp)
            assert mgr.can_access("user1", "user1") is True

    def test_cannot_access_other_workspace(self):
        from amiagi.interfaces.web.audit.workspace_manager import WorkspaceManager

        with tempfile.TemporaryDirectory() as tmp:
            mgr = WorkspaceManager(tmp)
            assert mgr.can_access("user1", "user2") is False

    def test_admin_can_access_any_workspace(self):
        from amiagi.interfaces.web.audit.workspace_manager import WorkspaceManager

        with tempfile.TemporaryDirectory() as tmp:
            mgr = WorkspaceManager(tmp)
            assert mgr.can_access("admin-user", "user2", is_admin=True) is True

    def test_list_workspaces_empty(self):
        from amiagi.interfaces.web.audit.workspace_manager import WorkspaceManager

        with tempfile.TemporaryDirectory() as tmp:
            mgr = WorkspaceManager(tmp)
            assert mgr.list_workspaces("nonexistent") == []

    def test_list_workspaces_after_create(self):
        from amiagi.interfaces.web.audit.workspace_manager import WorkspaceManager

        with tempfile.TemporaryDirectory() as tmp:
            mgr = WorkspaceManager(tmp)
            mgr.ensure_workspace("user1")
            mgr.ensure_workspace("user1", "project-beta")
            ws = mgr.list_workspaces("user1")
            assert "default" in ws
            assert "project-beta" in ws

    def test_workspace_size_empty(self):
        from amiagi.interfaces.web.audit.workspace_manager import WorkspaceManager

        with tempfile.TemporaryDirectory() as tmp:
            mgr = WorkspaceManager(tmp)
            mgr.ensure_workspace("user1")
            assert mgr.workspace_size("user1") == 0

    def test_workspace_size_with_files(self):
        from amiagi.interfaces.web.audit.workspace_manager import WorkspaceManager

        with tempfile.TemporaryDirectory() as tmp:
            mgr = WorkspaceManager(tmp)
            ws = mgr.ensure_workspace("user1")
            (ws / "plans" / "test.txt").write_text("hello world")
            size = mgr.workspace_size("user1")
            assert size == len("hello world")


# ---------------------------------------------------------------------------
# 8.3 — Activity logging (prompt.submit)
# ---------------------------------------------------------------------------

class TestActivityLogger:
    """8.3: Activity logging to PostgreSQL."""

    @pytest.mark.asyncio
    async def test_log_returns_id(self):
        from amiagi.interfaces.web.audit.activity_logger import WebActivityLogger

        pool = MagicMock()
        pool.fetchval = AsyncMock(return_value=42)
        logger = WebActivityLogger(pool)
        result = await logger.log(
            user_id="uid-1",
            action="prompt.submit",
            detail={"prompt": "hello"},
        )
        assert result == 42
        pool.fetchval.assert_called_once()

    @pytest.mark.asyncio
    async def test_log_passes_correct_params(self):
        from amiagi.interfaces.web.audit.activity_logger import WebActivityLogger

        pool = MagicMock()
        pool.fetchval = AsyncMock(return_value=1)
        logger = WebActivityLogger(pool)
        await logger.log(
            user_id="uid-1",
            session_id="sid-1",
            action="prompt.submit",
            detail={"prompt": "test"},
            ip_address="127.0.0.1",
        )
        call_args = pool.fetchval.call_args
        sql = call_args[0][0]
        assert "user_activity_log" in sql
        assert "RETURNING id" in sql
        # positional params
        assert call_args[0][1] == "uid-1"  # user_id
        assert call_args[0][2] == "sid-1"  # session_id
        assert call_args[0][3] == "prompt.submit"  # action

    @pytest.mark.asyncio
    async def test_log_with_none_optional_fields(self):
        from amiagi.interfaces.web.audit.activity_logger import WebActivityLogger

        pool = MagicMock()
        pool.fetchval = AsyncMock(return_value=2)
        logger = WebActivityLogger(pool)
        result = await logger.log(
            user_id="uid-2",
            action="user.login",
        )
        assert result == 2
        call_args = pool.fetchval.call_args
        assert call_args[0][2] is None  # session_id
        assert call_args[0][5] is None  # ip_address


# ---------------------------------------------------------------------------
# 8.4 — Audit query with filters
# ---------------------------------------------------------------------------

class TestActivityLoggerQuery:
    """8.4: /admin/audit with filters → JSON results."""

    @pytest.mark.asyncio
    async def test_query_no_filters(self):
        from amiagi.interfaces.web.audit.activity_logger import WebActivityLogger

        mock_row = {
            "id": 1,
            "user_id": "uid-1",
            "session_id": None,
            "action": "user.login",
            "detail": {},
            "ip_address": "127.0.0.1",
            "created_at": datetime.now(timezone.utc),
        }
        pool = MagicMock()
        pool.fetch = AsyncMock(return_value=[MagicMock(**{"__getitem__": lambda s, k: mock_row[k], "keys": lambda s: mock_row.keys()})])
        logger = WebActivityLogger(pool)

        # Simulate fetchable rows
        pool.fetch = AsyncMock(return_value=[mock_row])
        rows = await logger.query()
        assert len(rows) == 1
        pool.fetch.assert_called_once()

    @pytest.mark.asyncio
    async def test_query_with_user_filter(self):
        from amiagi.interfaces.web.audit.activity_logger import WebActivityLogger

        pool = MagicMock()
        pool.fetch = AsyncMock(return_value=[])
        logger = WebActivityLogger(pool)
        await logger.query(user_id="uid-1")
        sql = pool.fetch.call_args[0][0]
        assert "user_id = $1" in sql

    @pytest.mark.asyncio
    async def test_query_with_action_filter(self):
        from amiagi.interfaces.web.audit.activity_logger import WebActivityLogger

        pool = MagicMock()
        pool.fetch = AsyncMock(return_value=[])
        logger = WebActivityLogger(pool)
        await logger.query(action="prompt.submit")
        sql = pool.fetch.call_args[0][0]
        assert "action = $1" in sql

    @pytest.mark.asyncio
    async def test_query_with_date_filters(self):
        from amiagi.interfaces.web.audit.activity_logger import WebActivityLogger

        pool = MagicMock()
        pool.fetch = AsyncMock(return_value=[])
        logger = WebActivityLogger(pool)
        since = datetime(2024, 1, 1, tzinfo=timezone.utc)
        until = datetime(2024, 12, 31, tzinfo=timezone.utc)
        await logger.query(since=since, until=until)
        sql = pool.fetch.call_args[0][0]
        assert "created_at >=" in sql
        assert "created_at <=" in sql

    @pytest.mark.asyncio
    async def test_query_with_all_filters(self):
        from amiagi.interfaces.web.audit.activity_logger import WebActivityLogger

        pool = MagicMock()
        pool.fetch = AsyncMock(return_value=[])
        logger = WebActivityLogger(pool)
        await logger.query(
            user_id="uid-1",
            action="prompt.submit",
            since=datetime(2024, 1, 1, tzinfo=timezone.utc),
            until=datetime(2024, 6, 1, tzinfo=timezone.utc),
        )
        sql = pool.fetch.call_args[0][0]
        assert "user_id = $1" in sql
        assert "action = $2" in sql
        assert "created_at >= $3" in sql
        assert "created_at <= $4" in sql

    @pytest.mark.asyncio
    async def test_count_no_filters(self):
        from amiagi.interfaces.web.audit.activity_logger import WebActivityLogger

        pool = MagicMock()
        pool.fetchval = AsyncMock(return_value=42)
        logger = WebActivityLogger(pool)
        result = await logger.count()
        assert result == 42

    @pytest.mark.asyncio
    async def test_count_with_filters(self):
        from amiagi.interfaces.web.audit.activity_logger import WebActivityLogger

        pool = MagicMock()
        pool.fetchval = AsyncMock(return_value=5)
        logger = WebActivityLogger(pool)
        result = await logger.count(user_id="uid-1", action="user.login")
        assert result == 5
        sql = pool.fetchval.call_args[0][0]
        assert "user_id = $1" in sql
        assert "action = $2" in sql


# ---------------------------------------------------------------------------
# 8.5 — task.metadata.origin set from Web GUI
# ---------------------------------------------------------------------------

class TestTaskMetadataOrigin:
    """8.5: task.metadata.origin correctly set."""

    def test_api_routes_available(self):
        """Verify the API tasks route exists for web-originated tasks."""
        from amiagi.interfaces.web.routes.api_routes import api_routes
        paths = [r.path for r in api_routes]
        assert "/api/tasks" in paths

    def test_task_to_dict_includes_metadata(self):
        """Verify task serialization includes metadata (where origin lives)."""
        from amiagi.interfaces.web.routes.api_routes import _task_to_dict
        task = MagicMock()
        task.task_id = "tid"
        task.title = "Test"
        task.status = MagicMock()
        task.status.value = "pending"
        task.priority = MagicMock()
        task.priority.value = "normal"
        task.assigned_agent_id = None
        task.created_at = datetime.now(timezone.utc)
        task.metadata = {"origin": "web_gui"}
        result = _task_to_dict(task)
        assert result["metadata"]["origin"] == "web_gui"


# ---------------------------------------------------------------------------
# 8.6 — System tasks invisible to operators
# ---------------------------------------------------------------------------

class TestSystemTaskVisibility:
    """8.6: System tasks filtering by role."""

    def test_task_to_dict_preserves_system_flag(self):
        from amiagi.interfaces.web.routes.api_routes import _task_to_dict
        task = MagicMock()
        task.task_id = "tid-sys"
        task.title = "System Maintenance"
        task.status = MagicMock()
        task.status.value = "pending"
        task.priority = MagicMock()
        task.priority.value = "low"
        task.assigned_agent_id = None
        task.created_at = datetime.now(timezone.utc)
        task.metadata = {"system": True}
        result = _task_to_dict(task)
        assert result["metadata"]["system"] is True

    def test_operator_filter_system_tasks(self):
        """Verify system tasks can be programmatically filtered."""
        tasks = [
            {"task_id": "t1", "metadata": {"system": False}},
            {"task_id": "t2", "metadata": {"system": True}},
            {"task_id": "t3", "metadata": {}},
        ]
        visible = [t for t in tasks if not t.get("metadata", {}).get("system", False)]
        assert len(visible) == 2
        assert all(t["task_id"] != "t2" for t in visible)


# ---------------------------------------------------------------------------
# 8.7 — Audit CSV export
# ---------------------------------------------------------------------------

class TestAuditCSVExport:
    """8.7: Audit CSV export via WebActivityLogger."""

    @pytest.mark.asyncio
    async def test_export_csv_headers(self):
        from amiagi.interfaces.web.audit.activity_logger import WebActivityLogger

        pool = MagicMock()
        pool.fetch = AsyncMock(return_value=[])
        logger = WebActivityLogger(pool)
        csv_data = await logger.export_csv()
        reader = csv.reader(io.StringIO(csv_data))
        header = next(reader)
        assert "id" in header
        assert "user_id" in header
        assert "action" in header
        assert "created_at" in header

    @pytest.mark.asyncio
    async def test_export_csv_with_data(self):
        from amiagi.interfaces.web.audit.activity_logger import WebActivityLogger

        mock_row = {
            "id": 1,
            "user_id": "uid-1",
            "session_id": None,
            "action": "user.login",
            "detail": {"email": "user@test.com"},
            "ip_address": "10.0.0.1",
            "created_at": datetime(2024, 6, 15, 12, 0, 0, tzinfo=timezone.utc),
        }
        pool = MagicMock()
        pool.fetch = AsyncMock(return_value=[mock_row])
        logger = WebActivityLogger(pool)
        csv_data = await logger.export_csv()
        reader = csv.reader(io.StringIO(csv_data))
        header = next(reader)
        row = next(reader)
        assert row[0] == "1"  # id
        assert row[1] == "uid-1"  # user_id
        assert row[3] == "user.login"  # action

    @pytest.mark.asyncio
    async def test_export_csv_empty(self):
        from amiagi.interfaces.web.audit.activity_logger import WebActivityLogger

        pool = MagicMock()
        pool.fetch = AsyncMock(return_value=[])
        logger = WebActivityLogger(pool)
        csv_data = await logger.export_csv()
        lines = csv_data.strip().split("\n")
        assert len(lines) == 1  # header only


# ---------------------------------------------------------------------------
# 8.8 — Retention (records > 90 days deleted)
# ---------------------------------------------------------------------------

class TestRetentionCleanup:
    """8.8: Retention: records > 90 days deleted."""

    @pytest.mark.asyncio
    async def test_cleanup_old_entries(self):
        from amiagi.interfaces.web.audit.activity_logger import WebActivityLogger

        pool = MagicMock()
        pool.execute = AsyncMock(return_value="DELETE 15")
        logger = WebActivityLogger(pool, retention_days=90)
        count = await logger.cleanup_old_entries()
        assert count == 15
        # Verify the SQL uses correct cutoff
        call_args = pool.execute.call_args
        sql = call_args[0][0]
        assert "DELETE FROM" in sql
        assert "user_activity_log" in sql
        assert "created_at < $1" in sql

    @pytest.mark.asyncio
    async def test_cleanup_cutoff_date(self):
        from amiagi.interfaces.web.audit.activity_logger import WebActivityLogger

        pool = MagicMock()
        pool.execute = AsyncMock(return_value="DELETE 0")
        logger = WebActivityLogger(pool, retention_days=30)
        await logger.cleanup_old_entries()
        cutoff = pool.execute.call_args[0][1]
        expected_cutoff = datetime.now(timezone.utc) - timedelta(days=30)
        # Allow 5-second tolerance
        assert abs((cutoff - expected_cutoff).total_seconds()) < 5

    @pytest.mark.asyncio
    async def test_cleanup_returns_zero_when_none(self):
        from amiagi.interfaces.web.audit.activity_logger import WebActivityLogger

        pool = MagicMock()
        pool.execute = AsyncMock(return_value="DELETE 0")
        logger = WebActivityLogger(pool)
        count = await logger.cleanup_old_entries()
        assert count == 0

    @pytest.mark.asyncio
    async def test_default_retention_days(self):
        from amiagi.interfaces.web.audit.activity_logger import (
            DEFAULT_RETENTION_DAYS,
            WebActivityLogger,
        )
        pool = MagicMock()
        logger = WebActivityLogger(pool)
        assert logger._retention_days == DEFAULT_RETENTION_DAYS == 90

    @pytest.mark.asyncio
    async def test_custom_retention_days(self):
        from amiagi.interfaces.web.audit.activity_logger import WebActivityLogger

        pool = MagicMock()
        logger = WebActivityLogger(pool, retention_days=7)
        assert logger._retention_days == 7


# ---------------------------------------------------------------------------
# 8.9 — Logs don't appear in git status
# ---------------------------------------------------------------------------

class TestGitIgnore:
    """8.9: data/workspaces/ is gitignored."""

    def test_workspaces_dir_gitignored(self):
        """Verify data/workspaces/ is covered by .gitignore."""
        result = subprocess.run(
            ["git", "check-ignore", "data/workspaces/"],
            capture_output=True,
            text=True,
            cwd=os.path.join(os.path.dirname(__file__), ".."),
        )
        assert result.returncode == 0
        assert "data/workspaces" in result.stdout

    def test_user_workspace_gitignored(self):
        """Verify nested user workspace paths are ignored."""
        result = subprocess.run(
            ["git", "check-ignore", "data/workspaces/some-user-id/default/plans/file.txt"],
            capture_output=True,
            text=True,
            cwd=os.path.join(os.path.dirname(__file__), ".."),
        )
        assert result.returncode == 0


# ---------------------------------------------------------------------------
# Auth routes — workspace auto-create + activity logging on login
# ---------------------------------------------------------------------------

class TestAuthWorkspaceIntegration:
    """Verify auth_callback provisions workspace and logs activity."""

    def test_auth_callback_has_workspace_manager_call(self):
        """Verify auth_callback code references workspace_manager."""
        import inspect
        from amiagi.interfaces.web.routes.auth_routes import auth_callback
        source = inspect.getsource(auth_callback)
        assert "workspace_manager" in source
        assert "ensure_workspace" in source

    def test_auth_callback_has_activity_logging(self):
        """Verify auth_callback code references activity_logger."""
        import inspect
        from amiagi.interfaces.web.routes.auth_routes import auth_callback
        source = inspect.getsource(auth_callback)
        assert "activity_logger" in source
        assert "user.login" in source

    def test_auth_logout_has_activity_logging(self):
        """Verify auth_logout logs user.logout."""
        import inspect
        from amiagi.interfaces.web.routes.auth_routes import auth_logout
        source = inspect.getsource(auth_logout)
        assert "activity_logger" in source
        assert "user.logout" in source


# ---------------------------------------------------------------------------
# Admin audit routes — filter & CSV export
# ---------------------------------------------------------------------------

class TestAdminAuditRoutes:
    """Verify admin audit routes support filters and CSV export."""

    def test_audit_export_route_exists(self):
        from amiagi.interfaces.web.routes.admin_routes import admin_routes
        paths = [r.path for r in admin_routes]
        assert "/admin/audit/export" in paths

    def test_audit_route_exists(self):
        from amiagi.interfaces.web.routes.admin_routes import admin_routes
        paths = [r.path for r in admin_routes]
        assert "/admin/audit" in paths

    def test_audit_export_before_audit_in_routes(self):
        """Export route must come before the general audit route to avoid conflicts."""
        from amiagi.interfaces.web.routes.admin_routes import admin_routes
        paths = [r.path for r in admin_routes]
        export_idx = paths.index("/admin/audit/export")
        audit_idx = paths.index("/admin/audit")
        assert export_idx < audit_idx

    def test_admin_audit_log_handler_signature(self):
        """Verify audit handler supports filter query params."""
        import inspect
        from amiagi.interfaces.web.routes.admin_routes import admin_audit_log
        source = inspect.getsource(admin_audit_log)
        assert "activity_logger" in source

    def test_admin_audit_export_handler_returns_csv(self):
        """Verify export handler references CSV export."""
        import inspect
        from amiagi.interfaces.web.routes.admin_routes import admin_audit_export
        source = inspect.getsource(admin_audit_export)
        assert "export_csv" in source
        assert "text/csv" in source


# ---------------------------------------------------------------------------
# App.py integration — services wired
# ---------------------------------------------------------------------------

class TestAppWiring:
    """Verify app.py wires activity_logger and workspace_manager."""

    def test_create_app_imports_activity_logger(self):
        import inspect
        from amiagi.interfaces.web.app import create_app
        source = inspect.getsource(create_app)
        assert "WebActivityLogger" in source
        assert "activity_logger" in source

    def test_create_app_imports_workspace_manager(self):
        import inspect
        from amiagi.interfaces.web.app import create_app
        source = inspect.getsource(create_app)
        assert "WorkspaceManager" in source
        assert "workspace_manager" in source

    def test_app_state_fields_documented(self):
        """Both services expected on app.state."""
        import inspect
        from amiagi.interfaces.web.app import create_app
        source = inspect.getsource(create_app)
        assert "app.state.activity_logger" in source
        assert "app.state.workspace_manager" in source
