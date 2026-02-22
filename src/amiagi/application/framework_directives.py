from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class FrameworkDirective:
    action: str
    path: Path


_READ_FILE_PATTERNS = [
    re.compile(
        r"odczytaj\s+zawartość\s+pliku\s+(?:\"(?P<path_dq>[^\"]+)\"|'(?P<path_sq>[^']+)'|(?P<path_bare>\S+))",
        re.IGNORECASE,
    ),
    re.compile(
        r"przeczytaj\s+(?:treść\s+)?pliku\s+(?:\"(?P<path_dq>[^\"]+)\"|'(?P<path_sq>[^']+)'|(?P<path_bare>\S+))",
        re.IGNORECASE,
    ),
    re.compile(
        r"przeczytaj\s+zawartość\s+pliku\s+(?:\"(?P<path_dq>[^\"]+)\"|'(?P<path_sq>[^']+)'|(?P<path_bare>\S+))",
        re.IGNORECASE,
    ),
    re.compile(
        r"przeczytaj\s+plik\s+(?:\"(?P<path_dq>[^\"]+)\"|'(?P<path_sq>[^']+)'|(?P<path_bare>\S+))",
        re.IGNORECASE,
    ),
]


def parse_framework_directive(raw: str) -> FrameworkDirective | None:
    text = raw.strip()
    for pattern in _READ_FILE_PATTERNS:
        match = pattern.search(text)
        if match:
            path_text = (
                match.groupdict().get("path_dq")
                or match.groupdict().get("path_sq")
                or match.groupdict().get("path_bare")
                or ""
            ).strip().rstrip(",.;")
            if path_text:
                return FrameworkDirective(action="read_file", path=Path(path_text))
    return None
