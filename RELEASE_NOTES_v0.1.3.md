# amiagi v0.1.3

Release focused on terminal usability and publication-ready release artifacts.

## Highlights

- Added new runtime clear-screen commands:
  - `/cls` — clears main terminal screen,
  - `/cls all` — clears terminal screen and scrollback history.
- Added Textual clear-screen support with command parity:
  - `/cls` — clears main user panel,
  - `/cls all` — clears all panels (user, supervisor, executor, router).
- Extended test coverage for both new commands in CLI and Textual test suites.
- Updated release/documentation package for publication:
  - README (EN/PL) command docs,
  - release metadata and PR draft consistency.

## Compatibility

- Python: 3.10+
- OS: Linux

## Validation

- Targeted regression tests passed: `58 passed`.
- Full local test suite passed: `168 passed`.

## Safety

No permission policy expansion and no shell allowlist relaxation were introduced in this release.

Use only in isolated/sandboxed environments as described in `SECURITY.md`.
