from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from starlette.applications import Starlette
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.routing import Route
from starlette.testclient import TestClient

from amiagi.interfaces.web.routes.admin_routes import admin_routes


@dataclass
class _FakeUser:
    email: str = "admin@example.com"
    display_name: str = "Admin"
    user_id: str = "uid"
    session_id: str = "sid"
    permissions: list[str] = field(default_factory=lambda: ["admin.audit", "admin.users", "admin.roles"])
    roles: list[str] = field(default_factory=lambda: ["admin"])


class _FakeActivityLogger:
    def __init__(self, retention_days: int | None = 90):
        self._retention_days = retention_days
        self.query = AsyncMock(return_value=[])
        self.count = AsyncMock(return_value=0)
        self.export_rows = AsyncMock(return_value=[])
        self.export_csv = AsyncMock(return_value="id,user_id\n")
        self.log = AsyncMock(return_value=1)


class _FakeAuditRetentionStore:
    def __init__(self, found: bool = False, retention_days: int | None = None):
        self.found = found
        self.retention_days = retention_days
        self.saved: list[int | None] = []

    def load(self):
        return self.found, self.retention_days

    def save(self, retention_days: int | None) -> None:
        self.saved.append(retention_days)
        self.found = True
        self.retention_days = retention_days


def _make_app(*, activity_logger=None, db_pool=None, audit_retention_store=None):
    class _InjectUser(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):
            request.state.user = _FakeUser()
            return await call_next(request)

    app = Starlette(routes=list(admin_routes))
    app.state.activity_logger = activity_logger
    app.state.db_pool = db_pool or MagicMock()
    app.state.audit_retention_store = audit_retention_store
    app.add_middleware(_InjectUser)
    return app


class TestAuditClientSideRoutes:
    def test_admin_audit_supports_group_filters_limit_and_search(self):
        logger = _FakeActivityLogger()
        client = TestClient(_make_app(activity_logger=logger))

        response = client.get("/admin/audit?action=login&limit=20&q=auth&error_only=1")

        assert response.status_code == 200
        logger.query.assert_awaited_once_with(
            user_id=None,
            action="login",
            action_match="contains",
            since=None,
            until=None,
            session_id=None,
            search="auth",
            error_only=True,
            limit=20,
            offset=0,
        )
        logger.count.assert_awaited_once_with(
            user_id=None,
            action="login",
            action_match="contains",
            since=None,
            until=None,
            session_id=None,
            search="auth",
            error_only=True,
        )
        assert response.json()["retention_days"] == 90

    def test_admin_audit_retention_route_updates_runtime_setting(self):
        logger = _FakeActivityLogger(retention_days=90)
        store = _FakeAuditRetentionStore(found=True, retention_days=90)
        client = TestClient(_make_app(activity_logger=logger, audit_retention_store=store))

        response = client.put("/admin/audit/retention", json={"retention_days": "forever"})

        assert response.status_code == 200
        assert response.json()["retention_days"] is None
        assert logger._retention_days is None
        assert store.saved == [None]
        logger.log.assert_awaited_once()

    def test_admin_audit_reads_persisted_retention_policy(self):
        logger = _FakeActivityLogger(retention_days=30)
        store = _FakeAuditRetentionStore(found=True, retention_days=None)
        client = TestClient(_make_app(activity_logger=logger, audit_retention_store=store))

        response = client.get("/admin/audit")

        assert response.status_code == 200
        assert response.json()["retention_days"] is None
        assert logger._retention_days is None

    def test_admin_audit_fallback_applies_filters(self):
        pool = MagicMock()
        fake_conn = AsyncMock()
        fake_conn.fetchval.return_value = 1
        fake_conn.fetch.return_value = [{
            "id": 1,
            "user_id": "uid-1",
            "session_id": "sess-1",
            "email": "user@example.com",
            "action": "user.login",
            "detail": {"status": "ok"},
            "ip_address": "127.0.0.1",
            "created_at": None,
        }]
        pool.acquire.return_value.__aenter__ = AsyncMock(return_value=fake_conn)
        pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

        client = TestClient(_make_app(activity_logger=None, db_pool=pool))
        response = client.get("/admin/audit?action=login&limit=10&user=uid-1")

        assert response.status_code == 200
        fetchval_sql = fake_conn.fetchval.await_args.args[0]
        fetch_sql = fake_conn.fetch.await_args.args[0]
        assert "al.user_id = $1" in fetchval_sql
        assert "LOWER(al.action) LIKE LOWER($2)" in fetchval_sql
        assert "LOWER(al.action) LIKE LOWER($2)" in fetch_sql
        assert response.json()["items"][0]["session_id"] == "sess-1"


class TestAuditClientAssets:
    def test_audit_template_and_script_expose_new_controls(self):
        root = Path(__file__).resolve().parents[1]
        template = (root / "src/amiagi/interfaces/web/templates/admin/audit.html").read_text(encoding="utf-8")
        script = (root / "src/amiagi/interfaces/web/static/js/audit.js").read_text(encoding="utf-8")

        assert "filter-search" in template
        assert "audit-retention-select" in template
        assert "btn-retention-save" in template
        assert "auditOpenUser" in script
        assert "Open session replay" in script
        assert "/admin/audit/retention" in script


class TestAuditRetentionStore:
    def test_file_store_roundtrip_supports_forever_policy(self, tmp_path: Path) -> None:
        from amiagi.interfaces.web.audit.retention_store import AuditRetentionStore

        store = AuditRetentionStore(tmp_path / "audit_retention.json")
        assert store.load() == (False, None)

        store.save(None)
        assert store.load() == (True, None)

        store.save(30)
        assert store.load() == (True, 30)
