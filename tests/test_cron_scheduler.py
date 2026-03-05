"""Tests for CronScheduler — P11 Cron Jobs."""

from __future__ import annotations

import pytest
from datetime import datetime

from amiagi.interfaces.web.scheduling.cron_scheduler import (
    CronJob,
    CronScheduler,
    cron_matches,
    parse_cron,
)


# ── cron expression parser ───────────────────────────────────────


class TestParseCron:
    def test_all_stars(self) -> None:
        fields = parse_cron("* * * * *")
        assert fields["minute"] == set(range(0, 60))
        assert fields["hour"] == set(range(0, 24))

    def test_specific_values(self) -> None:
        fields = parse_cron("30 2 15 6 0")
        assert fields["minute"] == {30}
        assert fields["hour"] == {2}
        assert fields["day"] == {15}
        assert fields["month"] == {6}
        assert fields["weekday"] == {0}

    def test_range(self) -> None:
        fields = parse_cron("0-5 * * * *")
        assert fields["minute"] == {0, 1, 2, 3, 4, 5}

    def test_step(self) -> None:
        fields = parse_cron("*/15 * * * *")
        assert fields["minute"] == {0, 15, 30, 45}

    def test_comma(self) -> None:
        fields = parse_cron("0,30 * * * *")
        assert fields["minute"] == {0, 30}

    def test_invalid_field_count(self) -> None:
        with pytest.raises(ValueError, match="Expected 5"):
            parse_cron("* * *")


class TestCronMatches:
    def test_match(self) -> None:
        dt = datetime(2026, 3, 5, 14, 30)  # Thursday (weekday=3)
        assert cron_matches("30 14 * * *", dt) is True

    def test_no_match(self) -> None:
        dt = datetime(2026, 3, 5, 14, 30)
        assert cron_matches("0 0 * * *", dt) is False

    def test_weekday_match(self) -> None:
        dt = datetime(2026, 3, 5, 0, 0)  # Thursday = 3
        assert cron_matches("0 0 * * 3", dt) is True
        assert cron_matches("0 0 * * 1", dt) is False


# ── CronJob dataclass ────────────────────────────────────────────


class TestCronJob:
    def test_to_dict(self) -> None:
        job = CronJob(id="abc", name="test", cron_expr="0 2 * * *", task_title="Cleanup")
        d = job.to_dict()
        assert d["id"] == "abc"
        assert d["name"] == "test"
        assert d["cron_expr"] == "0 2 * * *"
        assert d["enabled"] is True

    def test_defaults(self) -> None:
        job = CronJob()
        assert len(job.id) == 12
        assert job.enabled is True
        assert job.last_run is None
