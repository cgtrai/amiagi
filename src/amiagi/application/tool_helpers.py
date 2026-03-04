"""Shared helper functions for tool execution.

These pure functions are used by both the RouterEngine (application layer)
and the interface adapters (textual_cli, cli).  Keeping them here avoids
circular imports between application and interfaces.
"""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import unquote

__all__ = [
    "resolve_tool_path",
    "is_path_within_work_dir",
    "parse_search_results_from_html",
]


# ---------------------------------------------------------------------------
# Path resolution
# ---------------------------------------------------------------------------


def resolve_tool_path(raw_path: str, work_dir: Path) -> Path:
    """Resolve a model-supplied path relative to *work_dir*.

    This handles the common cases where the model prefixes the path with the
    work directory name (or a variant with swapped hyphens / underscores)
    and collapses duplicate alias segments that occasionally appear in
    absolute paths.
    """

    def _alias_set() -> set[str]:
        canonical = work_dir.name
        return {
            canonical,
            canonical.replace("-", "_"),
            canonical.replace("_", "-"),
        }

    def _collapse_duplicate_alias_segments(path: Path, aliases: set[str]) -> Path:
        if not path.parts:
            return path

        anchor = path.anchor
        parts = list(path.parts)
        start_index = 1 if anchor else 0
        normalized_parts: list[str] = []
        previous_is_alias = False

        for part in parts[start_index:]:
            current_is_alias = part in aliases
            if current_is_alias and previous_is_alias:
                continue
            normalized_parts.append(part)
            previous_is_alias = current_is_alias

        if anchor:
            return Path(anchor, *normalized_parts)
        return Path(*normalized_parts) if normalized_parts else Path(".")

    cleaned = raw_path.strip()
    if not cleaned:
        return work_dir

    candidate = Path(cleaned)
    aliases = _alias_set()

    if candidate.is_absolute():
        return _collapse_duplicate_alias_segments(candidate, aliases)

    parts = candidate.parts
    if parts and parts[0] in aliases:
        candidate = Path(*parts[1:]) if len(parts) > 1 else Path(".")

    resolved = work_dir / candidate
    return _collapse_duplicate_alias_segments(resolved, aliases)


# ---------------------------------------------------------------------------
# Path security check
# ---------------------------------------------------------------------------


def is_path_within_work_dir(path: Path, work_dir: Path) -> bool:
    """Return True if *path* resolves to a location inside *work_dir*."""
    try:
        normalized_path = path.resolve(strict=False)
        normalized_work_dir = work_dir.resolve(strict=False)
    except Exception:
        return False
    return normalized_work_dir == normalized_path or normalized_work_dir in normalized_path.parents


# ---------------------------------------------------------------------------
# Search result parsing
# ---------------------------------------------------------------------------


def parse_search_results_from_html(
    html: str, engine: str, max_results: int,
) -> list[dict[str, str]]:
    """Extract search results from raw HTML returned by DuckDuckGo / Google."""
    results: list[dict[str, str]] = []

    if engine == "duckduckgo":
        pattern = re.compile(
            r'<a[^>]*class="result__a"[^>]*href="(?P<href>[^"]+)"[^>]*>(?P<title>.*?)</a>',
            re.IGNORECASE,
        )
        for match in pattern.finditer(html):
            href = match.group("href").strip()
            title = re.sub(r"<.*?>", "", match.group("title")).strip()
            if not href:
                continue
            results.append({"title": title or href, "url": href})
            if len(results) >= max_results:
                break
        return results

    pattern = re.compile(
        r'<a\s+href="/url\?q=(?P<url>[^"&]+)[^"]*"[^>]*>(?P<title>.*?)</a>',
        re.IGNORECASE,
    )
    for match in pattern.finditer(html):
        href = unquote(match.group("url").strip())
        if not href.startswith("http"):
            continue
        title = re.sub(r"<.*?>", "", match.group("title")).strip()
        results.append({"title": title or href, "url": href})
        if len(results) >= max_results:
            break
    return results
