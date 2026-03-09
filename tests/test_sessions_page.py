from __future__ import annotations

from pathlib import Path


_ROOT = Path(__file__).resolve().parent.parent / "src" / "amiagi" / "interfaces" / "web"
_TIMELINE = _ROOT / "static" / "js" / "components" / "session-timeline.js"


class TestSessionsPageRoute:
    def test_sessions_route_exists(self) -> None:
        from amiagi.interfaces.web.routes.dashboard_routes import dashboard_routes

        paths = [r.path for r in dashboard_routes]
        assert "/sessions" in paths

    def test_sessions_route_is_get(self) -> None:
        from amiagi.interfaces.web.routes.dashboard_routes import dashboard_routes

        method_map = {r.path: (r.methods or set()) for r in dashboard_routes}
        assert "GET" in method_map["/sessions"]


class TestSessionsTemplate:
    def test_sessions_template_exists(self) -> None:
        assert (_ROOT / "templates" / "sessions.html").exists()

    def test_sessions_template_uses_timeline_component(self) -> None:
        html = (_ROOT / "templates" / "sessions.html").read_text(encoding="utf-8")
        assert "<session-timeline" in html
        assert "/api/sessions" in html

    def test_command_rail_links_to_sessions(self) -> None:
        html = (_ROOT / "templates" / "partials" / "command_rail.html").read_text(encoding="utf-8")
        assert 'href="/sessions"' in html

    def test_sessions_template_supports_query_param_preselection(self) -> None:
        html = (_ROOT / "templates" / "sessions.html").read_text(encoding="utf-8")
        assert "URLSearchParams" in html
        assert "session_id" in html

    def test_sessions_template_uses_i18n_for_ui_strings(self) -> None:
        html = (_ROOT / "templates" / "sessions.html").read_text(encoding="utf-8")
        assert "sessions.search_placeholder" in html
        assert "sessions.select_session" in html
        assert "sessions.link_copied" in html

    def test_sessions_template_supports_agent_filter_and_actions(self) -> None:
        html = (_ROOT / "templates" / "sessions.html").read_text(encoding="utf-8")
        assert 'id="session-agent-filter"' in html
        assert "btn-export-session" in html
        assert "btn-share-session" in html
        assert "/api/sessions/" in html
        assert "/export?format=html" in html

    def test_session_timeline_supports_speed_controls(self) -> None:
        js = _TIMELINE.read_text(encoding="utf-8")
        assert "tl-speed-select" in js
        assert '<option value="1" selected>1x</option>' in js
        assert '<option value="2">2x</option>' in js
        assert '<option value="4">4x</option>' in js

    def test_command_rail_links_have_accessible_labels(self) -> None:
        html = (_ROOT / "templates" / "partials" / "command_rail.html").read_text(encoding="utf-8")
        assert 'aria-label="{{ _(\'sessions.title\') }}"' in html
        assert 'title="{{ _(\'nav.dashboard\') }}"' in html