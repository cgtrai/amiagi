from __future__ import annotations

from pathlib import Path


def test_memory_page_contains_add_and_cross_reference_hooks() -> None:
    template = Path("src/amiagi/interfaces/web/templates/memory.html").read_text(encoding="utf-8")
    js = Path("src/amiagi/interfaces/web/static/js/memory.js").read_text(encoding="utf-8")

    assert "btn-add-memory" in template
    assert "memory-selection-summary" in template
    assert "data-memory-link=\"task\"" in js
    assert "openMemoryCreator" in js


def test_memory_page_contains_type_badges_and_editor_hooks() -> None:
    js = Path("src/amiagi/interfaces/web/static/js/memory.js").read_text(encoding="utf-8")
    css = Path("src/amiagi/interfaces/web/static/css/memory.css").read_text(encoding="utf-8")

    assert "memory-type-badge" in js
    assert "openMemoryEditor" in js
    assert "em-type" in js
    assert ".memory-links" in css