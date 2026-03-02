"""Phase 4.4 — TraceViewer — Jaeger-style execution chain visualization.

Visualises the causal chain: user request → decomposition → agent executions
→ results.  Each trace is a tree of *spans* (units of work).
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class Span:
    """A single unit of work within a trace."""

    span_id: str
    trace_id: str
    parent_span_id: str = ""
    operation: str = ""  # e.g. "task.decompose", "agent.ask", "tool.run_shell"
    agent_id: str = ""
    task_id: str = ""
    start_time: float = field(default_factory=time.time)
    end_time: float | None = None
    status: str = "running"  # "running" | "completed" | "failed"
    tags: dict[str, Any] = field(default_factory=dict)
    logs: list[dict[str, Any]] = field(default_factory=list)

    @property
    def duration_ms(self) -> float | None:
        if self.end_time is None:
            return None
        return (self.end_time - self.start_time) * 1000

    def finish(self, *, status: str = "completed") -> None:
        self.end_time = time.time()
        self.status = status

    def add_log(self, message: str, **fields: Any) -> None:
        self.logs.append({
            "timestamp": time.time(),
            "message": message,
            **fields,
        })

    def to_dict(self) -> dict[str, Any]:
        return {
            "span_id": self.span_id,
            "trace_id": self.trace_id,
            "parent_span_id": self.parent_span_id,
            "operation": self.operation,
            "agent_id": self.agent_id,
            "task_id": self.task_id,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "duration_ms": self.duration_ms,
            "status": self.status,
            "tags": self.tags,
            "logs": self.logs,
        }


@dataclass
class Trace:
    """A complete trace — a tree of spans representing a user request lifecycle."""

    trace_id: str
    root_span_id: str = ""
    spans: list[Span] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def add_span(self, span: Span) -> None:
        self.spans.append(span)
        if not self.root_span_id:
            self.root_span_id = span.span_id

    def find_span(self, span_id: str) -> Span | None:
        for s in self.spans:
            if s.span_id == span_id:
                return s
        return None

    def children_of(self, span_id: str) -> list[Span]:
        return [s for s in self.spans if s.parent_span_id == span_id]

    @property
    def duration_ms(self) -> float | None:
        if not self.spans:
            return None
        start = min(s.start_time for s in self.spans)
        ends = [s.end_time for s in self.spans if s.end_time is not None]
        if not ends:
            return None
        return (max(ends) - start) * 1000

    @property
    def is_complete(self) -> bool:
        return all(s.status in ("completed", "failed") for s in self.spans)

    def to_dict(self) -> dict[str, Any]:
        return {
            "trace_id": self.trace_id,
            "root_span_id": self.root_span_id,
            "span_count": len(self.spans),
            "duration_ms": self.duration_ms,
            "is_complete": self.is_complete,
            "spans": [s.to_dict() for s in self.spans],
            "metadata": self.metadata,
        }

    def timeline(self) -> list[dict[str, Any]]:
        """Return spans sorted by start_time for timeline rendering."""
        return [s.to_dict() for s in sorted(self.spans, key=lambda s: s.start_time)]

    def tree(self) -> dict[str, Any]:
        """Return a nested tree structure (Jaeger-style)."""
        return self._build_subtree(self.root_span_id)

    def _build_subtree(self, span_id: str) -> dict[str, Any]:
        span = self.find_span(span_id)
        if span is None:
            return {}
        children = self.children_of(span_id)
        node: dict[str, Any] = span.to_dict()
        if children:
            node["children"] = [self._build_subtree(c.span_id) for c in children]
        return node


class TraceViewer:
    """Collects and queries execution traces.

    Provides timeline views, tree views and filtering for debugging
    and auditing agent execution flows.
    """

    def __init__(self, *, storage_dir: Path | None = None) -> None:
        self._traces: dict[str, Trace] = {}
        self._storage_dir = storage_dir
        if storage_dir is not None:
            storage_dir.mkdir(parents=True, exist_ok=True)

    # ---- span management ----

    _counter: int = 0

    def _next_id(self) -> str:
        TraceViewer._counter += 1
        return f"span-{int(time.time())}-{TraceViewer._counter}"

    def start_trace(self, trace_id: str, *, operation: str = "request", **tags: Any) -> Span:
        """Begin a new trace with a root span."""
        trace = Trace(trace_id=trace_id, metadata=tags)
        root = Span(
            span_id=self._next_id(),
            trace_id=trace_id,
            operation=operation,
            tags=tags,
        )
        trace.add_span(root)
        self._traces[trace_id] = trace
        return root

    def start_span(
        self,
        trace_id: str,
        *,
        parent_span_id: str = "",
        operation: str = "",
        agent_id: str = "",
        task_id: str = "",
        **tags: Any,
    ) -> Span | None:
        """Add a child span to an existing trace."""
        trace = self._traces.get(trace_id)
        if trace is None:
            return None
        span = Span(
            span_id=self._next_id(),
            trace_id=trace_id,
            parent_span_id=parent_span_id or trace.root_span_id,
            operation=operation,
            agent_id=agent_id,
            task_id=task_id,
            tags=tags,
        )
        trace.add_span(span)
        return span

    def finish_span(self, trace_id: str, span_id: str, *, status: str = "completed") -> bool:
        """Mark a span as finished."""
        trace = self._traces.get(trace_id)
        if trace is None:
            return False
        span = trace.find_span(span_id)
        if span is None:
            return False
        span.finish(status=status)

        # Auto-persist when trace is complete
        if trace.is_complete and self._storage_dir is not None:
            self._persist_trace(trace)
        return True

    # ---- queries ----

    def get_trace(self, trace_id: str) -> Trace | None:
        return self._traces.get(trace_id)

    def list_traces(self, *, limit: int = 50) -> list[dict[str, Any]]:
        """Return summaries of recent traces (newest first)."""
        all_traces = sorted(
            self._traces.values(),
            key=lambda t: t.spans[0].start_time if t.spans else 0,
            reverse=True,
        )
        return [
            {
                "trace_id": t.trace_id,
                "span_count": len(t.spans),
                "duration_ms": t.duration_ms,
                "is_complete": t.is_complete,
            }
            for t in all_traces[:limit]
        ]

    def timeline(self, trace_id: str) -> list[dict[str, Any]]:
        trace = self._traces.get(trace_id)
        if trace is None:
            return []
        return trace.timeline()

    def tree(self, trace_id: str) -> dict[str, Any]:
        trace = self._traces.get(trace_id)
        if trace is None:
            return {}
        return trace.tree()

    def search_spans(
        self,
        *,
        agent_id: str = "",
        operation: str = "",
        status: str = "",
    ) -> list[dict[str, Any]]:
        """Search across all traces for matching spans."""
        results: list[dict[str, Any]] = []
        for trace in self._traces.values():
            for span in trace.spans:
                if agent_id and span.agent_id != agent_id:
                    continue
                if operation and operation not in span.operation:
                    continue
                if status and span.status != status:
                    continue
                results.append(span.to_dict())
        return results

    # ---- persistence ----

    def _persist_trace(self, trace: Trace) -> None:
        if self._storage_dir is None:
            return
        path = self._storage_dir / f"{trace.trace_id}.json"
        path.write_text(
            json.dumps(trace.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def load_trace(self, trace_id: str) -> Trace | None:
        """Load a persisted trace from disk."""
        if self._storage_dir is None:
            return None
        path = self._storage_dir / f"{trace_id}.json"
        if not path.exists():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        trace = Trace(
            trace_id=data["trace_id"],
            root_span_id=data.get("root_span_id", ""),
            metadata=data.get("metadata", {}),
        )
        for sd in data.get("spans", []):
            span = Span(
                span_id=sd["span_id"],
                trace_id=sd["trace_id"],
                parent_span_id=sd.get("parent_span_id", ""),
                operation=sd.get("operation", ""),
                agent_id=sd.get("agent_id", ""),
                task_id=sd.get("task_id", ""),
                start_time=sd.get("start_time", 0),
                end_time=sd.get("end_time"),
                status=sd.get("status", "completed"),
                tags=sd.get("tags", {}),
                logs=sd.get("logs", []),
            )
            trace.add_span(span)
        self._traces[trace_id] = trace
        return trace
