from __future__ import annotations

from pathlib import Path


def test_cron_page_contains_reminder_style_planner_hooks() -> None:
    template = Path("src/amiagi/interfaces/web/templates/cron.html").read_text(encoding="utf-8")

    assert "cron-preset-card" in template
    assert "cron-frequency-chip" in template
    assert "cron-weekday-chip" in template
    assert "cron-summary-human" in template
    assert "cron-summary-expr" in template
    assert "schedule: getSchedulePayload()" in template
    assert "/api/cron/preview" in template


def test_cron_page_contains_human_first_copy() -> None:
    template = Path("src/amiagi/interfaces/web/templates/cron.html").read_text(encoding="utf-8")

    assert "Build recurring jobs like setting a reminder" in template
    assert "Expert mode with raw expression" in template
    assert "calendar or reminder app" in template