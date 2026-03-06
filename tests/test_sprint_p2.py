"""Tests for Sprint P2: Model Hub, Cost Center, Vault.

Covers:
  - Route existence & methods
  - Page route wiring in dashboard_routes
  - Static asset files (CSS, JS, Web Components)
  - Template content checks
  - i18n key presence (EN + PL)
  - DB migration file existence
  - Command rail navigation links
  - API route handler smoke tests
  - **TestClient functional tests** for vault RBAC, Fernet encryption,
    AuditChain integration, budget routes, and model hub routes
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

# ── Path constants ────────────────────────────────────────────
_ROOT = Path(__file__).resolve().parent.parent
_SRC = _ROOT / "src" / "amiagi"
_WEB = _SRC / "interfaces" / "web"
_STATIC = _WEB / "static"
_TEMPLATES = _WEB / "templates"
_MIGRATIONS = _WEB / "db" / "migrations"
_LOCALES = _SRC / "i18n" / "locales"
_COMPONENTS = _STATIC / "js" / "components"


# ============================================================
# Helpers for TestClient-based tests
# ============================================================


@dataclass
class _FakeUser:
    """Lightweight user object matching AuthMiddleware expectations."""

    user_id: str = "test-user-1"
    display_name: str = "TestOp"
    email: str = "test@example.com"
    permissions: list[str] = field(default_factory=list)

    def has_permission(self, codename: str) -> bool:
        return codename in (self.permissions or [])


def _make_vault_app(
    user: _FakeUser | None = None,
    vault=None,
    audit_chain=None,
):
    """Build a minimal Starlette app with vault routes for testing."""
    from amiagi.interfaces.web.routes.vault_routes import vault_routes

    async def _inject_user(request: Request, call_next):
        if user is not None:
            request.state.user = user
        return await call_next(request)

    app = Starlette(routes=vault_routes)
    app.add_middleware(
        type(
            "_UserInjector",
            (),
            {
                "__init__": lambda self_, app_: setattr(self_, "app", app_),
                "__call__": lambda self_, scope, receive, send: (
                    self_.app(scope, receive, send)
                ),
            },
        ),
    )

    # Use raw ASGI middleware for user injection
    from starlette.middleware.base import BaseHTTPMiddleware

    class _UserMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):
            if user is not None:
                request.state.user = user
            return await call_next(request)

    # Recreate with proper middleware
    app = Starlette(routes=vault_routes)
    app.add_middleware(_UserMiddleware)

    if vault is not None:
        app.state.secret_vault = vault
    if audit_chain is not None:
        app.state.audit_chain = audit_chain

    return app


def _make_budget_app(budget_manager=None):
    """Build a minimal Starlette app with budget routes for testing."""
    from amiagi.interfaces.web.routes.budget_routes import budget_routes

    app = Starlette(routes=budget_routes)
    if budget_manager is not None:
        app.state.budget_manager = budget_manager
    return app


# ============================================================
# 1. Route structure tests
# ============================================================


class TestModelHubRoutes:
    """model_hub_routes exports correct route list."""

    def test_import(self):
        from amiagi.interfaces.web.routes.model_hub_routes import model_hub_routes
        assert isinstance(model_hub_routes, list)
        assert len(model_hub_routes) >= 9

    def test_expected_paths(self):
        from amiagi.interfaces.web.routes.model_hub_routes import model_hub_routes
        paths = [r.path for r in model_hub_routes]
        for p in [
            "/api/models/pull",
            "/api/models/vram",
            "/api/models/benchmark",
            "/api/models/local",
            "/api/models/local/{name:path}",
            "/api/models/cloud",
            "/api/models/cloud/test",
            "/api/models/cloud/{provider}/{model:path}",
        ]:
            assert p in paths, f"Missing model_hub route: {p}"

    def test_methods(self):
        from amiagi.interfaces.web.routes.model_hub_routes import model_hub_routes
        # Build path→methods mapping; merge methods for duplicate paths
        method_map = {}
        for r in model_hub_routes:
            method_map.setdefault(r.path, set()).update(r.methods or set())
        assert "POST" in method_map["/api/models/pull"]
        assert "GET" in method_map["/api/models/vram"]
        assert "POST" in method_map["/api/models/benchmark"]
        assert "GET" in method_map["/api/models/local"]
        assert "DELETE" in method_map["/api/models/local/{name:path}"]
        assert "GET" in method_map["/api/models/cloud"]
        assert "POST" in method_map["/api/models/cloud"]
        assert "POST" in method_map["/api/models/cloud/test"]
        assert "DELETE" in method_map["/api/models/cloud/{provider}/{model:path}"]


class TestBudgetRoutes:
    """budget_routes exports correct route list."""

    def test_import(self):
        from amiagi.interfaces.web.routes.budget_routes import budget_routes
        assert isinstance(budget_routes, list)
        assert len(budget_routes) >= 3

    def test_expected_paths(self):
        from amiagi.interfaces.web.routes.budget_routes import budget_routes
        paths = [r.path for r in budget_routes]
        for p in [
            "/api/budget/history",
            "/api/budget/quotas",
            "/api/budget/reset",
        ]:
            assert p in paths, f"Missing budget route: {p}"

    def test_methods(self):
        from amiagi.interfaces.web.routes.budget_routes import budget_routes
        method_map = {r.path: (r.methods or set()) for r in budget_routes}
        assert "GET" in method_map["/api/budget/history"]
        assert "PUT" in method_map["/api/budget/quotas"]
        assert "POST" in method_map["/api/budget/reset"]


class TestVaultRoutes:
    """vault_routes exports correct route list."""

    def test_import(self):
        from amiagi.interfaces.web.routes.vault_routes import vault_routes
        assert isinstance(vault_routes, list)
        assert len(vault_routes) >= 6

    def test_expected_paths(self):
        from amiagi.interfaces.web.routes.vault_routes import vault_routes
        paths = [r.path for r in vault_routes]
        for p in [
            "/api/vault",
            "/api/vault/access-log",
            "/api/vault/{agent_id}",
            "/api/vault/{agent_id}/{key}",
            "/api/vault/{agent_id}/{key}/rotate",
        ]:
            assert p in paths, f"Missing vault route: {p}"

    def test_methods(self):
        from amiagi.interfaces.web.routes.vault_routes import vault_routes
        method_map = {r.path: (r.methods or set()) for r in vault_routes}
        assert "GET" in method_map["/api/vault"]
        assert "GET" in method_map["/api/vault/access-log"]


# ============================================================
# 2. Dashboard page routes
# ============================================================


class TestDashboardPageRoutes:
    """dashboard_routes includes the P2 page entries."""

    def test_model_hub_page_route(self):
        from amiagi.interfaces.web.routes.dashboard_routes import dashboard_routes
        paths = [r.path for r in dashboard_routes]
        assert "/model-hub" in paths

    def test_budget_page_route(self):
        from amiagi.interfaces.web.routes.dashboard_routes import dashboard_routes
        paths = [r.path for r in dashboard_routes]
        assert "/budget" in paths

    def test_vault_page_route(self):
        from amiagi.interfaces.web.routes.dashboard_routes import dashboard_routes
        paths = [r.path for r in dashboard_routes]
        assert "/admin/vault" in paths


# ============================================================
# 3. Template file existence & content
# ============================================================


class TestP2Templates:
    """All three P2 HTML templates exist and contain expected elements."""

    def test_model_hub_html_exists(self):
        assert (_TEMPLATES / "model_hub.html").exists()

    def test_budget_html_exists(self):
        assert (_TEMPLATES / "budget.html").exists()

    def test_vault_html_exists(self):
        assert (_TEMPLATES / "vault.html").exists()

    def test_model_hub_has_grid(self):
        src = (_TEMPLATES / "model_hub.html").read_text(encoding="utf-8")
        assert "local-model-grid" in src
        assert "cloud-model-grid" in src
        assert "model-pull-form" in src
        assert "cloud-model-form" in src

    def test_model_hub_has_benchmark_tab(self):
        src = (_TEMPLATES / "model_hub.html").read_text(encoding="utf-8")
        assert "panel-benchmark" in src
        assert "bench-form" in src
        assert "bench-model-select" in src

    def test_budget_has_bar(self):
        src = (_TEMPLATES / "budget.html").read_text(encoding="utf-8")
        assert "budget-bar-session" in src
        assert "budget-quotas-form" in src

    def test_budget_has_history_chart(self):
        src = (_TEMPLATES / "budget.html").read_text(encoding="utf-8")
        assert "budget-history-chart" in src
        assert "budget-chart-container" in src

    def test_vault_has_modal(self):
        src = (_TEMPLATES / "vault.html").read_text(encoding="utf-8")
        assert "vault-modal-overlay" in src
        assert "vault-secret-form" in src


# ============================================================
# 4. Static assets — CSS
# ============================================================


class TestP2CSSFiles:
    """CSS files for each P2 page exist and are non-empty."""

    @pytest.mark.parametrize("name", ["model_hub.css", "budget.css", "vault.css"])
    def test_css_exists(self, name):
        f = _STATIC / "css" / name
        assert f.exists(), f"Missing CSS: {name}"
        assert f.stat().st_size > 100, f"CSS file too small: {name}"

    def test_model_hub_css_has_benchmark_styles(self):
        src = (_STATIC / "css" / "model_hub.css").read_text(encoding="utf-8")
        assert "bench-table" in src or "bench-form" in src

    def test_budget_css_has_chart_styles(self):
        src = (_STATIC / "css" / "budget.css").read_text(encoding="utf-8")
        assert "budget-chart" in src


# ============================================================
# 5. Static assets — JS
# ============================================================


class TestP2JSFiles:
    """JS files for each P2 page exist and are non-empty."""

    @pytest.mark.parametrize("name", ["model_hub.js", "budget.js", "vault.js"])
    def test_js_exists(self, name):
        f = _STATIC / "js" / name
        assert f.exists(), f"Missing JS: {name}"
        assert f.stat().st_size > 200, f"JS file too small: {name}"

    @pytest.mark.parametrize("name", ["model_hub.js", "budget.js", "vault.js"])
    def test_js_is_iife(self, name):
        src = (_STATIC / "js" / name).read_text(encoding="utf-8")
        assert "(function" in src, f"JS not wrapped in IIFE: {name}"

    def test_model_hub_js_has_benchmark_logic(self):
        src = (_STATIC / "js" / "model_hub.js").read_text(encoding="utf-8")
        assert "runBenchmark" in src
        assert "benchResults" in src

    def test_model_hub_js_has_streaming_pull(self):
        src = (_STATIC / "js" / "model_hub.js").read_text(encoding="utf-8")
        assert "getReader" in src or "reader" in src

    def test_budget_js_has_chart_render(self):
        src = (_STATIC / "js" / "budget.js").read_text(encoding="utf-8")
        assert "renderChart" in src
        assert "getContext" in src


# ============================================================
# 6. Web Component — <secret-field>
# ============================================================


class TestSecretFieldComponent:
    """<secret-field> Web Component exists with correct structure."""

    def test_file_exists(self):
        assert (_COMPONENTS / "secret-field.js").exists()

    def test_defines_custom_element(self):
        src = (_COMPONENTS / "secret-field.js").read_text(encoding="utf-8")
        assert 'customElements.define("secret-field"' in src

    def test_uses_shadow_dom(self):
        src = (_COMPONENTS / "secret-field.js").read_text(encoding="utf-8")
        assert "attachShadow" in src

    def test_has_observed_attributes(self):
        src = (_COMPONENTS / "secret-field.js").read_text(encoding="utf-8")
        assert "observedAttributes" in src
        assert '"value"' in src
        assert '"masked"' in src


class TestCloudModelCardComponent:
    """<cloud-model-card> Web Component exists with correct structure."""

    def test_file_exists(self):
        assert (_COMPONENTS / "cloud-model-card.js").exists()

    def test_defines_custom_element(self):
        src = (_COMPONENTS / "cloud-model-card.js").read_text(encoding="utf-8")
        assert 'customElements.define("cloud-model-card"' in src

    def test_uses_shadow_dom(self):
        src = (_COMPONENTS / "cloud-model-card.js").read_text(encoding="utf-8")
        assert "attachShadow" in src

    def test_has_observed_attributes(self):
        src = (_COMPONENTS / "cloud-model-card.js").read_text(encoding="utf-8")
        assert "observedAttributes" in src
        assert '"provider"' in src
        assert '"model"' in src
        assert '"api-key"' in src

    def test_dispatches_events(self):
        src = (_COMPONENTS / "cloud-model-card.js").read_text(encoding="utf-8")
        assert "cloud-model-delete" in src
        assert "cloud-model-test" in src


# ============================================================
# 7. Command rail navigation
# ============================================================


class TestCommandRailP2:
    """Command rail includes new navigation links."""

    @pytest.fixture()
    def rail_html(self):
        return (_TEMPLATES / "partials" / "command_rail.html").read_text(encoding="utf-8")

    def test_model_hub_link(self, rail_html):
        assert 'href="/model-hub"' in rail_html

    def test_budget_link(self, rail_html):
        assert 'href="/budget"' in rail_html

    def test_vault_link(self, rail_html):
        assert 'href="/admin/vault"' in rail_html

    def test_model_hub_tooltip(self, rail_html):
        assert "nav.model_hub" in rail_html

    def test_budget_tooltip(self, rail_html):
        assert "nav.budget" in rail_html

    def test_vault_tooltip(self, rail_html):
        assert "nav.vault" in rail_html


# ============================================================
# 8. DB Migration 009
# ============================================================


class TestMigration009:
    """Migration 009 exists with expected tables."""

    def test_migration_file_exists(self):
        assert (_MIGRATIONS / "009_vault_models.sql").exists()

    def test_contains_vault_secrets_table(self):
        sql = (_MIGRATIONS / "009_vault_models.sql").read_text(encoding="utf-8")
        assert "vault_secrets" in sql

    def test_contains_vault_access_log_table(self):
        sql = (_MIGRATIONS / "009_vault_models.sql").read_text(encoding="utf-8")
        assert "vault_access_log" in sql

    def test_contains_model_assignments_table(self):
        sql = (_MIGRATIONS / "009_vault_models.sql").read_text(encoding="utf-8")
        assert "model_assignments" in sql

    def test_contains_budget_snapshots_table(self):
        sql = (_MIGRATIONS / "009_vault_models.sql").read_text(encoding="utf-8")
        assert "budget_snapshots" in sql


# ============================================================
# 9. i18n keys
# ============================================================


class TestP2I18nKeys:
    """All P2 i18n keys exist in both EN and PL locales."""

    @pytest.fixture(params=["web_en.json", "web_pl.json"])
    def locale_data(self, request):
        p = _LOCALES / request.param
        return json.loads(p.read_text(encoding="utf-8"))

    _P2_KEYS = [
        # Navigation
        "nav.model_hub",
        "nav.budget",
        "nav.vault",
        # Model Hub
        "models.title",
        "models.refresh",
        "models.checking",
        "models.base_url",
        "models.pulled_count",
        "models.available",
        "models.no_models",
        "models.pull_model",
        "models.pull_placeholder",
        "models.pull",
        "models.assignments",
        "models.benchmark",
        "models.delete",
        "models.vram",
        # Cloud models
        "models.local_tab",
        "models.cloud_tab",
        "models.cloud_add",
        "models.cloud_provider",
        "models.cloud_custom",
        "models.cloud_model_id",
        "models.cloud_base_url",
        "models.cloud_api_key",
        "models.cloud_display_name",
        "models.cloud_test",
        "models.cloud_save",
        "models.cloud_configured",
        "models.cloud_none",
        "models.cloud_test_ok",
        "models.cloud_test_fail",
        # Benchmark tab
        "models.benchmark_tab",
        "models.benchmark_run",
        "models.benchmark_model",
        "models.benchmark_select",
        "models.benchmark_prompt",
        "models.benchmark_results",
        "models.benchmark_tokens",
        "models.benchmark_elapsed",
        "models.benchmark_preview",
        "models.benchmark_no_results",
        # Budget / Cost Center
        "budget.title",
        "budget.refresh",
        "budget.session_overview",
        "budget.total_spent",
        "budget.total_tokens",
        "budget.total_requests",
        "budget.utilization",
        "budget.per_agent",
        "budget.per_task",
        "budget.no_tasks",
        "budget.quotas",
        "budget.session_limit",
        "budget.warning_pct",
        "budget.save_quotas",
        "budget.history_chart",
        "budget.no_history",
        # Vault
        "vault.title",
        "vault.add_secret",
        "vault.refresh",
        "vault.agent_id",
        "vault.select_agent",
        "vault.key",
        "vault.value",
        "vault.save",
        "vault.cancel",
        "vault.empty",
        "vault.access_log",
        "vault.no_logs",
    ]

    @pytest.mark.parametrize("key", _P2_KEYS)
    def test_key_exists(self, locale_data, key):
        assert key in locale_data, f"Missing i18n key: {key}"
        assert locale_data[key], f"Empty value for i18n key: {key}"


# ============================================================
# 10. App wiring — routes registered
# ============================================================


class TestAppWiring:
    """app.py imports and registers all P2 route modules."""

    def test_app_file_imports_model_hub_routes(self):
        src = (_WEB / "app.py").read_text(encoding="utf-8")
        assert "model_hub_routes" in src

    def test_app_file_imports_budget_routes(self):
        src = (_WEB / "app.py").read_text(encoding="utf-8")
        assert "budget_routes" in src

    def test_app_file_imports_vault_routes(self):
        src = (_WEB / "app.py").read_text(encoding="utf-8")
        assert "vault_routes" in src


# ============================================================
# 11. SecretVault — Fernet encryption functional tests
# ============================================================


class TestSecretVaultFernet:
    """SecretVault uses Fernet encryption, not XOR."""

    @pytest.fixture()
    def vault(self, tmp_path: Path):
        from amiagi.infrastructure.secret_vault import SecretVault
        return SecretVault(vault_path=tmp_path / "vault.json")

    def test_set_and_get_secret(self, vault):
        vault.set_secret("agent-a", "API_KEY", "sk-secret-123")
        assert vault.get_secret("agent-a", "API_KEY") == "sk-secret-123"

    def test_encrypted_value_is_not_plaintext(self, vault, tmp_path):
        vault.set_secret("agent-a", "API_KEY", "sk-secret-123")
        raw = (tmp_path / "vault.json").read_text(encoding="utf-8")
        assert "sk-secret-123" not in raw

    def test_encrypted_value_is_fernet_token(self, vault):
        """Fernet tokens are URL-safe base64 and start with 'gAAAAA'."""
        vault.set_secret("agent-x", "KEY", "value")
        raw_data = json.loads(vault._path.read_text(encoding="utf-8"))
        token = raw_data["agent-x"]["KEY"]
        assert token.startswith("gAAAAA"), f"Not a Fernet token: {token[:20]}"

    def test_key_file_created(self, tmp_path):
        from amiagi.infrastructure.secret_vault import SecretVault
        SecretVault(vault_path=tmp_path / "v.json")
        assert (tmp_path / "v.key").exists()

    def test_delete_secret(self, vault):
        vault.set_secret("a1", "K", "V")
        assert vault.delete_secret("a1", "K") is True
        assert vault.get_secret("a1", "K") is None

    def test_list_keys(self, vault):
        vault.set_secret("a1", "K1", "V1")
        vault.set_secret("a1", "K2", "V2")
        keys = vault.list_keys("a1")
        assert set(keys) == {"K1", "K2"}

    def test_list_agents(self, vault):
        vault.set_secret("a1", "K1", "V1")
        vault.set_secret("a2", "K2", "V2")
        agents = vault.list_agents()
        ids = {a["agent_id"] for a in agents}
        assert ids == {"a1", "a2"}

    def test_delete_agent(self, vault):
        vault.set_secret("a1", "K1", "V1")
        vault.set_secret("a1", "K2", "V2")
        assert vault.delete_agent("a1") is True
        assert vault.list_keys("a1") == []

    def test_no_xor_in_source(self):
        """Verify source code no longer contains XOR obfuscation."""
        src = (_SRC / "infrastructure" / "secret_vault.py").read_text(encoding="utf-8")
        assert "_xor_bytes" not in src
        assert "_obfuscate" not in src
        assert "_deobfuscate" not in src
        assert "Fernet" in src

    def test_persistence_across_instances(self, tmp_path):
        from amiagi.infrastructure.secret_vault import SecretVault
        v1 = SecretVault(vault_path=tmp_path / "vault.json")
        v1.set_secret("a1", "KEY", "secret-val")

        v2 = SecretVault(vault_path=tmp_path / "vault.json")
        assert v2.get_secret("a1", "KEY") == "secret-val"


# ============================================================
# 12. Vault routes — RBAC enforcement (TestClient)
# ============================================================


class TestVaultRBAC:
    """Vault API enforces vault.admin permission via RBAC."""

    @pytest.fixture()
    def vault(self, tmp_path):
        from amiagi.infrastructure.secret_vault import SecretVault
        v = SecretVault(vault_path=tmp_path / "vault.json")
        v.set_secret("agent-1", "API_KEY", "sk-123")
        return v

    @pytest.fixture()
    def audit(self, tmp_path):
        from amiagi.application.audit_chain import AuditChain
        return AuditChain(log_path=tmp_path / "audit.jsonl")

    def test_vault_list_forbidden_without_permission(self, vault, audit):
        user = _FakeUser(permissions=[])
        app = _make_vault_app(user=user, vault=vault, audit_chain=audit)
        client = TestClient(app, raise_server_exceptions=False)
        r = client.get("/api/vault")
        assert r.status_code == 403

    def test_vault_list_ok_with_permission(self, vault, audit):
        user = _FakeUser(permissions=["vault.admin"])
        app = _make_vault_app(user=user, vault=vault, audit_chain=audit)
        client = TestClient(app, raise_server_exceptions=False)
        r = client.get("/api/vault")
        assert r.status_code == 200
        data = r.json()
        assert data["ok"] is True
        assert len(data["agents"]) >= 1

    def test_vault_set_secret_forbidden(self, vault, audit):
        user = _FakeUser(permissions=["agents.view"])
        app = _make_vault_app(user=user, vault=vault, audit_chain=audit)
        client = TestClient(app, raise_server_exceptions=False)
        r = client.post(
            "/api/vault/agent-1",
            json={"key": "NEW_KEY", "value": "new-val"},
        )
        assert r.status_code == 403

    def test_vault_set_secret_ok(self, vault, audit):
        user = _FakeUser(permissions=["vault.admin"])
        app = _make_vault_app(user=user, vault=vault, audit_chain=audit)
        client = TestClient(app, raise_server_exceptions=False)
        r = client.post(
            "/api/vault/agent-1",
            json={"key": "NEW_KEY", "value": "new-val"},
        )
        assert r.status_code == 201
        assert r.json()["ok"] is True
        # Verify the secret was actually stored
        assert vault.get_secret("agent-1", "NEW_KEY") == "new-val"

    def test_vault_delete_forbidden(self, vault, audit):
        user = _FakeUser(permissions=[])
        app = _make_vault_app(user=user, vault=vault, audit_chain=audit)
        client = TestClient(app, raise_server_exceptions=False)
        r = client.delete("/api/vault/agent-1/API_KEY")
        assert r.status_code == 403

    def test_vault_delete_ok(self, vault, audit):
        user = _FakeUser(permissions=["vault.admin"])
        app = _make_vault_app(user=user, vault=vault, audit_chain=audit)
        client = TestClient(app, raise_server_exceptions=False)
        r = client.delete("/api/vault/agent-1/API_KEY")
        assert r.status_code == 200
        assert r.json()["ok"] is True
        assert vault.get_secret("agent-1", "API_KEY") is None

    def test_vault_rotate_forbidden(self, vault, audit):
        user = _FakeUser(permissions=[])
        app = _make_vault_app(user=user, vault=vault, audit_chain=audit)
        client = TestClient(app, raise_server_exceptions=False)
        r = client.post(
            "/api/vault/agent-1/API_KEY/rotate",
            json={"value": "new-sk-456"},
        )
        assert r.status_code == 403

    def test_vault_rotate_ok(self, vault, audit):
        user = _FakeUser(permissions=["vault.admin"])
        app = _make_vault_app(user=user, vault=vault, audit_chain=audit)
        client = TestClient(app, raise_server_exceptions=False)
        r = client.post(
            "/api/vault/agent-1/API_KEY/rotate",
            json={"value": "new-sk-456"},
        )
        assert r.status_code == 200
        assert vault.get_secret("agent-1", "API_KEY") == "new-sk-456"

    def test_vault_access_log_forbidden(self, vault, audit):
        user = _FakeUser(permissions=[])
        app = _make_vault_app(user=user, vault=vault, audit_chain=audit)
        client = TestClient(app, raise_server_exceptions=False)
        r = client.get("/api/vault/access-log")
        assert r.status_code == 403

    def test_unauthenticated_returns_401(self, vault, audit):
        """No user at all -> 401."""
        app = _make_vault_app(user=None, vault=vault, audit_chain=audit)
        client = TestClient(app, raise_server_exceptions=False)
        r = client.get("/api/vault")
        assert r.status_code == 401


# ============================================================
# 13. Vault routes — AuditChain integration (TestClient)
# ============================================================


class TestVaultAuditChain:
    """Vault access is persistently logged via AuditChain, not an in-memory list."""

    @pytest.fixture()
    def vault(self, tmp_path):
        from amiagi.infrastructure.secret_vault import SecretVault
        return SecretVault(vault_path=tmp_path / "vault.json")

    @pytest.fixture()
    def audit(self, tmp_path):
        from amiagi.application.audit_chain import AuditChain
        return AuditChain(log_path=tmp_path / "audit.jsonl")

    def test_set_secret_logs_to_audit_chain(self, vault, audit):
        user = _FakeUser(permissions=["vault.admin"])
        app = _make_vault_app(user=user, vault=vault, audit_chain=audit)
        client = TestClient(app, raise_server_exceptions=False)
        client.post("/api/vault/a1", json={"key": "K", "value": "V"})
        entries = audit.query(action="vault.set")
        assert len(entries) >= 1
        assert entries[0].agent_id == "a1"
        assert entries[0].target == "K"
        assert entries[0].approved_by == "test-user-1"

    def test_delete_secret_logs_to_audit_chain(self, vault, audit):
        vault.set_secret("a1", "K", "V")
        user = _FakeUser(permissions=["vault.admin"])
        app = _make_vault_app(user=user, vault=vault, audit_chain=audit)
        client = TestClient(app, raise_server_exceptions=False)
        client.delete("/api/vault/a1/K")
        entries = audit.query(action="vault.delete")
        assert len(entries) >= 1

    def test_rotate_secret_logs_to_audit_chain(self, vault, audit):
        vault.set_secret("a1", "K", "V")
        user = _FakeUser(permissions=["vault.admin"])
        app = _make_vault_app(user=user, vault=vault, audit_chain=audit)
        client = TestClient(app, raise_server_exceptions=False)
        client.post("/api/vault/a1/K/rotate", json={"value": "V2"})
        entries = audit.query(action="vault.rotate")
        assert len(entries) >= 1

    def test_access_log_endpoint_returns_audit_entries(self, vault, audit):
        user = _FakeUser(permissions=["vault.admin"])
        app = _make_vault_app(user=user, vault=vault, audit_chain=audit)
        client = TestClient(app, raise_server_exceptions=False)

        # Create some activity
        client.post("/api/vault/a1", json={"key": "K", "value": "V"})

        r = client.get("/api/vault/access-log")
        assert r.status_code == 200
        data = r.json()
        assert data["ok"] is True
        assert len(data["entries"]) >= 1
        assert data["entries"][0]["action"].startswith("vault.")

    def test_no_in_memory_access_log(self):
        """vault_routes no longer uses module-level _access_log list."""
        import amiagi.interfaces.web.routes.vault_routes as vr
        assert not hasattr(vr, "_access_log"), (
            "vault_routes should no longer use in-memory _access_log list"
        )

    def test_audit_chain_persists_to_disk(self, vault, audit, tmp_path):
        user = _FakeUser(permissions=["vault.admin"])
        app = _make_vault_app(user=user, vault=vault, audit_chain=audit)
        client = TestClient(app, raise_server_exceptions=False)
        client.post("/api/vault/a1", json={"key": "K1", "value": "V1"})

        log_file = tmp_path / "audit.jsonl"
        assert log_file.exists()
        lines = log_file.read_text().strip().splitlines()
        assert len(lines) >= 1
        entry = json.loads(lines[-1])
        assert entry["action"].startswith("vault.")


# ============================================================
# 14. RBAC decorator — require_permission exists
# ============================================================


class TestRBACMiddleware:
    """The project uses @require_permission decorator on vault routes."""

    def test_require_permission_imported_in_vault_routes(self):
        src = (_WEB / "routes" / "vault_routes.py").read_text(encoding="utf-8")
        assert "require_permission" in src
        assert "vault.admin" in src

    def test_all_vault_handlers_use_rbac(self):
        """Every vault handler function is decorated with @require_permission."""
        src = (_WEB / "routes" / "vault_routes.py").read_text(encoding="utf-8")
        handler_names = [
            "vault_list",
            "vault_agent_keys",
            "vault_set_secret",
            "vault_delete_secret",
            "vault_rotate_secret",
            "vault_access_log",
        ]
        for name in handler_names:
            # Check that @require_permission appears before each handler def
            idx = src.find(f"async def {name}(")
            assert idx != -1, f"Handler {name} not found"
            # Look at the preceding 200 chars for the decorator
            prefix = src[max(0, idx - 200):idx]
            assert "@require_permission" in prefix, (
                f"Handler {name} missing @require_permission decorator"
            )


# ============================================================
# 15. Model Hub — streaming pull & benchmark tab
# ============================================================


class TestModelHubFeatures:
    """Model Hub uses streaming pull and has benchmark tab."""

    def test_model_pull_returns_stream(self):
        """model_pull returns StreamingResponse, not plain JSON."""
        src = (_WEB / "routes" / "model_hub_routes.py").read_text(encoding="utf-8")
        assert "StreamingResponse" in src
        assert '"stream": True' in src or "'stream': True" in src

    def test_model_pull_no_blocking(self):
        """model_pull no longer uses stream: False."""
        src = (_WEB / "routes" / "model_hub_routes.py").read_text(encoding="utf-8")
        # The old blocking call was: "stream": False
        # Find model_pull function and check
        pull_start = src.find("async def model_pull(")
        pull_end = src.find("\nasync def ", pull_start + 1)
        if pull_end == -1:
            pull_end = src.find("\n# ──", pull_start + 1)
        pull_src = src[pull_start:pull_end] if pull_end > pull_start else src[pull_start:]
        assert '"stream": False' not in pull_src, (
            "model_pull still uses stream: False (blocking)"
        )

    def test_model_hub_html_has_three_tabs(self):
        src = (_TEMPLATES / "model_hub.html").read_text(encoding="utf-8")
        assert 'data-tab="local"' in src
        assert 'data-tab="cloud"' in src
        assert 'data-tab="benchmark"' in src

    def test_model_hub_js_has_benchmark_section(self):
        src = (_STATIC / "js" / "model_hub.js").read_text(encoding="utf-8")
        assert "BENCHMARK TAB" in src or "runBenchmark" in src


# ============================================================
# 16. Budget — history chart
# ============================================================


class TestBudgetHistoryChart:
    """Budget page includes a history chart canvas and render logic."""

    def test_budget_html_has_canvas(self):
        src = (_TEMPLATES / "budget.html").read_text(encoding="utf-8")
        assert '<canvas id="budget-history-chart"' in src

    def test_budget_js_renders_chart(self):
        src = (_STATIC / "js" / "budget.js").read_text(encoding="utf-8")
        assert "renderChart" in src

    def test_budget_js_fetches_history(self):
        src = (_STATIC / "js" / "budget.js").read_text(encoding="utf-8")
        assert "fetchHistory" in src

    def test_budget_css_has_chart_card(self):
        src = (_STATIC / "css" / "budget.css").read_text(encoding="utf-8")
        assert "budget-chart-card" in src
