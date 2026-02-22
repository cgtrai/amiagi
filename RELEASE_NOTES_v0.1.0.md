# amiagi v0.1.0

First public release of `amiagi`.

## Highlights

- CLI-first local framework for controlled LLM autonomy experiments.
- Layered architecture: `domain`, `application`, `infrastructure`, `interfaces`.
- Local Ollama integration with executor/supervisor split.
- Persistent memory in SQLite and JSONL audit logging.
- Permission-gated resource access and shell allowlist policy.
- Runtime VRAM-aware queueing behavior with optional override (`-vram-off`).
- Session continuity features (`startup_seed`, summaries, restart flow).

## Repository and maintainability improvements

- Environment-agnostic setup docs (no hard dependency on a single Conda env name).
- Added contributor workflow documentation: `CONTRIBUTING.md`.
- Added release process checklist: `RELEASE_CHECKLIST.md`.
- Added PR template: `.github/pull_request_template.md`.
- Improved repository hygiene with updated `.gitignore` for runtime/build artifacts.

## Compatibility

- Python: 3.10+
- OS: Linux
- CI: GitHub Actions (3.10, 3.11, 3.12)

## Verification

- Full local test suite passed: `135 passed`.

## Safety

This project can execute model-generated code and shell commands. Use only in isolated/sandboxed environments and follow `SECURITY.md`.
