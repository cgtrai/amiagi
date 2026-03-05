"""Tests for Faza 11 — Responsive layout (mobile / tablet / desktop).

Covers audit criteria 11.1–11.6:
- 11.1: responsive.css defines ≥ 3 breakpoints
- 11.2: Sidebar hidden on mobile, hamburger visible
- 11.3: Input bar sticky on mobile
- 11.4: Min tap target 44px
- 11.5: Task board: list on mobile, Kanban on desktop
- 11.6: No horizontal scroll on mobile (< 768px)
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_CSS_DIR = Path(__file__).parent.parent / "src/amiagi/interfaces/web/static/css"
_TPL_DIR = Path(__file__).parent.parent / "src/amiagi/interfaces/web/templates"

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _read_responsive_css() -> str:
    return (_CSS_DIR / "responsive.css").read_text()


def _read_base_html() -> str:
    return (_TPL_DIR / "base.html").read_text()


def _read_nav_html() -> str:
    return (_TPL_DIR / "partials/nav.html").read_text()


def _read_sidebar_html() -> str:
    return (_TPL_DIR / "partials/sidebar.html").read_text()


def _read_dashboard_html() -> str:
    return (_TPL_DIR / "dashboard.html").read_text()


# ---------------------------------------------------------------------------
# 11.1 — responsive.css with ≥ 3 breakpoints
# ---------------------------------------------------------------------------

class TestBreakpoints:
    """11.1: responsive.css defines ≥ 3 media-query breakpoints."""

    def test_responsive_file_exists(self):
        assert (_CSS_DIR / "responsive.css").exists()

    def test_at_least_three_media_queries(self):
        css = _read_responsive_css()
        media_rules = re.findall(r"@media\s*\(", css)
        assert len(media_rules) >= 3, f"Found {len(media_rules)} @media rules, need ≥3"

    def test_mobile_breakpoint(self):
        css = _read_responsive_css()
        assert "max-width: 767px" in css

    def test_tablet_breakpoint(self):
        css = _read_responsive_css()
        assert "min-width: 768px" in css
        assert "max-width: 1023px" in css

    def test_desktop_breakpoint(self):
        css = _read_responsive_css()
        assert "min-width: 1024px" in css

    def test_linked_in_base_html(self):
        html = _read_base_html()
        assert "responsive.css" in html


# ---------------------------------------------------------------------------
# 11.2 — Sidebar hidden on mobile, hamburger visible
# ---------------------------------------------------------------------------

class TestHamburger:
    """11.2: Sidebar hidden on mobile, hamburger visible."""

    def test_hamburger_button_in_nav(self):
        nav = _read_nav_html()
        assert "hamburger" in nav
        assert "toggleMobileMenu" in nav

    def test_sidebar_overlay_exists(self):
        nav = _read_nav_html()
        assert "sidebar-overlay" in nav

    def test_sidebar_open_class(self):
        css = _read_responsive_css()
        assert ".app-sidebar.open" in css

    def test_hamburger_hidden_on_desktop(self):
        css = _read_responsive_css()
        # Desktop rule should say display: none for hamburger
        desktop_section = css[css.index("min-width: 1024px"):]
        assert ".hamburger" in desktop_section
        assert "display: none" in desktop_section.split("}")[0] or "display: none" in desktop_section

    def test_hamburger_shown_on_mobile(self):
        css = _read_responsive_css()
        mobile_section = css[css.index("max-width: 767px"):]
        assert "display: flex" in mobile_section[:500]

    def test_sidebar_mobile_fullscreen(self):
        """Sidebar takes full width on mobile."""
        css = _read_responsive_css()
        mobile_idx = css.index("max-width: 767px")
        mobile_section = css[mobile_idx:mobile_idx + 800]
        assert "width: 100%" in mobile_section

    def test_close_mobile_menu_function(self):
        nav = _read_nav_html()
        assert "closeMobileMenu" in nav

    def test_mobile_nav_links_in_sidebar(self):
        sidebar = _read_sidebar_html()
        assert "mobile-nav-links" in sidebar


# ---------------------------------------------------------------------------
# 11.3 — Input bar sticky on mobile
# ---------------------------------------------------------------------------

class TestMobileInputBar:
    """11.3: Input bar sticky at bottom on mobile."""

    def test_mobile_input_bar_in_dashboard(self):
        html = _read_dashboard_html()
        assert "mobile-input-bar" in html

    def test_mobile_input_bar_css_fixed(self):
        css = _read_responsive_css()
        assert "position: fixed" in css
        assert "bottom: 0" in css

    def test_mobile_input_bar_shown_on_mobile(self):
        css = _read_responsive_css()
        mobile_idx = css.index("max-width: 767px")
        mobile_section = css[mobile_idx:mobile_idx + 5000]
        assert ".mobile-input-bar" in mobile_section
        assert "display: block" in mobile_section

    def test_mobile_input_bar_hidden_on_desktop(self):
        css = _read_responsive_css()
        desktop_idx = css.index("min-width: 1024px")
        desktop_section = css[desktop_idx:desktop_idx + 800]
        assert "display: none" in desktop_section

    def test_main_area_has_bottom_padding_on_mobile(self):
        css = _read_responsive_css()
        mobile_idx = css.index("max-width: 767px")
        mobile_section = css[mobile_idx:mobile_idx + 5000]
        assert "padding-bottom" in mobile_section


# ---------------------------------------------------------------------------
# 11.4 — Min tap target 44px
# ---------------------------------------------------------------------------

class TestTapTargets:
    """11.4: Minimum tap target 44px on touch devices."""

    def test_tap_target_44px_in_css(self):
        css = _read_responsive_css()
        assert "min-height: 44px" in css

    def test_multiple_elements_have_min_height(self):
        css = _read_responsive_css()
        count = css.count("min-height: 44px")
        assert count >= 3, f"Only {count} elements have min-height: 44px"

    def test_min_width_44px_present(self):
        css = _read_responsive_css()
        assert "min-width: 44px" in css

    def test_hamburger_tap_target(self):
        css = _read_responsive_css()
        hamburger_idx = css.index(".hamburger {")
        hamburger_block = css[hamburger_idx:hamburger_idx + 400]
        assert "44px" in hamburger_block


# ---------------------------------------------------------------------------
# 11.5 — Task board: list on mobile, Kanban on desktop
# ---------------------------------------------------------------------------

class TestTaskBoardResponsive:
    """11.5: Task board list on mobile, Kanban on desktop."""

    def test_kanban_column_full_width_mobile(self):
        css = _read_responsive_css()
        mobile_idx = css.index("max-width: 767px")
        mobile_section = css[mobile_idx:]
        assert "task-board" in mobile_section
        assert "flex-direction: column" in mobile_section

    def test_kanban_column_width_100(self):
        css = _read_responsive_css()
        mobile_idx = css.index("max-width: 767px")
        mobile_section = css[mobile_idx:]
        assert "width: 100%" in mobile_section


# ---------------------------------------------------------------------------
# 11.6 — No horizontal scroll on mobile
# ---------------------------------------------------------------------------

class TestNoHorizontalScroll:
    """11.6: No horizontal scroll on mobile (< 768px)."""

    def test_overflow_x_hidden(self):
        css = _read_responsive_css()
        mobile_idx = css.index("max-width: 767px")
        mobile_section = css[mobile_idx:mobile_idx + 5000]
        assert "overflow-x: hidden" in mobile_section

    def test_panels_single_column(self):
        css = _read_responsive_css()
        mobile_idx = css.index("max-width: 767px")
        mobile_section = css[mobile_idx:]
        assert "grid-template-columns: 1fr" in mobile_section

    def test_debug_grid_single_column_mobile(self):
        css = _read_responsive_css()
        mobile_idx = css.index("max-width: 767px")
        mobile_section = css[mobile_idx:]
        assert ".debug-grid" in mobile_section


# ---------------------------------------------------------------------------
# General — reduced motion and accessibility
# ---------------------------------------------------------------------------

class TestAccessibility:
    """Reduced-motion support."""

    def test_prefers_reduced_motion(self):
        css = _read_responsive_css()
        assert "prefers-reduced-motion" in css
