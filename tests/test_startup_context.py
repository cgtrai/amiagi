from __future__ import annotations

from amiagi.application.startup_context import build_startup_summary


def test_build_startup_summary_contains_world_learning_conclusion() -> None:
    source = """
>>> Startujmy z kodowaniem
```python
print('ignore')
```
>>> Potrzebuję pamięci i ewaluacji
""".strip()

    summary = build_startup_summary(source)

    assert "napisaliśmy ten program" in summary
    assert "model może zacząć poznawać świat" in summary
    assert "ignore" not in summary
