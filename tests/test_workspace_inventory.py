from __future__ import annotations

import json
from pathlib import Path

from amiagi.system_tools.workspace_inventory import analyze_workspace, render_report


def test_analyze_workspace_groups_and_sorts_results(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "docs").mkdir()
    (tmp_path / "data").mkdir()

    (tmp_path / "src" / "a.py").write_text("# comment\n\nprint('a')\nprint('b')\n", encoding="utf-8")
    (tmp_path / "src" / "b.py").write_text("print('c')\n", encoding="utf-8")
    (tmp_path / "docs" / "guide.md").write_text("# Title\n\nLine 1\nLine 2\n", encoding="utf-8")
    (tmp_path / "data" / "items.json").write_text(json.dumps([{"id": 1}, {"id": 2}, {"id": 3}]), encoding="utf-8")

    report = analyze_workspace(tmp_path)

    assert report.scanned_files == 4
    assert report.matched_files == 4
    assert report.ignored_files == 0
    assert [section.title for section in report.sections] == ["Kodowanie", "Dane JSON", "Instrukcje"]
    assert report.sections[0].rows[0].label == "Skrypty Python"
    assert report.sections[0].rows[0].units == 3
    assert report.sections[1].rows[0].units == 3
    assert report.sections[2].rows[0].units == 3


def test_analyze_workspace_respects_gitignore_with_negation(tmp_path: Path) -> None:
    (tmp_path / ".gitignore").write_text(
        ".venv/\n*.sqlite\namiagi-my-work/*\n!amiagi-my-work/wprowadzenie.md\n",
        encoding="utf-8",
    )
    (tmp_path / ".venv").mkdir()
    (tmp_path / ".venv" / "hidden.py").write_text("print('ignored')\n", encoding="utf-8")
    (tmp_path / "db.sqlite").write_text("sqlite", encoding="utf-8")
    (tmp_path / "script.py").write_text("print('ok')\n", encoding="utf-8")
    (tmp_path / "amiagi-my-work").mkdir()
    (tmp_path / "amiagi-my-work" / "note.md").write_text("skip me\n", encoding="utf-8")
    (tmp_path / "amiagi-my-work" / "wprowadzenie.md").write_text("keep me\n", encoding="utf-8")

    report = analyze_workspace(tmp_path)

    assert report.scanned_files == 3
    assert report.matched_files == 2
    assert report.skipped_files == 1
    assert report.ignored_files == 3
    assert [section.title for section in report.sections] == ["Kodowanie", "Instrukcje"]
    assert report.sections[0].rows[0].label == "Skrypty Python"
    assert report.sections[0].rows[0].units == 1
    assert report.sections[-1].rows[0].label == "Markdown"
    assert report.sections[-1].rows[0].units == 1


def test_render_report_contains_section_totals(tmp_path: Path) -> None:
    (tmp_path / "notes.md").write_text("One\nTwo\n\nThree\n", encoding="utf-8")

    report = analyze_workspace(tmp_path)
    rendered = render_report(report)

    assert "Analiza katalogu:" in rendered
    assert "Instrukcje" in rendered
    assert "Razem" in rendered
    assert "Wykluczone przez .gitignore" in rendered
