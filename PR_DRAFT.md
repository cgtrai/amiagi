## Summary

This PR improves repository consistency, onboarding quality, and GitHub maintainability for `amiagi`.

### Update (v0.1.3 scope)

- Aligned executor role prompt with autonomous runtime role.
- Fixed passive-state accounting after supervisor/autonomy repairs.
- Hardened supervisor evidence criteria against declarative completion claims.
- Added optional Textual tri-pane UI (`--ui textual`) for user/supervisor/executor dialogue visibility.
- Added release notes for `v0.1.1` and bumped project version to `0.1.1`.
- Added runtime model management commands in CLI and Textual (`/models show`, `/models chose <nr>`, `/models current`).
- Added automatic default executor model selection from local Ollama list on startup.
- Normalized user-facing model output to plain text (raw `tool_call` payloads remain in technical logs).
- Added runtime clear-screen commands (`/cls`, `/cls all`) in both CLI and Textual.
- Added dedicated release notes for `v0.1.3` and bumped project version to `0.1.3`.

### What changed

- Removed hard dependency in docs on a specific Conda environment name (`deeplob`), replacing it with neutral instructions for user-defined environment names.
- Added robust startup dialogue path resolution in runtime:
  - if `wprowadzenie.md` (means initial tasks) is not found in the current working directory,
  - runtime now falls back to `AMIAGI_WORK_DIR/wprowadzenie.md`.
- Added regression test for startup dialogue fallback behavior.
- Improved repository hygiene by extending `.gitignore` for generated runtime/build artifacts.
- Added contributor and release process documentation:
  - `CONTRIBUTING.md`
  - `RELEASE_CHECKLIST.md`
  - `.github/pull_request_template.md`
- Linked contributing and release process docs from both README variants.

## Motivation

- Make setup and usage environment-agnostic for all users (not tied to one local Conda env).
- Improve runtime resilience and reduce friction for default project structure usage.
- Raise repository quality to a professional GitHub standard with clearer collaboration and release flows.

## Files touched

- `.gitignore`
- `README.md`
- `README.pl.md`
- `RELEASE_NOTES_v0.1.3.md`
- `src/amiagi/main.py`
- `tests/test_main_interrupt.py`
- `CONTRIBUTING.md`
- `RELEASE_CHECKLIST.md`
- `.github/pull_request_template.md`
- `src/amiagi/interfaces/cli.py`
- `src/amiagi/interfaces/textual_cli.py`
- `src/amiagi/infrastructure/ollama_client.py`
- `tests/test_cli_runtime_flow.py`
- `tests/test_textual_cli.py`
- `pyproject.toml`

## Validation

- Full test suite run locally:
  - `pytest -q`
  - Result: **168 passed**
- No diagnostics errors in modified documentation/config files.

## Risk assessment

- **Low risk**.
- Runtime logic change is narrowly scoped to startup dialogue path resolution and is covered by a dedicated test.
- Documentation and process additions do not alter core business logic.

## Backward compatibility

- Existing invocation paths remain supported.
- Existing users with custom startup dialogue paths are unaffected.
- New fallback only improves behavior when the default relative path is missing.

## Notes for reviewers

- Focus review on `src/amiagi/main.py` fallback logic and corresponding test coverage in `tests/test_main_interrupt.py`.
- Docs updates are aligned across EN/PL README files.
