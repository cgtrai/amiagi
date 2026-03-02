"""Lightweight JSON-based translation loader.

Locale files are stored as ``<lang>.json`` in the ``locales/`` subdirectory
next to this module.  Each file maps dotted string keys to translated text
that may contain ``{variable}`` placeholders resolved via ``str.format()``.

The ``_()`` function is the main entry point::

    from amiagi.i18n import _
    msg = _("dashboard.started", url="http://localhost:8080")
    # → "Dashboard uruchomiony: http://localhost:8080"  (pl)
    # → "Dashboard started: http://localhost:8080"      (en)

If a key is missing from the active locale, it falls back to Polish (``pl``),
then to the raw key itself — so untranslated strings never crash the app.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

_LOCALES_DIR = Path(__file__).parent / "locales"

_current_lang: str = "pl"
_strings: dict[str, str] = {}
_fallback: dict[str, str] = {}  # always Polish


def _load_locale(lang: str) -> dict[str, str]:
    """Load a locale JSON file, skipping comment keys (starting with ``_``)."""
    path = _LOCALES_DIR / f"{lang}.json"
    if not path.exists():
        return {}
    with path.open(encoding="utf-8") as f:
        raw: dict[str, str] = json.load(f)
    return {k: v for k, v in raw.items() if not k.startswith("_")}


def set_language(lang: str) -> None:
    """Switch the active language.  Falls back to ``pl`` for unknown codes."""
    global _current_lang, _strings, _fallback
    lang = lang.strip().lower()
    _current_lang = lang
    _strings = _load_locale(lang)
    if lang != "pl":
        _fallback = _load_locale("pl")
    else:
        _fallback = _strings


def get_language() -> str:
    """Return the currently active language code."""
    return _current_lang


def available_languages() -> list[str]:
    """List language codes that have locale files."""
    return sorted(
        p.stem for p in _LOCALES_DIR.glob("*.json") if not p.name.startswith("_")
    )


def _(key: str, **kwargs: Any) -> str:
    """Translate *key* using the active locale.

    Falls back to Polish, then to the raw key.
    Any ``{name}`` placeholders in the template are filled from *kwargs*.
    """
    template = _strings.get(key) or _fallback.get(key) or key
    if kwargs:
        try:
            return template.format(**kwargs)
        except (KeyError, IndexError, ValueError):
            return template
    return template


# ------------------------------------------------------------------
# Auto-initialise from environment on import
# ------------------------------------------------------------------
_initial_lang = os.environ.get("AMIAGI_LANG", "pl").strip().lower()
set_language(_initial_lang)
