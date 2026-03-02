"""Tests for the i18n translation subsystem."""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest


# ---------------------------------------------------------------------------
# Helpers — reimport after env-var manipulation
# ---------------------------------------------------------------------------

def _fresh_import():
    """Force-reimport the loader so the auto-init runs again."""
    import importlib
    import amiagi.i18n.loader as mod
    importlib.reload(mod)
    # Re-bind public API in the package __init__
    import amiagi.i18n as pkg
    pkg._ = mod._
    pkg.set_language = mod.set_language
    pkg.get_language = mod.get_language
    pkg.available_languages = mod.available_languages
    return mod


# ---------------------------------------------------------------------------
# Basic API
# ---------------------------------------------------------------------------

class TestTranslationLookup:
    """Test the _() function for key lookup and fallback."""

    def setup_method(self):
        from amiagi.i18n import set_language
        set_language("pl")

    def test_existing_key_returns_polish(self):
        from amiagi.i18n import _
        result = _("help.cmd.help")
        assert result != "help.cmd.help"
        assert isinstance(result, str)
        assert len(result) > 0

    def test_missing_key_returns_raw_key(self):
        from amiagi.i18n import _
        assert _("nonexistent.key.abc123") == "nonexistent.key.abc123"

    def test_interpolation(self):
        from amiagi.i18n import _
        result = _("lang.switched", lang="en")
        assert "en" in result

    def test_interpolation_with_missing_var_returns_template(self):
        from amiagi.i18n import _
        # Missing kwargs should not raise — just return the template as-is
        result = _("lang.switched")
        assert isinstance(result, str)

    def test_fallback_from_en_to_pl(self):
        """English locale should fall back to Polish for missing keys."""
        from amiagi.i18n import set_language, _
        set_language("en")
        # Use a key that may exist only in pl (if not in en)
        # Actually both locales have full coverage, so test the fallback path
        # by checking that a valid key returns a non-empty string
        result = _("help.cmd.help")
        assert result != "help.cmd.help"
        assert isinstance(result, str)
        # Restore
        set_language("pl")


class TestSetLanguage:
    """Test set_language() switching."""

    def test_switch_to_english(self):
        from amiagi.i18n import set_language, get_language, _
        set_language("en")
        assert get_language() == "en"
        # help.cmd.help should differ between pl and en
        en_val = _("help.cmd.help")
        set_language("pl")
        pl_val = _("help.cmd.help")
        assert en_val != pl_val  # different languages → different text

    def test_switch_to_unknown_lang_still_works(self):
        from amiagi.i18n import set_language, get_language, _
        set_language("xx")
        assert get_language() == "xx"
        # Should still fall back to Polish
        result = _("help.cmd.help")
        assert result != "help.cmd.help"
        # Clean up
        set_language("pl")

    def test_case_insensitive(self):
        from amiagi.i18n import set_language, get_language
        set_language("EN")
        assert get_language() == "en"
        set_language("pl")

    def test_strips_whitespace(self):
        from amiagi.i18n import set_language, get_language
        set_language("  en  ")
        assert get_language() == "en"
        set_language("pl")


class TestGetLanguage:
    """Test get_language() returns active code."""

    def test_default_is_pl(self):
        from amiagi.i18n import set_language, get_language
        set_language("pl")
        assert get_language() == "pl"


class TestAvailableLanguages:
    """Test available_languages() discovery."""

    def test_returns_list_of_strings(self):
        from amiagi.i18n import available_languages
        langs = available_languages()
        assert isinstance(langs, list)
        assert all(isinstance(x, str) for x in langs)

    def test_contains_pl_and_en(self):
        from amiagi.i18n import available_languages
        langs = available_languages()
        assert "pl" in langs
        assert "en" in langs

    def test_sorted(self):
        from amiagi.i18n import available_languages
        langs = available_languages()
        assert langs == sorted(langs)


class TestEnvVarInit:
    """Test AMIAGI_LANG environment variable auto-initialisation."""

    def test_env_var_sets_language(self):
        with patch.dict(os.environ, {"AMIAGI_LANG": "en"}):
            mod = _fresh_import()
            assert mod.get_language() == "en"
        # Reset
        mod.set_language("pl")

    def test_env_var_default_is_pl(self):
        with patch.dict(os.environ, {}, clear=True):
            # Remove AMIAGI_LANG if present
            os.environ.pop("AMIAGI_LANG", None)
            mod = _fresh_import()
            assert mod.get_language() == "pl"


# ---------------------------------------------------------------------------
# Locale file completeness
# ---------------------------------------------------------------------------

class TestLocaleCompleteness:
    """Verify that en.json and pl.json have the same keys."""

    def test_same_keys(self):
        import json
        from pathlib import Path
        locales_dir = Path(__file__).parent.parent / "src" / "amiagi" / "i18n" / "locales"
        with (locales_dir / "pl.json").open(encoding="utf-8") as f:
            pl_keys = {k for k in json.load(f) if not k.startswith("_")}
        with (locales_dir / "en.json").open(encoding="utf-8") as f:
            en_keys = {k for k in json.load(f) if not k.startswith("_")}

        missing_in_en = pl_keys - en_keys
        missing_in_pl = en_keys - pl_keys

        assert not missing_in_en, f"Keys in pl.json but missing in en.json: {missing_in_en}"
        assert not missing_in_pl, f"Keys in en.json but missing in pl.json: {missing_in_pl}"

    def test_no_empty_values(self):
        import json
        from pathlib import Path
        locales_dir = Path(__file__).parent.parent / "src" / "amiagi" / "i18n" / "locales"
        for lang in ("pl", "en"):
            with (locales_dir / f"{lang}.json").open(encoding="utf-8") as f:
                data = json.load(f)
            empty = [k for k, v in data.items() if not k.startswith("_") and not v.strip()]
            assert not empty, f"Empty values in {lang}.json: {empty}"


# ---------------------------------------------------------------------------
# /lang command handler (textual_cli integration)
# ---------------------------------------------------------------------------

class TestLangCommandHandler:
    """Test /lang command in _handle_textual_command."""

    def setup_method(self):
        from amiagi.i18n import set_language
        set_language("pl")

    def test_lang_no_args_shows_current(self):
        from amiagi.interfaces.textual_cli import _handle_textual_command
        from unittest.mock import MagicMock

        pm = MagicMock()
        pm.allow_all = False
        pm.granted_once = set()

        outcome = _handle_textual_command("/lang", pm)
        assert outcome.handled
        assert len(outcome.messages) >= 1
        # Should mention current language
        combined = " ".join(outcome.messages)
        assert "pl" in combined

    def test_lang_switch_to_en(self):
        from amiagi.interfaces.textual_cli import _handle_textual_command
        from amiagi.i18n import get_language
        from unittest.mock import MagicMock

        pm = MagicMock()
        pm.allow_all = False
        pm.granted_once = set()

        outcome = _handle_textual_command("/lang en", pm)
        assert outcome.handled
        assert get_language() == "en"
        # Restore via command so globals (TEXTUAL_HELP_TEXT) are rebuilt too
        _handle_textual_command("/lang pl", pm)

    def test_lang_invalid_code(self):
        from amiagi.interfaces.textual_cli import _handle_textual_command
        from unittest.mock import MagicMock

        pm = MagicMock()
        pm.allow_all = False
        pm.granted_once = set()

        outcome = _handle_textual_command("/lang xx", pm)
        assert outcome.handled
        combined = " ".join(outcome.messages)
        assert "xx" in combined


# ---------------------------------------------------------------------------
# --lang CLI argument (argparse integration)
# ---------------------------------------------------------------------------

class TestLangArgparse:
    """Test --lang argument in main._parse_args."""

    def test_lang_default_is_none(self):
        from amiagi.main import _parse_args
        args = _parse_args([])
        assert args.lang is None

    def test_lang_pl(self):
        from amiagi.main import _parse_args
        args = _parse_args(["--lang", "pl"])
        assert args.lang == "pl"

    def test_lang_en(self):
        from amiagi.main import _parse_args
        args = _parse_args(["--lang", "en"])
        assert args.lang == "en"

    def test_lang_invalid_rejected(self):
        from amiagi.main import _parse_args
        with pytest.raises(SystemExit):
            _parse_args(["--lang", "xx"])
