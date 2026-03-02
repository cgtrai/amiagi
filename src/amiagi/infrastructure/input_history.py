"""Persistent input history for CLI/TUI interfaces.

Stores command history in a plain text file (one command per line)
and exposes a readline-style cursor for up/down navigation.
"""

from __future__ import annotations

from pathlib import Path

_DEFAULT_MAX_ENTRIES = 500


class InputHistory:
    """Readline-like history backed by a text file."""

    def __init__(
        self,
        path: Path,
        *,
        max_entries: int = _DEFAULT_MAX_ENTRIES,
    ) -> None:
        self._path = path
        self._max_entries = max_entries
        self._entries: list[str] = []
        self._cursor: int = 0  # points *past* the last entry (new-command position)
        self._draft: str = ""  # text typed before navigating
        self._load()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def add(self, text: str) -> None:
        """Append *text* to history (deduplicates consecutive entries)."""
        stripped = text.strip()
        if not stripped:
            return
        if self._entries and self._entries[-1] == stripped:
            # Don't duplicate the most recent entry.
            self._reset_cursor()
            return
        self._entries.append(stripped)
        if len(self._entries) > self._max_entries:
            self._entries = self._entries[-self._max_entries :]
        self._save()
        self._reset_cursor()

    def older(self, current_text: str = "") -> str | None:
        """Move cursor one step back (↑).  Returns the entry or *None*."""
        if not self._entries:
            return None
        if self._cursor == len(self._entries):
            # Leaving the "new command" position — save draft.
            self._draft = current_text
        if self._cursor <= 0:
            return self._entries[0]
        self._cursor -= 1
        return self._entries[self._cursor]

    def newer(self) -> str | None:
        """Move cursor one step forward (↓).  Returns entry/draft or *None*."""
        if self._cursor >= len(self._entries):
            return None
        self._cursor += 1
        if self._cursor == len(self._entries):
            return self._draft
        return self._entries[self._cursor]

    def reset_cursor(self) -> None:
        """Reset cursor to the newest position (after submitting)."""
        self._reset_cursor()

    @property
    def entries(self) -> list[str]:
        return list(self._entries)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _reset_cursor(self) -> None:
        self._cursor = len(self._entries)
        self._draft = ""

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            text = self._path.read_text(encoding="utf-8")
            self._entries = [
                line for line in text.splitlines() if line.strip()
            ][-self._max_entries :]
        except Exception:
            self._entries = []
        self._reset_cursor()

    def _save(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(
                "\n".join(self._entries[-self._max_entries :]) + "\n",
                encoding="utf-8",
            )
        except Exception:
            pass
