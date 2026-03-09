from pathlib import Path


def test_budget_template_contains_energy_daily_and_sortable_task_table() -> None:
    template = Path('src/amiagi/interfaces/web/templates/budget.html').read_text(encoding='utf-8')

    assert 'budget-energy-cost' in template
    assert 'budget-daily-quota' in template
    assert 'budget-task-table' in template
    assert 'budget-sort-btn' in template
    assert 'data-sort="costPer1k"' in template


def test_budget_js_contains_energy_daily_and_task_sort_logic() -> None:
    source = Path('src/amiagi/interfaces/web/static/js/budget.js').read_text(encoding='utf-8')

    assert 'budget-energy-cost' in source
    assert 'budget-daily-quota' in source
    assert 'taskSort' in source
    assert 'costPer1k' in source
    assert 'budget-sort-btn' in source