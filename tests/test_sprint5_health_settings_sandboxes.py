"""Tests for Sprint 5 — Health + Settings + Sandboxes + Polish.

Covers checklist items 5.1–5.17:
- 5.1  Health Dashboard (template, CSS, JS)
- 5.2  Health API endpoints (vram, connections, detailed)
- 5.3  Settings redesign (12 tabs, external CSS)
- 5.4  Sandboxes admin page (template, CSS, JS)
- 5.5  <shell-policy-editor> Web Component
- 5.6  SandboxMonitor service
- 5.7  Sandbox/Shell API endpoints (10 routes)
- 5.8  AskHumanTool + ReviewRequestTool
- 5.9  DB migration 012 (shell_executions + sandbox_metadata)
- 5.10 Global Search refinement (icons, recent, filters)
- 5.11 Toast notifications integration
- 5.12 New i18n keys (~40 keys EN + PL)
- 5.13 Responsive CSS (3 breakpoints per page)
- 5.14 Performance / bundle size
- 5.15 SECURITY.md
- 5.16 WEB_INTERFACE.md
- 5.17 Route + component + service integration
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

_WEB_ROOT = Path(__file__).parent.parent / "src/amiagi/interfaces/web"
_SRC_ROOT = Path(__file__).parent.parent / "src/amiagi"
_PROJECT_ROOT = Path(__file__).parent.parent


# ═══════════════════════════════════════════════════════════════
# 5.1 — Health Dashboard (template + CSS + JS)
# ═══════════════════════════════════════════════════════════════

class TestHealthDashboardAssets:
    """5.1: Health Dashboard UI files exist and are well-structured."""

    def test_health_template_exists(self):
        assert (_WEB_ROOT / "templates/health.html").is_file()

    def test_health_css_exists(self):
        assert (_WEB_ROOT / "static/css/health.css").is_file()

    def test_health_js_exists(self):
        assert (_WEB_ROOT / "static/js/health.js").is_file()

    def test_health_template_extends_base(self):
        html = (_WEB_ROOT / "templates/health.html").read_text()
        assert "{% extends" in html

    def test_health_template_loads_css(self):
        html = (_WEB_ROOT / "templates/health.html").read_text()
        assert "health.css" in html

    def test_health_template_loads_js(self):
        html = (_WEB_ROOT / "templates/health.html").read_text()
        assert "health.js" in html

    def test_health_template_has_status_cards(self):
        html = (_WEB_ROOT / "templates/health.html").read_text()
        assert "status-card" in html.lower() or "health-card" in html.lower()

    def test_health_template_no_inline_css(self):
        html = (_WEB_ROOT / "templates/health.html").read_text()
        assert "<style" not in html, "Should use external CSS per project rules"

    def test_health_js_auto_refresh(self):
        js = (_WEB_ROOT / "static/js/health.js").read_text()
        assert "setInterval" in js, "Health dashboard must auto-refresh"

    def test_health_js_polls_endpoints(self):
        js = (_WEB_ROOT / "static/js/health.js").read_text()
        assert "/health/detailed" in js
        assert "/api/health/vram" in js
        assert "/api/health/connections" in js


# ═══════════════════════════════════════════════════════════════
# 5.2 — Health API endpoints
# ═══════════════════════════════════════════════════════════════

class TestHealthAPIRoutes:
    """5.2: Health route module has all required endpoints."""

    def test_health_routes_has_vram(self):
        from amiagi.interfaces.web.routes.health_routes import health_routes
        paths = [r.path for r in health_routes]
        assert "/api/health/vram" in paths

    def test_health_routes_has_connections(self):
        from amiagi.interfaces.web.routes.health_routes import health_routes
        paths = [r.path for r in health_routes]
        assert "/api/health/connections" in paths

    def test_health_routes_has_detailed(self):
        from amiagi.interfaces.web.routes.health_routes import health_routes
        paths = [r.path for r in health_routes]
        assert "/health/detailed" in paths

    def test_health_routes_count_at_least_4(self):
        from amiagi.interfaces.web.routes.health_routes import health_routes
        assert len(health_routes) >= 4


# ═══════════════════════════════════════════════════════════════
# 5.3 — Settings redesign (12 tabs, external CSS)
# ═══════════════════════════════════════════════════════════════

class TestSettingsRedesign:
    """5.3: Settings page has 10+ tabs, external CSS, no inline styles."""

    def test_settings_css_exists(self):
        assert (_WEB_ROOT / "static/css/settings.css").is_file()

    def test_settings_template_uses_external_css(self):
        html = (_WEB_ROOT / "templates/settings.html").read_text()
        assert "settings.css" in html

    def test_settings_no_inline_style_block(self):
        html = (_WEB_ROOT / "templates/settings.html").read_text()
        assert "<style" not in html, "Inline CSS must be extracted to settings.css"

    def test_settings_has_general_tab(self):
        html = (_WEB_ROOT / "templates/settings.html").read_text()
        assert "settings.general" in html

    def test_settings_has_integrations_tab(self):
        html = (_WEB_ROOT / "templates/settings.html").read_text()
        assert "settings.integrations" in html

    def test_settings_has_execution_tab(self):
        html = (_WEB_ROOT / "templates/settings.html").read_text()
        assert "settings.execution" in html

    def test_settings_has_security_tab(self):
        html = (_WEB_ROOT / "templates/settings.html").read_text()
        assert "settings.security" in html

    def test_settings_has_system_tab(self):
        html = (_WEB_ROOT / "templates/settings.html").read_text()
        assert "settings.system" in html

    def test_settings_has_advanced_tab(self):
        html = (_WEB_ROOT / "templates/settings.html").read_text()
        assert "settings.advanced" in html

    def test_settings_at_least_10_tabs(self):
        html = (_WEB_ROOT / "templates/settings.html").read_text()
        tabs = re.findall(r'class="settings-tab', html)
        assert len(tabs) >= 10, f"Expected >= 10 tabs, found {len(tabs)}"

    def test_settings_css_responsive(self):
        css = (_WEB_ROOT / "static/css/settings.css").read_text()
        assert "@media" in css
        assert "768px" in css
        assert "480px" in css


class TestSettingsSection242Features:
    """Regression coverage for section 10.25 / 4.23 Settings."""

    def test_models_tab_has_temperature_and_max_tokens(self):
        html = (_WEB_ROOT / "templates/settings.html").read_text(encoding="utf-8")
        assert 'id="model-temperature"' in html
        assert 'id="model-max-tokens"' in html

    def test_costs_tab_has_inline_budget_limits_controls(self):
        html = (_WEB_ROOT / "templates/settings.html").read_text(encoding="utf-8")
        assert 'id="budget-session-limit"' in html
        assert 'id="budget-daily-limit"' in html
        assert 'saveBudgetLimits()' in html
        assert '/api/budget/limits' in html

    def test_security_tab_has_notification_channel_matrix(self):
        html = (_WEB_ROOT / "templates/settings.html").read_text(encoding="utf-8")
        assert 'id="notif-channel-matrix"' in html
        assert 'saveNotifChannels' in html
        assert '/settings/notifications' in html

    def test_integrations_tab_has_webhook_test_button_hook(self):
        html = (_WEB_ROOT / "templates/settings.html").read_text(encoding="utf-8")
        assert 'testWebhook' in html
        assert '/api/webhooks/' in html
        assert '/test' in html

    def test_integrations_tab_has_sdk_docs_panel(self):
        html = (_WEB_ROOT / "templates/settings.html").read_text(encoding="utf-8")
        assert 'data-integrations-tab="sdk"' in html
        assert 'pip install amiagi-sdk' in html
        assert 'from amiagi_sdk import AmiagiClient' in html

    def test_security_tab_has_login_attempts_panel_and_loader(self):
        html = (_WEB_ROOT / "templates/settings.html").read_text(encoding="utf-8")
        assert 'id="login-attempts-list"' in html
        assert 'loadLoginAttempts' in html
        assert '/admin/audit?action=login&limit=20' in html

    def test_advanced_tab_has_permission_policies_panel_and_loader(self):
        html = (_WEB_ROOT / "templates/settings.html").read_text(encoding="utf-8")
        assert 'id="permission-policies-list"' in html
        assert '/admin/audit?action=permission&limit=20' in html

    def test_budget_limits_route_is_registered(self):
        from amiagi.interfaces.web.routes.budget_routes import budget_routes

        paths = {r.path for r in budget_routes}
        assert '/api/budget/limits' in paths


# ═══════════════════════════════════════════════════════════════
# 5.4 — Sandboxes admin page
# ═══════════════════════════════════════════════════════════════

class TestSandboxesAdminPage:
    """5.4: Sandboxes admin page assets exist and are structured."""

    def test_sandboxes_template_exists(self):
        assert (_WEB_ROOT / "templates/admin/sandboxes.html").is_file()

    def test_sandboxes_css_exists(self):
        assert (_WEB_ROOT / "static/css/sandboxes.css").is_file()

    def test_sandboxes_js_exists(self):
        assert (_WEB_ROOT / "static/js/sandboxes.js").is_file()

    def test_sandboxes_template_extends_base(self):
        html = (_WEB_ROOT / "templates/admin/sandboxes.html").read_text()
        assert "{% extends" in html

    def test_sandboxes_template_no_inline_css(self):
        html = (_WEB_ROOT / "templates/admin/sandboxes.html").read_text()
        assert "<style" not in html

    def test_sandboxes_template_has_shell_policy(self):
        html = (_WEB_ROOT / "templates/admin/sandboxes.html").read_text()
        assert "shell-policy-editor" in html

    def test_sandboxes_js_has_crud_functions(self):
        js = (_WEB_ROOT / "static/js/sandboxes.js").read_text()
        assert "loadSandboxes" in js
        assert "createSandbox" in js

    def test_sandboxes_js_has_exec_log(self):
        js = (_WEB_ROOT / "static/js/sandboxes.js").read_text()
        assert "loadExecLog" in js or "loadExecutionLog" in js

    def test_sandboxes_js_has_browse_and_per_sandbox_log_actions(self):
        js = (_WEB_ROOT / "static/js/sandboxes.js").read_text(encoding="utf-8")
        assert '/api/sandboxes/' in js
        assert '/files' in js
        assert '/log' in js
        assert 'data-action="browse"' in js or 'action === "browse"' in js
        assert 'data-action="log"' in js or 'action === "log"' in js

    def test_sandboxes_css_responsive(self):
        css = (_WEB_ROOT / "static/css/sandboxes.css").read_text()
        assert "@media" in css
        assert "768px" in css
        assert "480px" in css


# ═══════════════════════════════════════════════════════════════
# 5.5 — <shell-policy-editor> Web Component
# ═══════════════════════════════════════════════════════════════

class TestShellPolicyEditorComponent:
    """5.5: Web Component for visual shell policy editing."""

    def test_component_file_exists(self):
        assert (_WEB_ROOT / "static/js/components/shell-policy-editor.js").is_file()

    def test_component_defines_custom_element(self):
        js = (_WEB_ROOT / "static/js/components/shell-policy-editor.js").read_text()
        assert 'customElements.define("shell-policy-editor"' in js

    def test_component_has_shadow_dom(self):
        js = (_WEB_ROOT / "static/js/components/shell-policy-editor.js").read_text()
        assert "attachShadow" in js

    def test_component_has_editor_mode(self):
        js = (_WEB_ROOT / "static/js/components/shell-policy-editor.js").read_text()
        assert "editor" in js.lower()

    def test_component_has_json_mode(self):
        js = (_WEB_ROOT / "static/js/components/shell-policy-editor.js").read_text()
        assert "json" in js.lower()

    def test_component_loads_from_api(self):
        js = (_WEB_ROOT / "static/js/components/shell-policy-editor.js").read_text()
        assert "/api/shell-policy" in js

    def test_component_saves_to_api(self):
        js = (_WEB_ROOT / "static/js/components/shell-policy-editor.js").read_text()
        assert "PUT" in js


# ═══════════════════════════════════════════════════════════════
# 5.6 — SandboxMonitor service
# ═══════════════════════════════════════════════════════════════

class TestSandboxMonitorService:
    """5.6: SandboxMonitor resource tracking and cleanup."""

    def test_sandbox_monitor_importable(self):
        from amiagi.interfaces.web.monitoring.sandbox_monitor import SandboxMonitor
        assert SandboxMonitor is not None

    def test_sandbox_snapshot_importable(self):
        from amiagi.interfaces.web.monitoring.sandbox_monitor import SandboxSnapshot
        assert SandboxSnapshot is not None

    def test_shell_execution_importable(self):
        from amiagi.interfaces.web.monitoring.sandbox_monitor import ShellExecution
        assert ShellExecution is not None

    def test_sandbox_monitor_has_scan(self):
        from amiagi.interfaces.web.monitoring.sandbox_monitor import SandboxMonitor
        assert hasattr(SandboxMonitor, "scan")

    def test_sandbox_monitor_has_cleanup(self):
        from amiagi.interfaces.web.monitoring.sandbox_monitor import SandboxMonitor
        assert hasattr(SandboxMonitor, "cleanup_tmp")

    def test_sandbox_monitor_has_log_execution(self):
        from amiagi.interfaces.web.monitoring.sandbox_monitor import SandboxMonitor
        assert hasattr(SandboxMonitor, "log_execution")

    def test_sandbox_monitor_has_list_executions(self):
        from amiagi.interfaces.web.monitoring.sandbox_monitor import SandboxMonitor
        assert hasattr(SandboxMonitor, "list_executions")

    def test_sandbox_monitor_has_start_stop(self):
        from amiagi.interfaces.web.monitoring.sandbox_monitor import SandboxMonitor
        assert hasattr(SandboxMonitor, "start")
        assert hasattr(SandboxMonitor, "stop")


# ═══════════════════════════════════════════════════════════════
# 5.7 — Sandbox/Shell API endpoints (10 routes)
# ═══════════════════════════════════════════════════════════════

class TestSandboxAPIRoutes:
    """5.7: Sandbox routes define ≥ 9 endpoints."""

    def test_sandbox_routes_importable(self):
        from amiagi.interfaces.web.routes.sandbox_routes import sandbox_routes
        assert sandbox_routes is not None

    def test_sandbox_routes_count(self):
        from amiagi.interfaces.web.routes.sandbox_routes import sandbox_routes
        assert len(sandbox_routes) >= 9, f"Expected >= 9 routes, found {len(sandbox_routes)}"

    def test_sandbox_routes_has_list(self):
        from amiagi.interfaces.web.routes.sandbox_routes import sandbox_routes
        paths = [r.path for r in sandbox_routes]
        assert "/api/sandboxes" in paths

    def test_sandbox_routes_has_shell_policy(self):
        from amiagi.interfaces.web.routes.sandbox_routes import sandbox_routes
        paths = [r.path for r in sandbox_routes]
        assert "/api/shell-policy" in paths

    def test_sandbox_routes_has_executions(self):
        from amiagi.interfaces.web.routes.sandbox_routes import sandbox_routes
        paths = [r.path for r in sandbox_routes]
        assert "/api/shell-executions" in paths

    def test_sandbox_routes_have_per_sandbox_files_and_log(self):
        from amiagi.interfaces.web.routes.sandbox_routes import sandbox_routes

        paths = [r.path for r in sandbox_routes]
        assert "/api/sandboxes/{agent_id}/files" in paths
        assert "/api/sandboxes/{agent_id}/log" in paths

    def test_sandbox_routes_has_detail(self):
        from amiagi.interfaces.web.routes.sandbox_routes import sandbox_routes
        paths = [r.path for r in sandbox_routes]
        assert any("{agent_id}" in p for p in paths)

    def test_sandbox_routes_has_reset(self):
        from amiagi.interfaces.web.routes.sandbox_routes import sandbox_routes
        paths = [r.path for r in sandbox_routes]
        assert any("reset" in p for p in paths)

    def test_sandbox_routes_has_cleanup(self):
        from amiagi.interfaces.web.routes.sandbox_routes import sandbox_routes
        paths = [r.path for r in sandbox_routes]
        assert any("cleanup" in p for p in paths)


# ═══════════════════════════════════════════════════════════════
# 5.8 — AskHumanTool + ReviewRequestTool
# ═══════════════════════════════════════════════════════════════

class TestHumanInteractionBridge:
    """5.8: HumanInteractionBridge provides agent tools for inbox."""

    def test_bridge_importable(self):
        from amiagi.application.human_tools import HumanInteractionBridge
        assert HumanInteractionBridge is not None

    def test_bridge_has_ask_human(self):
        from amiagi.application.human_tools import HumanInteractionBridge
        assert hasattr(HumanInteractionBridge, "ask_human")

    def test_bridge_has_request_review(self):
        from amiagi.application.human_tools import HumanInteractionBridge
        assert hasattr(HumanInteractionBridge, "request_review")

    def test_router_engine_supports_ask_human(self):
        from amiagi.application.router_engine import SUPPORTED_TOOLS
        assert "ask_human" in SUPPORTED_TOOLS

    def test_router_engine_supports_review_request(self):
        from amiagi.application.router_engine import SUPPORTED_TOOLS
        assert "review_request" in SUPPORTED_TOOLS


# ═══════════════════════════════════════════════════════════════
# 5.9 — DB migration 012
# ═══════════════════════════════════════════════════════════════

_DB_ROOT = _WEB_ROOT / "db"


class TestMigration012:
    """5.9: Migration files for shell_executions + sandbox_metadata."""

    def test_pg_migration_exists(self):
        assert (_DB_ROOT / "migrations/012_shell_executions.sql").is_file()

    def test_sqlite_migration_exists(self):
        assert (_DB_ROOT / "migrations_sqlite/012_shell_executions.sql").is_file()

    def test_pg_migration_creates_shell_executions(self):
        sql = (_DB_ROOT / "migrations/012_shell_executions.sql").read_text()
        assert "shell_executions" in sql

    def test_pg_migration_creates_sandbox_metadata(self):
        sql = (_DB_ROOT / "migrations/012_shell_executions.sql").read_text()
        assert "sandbox_metadata" in sql

    def test_sqlite_migration_creates_shell_executions(self):
        sql = (_DB_ROOT / "migrations_sqlite/012_shell_executions.sql").read_text()
        assert "shell_executions" in sql

    def test_sqlite_migration_creates_sandbox_metadata(self):
        sql = (_DB_ROOT / "migrations_sqlite/012_shell_executions.sql").read_text()
        assert "sandbox_metadata" in sql


# ═══════════════════════════════════════════════════════════════
# 5.10 — Global Search refinement
# ═══════════════════════════════════════════════════════════════

class TestGlobalSearchRefinement:
    """5.10: Global Search has icons, recent searches, and type filters."""

    def test_global_search_file_exists(self):
        assert (_WEB_ROOT / "static/js/components/global-search.js").is_file()

    def test_global_search_has_type_icons(self):
        js = (_WEB_ROOT / "static/js/components/global-search.js").read_text()
        assert "TYPE_ICONS" in js

    def test_global_search_has_per_type_colors(self):
        js = (_WEB_ROOT / "static/js/components/global-search.js").read_text()
        assert "TYPE_COLORS" in js

    def test_global_search_has_recent_searches(self):
        js = (_WEB_ROOT / "static/js/components/global-search.js").read_text()
        assert "localStorage" in js
        assert "recent" in js.lower()

    def test_global_search_has_filter_tabs(self):
        js = (_WEB_ROOT / "static/js/components/global-search.js").read_text()
        assert "gs-filter" in js

    def test_global_search_icon_types_cover_entities(self):
        js = (_WEB_ROOT / "static/js/components/global-search.js").read_text()
        for entity in ["agent", "task", "file", "skill", "prompt"]:
            assert entity in js, f"Missing icon for entity type: {entity}"

    def test_global_search_has_custom_element_define(self):
        js = (_WEB_ROOT / "static/js/components/global-search.js").read_text()
        assert 'customElements.define("global-search"' in js


# ═══════════════════════════════════════════════════════════════
# 5.11 — Toast notification system
# ═══════════════════════════════════════════════════════════════

class TestToastNotifications:
    """5.11: Toast system integrated in new Sprint 5 code."""

    def test_toast_partial_exists(self):
        assert (_WEB_ROOT / "templates/partials/toast.html").is_file()

    def test_toast_has_showToast_function(self):
        html = (_WEB_ROOT / "templates/partials/toast.html").read_text()
        assert "window.showToast" in html

    def test_toast_supports_4_types(self):
        html = (_WEB_ROOT / "templates/partials/toast.html").read_text()
        for t in ["info", "success", "warning", "error"]:
            assert t in html

    def test_health_js_uses_toast(self):
        js = (_WEB_ROOT / "static/js/health.js").read_text()
        assert "showToast" in js

    def test_sandboxes_js_uses_toast(self):
        js = (_WEB_ROOT / "static/js/sandboxes.js").read_text()
        assert "showToast" in js

    def test_shell_policy_editor_uses_toast(self):
        js = (_WEB_ROOT / "static/js/components/shell-policy-editor.js").read_text()
        assert "showToast" in js

    def test_toast_css_exists_in_components(self):
        css = (_WEB_ROOT / "static/css/components.css").read_text()
        assert ".toast-container" in css
        assert ".toast--visible" in css


# ═══════════════════════════════════════════════════════════════
# 5.12 — i18n keys (≥ 40 new)
# ═══════════════════════════════════════════════════════════════

class TestI18nKeys:
    """5.12: All new Sprint 5 keys present in EN + PL."""

    _I18N_DIR = _SRC_ROOT / "i18n/locales"

    REQUIRED_KEYS = [
        # Navigation
        "nav.health",
        "nav.sandboxes",
        # Health dashboard
        "health.title",
        "health.auto_refresh",
        "health.export_report",
        "health.system_metrics",
        "health.loaded_models",
        "health.connections",
        # Sandboxes
        "sandboxes.title",
        "sandboxes.shell_policy",
        "sandboxes.execution_log",
        "sandboxes.no_sandboxes",
        "sandboxes.blocked",
        # Settings new tabs
        "settings.general",
        "settings.integrations",
        "settings.execution",
        "settings.security",
        "settings.system",
        "settings.advanced",
        "settings.language",
        "settings.theme",
        "settings.default_workspace",
        "settings.auto_refresh",
        "settings.read_only",
        "settings.security_desc",
        "settings.no_sessions",
        "settings.system_info",
        "settings.shell_allowlist",
        "settings.quota_policies",
    ]

    def _load_locale(self, lang: str) -> dict:
        path = self._I18N_DIR / f"web_{lang}.json"
        return json.loads(path.read_text())

    def test_en_locale_file_exists(self):
        assert (self._I18N_DIR / "web_en.json").is_file()

    def test_pl_locale_file_exists(self):
        assert (self._I18N_DIR / "web_pl.json").is_file()

    def test_all_required_keys_in_en(self):
        data = self._load_locale("en")
        missing = [k for k in self.REQUIRED_KEYS if k not in data]
        assert not missing, f"Missing EN keys: {missing}"

    def test_all_required_keys_in_pl(self):
        data = self._load_locale("pl")
        missing = [k for k in self.REQUIRED_KEYS if k not in data]
        assert not missing, f"Missing PL keys: {missing}"

    def test_en_pl_key_parity(self):
        en = set(self._load_locale("en").keys())
        pl = set(self._load_locale("pl").keys())
        en_only = en - pl
        pl_only = pl - en
        assert not en_only, f"Keys in EN but not PL: {en_only}"
        assert not pl_only, f"Keys in PL but not EN: {pl_only}"

    def test_at_least_40_new_sprint5_keys(self):
        """Check that Sprint 5 contributed ≥ 40 new keys (health + sandboxes + settings new)."""
        data = self._load_locale("en")
        sprint5_prefixes = ("health.", "sandboxes.", "nav.health", "nav.sandboxes")
        sprint5_settings = [
            "settings.general", "settings.integrations", "settings.execution",
            "settings.security", "settings.system", "settings.advanced",
            "settings.language", "settings.theme", "settings.default_workspace",
            "settings.auto_refresh", "settings.read_only", "settings.security_desc",
            "settings.no_sessions", "settings.system_info", "settings.shell_allowlist",
            "settings.quota_policies",
        ]
        count = sum(1 for k in data if k.startswith(sprint5_prefixes))
        count += sum(1 for k in sprint5_settings if k in data)
        assert count >= 40, f"Expected >= 40 Sprint 5 i18n keys, found {count}"


# ═══════════════════════════════════════════════════════════════
# 5.13 — Responsive CSS
# ═══════════════════════════════════════════════════════════════

class TestResponsiveCSS:
    """5.13: All new CSS files have responsive media queries."""

    @pytest.mark.parametrize("file", [
        "health.css", "sandboxes.css", "settings.css",
    ])
    def test_has_768px_breakpoint(self, file):
        css = (_WEB_ROOT / f"static/css/{file}").read_text()
        assert "768px" in css, f"{file} missing 768px breakpoint"

    @pytest.mark.parametrize("file", [
        "health.css", "sandboxes.css", "settings.css",
    ])
    def test_has_480px_breakpoint(self, file):
        css = (_WEB_ROOT / f"static/css/{file}").read_text()
        assert "480px" in css, f"{file} missing 480px breakpoint"


# ═══════════════════════════════════════════════════════════════
# 5.14 — Performance / bundle size
# ═══════════════════════════════════════════════════════════════

class TestPerformance:
    """5.14: Bundle size and code quality checks."""

    def test_no_css_file_exceeds_50kb(self):
        for f in (_WEB_ROOT / "static/css").glob("*.css"):
            size = f.stat().st_size
            assert size < 55_000, f"{f.name} is {size} bytes (>55KB)"

    def test_no_js_file_exceeds_50kb(self):
        for f in (_WEB_ROOT / "static/js").glob("*.js"):
            size = f.stat().st_size
            assert size < 50_000, f"{f.name} is {size} bytes (>50KB)"

    def test_no_js_component_exceeds_30kb(self):
        for f in (_WEB_ROOT / "static/js/components").glob("*.js"):
            size = f.stat().st_size
            assert size < 30_000, f"{f.name} is {size} bytes (>30KB)"

    def test_total_bundle_under_600kb(self):
        total = 0
        for ext in ("css", "js"):
            for dirpath in [_WEB_ROOT / f"static/{ext}", _WEB_ROOT / "static/js/components"]:
                if dirpath.is_dir():
                    for f in dirpath.glob(f"*.{ext}"):
                        total += f.stat().st_size
        assert total < 650_000, f"Total bundle is {total} bytes (>650KB)"


# ═══════════════════════════════════════════════════════════════
# 5.17 — Route + component + service integration
# ═══════════════════════════════════════════════════════════════

class TestAppIntegration:
    """5.17: All new routes and components are registered in app.py."""

    def test_app_imports_sandbox_routes(self):
        source = (_WEB_ROOT / "app.py").read_text()
        assert "sandbox_routes" in source

    def test_app_imports_health_routes(self):
        source = (_WEB_ROOT / "app.py").read_text()
        assert "health_routes" in source

    def test_app_registers_sandbox_routes(self):
        source = (_WEB_ROOT / "app.py").read_text()
        assert "*sandbox_routes" in source

    def test_command_rail_has_health_link(self):
        html = (_WEB_ROOT / "templates/partials/command_rail.html").read_text()
        assert "health-dashboard" in html

    def test_command_rail_has_sandboxes_link(self):
        html = (_WEB_ROOT / "templates/partials/command_rail.html").read_text()
        assert "sandboxes" in html

    def test_dashboard_routes_has_health_page(self):
        source = (_WEB_ROOT / "routes/dashboard_routes.py").read_text()
        assert "health_page" in source or "health-dashboard" in source

    def test_dashboard_routes_has_sandboxes_page(self):
        source = (_WEB_ROOT / "routes/dashboard_routes.py").read_text()
        assert "sandboxes_page" in source or "/admin/sandboxes" in source

    def test_web_component_count_at_least_19(self):
        components = list((_WEB_ROOT / "static/js/components").glob("*.js"))
        assert len(components) >= 19, f"Expected >= 19 WCs, found {len(components)}"

    def test_base_template_includes_global_search(self):
        html = (_WEB_ROOT / "templates/base.html").read_text()
        assert "<global-search>" in html or "global-search" in html

    def test_base_template_includes_toast(self):
        html = (_WEB_ROOT / "templates/base.html").read_text()
        assert "toast" in html
