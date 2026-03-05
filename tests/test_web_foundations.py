"""Foundation tests for the web interface package (Phase 0.8).

Validates imports, directory layout, Settings fields, and SQL migrations.
"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest


WEB_ROOT = Path(__file__).resolve().parent.parent / "src" / "amiagi" / "interfaces" / "web"


# ── 1. Package imports ─────────────────────────────────────────

_WEB_MODULES = [
    "amiagi.interfaces.web.app",
    "amiagi.interfaces.web.run",
    "amiagi.interfaces.web.web_adapter",
    "amiagi.interfaces.web.i18n_web",
    "amiagi.interfaces.web.auth",
    "amiagi.interfaces.web.audit",
    "amiagi.interfaces.web.monitoring",
    "amiagi.interfaces.web.skills",
]


@pytest.mark.parametrize("module_name", _WEB_MODULES)
def test_web_package_import(module_name: str) -> None:
    """Each web sub-package must be importable without side effects."""
    mod = importlib.import_module(module_name)
    assert mod is not None


# ── 2. Directory tree ──────────────────────────────────────────

_EXPECTED_DIRS = [
    "auth",
    "audit",
    "db",
    "db/migrations",
    "files",
    "monitoring",
    "productivity",
    "rbac",
    "routes",
    "skills",
    "static",
    "static/css",
    "static/js",
    "static/js/components",
    "task_templates",
    "templates",
    "ws",
]


@pytest.mark.parametrize("subdir", _EXPECTED_DIRS)
def test_web_directory_exists(subdir: str) -> None:
    """Required directories must exist under the web package root."""
    d = WEB_ROOT / subdir
    assert d.is_dir(), f"Missing directory: {d}"


# ── 3. Settings fields ────────────────────────────────────────

def test_settings_has_web_fields() -> None:
    """Settings must expose web-related knobs (dashboard_port, etc.)."""
    from amiagi.config import Settings

    s = Settings()
    assert hasattr(s, "dashboard_port")
    assert isinstance(s.dashboard_port, int)
    assert s.dashboard_port > 0


# ── 4. SQL migration files ────────────────────────────────────

_EXPECTED_MIGRATIONS = [
    "001_init.sql",
    "002_skills.sql",
    "003_productivity.sql",
    "004_monitoring.sql",
    "005_task_templates.sql",
]


@pytest.mark.parametrize("filename", _EXPECTED_MIGRATIONS)
def test_sql_migration_exists(filename: str) -> None:
    """Each schema migration SQL file must be present and non-empty."""
    path = WEB_ROOT / "db" / "migrations" / filename
    assert path.is_file(), f"Missing migration: {path}"
    assert path.stat().st_size > 0, f"Empty migration file: {path}"
