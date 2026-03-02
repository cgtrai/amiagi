"""Tests for TraceViewer — Phase 4 tracing infrastructure."""

from __future__ import annotations

import time
from pathlib import Path

from amiagi.infrastructure.trace_viewer import Span, Trace, TraceViewer


# ---- Span tests ----

def test_span_creation() -> None:
    span = Span(span_id="s1", trace_id="t1", operation="test_op")
    assert span.span_id == "s1"
    assert span.trace_id == "t1"
    assert span.status == "running"
    assert span.duration_ms is None


def test_span_finish() -> None:
    span = Span(span_id="s1", trace_id="t1", operation="op")
    span.finish(status="completed")
    assert span.status == "completed"
    assert span.end_time is not None
    assert span.duration_ms is not None
    assert span.duration_ms >= 0


def test_span_add_log() -> None:
    span = Span(span_id="s1", trace_id="t1")
    span.add_log("test message", key="value")
    assert len(span.logs) == 1
    assert span.logs[0]["message"] == "test message"
    assert span.logs[0]["key"] == "value"


def test_span_to_dict() -> None:
    span = Span(span_id="s1", trace_id="t1", operation="op", agent_id="a1")
    d = span.to_dict()
    assert d["span_id"] == "s1"
    assert d["trace_id"] == "t1"
    assert d["operation"] == "op"
    assert d["agent_id"] == "a1"


# ---- Trace tests ----

def test_trace_add_and_find_span() -> None:
    trace = Trace(trace_id="t1")
    span = Span(span_id="s1", trace_id="t1", operation="root")
    trace.add_span(span)
    assert trace.root_span_id == "s1"
    assert trace.find_span("s1") is span
    assert trace.find_span("nonexistent") is None


def test_trace_children_of() -> None:
    trace = Trace(trace_id="t1")
    root = Span(span_id="root", trace_id="t1")
    child1 = Span(span_id="c1", trace_id="t1", parent_span_id="root")
    child2 = Span(span_id="c2", trace_id="t1", parent_span_id="root")
    trace.add_span(root)
    trace.add_span(child1)
    trace.add_span(child2)

    children = trace.children_of("root")
    assert len(children) == 2
    assert children[0].span_id == "c1"


def test_trace_timeline() -> None:
    trace = Trace(trace_id="t1")
    s1 = Span(span_id="s1", trace_id="t1", operation="first")
    s2 = Span(span_id="s2", trace_id="t1", operation="second")
    trace.add_span(s1)
    trace.add_span(s2)

    tl = trace.timeline()
    assert len(tl) == 2
    assert tl[0]["operation"] in ("first", "second")


def test_trace_tree() -> None:
    trace = Trace(trace_id="t1")
    root = Span(span_id="root", trace_id="t1", operation="root_op")
    child = Span(span_id="c1", trace_id="t1", parent_span_id="root", operation="child_op")
    trace.add_span(root)
    trace.add_span(child)

    tree = trace.tree()
    assert tree["span_id"] == "root"
    assert len(tree["children"]) == 1
    assert tree["children"][0]["span_id"] == "c1"


def test_trace_is_complete() -> None:
    trace = Trace(trace_id="t1")
    s = Span(span_id="s1", trace_id="t1")
    trace.add_span(s)
    assert not trace.is_complete
    s.finish()
    assert trace.is_complete


def test_trace_to_dict() -> None:
    trace = Trace(trace_id="t1")
    trace.add_span(Span(span_id="s1", trace_id="t1"))
    d = trace.to_dict()
    assert d["trace_id"] == "t1"
    assert len(d["spans"]) == 1


# ---- TraceViewer tests ----

def test_trace_viewer_start_trace(tmp_path: Path) -> None:
    tv = TraceViewer(storage_dir=tmp_path)
    span = tv.start_trace("t1", operation="request")
    assert span.trace_id == "t1"
    assert span.operation == "request"
    trace = tv.get_trace("t1")
    assert trace is not None
    assert len(trace.spans) == 1


def test_trace_viewer_start_span(tmp_path: Path) -> None:
    tv = TraceViewer(storage_dir=tmp_path)
    root = tv.start_trace("t1")
    child = tv.start_span("t1", parent_span_id=root.span_id, operation="child")
    assert child is not None
    assert child.parent_span_id == root.span_id
    trace = tv.get_trace("t1")
    assert trace is not None
    assert len(trace.spans) == 2


def test_trace_viewer_finish_span(tmp_path: Path) -> None:
    tv = TraceViewer(storage_dir=tmp_path)
    root = tv.start_trace("t1")
    result = tv.finish_span("t1", root.span_id, status="completed")
    assert result is True
    trace = tv.get_trace("t1")
    assert trace is not None
    assert trace.spans[0].status == "completed"


def test_trace_viewer_finish_nonexistent_span(tmp_path: Path) -> None:
    tv = TraceViewer(storage_dir=tmp_path)
    tv.start_trace("t1")
    result = tv.finish_span("t1", "nonexistent")
    assert result is False


def test_trace_viewer_list_traces(tmp_path: Path) -> None:
    tv = TraceViewer(storage_dir=tmp_path)
    tv.start_trace("t1")
    tv.start_trace("t2")
    traces = tv.list_traces()
    assert len(traces) == 2


def test_trace_viewer_timeline(tmp_path: Path) -> None:
    tv = TraceViewer(storage_dir=tmp_path)
    tv.start_trace("t1", operation="req")
    tv.start_span("t1", operation="sub")
    tl = tv.timeline("t1")
    assert len(tl) == 2


def test_trace_viewer_tree(tmp_path: Path) -> None:
    tv = TraceViewer(storage_dir=tmp_path)
    root = tv.start_trace("t1", operation="root_op")
    tv.start_span("t1", parent_span_id=root.span_id, operation="child_op")
    tree = tv.tree("t1")
    assert tree["span_id"] == root.span_id
    assert len(tree["children"]) == 1


def test_trace_viewer_search_spans(tmp_path: Path) -> None:
    tv = TraceViewer(storage_dir=tmp_path)
    root = tv.start_trace("t1", operation="req")
    tv.start_span("t1", parent_span_id=root.span_id, operation="sub", agent_id="a2")

    results = tv.search_spans(agent_id="a2")
    assert len(results) == 1
    assert results[0]["agent_id"] == "a2"


def test_trace_viewer_nonexistent_trace(tmp_path: Path) -> None:
    tv = TraceViewer(storage_dir=tmp_path)
    assert tv.get_trace("nope") is None
    assert tv.timeline("nope") == []
    assert tv.tree("nope") == {}


def test_trace_viewer_no_storage() -> None:
    tv = TraceViewer()
    root = tv.start_trace("t1")
    assert root.trace_id == "t1"
    assert tv.get_trace("t1") is not None
