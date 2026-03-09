# Release Checklist

Use this checklist before tagging a new release.

## 1) Code quality and tests

- [x] Local test suite passes (`pytest -q`) — 2543 passed, 0 warnings
- [ ] CI workflow is green on the target branch
- [x] No unresolved critical issues in open PRs

## 2) Repository hygiene

- [x] No generated artifacts are staged (`logs/*.jsonl`, `data/*.db`, cache/build files)
- [x] `.gitignore` still matches current runtime/build outputs
- [x] Version and metadata are coherent (`pyproject.toml`, package metadata)

## 3) Documentation and contributor UX

- [x] Setup instructions work for both `venv` and Conda custom env names
- [x] Runtime flags and defaults are documented
- [x] Key docs are updated if behavior changed (`README.md`, `README.pl.md`, `CONTRIBUTING.md`, `SECURITY.md`)

## 4) Security and runtime safety

- [x] Shell policy defaults remain restrictive (`config/shell_allowlist.json`)
- [x] High-risk execution disclaimer is present and accurate
- [x] Any security-impacting change is called out in release notes

## 5) Final release steps

- [x] Update version in `pyproject.toml` (v1.3.0)
- [x] Create release notes (highlights + breaking changes + migration notes)
- [ ] Tag release in GitHub
- [ ] Verify installation path (`pip install -e .` and CLI entry `amiagi`)
