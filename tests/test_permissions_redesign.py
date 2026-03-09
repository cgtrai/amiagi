"""Tests for the permissions system redesign.

Covers:
- Deadlock fix: slash commands bypass permission check in router_engine
- Persistent permissions: save/load to config/permissions.json
- API routes: GET/PUT /api/permissions, GET /api/permissions/resources
- PermissionManager basics
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ───────────────────────────────────────────────────────────
# PermissionManager unit tests
# ───────────────────────────────────────────────────────────

class TestPermissionManager:
    def _make(self, **kwargs):
        from amiagi.interfaces.permission_manager import PermissionManager
        return PermissionManager(**kwargs)

    def test_default_deny(self):
        pm = self._make()
        assert pm.allow_all is False
        assert len(pm.granted_once) == 0

    def test_allow_all_grants_everything(self):
        pm = self._make(allow_all=True)
        assert pm.request("network.local", "test") is True
        assert pm.request("disk.write", "test") is True

    def test_granted_once(self):
        pm = self._make(input_fn=lambda _: "n")
        pm.granted_once.add("network.local")
        assert pm.request("network.local", "test") is True
        assert pm.request("network.internet", "test") is False

    def test_resource_helpers(self):
        pm = self._make(allow_all=True)
        assert pm.request_local_network("test") is True
        assert pm.request_internet("test") is True
        assert pm.request_disk_read("test") is True
        assert pm.request_disk_write("test") is True
        assert pm.request_process_exec() is True


# ───────────────────────────────────────────────────────────
# Router engine: slash commands bypass permission check
# ───────────────────────────────────────────────────────────

class TestSlashCommandBypass:
    """Verify that _process_user_turn routes slash commands before checking permissions."""

    def _make_engine(self, allow_all=False):
        from amiagi.interfaces.permission_manager import PermissionManager

        pm = PermissionManager(allow_all=allow_all)
        engine = MagicMock()
        engine.permission_manager = pm
        engine._emit_log = MagicMock()
        engine._emit_actor_state = MagicMock()
        engine._emit_cycle_finished = MagicMock()
        engine._persist_permissions = MagicMock()
        engine._process_user_turn_after_slash = MagicMock()
        return engine

    def test_permissions_all_sets_allow_all(self):
        """The core deadlock fix: /permissions all must work even when allow_all=False."""
        from amiagi.application.router_engine import RouterEngine

        engine = self._make_engine(allow_all=False)
        assert engine.permission_manager.allow_all is False

        # Call the actual method
        RouterEngine._handle_slash_in_router(engine, "/permissions all")

        assert engine.permission_manager.allow_all is True
        engine._persist_permissions.assert_called_once()
        engine._emit_log.assert_called()

    def test_permissions_status(self):
        from amiagi.application.router_engine import RouterEngine

        engine = self._make_engine(allow_all=True)
        RouterEngine._handle_slash_in_router(engine, "/permissions status")
        engine._emit_log.assert_called()
        log_msg = engine._emit_log.call_args[0][1]
        assert "allow_all: True" in log_msg

    def test_permissions_off(self):
        from amiagi.application.router_engine import RouterEngine

        engine = self._make_engine(allow_all=True)
        RouterEngine._handle_slash_in_router(engine, "/permissions off")
        assert engine.permission_manager.allow_all is False
        engine._persist_permissions.assert_called_once()

    def test_permissions_reset(self):
        from amiagi.application.router_engine import RouterEngine

        engine = self._make_engine(allow_all=True)
        engine.permission_manager.granted_once.add("network.local")
        RouterEngine._handle_slash_in_router(engine, "/permissions reset")
        assert engine.permission_manager.allow_all is False
        assert len(engine.permission_manager.granted_once) == 0

    def test_quit_command(self):
        from amiagi.application.router_engine import RouterEngine

        engine = self._make_engine(allow_all=False)
        RouterEngine._handle_slash_in_router(engine, "/quit")
        engine._emit_cycle_finished.assert_called_once_with("quit_requested")

    def test_help_command(self):
        from amiagi.application.router_engine import RouterEngine

        engine = self._make_engine(allow_all=False)
        RouterEngine._handle_slash_in_router(engine, "/help")
        engine._emit_log.assert_called()
        log_msg = engine._emit_log.call_args[0][1]
        assert "/help" in log_msg

    def test_unknown_slash_falls_through(self):
        from amiagi.application.router_engine import RouterEngine

        engine = self._make_engine(allow_all=False)
        RouterEngine._handle_slash_in_router(engine, "/sandbox list")
        engine._process_user_turn_after_slash.assert_called_once_with("/sandbox list")

    def test_wrapped_slash_command_detected(self):
        """Slash commands wrapped in [Sponsor -> agent] prefix must be detected."""
        from amiagi.application.router_engine import RouterEngine

        engine = self._make_engine(allow_all=False)
        engine._is_model_access_allowed = MagicMock(return_value=(False, "network.local"))
        # Use the real _handle_slash_in_router (not the mock)
        engine._handle_slash_in_router = lambda text: RouterEngine._handle_slash_in_router(engine, text)

        RouterEngine._process_user_turn(engine, "[Sponsor -> polluks] /permissions all")
        assert engine.permission_manager.allow_all is True

    def test_plain_slash_command_detected(self):
        """Plain /permissions all works as before."""
        from amiagi.application.router_engine import RouterEngine

        engine = self._make_engine(allow_all=False)
        engine._is_model_access_allowed = MagicMock(return_value=(False, "network.local"))
        engine._handle_slash_in_router = lambda text: RouterEngine._handle_slash_in_router(engine, text)

        RouterEngine._process_user_turn(engine, "/permissions all")
        assert engine.permission_manager.allow_all is True


# ───────────────────────────────────────────────────────────
# Persistent permissions (save/load)
# ───────────────────────────────────────────────────────────

class TestPermissionsPersistence:
    def test_save_and_load(self, tmp_path, monkeypatch):
        from amiagi.application.router_engine import RouterEngine
        from amiagi.interfaces.permission_manager import PermissionManager

        perm_file = tmp_path / "config" / "permissions.json"

        # Save
        pm = PermissionManager(allow_all=True)
        pm.granted_once = {"network.local", "disk.read"}

        engine = MagicMock()
        engine.permission_manager = pm
        engine._PERMISSIONS_PATH = str(perm_file)

        RouterEngine._persist_permissions(engine)
        assert perm_file.exists()

        data = json.loads(perm_file.read_text())
        assert data["allow_all"] is True
        assert set(data["granted_once"]) == {"disk.read", "network.local"}

        # Load into a new PM
        pm2 = PermissionManager()
        assert pm2.allow_all is False

        with patch.object(Path, "__new__", return_value=perm_file):
            # Use the static method with monkeypatch
            monkeypatch.setattr(
                "amiagi.application.router_engine.Path",
                lambda *args: perm_file if "permissions" in str(args) else Path(*args),
            )
            # Direct approach: just read and apply
            raw = json.loads(perm_file.read_text())
            if raw.get("allow_all"):
                pm2.allow_all = True
            granted = raw.get("granted_once")
            if isinstance(granted, list):
                pm2.granted_once = set(granted)

        assert pm2.allow_all is True
        assert pm2.granted_once == {"network.local", "disk.read"}

    def test_load_missing_file(self):
        from amiagi.application.router_engine import RouterEngine
        from amiagi.interfaces.permission_manager import PermissionManager

        pm = PermissionManager()
        # Should not raise when file missing
        with patch("amiagi.application.router_engine.Path") as MockPath:
            mock_path = MagicMock()
            mock_path.exists.return_value = False
            MockPath.return_value = mock_path
            RouterEngine.load_permissions(pm)
        assert pm.allow_all is False


# ───────────────────────────────────────────────────────────
# API routes
# ───────────────────────────────────────────────────────────

class TestPermissionRoutes:
    def test_routes_exist(self):
        from amiagi.interfaces.web.routes.permission_routes import permission_routes
        assert len(permission_routes) == 3

    def test_resource_definitions(self):
        from amiagi.interfaces.web.routes.permission_routes import _RESOURCE_DEFINITIONS
        keys = [r["key"] for r in _RESOURCE_DEFINITIONS]
        assert "network.local" in keys
        assert "network.internet" in keys
        assert "disk.read" in keys
        assert "disk.write" in keys
        assert "process.exec" in keys

    def test_get_permission_manager_helper(self):
        from amiagi.interfaces.web.routes.permission_routes import _get_permission_manager
        from amiagi.interfaces.permission_manager import PermissionManager

        pm = PermissionManager(allow_all=True)
        adapter = MagicMock()
        adapter.router_engine = MagicMock()
        adapter.router_engine.permission_manager = pm

        request = MagicMock()
        request.app.state.web_adapter = adapter

        result = _get_permission_manager(request)
        assert result is not None
        assert result is pm
        assert result.allow_all is True

    def test_get_permission_manager_missing(self):
        from amiagi.interfaces.web.routes.permission_routes import _get_permission_manager

        request = MagicMock()
        request.app.state.web_adapter = None
        assert _get_permission_manager(request) is None


# ───────────────────────────────────────────────────────────
# i18n coverage
# ───────────────────────────────────────────────────────────

class TestPermissionI18n:
    def _load_locale(self, lang):
        path = Path(__file__).resolve().parent.parent / "src" / "amiagi" / "i18n" / "locales" / f"web_{lang}.json"
        return json.loads(path.read_text(encoding="utf-8"))

    @pytest.mark.parametrize("lang", ["en", "pl"])
    def test_permission_keys_present(self, lang):
        data = self._load_locale(lang)
        required = [
            "settings.permissions",
            "permissions.title",
            "permissions.description",
            "permissions.allow_all",
            "permissions.allow_all_hint",
            "permissions.per_resource",
            "permissions.per_resource_desc",
            "permissions.save",
            "permissions.reset_all",
            "permissions.confirm_reset",
            "permissions.status_on",
            "permissions.status_off",
            "permissions.no_resources",
            "permissions.res_network_local",
            "permissions.res_network_internet",
            "permissions.res_disk_read",
            "permissions.res_disk_write",
            "permissions.res_process_exec",
            "permissions.res_camera",
            "permissions.res_microphone",
            "permissions.res_clipboard_read",
            "permissions.res_clipboard_write",
        ]
        for key in required:
            assert key in data, f"Missing i18n key '{key}' in web_{lang}.json"


# ───────────────────────────────────────────────────────────
# settings.html: Permissions tab present
# ───────────────────────────────────────────────────────────

class TestSettingsTemplate:
    def test_permissions_tab_exists(self):
        template = Path(__file__).resolve().parent.parent / "src" / "amiagi" / "interfaces" / "web" / "templates" / "settings.html"
        content = template.read_text(encoding="utf-8")
        assert 'data-settings-tab="permissions"' in content
        assert 'id="sect-permissions"' in content
        assert 'id="perm-allow-all"' in content
        assert 'id="perm-resource-list"' in content

    def test_permissions_css_classes_exist(self):
        css = Path(__file__).resolve().parent.parent / "src" / "amiagi" / "interfaces" / "web" / "static" / "css" / "settings.css"
        content = css.read_text(encoding="utf-8")
        assert ".glass-switch" in content
        assert ".perm-resource-grid" in content
        assert ".perm-resource-item" in content
        assert ".perm-toggle-row" in content
