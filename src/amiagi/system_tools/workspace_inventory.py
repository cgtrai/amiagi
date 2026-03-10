from __future__ import annotations

import argparse
import fnmatch
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable


MetricKind = str


@dataclass(frozen=True)
class GroupSpec:
    section: str
    label: str
    extensions: frozenset[str]
    metric_kind: MetricKind
    comment_prefixes: tuple[str, ...] = ()


@dataclass
class GroupTotals:
    label: str
    files: int = 0
    units: int = 0
    size_bytes: int = 0

    def add(self, *, units: int, size_bytes: int) -> None:
        self.files += 1
        self.units += units
        self.size_bytes += size_bytes

    def to_dict(self) -> dict[str, int | float | str]:
        return {
            "label": self.label,
            "files": self.files,
            "units": self.units,
            "size_bytes": self.size_bytes,
            "size_mb": round(self.size_bytes / (1024 * 1024), 3),
        }


@dataclass
class SectionReport:
    key: str
    title: str
    unit_label: str
    rows: list[GroupTotals] = field(default_factory=list)

    @property
    def totals(self) -> GroupTotals:
        total = GroupTotals(label="Razem")
        for row in self.rows:
            total.files += row.files
            total.units += row.units
            total.size_bytes += row.size_bytes
        return total

    def to_dict(self) -> dict[str, object]:
        return {
            "key": self.key,
            "title": self.title,
            "unit_label": self.unit_label,
            "rows": [row.to_dict() for row in self.rows],
            "totals": self.totals.to_dict(),
        }


@dataclass
class WorkspaceReport:
    root_path: str
    sections: list[SectionReport]
    scanned_files: int
    matched_files: int
    skipped_files: int
    ignored_files: int = 0

    def to_dict(self) -> dict[str, object]:
        return {
            "path": self.root_path,
            "scanned_files": self.scanned_files,
            "matched_files": self.matched_files,
            "skipped_files": self.skipped_files,
            "ignored_files": self.ignored_files,
            "sections": [section.to_dict() for section in self.sections],
            "grand_totals": {
                "files": sum(section.totals.files for section in self.sections),
                "size_bytes": sum(section.totals.size_bytes for section in self.sections),
                "size_mb": round(
                    sum(section.totals.size_bytes for section in self.sections) / (1024 * 1024),
                    3,
                ),
            },
        }


@dataclass(frozen=True)
class GitIgnoreRule:
    base_dir: Path
    pattern: str
    negated: bool
    directory_only: bool
    anchored: bool
    basename_only: bool


_SECTIONS: dict[str, tuple[str, str]] = {
    "code": ("Kodowanie", "linie kodu"),
    "data": ("Dane JSON", "rekordy"),
    "docs": ("Instrukcje", "linie treści"),
}

_GROUP_SPECS: tuple[GroupSpec, ...] = (
    GroupSpec("code", "Skrypty Python", frozenset({".py", ".pyw"}), "code", ("#",)),
    GroupSpec(
        "code",
        "Skrypty JS/TS",
        frozenset({".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx"}),
        "code",
        ("//",),
    ),
    GroupSpec("code", "Skrypty Shell", frozenset({".sh", ".bash", ".zsh", ".ksh"}), "code", ("#",)),
    GroupSpec("code", "Skrypty PowerShell", frozenset({".ps1", ".psm1", ".psd1"}), "code", ("#",)),
    GroupSpec("code", "Skrypty SQL", frozenset({".sql"}), "code", ("--",)),
    GroupSpec(
        "code",
        "Skrypty inne",
        frozenset({".rb", ".php", ".pl", ".lua", ".r", ".awk", ".tcl", ".groovy"}),
        "code",
        ("#", "--"),
    ),
    GroupSpec("data", "Pliki JSON", frozenset({".json"}), "json"),
    GroupSpec("data", "Pliki JSONL", frozenset({".jsonl", ".ndjson"}), "jsonl"),
    GroupSpec("docs", "Markdown", frozenset({".md", ".markdown"}), "text"),
    GroupSpec("docs", "Tekst", frozenset({".txt", ".text", ".log"}), "text"),
    GroupSpec("docs", "Dokumentacja strukturalna", frozenset({".rst", ".adoc", ".asciidoc"}), "text"),
)

_SPEC_BY_EXTENSION = {
    extension: spec
    for spec in _GROUP_SPECS
    for extension in spec.extensions
}


def analyze_workspace(path: str | Path) -> WorkspaceReport:
    root = Path(path).expanduser().resolve()
    if not root.exists() or not root.is_dir():
        raise FileNotFoundError(f"Directory not found: {root}")

    groups: dict[tuple[str, str], GroupTotals] = {}
    gitignore_rules = _load_gitignore_rules(root)
    scanned_files = 0
    matched_files = 0
    ignored_files = 0

    for dir_path, dir_names, file_names in os.walk(root, followlinks=False):
        kept_dir_names: list[str] = []
        for name in sorted(dir_names):
            candidate_dir = Path(dir_path, name)
            if candidate_dir.is_symlink():
                continue
            if _is_gitignored(candidate_dir, root=root, rules=gitignore_rules, is_dir=True):
                ignored_files += _count_files_in_tree(candidate_dir)
                continue
            kept_dir_names.append(name)
        dir_names[:] = kept_dir_names

        for file_name in sorted(file_names):
            candidate = Path(dir_path) / file_name
            if candidate.is_symlink() or not candidate.is_file():
                continue
            if _is_gitignored(candidate, root=root, rules=gitignore_rules, is_dir=False):
                ignored_files += 1
                continue

            scanned_files += 1
            spec = _SPEC_BY_EXTENSION.get(candidate.suffix.lower())
            if spec is None:
                continue

            matched_files += 1
            metric_value = _measure_file(candidate, spec)
            size_bytes = candidate.stat().st_size
            key = (spec.section, spec.label)
            group = groups.setdefault(key, GroupTotals(label=spec.label))
            group.add(units=metric_value, size_bytes=size_bytes)

    sections: list[SectionReport] = []
    for section_key, (title, unit_label) in _SECTIONS.items():
        rows = [
            row
            for (row_section, _), row in groups.items()
            if row_section == section_key
        ]
        rows = [row for row in rows if row.files > 0]
        rows.sort(key=lambda row: (-row.units, -row.size_bytes, row.label.lower()))
        if rows:
            sections.append(SectionReport(key=section_key, title=title, unit_label=unit_label, rows=rows))

    return WorkspaceReport(
        root_path=str(root),
        sections=sections,
        scanned_files=scanned_files,
        matched_files=matched_files,
        skipped_files=scanned_files - matched_files,
        ignored_files=ignored_files,
    )


def render_report(report: WorkspaceReport) -> str:
    blocks = [
        f"Analiza katalogu: {report.root_path}",
        (
            f"Przeskanowane pliki: {report.scanned_files} | Ujęte w raporcie: {report.matched_files} | "
            f"Pominięte: {report.skipped_files} | Wykluczone przez .gitignore: {report.ignored_files}"
        ),
    ]
    for section in report.sections:
        blocks.append("")
        blocks.extend(_render_section(section))
    return "\n".join(blocks)


def cli_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Analiza struktury pracy agentów: pliki, linie kodu, rekordy JSON i linie treści."
    )
    parser.add_argument("--path", default=".", help="Katalog startowy analizy. Domyślnie bieżący katalog.")
    parser.add_argument(
        "--format",
        choices=("txt", "json"),
        default="txt",
        help="Format wyjścia: txt albo json. Domyślnie txt.",
    )
    args = parser.parse_args(argv)

    report = analyze_workspace(args.path)
    if args.format == "json":
        print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
    else:
        print(render_report(report))
    return 0


def _measure_file(path: Path, spec: GroupSpec) -> int:
    handlers: dict[MetricKind, Callable[[Path, GroupSpec], int]] = {
        "code": _count_code_lines,
        "json": _count_json_records,
        "jsonl": _count_jsonl_records,
        "text": _count_text_lines,
    }
    return handlers[spec.metric_kind](path, spec)


def _count_code_lines(path: Path, spec: GroupSpec) -> int:
    count = 0
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for raw_line in handle:
            stripped = raw_line.strip()
            if not stripped:
                continue
            if any(stripped.startswith(prefix) for prefix in spec.comment_prefixes):
                continue
            count += 1
    return count


def _count_text_lines(path: Path, spec: GroupSpec) -> int:
    del spec
    count = 0
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for raw_line in handle:
            if raw_line.strip():
                count += 1
    return count


def _count_json_records(path: Path, spec: GroupSpec) -> int:
    del spec
    try:
        payload = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except json.JSONDecodeError:
        return 0
    if isinstance(payload, list):
        return len(payload)
    if isinstance(payload, dict):
        return 1
    return 1


def _count_jsonl_records(path: Path, spec: GroupSpec) -> int:
    del spec
    count = 0
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for raw_line in handle:
            if raw_line.strip():
                count += 1
    return count


def _render_section(section: SectionReport) -> list[str]:
    rows = section.rows + [section.totals]
    columns = [section.title, "pliki", section.unit_label, "łącznie w MB"]
    widths = [
        max(len(columns[0]), *(len(row.label) for row in rows)),
        max(len(columns[1]), *(len(str(row.files)) for row in rows)),
        max(len(columns[2]), *(len(str(row.units)) for row in rows)),
        max(len(columns[3]), *(len(_format_size_mb(row.size_bytes)) for row in rows)),
    ]

    separator = "+".join("-" * (width + 2) for width in widths)
    lines = [separator, _format_row(columns, widths), separator]
    for row in section.rows:
        lines.append(
            _format_row(
                [row.label, str(row.files), str(row.units), _format_size_mb(row.size_bytes)],
                widths,
            )
        )
    lines.append(separator)
    totals = section.totals
    lines.append(
        _format_row(
            [totals.label, str(totals.files), str(totals.units), _format_size_mb(totals.size_bytes)],
            widths,
        )
    )
    lines.append(separator)
    return lines


def _format_row(values: list[str], widths: list[int]) -> str:
    return " | ".join(value.ljust(width) for value, width in zip(values, widths, strict=True))


def _format_size_mb(size_bytes: int) -> str:
    return f"{size_bytes / (1024 * 1024):.1f}"


def _load_gitignore_rules(root: Path) -> list[GitIgnoreRule]:
    scoped_dirs: list[Path] = []
    current = root
    while True:
        scoped_dirs.append(current)
        if (current / ".git").exists() or current == current.parent:
            break
        current = current.parent

    rules: list[GitIgnoreRule] = []
    for base_dir in reversed(scoped_dirs):
        gitignore_path = base_dir / ".gitignore"
        if not gitignore_path.is_file():
            continue
        rules.extend(_parse_gitignore(gitignore_path, base_dir))
    return rules


def _parse_gitignore(gitignore_path: Path, base_dir: Path) -> list[GitIgnoreRule]:
    rules: list[GitIgnoreRule] = []
    for raw_line in gitignore_path.read_text(encoding="utf-8", errors="replace").splitlines():
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        negated = stripped.startswith("!")
        if negated:
            stripped = stripped[1:]
        if not stripped:
            continue

        anchored = stripped.startswith("/")
        if anchored:
            stripped = stripped[1:]

        directory_only = stripped.endswith("/")
        if directory_only:
            stripped = stripped[:-1]
        if not stripped:
            continue

        rules.append(
            GitIgnoreRule(
                base_dir=base_dir,
                pattern=stripped,
                negated=negated,
                directory_only=directory_only,
                anchored=anchored,
                basename_only="/" not in stripped,
            )
        )
    return rules


def _is_gitignored(path: Path, *, root: Path, rules: list[GitIgnoreRule], is_dir: bool) -> bool:
    if not rules:
        return False

    ignored = False
    resolved_path = path.resolve()
    resolved_root = root.resolve()
    if not resolved_path.is_relative_to(resolved_root):
        return False

    for rule in rules:
        try:
            relative = resolved_path.relative_to(rule.base_dir.resolve())
        except ValueError:
            continue
        if _rule_matches(rule, relative, is_dir=is_dir):
            ignored = not rule.negated
    return ignored


def _rule_matches(rule: GitIgnoreRule, relative: Path, *, is_dir: bool) -> bool:
    if rule.directory_only and not is_dir:
        return False

    relative_posix = relative.as_posix()
    if relative_posix == ".":
        return False

    if rule.basename_only:
        return fnmatch.fnmatch(relative.name, rule.pattern)

    if rule.anchored:
        return fnmatch.fnmatch(relative_posix, rule.pattern)

    return fnmatch.fnmatch(relative_posix, rule.pattern) or fnmatch.fnmatch(relative_posix, f"*/{rule.pattern}")


def _count_files_in_tree(root: Path) -> int:
    count = 0
    for dir_path, dir_names, file_names in os.walk(root, followlinks=False):
        dir_names[:] = [name for name in dir_names if not Path(dir_path, name).is_symlink()]
        for file_name in file_names:
            candidate = Path(dir_path) / file_name
            if candidate.is_symlink() or not candidate.is_file():
                continue
            count += 1
    return count


if __name__ == "__main__":
    raise SystemExit(cli_main())