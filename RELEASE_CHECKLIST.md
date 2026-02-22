# Release Checklist

Use this checklist before tagging a new release.

## 1) Code quality and tests

- [ ] Local test suite passes (`pytest -q`)
- [ ] CI workflow is green on the target branch
- [ ] No unresolved critical issues in open PRs

## 2) Repository hygiene

- [ ] No generated artifacts are staged (`logs/*.jsonl`, `data/*.db`, cache/build files)
- [ ] `.gitignore` still matches current runtime/build outputs
- [ ] Version and metadata are coherent (`pyproject.toml`, package metadata)

## 3) Documentation and contributor UX

- [ ] Setup instructions work for both `venv` and Conda custom env names
- [ ] Runtime flags and defaults are documented
- [ ] Key docs are updated if behavior changed (`README.md`, `README.pl.md`, `CONTRIBUTING.md`, `SECURITY.md`)

## 4) Security and runtime safety

- [ ] Shell policy defaults remain restrictive (`config/shell_allowlist.json`)
- [ ] High-risk execution disclaimer is present and accurate
- [ ] Any security-impacting change is called out in release notes

## 5) Final release steps

- [ ] Update version in `pyproject.toml` (if applicable)
- [ ] Create release notes (highlights + breaking changes + migration notes)
- [ ] Tag release in GitHub
- [ ] Verify installation path (`pip install -e .` and CLI entry `amiagi`)
