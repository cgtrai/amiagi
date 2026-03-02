"""Internationalization (i18n) module for amiagi.

Provides a lightweight, JSON-based translation system.

Usage::

    from amiagi.i18n import _, set_language, get_language

    set_language("en")          # switch to English
    print(_("dashboard.started", url="http://localhost:8080"))

Language resolution order:
1. Explicit ``set_language()`` call
2. ``--lang`` CLI parameter
3. ``AMIAGI_LANG`` environment variable
4. Default: ``"pl"``
"""

from amiagi.i18n.loader import _, set_language, get_language, available_languages

__all__ = ["_", "set_language", "get_language", "available_languages"]
