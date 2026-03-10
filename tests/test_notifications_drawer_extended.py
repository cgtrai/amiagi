from __future__ import annotations

from pathlib import Path


def test_nav_notification_drawer_contains_channel_and_grouping_hooks() -> None:
    nav = Path("src/amiagi/interfaces/web/templates/partials/nav.html").read_text(encoding="utf-8")

    assert "renderNotificationCenter" in nav
    assert "openNotificationChannels" in nav
    assert "/api/notifications/preferences" in nav
    assert "toggleMuteAgent" in nav
    assert "method: 'PUT'" in nav
    assert "🔔" not in nav
    assert "🌐" not in nav
    assert "⚙️" not in nav
    assert "📡" not in nav
    assert "🔇" not in nav
    assert ">✓<" not in nav


def test_sidebar_mobile_notifications_use_drawer_flow() -> None:
    sidebar = Path("src/amiagi/interfaces/web/templates/partials/sidebar.html").read_text(encoding="utf-8")

    assert "toggleNotificationDrawer(); closeMobileMenu();" in sidebar
    assert "toggleNotificationDropdown" not in sidebar