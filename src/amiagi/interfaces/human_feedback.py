"""HumanFeedbackCollector — captures Sponsor thumbs up/down + comments."""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class FeedbackEntry:
    """A single piece of human feedback."""

    agent_id: str
    rating: int  # +1 = thumbs up, -1 = thumbs down, 0 = neutral
    comment: str = ""
    context: str = ""  # what was the agent's response about
    timestamp: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)


class HumanFeedbackCollector:
    """Collects and persists Sponsor feedback on agent responses.

    Feedback is stored in a JSONL file for future prompt tuning
    or fine-tuning.

    Usage::

        collector = HumanFeedbackCollector(Path("./data/feedback.jsonl"))
        collector.record(FeedbackEntry(agent_id="polluks", rating=1, comment="Great!"))
        recent = collector.query(agent_id="polluks")
    """

    def __init__(self, feedback_path: Path) -> None:
        self._path = feedback_path
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._entries: list[FeedbackEntry] = []

    def record(self, entry: FeedbackEntry) -> None:
        """Record a feedback entry."""
        with self._lock:
            self._entries.append(entry)
            self._append_to_disk(entry)

    def thumbs_up(
        self,
        agent_id: str,
        *,
        comment: str = "",
        context: str = "",
    ) -> FeedbackEntry:
        """Convenience: record a positive rating."""
        entry = FeedbackEntry(
            agent_id=agent_id,
            rating=1,
            comment=comment,
            context=context,
        )
        self.record(entry)
        return entry

    def thumbs_down(
        self,
        agent_id: str,
        *,
        comment: str = "",
        context: str = "",
    ) -> FeedbackEntry:
        """Convenience: record a negative rating."""
        entry = FeedbackEntry(
            agent_id=agent_id,
            rating=-1,
            comment=comment,
            context=context,
        )
        self.record(entry)
        return entry

    def query(
        self,
        *,
        agent_id: str | None = None,
        rating: int | None = None,
        limit: int = 100,
    ) -> list[FeedbackEntry]:
        """Return feedback entries, newest first."""
        with self._lock:
            result = list(self._entries)

        if agent_id is not None:
            result = [e for e in result if e.agent_id == agent_id]
        if rating is not None:
            result = [e for e in result if e.rating == rating]

        result.sort(key=lambda e: e.timestamp, reverse=True)
        return result[:limit]

    def count(self, agent_id: str | None = None) -> int:
        with self._lock:
            if agent_id is None:
                return len(self._entries)
            return sum(1 for e in self._entries if e.agent_id == agent_id)

    def summary(self) -> dict[str, dict[str, int]]:
        """Per-agent summary: {agent_id: {positive, negative, total}}."""
        stats: dict[str, dict[str, int]] = {}
        with self._lock:
            for e in self._entries:
                if e.agent_id not in stats:
                    stats[e.agent_id] = {"positive": 0, "negative": 0, "total": 0}
                stats[e.agent_id]["total"] += 1
                if e.rating > 0:
                    stats[e.agent_id]["positive"] += 1
                elif e.rating < 0:
                    stats[e.agent_id]["negative"] += 1
        return stats

    # ---- persistence ----

    def _append_to_disk(self, entry: FeedbackEntry) -> None:
        record = {
            "agent_id": entry.agent_id,
            "rating": entry.rating,
            "comment": entry.comment,
            "context": entry.context,
            "timestamp": entry.timestamp,
            "metadata": entry.metadata,
        }
        with self._path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
