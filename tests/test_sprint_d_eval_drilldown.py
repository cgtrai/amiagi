from __future__ import annotations

from pathlib import Path


def test_evaluations_template_contains_rubric_dropdown_and_baseline_column() -> None:
    template = Path("src/amiagi/interfaces/web/templates/evaluations.html").read_text(encoding="utf-8")

    assert 'id="eval-rubric"' in template
    assert 'value="code_quality"' in template
    assert 'value="rag_quality"' in template
    assert 'id="custom-rubric-wrap"' in template
    assert 'id="card-total-runs"' in template
    assert 'id="card-pass-rate"' in template
    assert 'id="card-avg-score"' in template
    assert 'id="eval-last-run-date"' in template
    assert 'id="eval-detail-dialog"' in template
    assert 'eval.col_vs_baseline' in template


def test_evaluations_template_contains_detail_dialog() -> None:
    template = Path("src/amiagi/interfaces/web/templates/evaluations.html").read_text(encoding="utf-8")

    assert "eval-detail-dialog" in template
    assert "eval-detail-body" in template


def test_evaluations_js_contains_run_detail_loader() -> None:
    script = Path("src/amiagi/interfaces/web/static/js/evaluations.js").read_text(encoding="utf-8")

    assert "openRunDetail" in script
    assert "/api/evaluations/${encodeURIComponent(runId)}" in script
    assert "showModal" in script


def test_evaluations_js_contains_chart_drilldown() -> None:
    script = Path("src/amiagi/interfaces/web/static/js/evaluations.js").read_text(encoding="utf-8")

    assert 'eval-detail-chart' in script
    assert 'point-click' in script
    assert 'eval-scenario-drilldown' in script
    assert 'renderScenarioDetail' in script


def test_evaluations_js_contains_summary_tiles_and_config_detail() -> None:
    script = Path("src/amiagi/interfaces/web/static/js/evaluations.js").read_text(encoding="utf-8")

    assert 'total-runs-count' in script
    assert 'eval-pass-rate' in script
    assert 'eval-avg-score' in script
    assert 'eval-last-run-date' in script
    assert 'baseline_score' in script
    assert 'regression-badge' in script
    assert 'run.config' in script
    assert '⚙️ Config' in script


def test_evaluation_components_exist_for_badge_and_chart() -> None:
    badge = Path("src/amiagi/interfaces/web/static/js/components/regression-badge.js").read_text(encoding="utf-8")
    chart = Path("src/amiagi/interfaces/web/static/js/components/eval-chart.js").read_text(encoding="utf-8")

    assert 'customElements.define("regression-badge"' in badge
    assert 'delta' in badge and 'threshold' in badge
    assert 'customElements.define("eval-chart"' in chart
    assert 'point-click' in chart
