from pathlib import Path


def test_files_template_contains_tabs_tree_and_results() -> None:
    template = Path('src/amiagi/interfaces/web/templates/files.html').read_text(encoding='utf-8')

    assert 'files-tab' in template
    assert 'uploads-panel' in template
    assert 'workspace-panel' in template
    assert 'results-panel' in template
    assert '/workspace/uploads' in template
    assert 'openSendToAgent' in template
    assert 'XMLHttpRequest' in template
    assert 'amiagi.pendingAttachments' in template


def test_files_styles_support_tree_preview_and_progress() -> None:
    styles = Path('src/amiagi/interfaces/web/static/css/files-page.css').read_text(encoding='utf-8')

    assert '.workspace-browser' in styles
    assert '.tree-file' in styles
    assert '.upload-progress__bar' in styles
    assert '.results-grid' in styles