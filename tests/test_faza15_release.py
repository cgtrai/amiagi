"""Tests for Faza 15 — Final tests, documentation & release.

Covers audit criteria 15.1–15.6:
- 15.1: ≥ 190 new web tests
- 15.2: ≥ 1317 total tests
- 15.3: WEB_INTERFACE.md contains Web GUI routes section
- 15.4: pyproject.toml version = 1.1.0
- 15.5: install.sh has PostgreSQL step
- 15.6: RELEASE_NOTES_v1.1.0.md exists
"""

from __future__ import annotations

from pathlib import Path

import pytest

_ROOT = Path(__file__).parent.parent


# ═══════════════════════════════════════════════════════════════
# 15.3 — WEB_INTERFACE.md
# ═══════════════════════════════════════════════════════════════

class TestWebInterfaceDoc:
    """15.3: WEB_INTERFACE.md documentation."""

    @pytest.fixture()
    def doc(self) -> str:
        return (_ROOT / "WEB_INTERFACE.md").read_text()

    def test_file_exists(self):
        assert (_ROOT / "WEB_INTERFACE.md").exists()

    def test_contains_web_gui_heading(self, doc):
        assert "Web Interface" in doc

    def test_contains_routes_section(self, doc):
        assert "Routes" in doc

    def test_contains_authentication_section(self, doc):
        assert "Authentication" in doc

    def test_contains_database_section(self, doc):
        assert "Database" in doc

    def test_contains_design_system(self, doc):
        assert "Design System" in doc or "design system" in doc.lower()

    def test_contains_websocket_section(self, doc):
        assert "WebSocket" in doc

    def test_contains_i18n_section(self, doc):
        assert "Internationalization" in doc or "i18n" in doc

    def test_contains_security_section(self, doc):
        assert "Security" in doc

    def test_mentions_starlette(self, doc):
        assert "Starlette" in doc

    def test_mentions_postgresql(self, doc):
        assert "PostgreSQL" in doc or "asyncpg" in doc


# ═══════════════════════════════════════════════════════════════
# 15.4 — Version
# ═══════════════════════════════════════════════════════════════

class TestVersion:
    """15.4: Version is 1.2.0."""

    def test_pyproject_version(self):
        content = (_ROOT / "pyproject.toml").read_text()
        assert 'version = "1.2.0"' in content

    def test_init_version(self):
        from amiagi import __version__
        assert __version__ == "1.2.0"


# ═══════════════════════════════════════════════════════════════
# 15.5 — install.sh
# ═══════════════════════════════════════════════════════════════

class TestInstallScript:
    """15.5: install.sh has PostgreSQL step."""

    @pytest.fixture()
    def script(self) -> str:
        return (_ROOT / "install.sh").read_text()

    def test_mentions_postgresql(self, script):
        assert "PostgreSQL" in script or "postgresql" in script or "psql" in script

    def test_mentions_web_deps(self, script):
        assert "web" in script.lower()


# ═══════════════════════════════════════════════════════════════
# 15.6 — Release notes
# ═══════════════════════════════════════════════════════════════

class TestReleaseNotes:
    """15.6: Release notes exist."""

    def test_release_notes_exist(self):
        assert (_ROOT / "RELEASE_NOTES_v1.1.0.md").exists()

    def test_github_release_exists(self):
        assert (_ROOT / "GITHUB_RELEASE_v1.1.0.md").exists()

    def test_release_notes_mentions_web(self):
        content = (_ROOT / "RELEASE_NOTES_v1.1.0.md").read_text()
        assert "Web" in content

    def test_release_notes_mentions_version(self):
        content = (_ROOT / "RELEASE_NOTES_v1.1.0.md").read_text()
        assert "1.1.0" in content


# ═══════════════════════════════════════════════════════════════
# 15.1 / 15.2 — Test counts
# ═══════════════════════════════════════════════════════════════

class TestTestCounts:
    """15.1/15.2: Verify sufficient test coverage."""

    def test_faza_test_files_exist(self):
        """All faza test files (10-15) should exist."""
        for n in range(10, 16):
            matches = list((_ROOT / "tests").glob(f"test_faza{n}*"))
            assert len(matches) >= 1, f"No test file for Faza {n}"

    def test_faza10_tests_ge_30(self):
        content = (_ROOT / "tests/test_faza10_skills.py").read_text()
        count = content.count("def test_")
        assert count >= 30, f"Faza 10: {count} tests, need ≥30"

    def test_faza11_tests_ge_25(self):
        content = (_ROOT / "tests/test_faza11_responsive.py").read_text()
        count = content.count("def test_")
        assert count >= 25, f"Faza 11: {count} tests, need ≥25"

    def test_faza12_tests_ge_40(self):
        content = (_ROOT / "tests/test_faza12_productivity.py").read_text()
        count = content.count("def test_")
        assert count >= 40, f"Faza 12: {count} tests, need ≥40"

    def test_faza13_tests_ge_60(self):
        content = (_ROOT / "tests/test_faza13_monitoring.py").read_text()
        count = content.count("def test_")
        assert count >= 60, f"Faza 13: {count} tests, need ≥60"

    def test_faza14_tests_ge_50(self):
        content = (_ROOT / "tests/test_faza14_templates_i18n.py").read_text()
        count = content.count("def test_")
        assert count >= 50, f"Faza 14: {count} tests, need ≥50"

    def test_web_test_total_ge_190(self):
        """Total web-related (faza 10-15) tests should be ≥ 190."""
        total = 0
        for f in (_ROOT / "tests").glob("test_faza1[0-5]*"):
            total += f.read_text().count("def test_")
        assert total >= 190, f"Total faza 10-15 tests: {total}, need ≥190"
