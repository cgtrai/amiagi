"""Per-user settings persistence for the web interface."""

from __future__ import annotations

import json
from typing import Any


DEFAULT_USER_SETTINGS: dict[str, Any] = {
    "language": "pl",
    "theme": "dark",
    "default_workspace": "default",
    "auto_refresh_seconds": 30,
    "panel_preferences": {},
}


class UserSettingsRepository:
    """Store and retrieve UI preferences for a single user."""

    def __init__(self, pool: Any) -> None:
        self._pool = pool

    async def get_for_user(self, user_id: str) -> dict[str, Any]:
        row = await self._pool.fetchrow(
            "SELECT settings FROM dbo.user_settings WHERE user_id = $1",
            user_id,
        )
        if row is None:
            return dict(DEFAULT_USER_SETTINGS)

        raw = row.get("settings", {})
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except json.JSONDecodeError:
                raw = {}
        if not isinstance(raw, dict):
            raw = {}
        return self._normalize(raw)

    async def save_for_user(self, user_id: str, settings: dict[str, Any]) -> dict[str, Any]:
        normalized = self._normalize(settings)
        payload = json.dumps(normalized, ensure_ascii=False)
        await self._pool.execute(
            """
            INSERT INTO dbo.user_settings (user_id, settings, updated_at)
            VALUES ($1, $2::jsonb, now())
            ON CONFLICT (user_id)
            DO UPDATE SET settings = EXCLUDED.settings, updated_at = now()
            """,
            user_id,
            payload,
        )
        return normalized

    def _normalize(self, settings: dict[str, Any]) -> dict[str, Any]:
        merged = dict(DEFAULT_USER_SETTINGS)
        merged.update(settings or {})

        language = str(merged.get("language", "pl") or "pl").lower()
        if language not in {"pl", "en"}:
            language = "pl"
        merged["language"] = language

        theme = str(merged.get("theme", "dark") or "dark").strip().lower()
        merged["theme"] = theme or "dark"

        workspace = str(merged.get("default_workspace", "default") or "default").strip()
        merged["default_workspace"] = workspace or "default"

        try:
            auto_refresh = int(merged.get("auto_refresh_seconds", 30))
        except (TypeError, ValueError):
            auto_refresh = 30
        if auto_refresh < 0:
            auto_refresh = 0
        merged["auto_refresh_seconds"] = auto_refresh

        panel_preferences = merged.get("panel_preferences")
        merged["panel_preferences"] = panel_preferences if isinstance(panel_preferences, dict) else {}
        return merged