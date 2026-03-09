from __future__ import annotations

from pathlib import Path


def test_workflows_page_contains_edit_clone_and_live_preview_hooks() -> None:
    template = Path("src/amiagi/interfaces/web/templates/workflows.html").read_text(encoding="utf-8")

    assert "workflow-editor-preview" in template
    assert "workflow-preview-errors" in template
    assert "workflow-dialog-mode-badge" in template
    assert "Live DAG Preview" in template


def test_workflows_page_contains_definition_controls() -> None:
    js = Path("src/amiagi/interfaces/web/static/js/workflows.js").read_text(encoding="utf-8")

    assert ">Edit</button>" in js
    assert ">Clone</button>" in js
    assert "/api/workflows/${defId}/clone" in js
    assert "Save changes" in js


def test_workflows_ui_avoids_colorful_emoji_action_labels() -> None:
    template = Path("src/amiagi/interfaces/web/templates/workflows.html").read_text(encoding="utf-8")
    js = Path("src/amiagi/interfaces/web/static/js/workflows.js").read_text(encoding="utf-8")

    assert "⏸" not in template
    assert "▶" not in template
    assert "🚀 Start" not in js
    assert "✏ Edit" not in js
    assert "⧉ Clone" not in js
    assert "🗑" not in js