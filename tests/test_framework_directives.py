from __future__ import annotations

from amiagi.application.framework_directives import parse_framework_directive


def test_parse_read_file_directive_with_quotes() -> None:
    directive = parse_framework_directive(
        'odczytaj zawartość pliku "/home/chestr/Documents/projekty/amiagi/początkowe_konsultacje.md"'
    )

    assert directive is not None
    assert directive.action == "read_file"
    assert str(directive.path).endswith("początkowe_konsultacje.md")


def test_parse_read_file_directive_with_przeczytaj() -> None:
    directive = parse_framework_directive("przeczytaj plik /tmp/a.txt")

    assert directive is not None
    assert directive.action == "read_file"
    assert str(directive.path) == "/tmp/a.txt"


def test_parse_unknown_directive_returns_none() -> None:
    directive = parse_framework_directive("zrób coś innego")
    assert directive is None


def test_parse_read_file_directive_with_relative_path() -> None:
    directive = parse_framework_directive("przeczytaj plik wprowadzenie.md")

    assert directive is not None
    assert directive.action == "read_file"
    assert str(directive.path) == "wprowadzenie.md"


def test_parse_read_file_directive_with_tresc_phrase() -> None:
    directive = parse_framework_directive("przeczytaj treść pliku ./docs/plan.md")

    assert directive is not None
    assert directive.action == "read_file"
    assert str(directive.path) == "docs/plan.md"


def test_parse_read_file_directive_with_trailing_instruction() -> None:
    directive = parse_framework_directive(
        "przeczytaj zawartość pliku /home/chestr/Documents/projekty/amiagi/wprowadzenie.md, rozpocznij opisany w nim eksperyment"
    )

    assert directive is not None
    assert directive.action == "read_file"
    assert str(directive.path) == "/home/chestr/Documents/projekty/amiagi/wprowadzenie.md"
