"""SessionReplay — replays JSONL logs for debugging and auditing."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ReplayEvent:
    """A single event from a JSONL log."""

    timestamp: str
    source: str   # which log file
    event_type: str
    data: dict[str, Any] = field(default_factory=dict)


class SessionReplay:
    """Loads and merges JSONL logs into a unified timeline for replay.

    Usage::

        replay = SessionReplay(log_dir=Path("./logs"))
        events = replay.load_session()
        for ev in events:
            print(f"[{ev.timestamp}] {ev.source}: {ev.event_type}")
    """

    # Known JSONL log files to merge
    DEFAULT_LOG_FILES = [
        "activity.jsonl",
        "model_io_executor.jsonl",
        "model_io_supervisor.jsonl",
        "supervision_dialogue.jsonl",
        "router_mailbox.jsonl",
        "agent_lifecycle.jsonl",
    ]

    def __init__(self, log_dir: Path = Path("./logs")) -> None:
        self._log_dir = log_dir

    def load_session(
        self,
        *,
        since: str | None = None,
        until: str | None = None,
        sources: list[str] | None = None,
        limit: int = 10_000,
    ) -> list[ReplayEvent]:
        """Load and merge all JSONL logs into a sorted timeline.

        Parameters
        ----------
        since : optional ISO timestamp lower bound
        until : optional ISO timestamp upper bound
        sources : optional list of log filenames to include
        limit : max number of events to return
        """
        target_files = sources or self.DEFAULT_LOG_FILES
        all_events: list[ReplayEvent] = []

        for filename in target_files:
            path = self._log_dir / filename
            if not path.exists():
                continue
            events = self._load_file(path, source=filename)
            all_events.extend(events)

        # Filter by time range
        if since is not None:
            all_events = [e for e in all_events if e.timestamp >= since]
        if until is not None:
            all_events = [e for e in all_events if e.timestamp <= until]

        # Sort by timestamp
        all_events.sort(key=lambda e: e.timestamp)

        return all_events[:limit]

    def list_sources(self) -> list[str]:
        """Return log files that exist in the log directory."""
        return [
            f for f in self.DEFAULT_LOG_FILES
            if (self._log_dir / f).exists()
        ]

    def event_count(self) -> dict[str, int]:
        """Return per-source event counts."""
        counts: dict[str, int] = {}
        for filename in self.DEFAULT_LOG_FILES:
            path = self._log_dir / filename
            if path.exists():
                try:
                    counts[filename] = sum(
                        1 for line in path.open("r", encoding="utf-8")
                        if line.strip()
                    )
                except Exception:
                    counts[filename] = 0
        return counts

    # ---- internals ----

    @staticmethod
    def _load_file(path: Path, source: str) -> list[ReplayEvent]:
        events: list[ReplayEvent] = []
        try:
            with path.open("r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                        timestamp = data.get("timestamp", "")
                        event_type = (
                            data.get("event", "")
                            or data.get("action", "")
                            or data.get("type", "unknown")
                        )
                        events.append(ReplayEvent(
                            timestamp=timestamp,
                            source=source,
                            event_type=event_type,
                            data=data,
                        ))
                    except (json.JSONDecodeError, ValueError):
                        continue
        except Exception:
            pass
        return events
