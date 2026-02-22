## Summary

This PR improves repository consistency, onboarding quality, and GitHub maintainability for `amiagi`.

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
- `src/amiagi/main.py`
- `tests/test_main_interrupt.py`
- `CONTRIBUTING.md`
- `RELEASE_CHECKLIST.md`
- `.github/pull_request_template.md`

## Validation

- Full test suite run locally:
  - `pytest -q`
  - Result: **135 passed**
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
