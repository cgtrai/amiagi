"""Web-specific i18n integration.

Loads ``web_pl.json`` / ``web_en.json`` locale files and exposes
a ``_()`` translation function suitable for Jinja2 template globals.

Language detection order:
1. ``lang`` cookie
2. ``Accept-Language`` header
3. Fallback: ``pl``
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from starlette.requests import Request

logger = logging.getLogger(__name__)

_LOCALES_DIR = Path(__file__).parent.parent.parent / "i18n" / "locales"

_web_strings: dict[str, dict[str, str]] = {}  # lang → {key → value}
_SUPPORTED_LANGS = ("pl", "en")
_DEFAULT_LANG = "pl"


def _ensure_loaded() -> None:
    """Lazy-load web locale files on first use."""
    if _web_strings:
        return
    for lang in _SUPPORTED_LANGS:
        path = _LOCALES_DIR / f"web_{lang}.json"
        if path.exists():
            with path.open(encoding="utf-8") as f:
                raw = json.load(f)
            _web_strings[lang] = {k: v for k, v in raw.items() if not k.startswith("_")}
        else:
            _web_strings[lang] = {}


def get_web_translation(key: str, lang: str = "pl", **kwargs: Any) -> str:
    """Translate a web UI key for the given language."""
    _ensure_loaded()
    strings = _web_strings.get(lang, {})
    fallback = _web_strings.get(_DEFAULT_LANG, {})
    template = strings.get(key) or fallback.get(key) or key
    if kwargs:
        try:
            return template.format(**kwargs)
        except (KeyError, IndexError, ValueError):
            return template
    return template


def detect_language(request: Request) -> str:
    """Detect language from cookie → Accept-Language → default."""
    # 1. Cookie
    lang_cookie = request.cookies.get("lang")
    if lang_cookie and lang_cookie in _SUPPORTED_LANGS:
        return lang_cookie

    # 2. Accept-Language header
    accept = request.headers.get("accept-language", "")
    for part in accept.split(","):
        code = part.split(";")[0].strip().lower()[:2]
        if code in _SUPPORTED_LANGS:
            return code

    return _DEFAULT_LANG


def make_translator(request: Request):
    """Create a ``_()`` function bound to the request's detected language."""
    lang = detect_language(request)

    def _(key: str, **kwargs: Any) -> str:
        return get_web_translation(key, lang=lang, **kwargs)

    return _, lang


def get_translations_json(lang: str = "pl") -> str:
    """Return the full web translations dict serialised as a JSON string.

    This is injected into ``base.html`` as ``window._i18n`` so that
    vanilla-JS components can call ``window.t("key", "fallback")``.
    """
    _ensure_loaded()
    strings = _web_strings.get(lang, {})
    return json.dumps(strings, ensure_ascii=False)
