from __future__ import annotations

from amiagi.application.discussion_sync import extract_dialogue_without_code


def test_extract_dialogue_without_code_removes_fenced_blocks() -> None:
    source = """Linia 1
```python
print('x')
```
Linia 2
"""

    result = extract_dialogue_without_code(source)

    assert "Linia 1" in result
    assert "Linia 2" in result
    assert "print('x')" not in result
