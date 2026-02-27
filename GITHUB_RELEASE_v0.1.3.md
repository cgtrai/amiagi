# amiagi v0.1.3

Release focused on terminal usability and publish-ready release documentation.

## ✨ Highlights

- Added new clear-screen runtime commands:
  - `/cls` — clears main screen,
  - `/cls all` — clears all screens (CLI: with scrollback cleanup, Textual: all panels).
- Added command parity in both interfaces:
  - standard CLI,
  - Textual UI.
- Improved operator UX by enabling quick workspace cleanup without restarting session.
- Updated release documentation package for publication (`README`, `README.pl`, PR draft, release notes).

## 🧪 Validation

- Targeted regressions (CLI + Textual): `58 passed`.
- Full local test suite: `168 passed`.

## 🔒 Safety

- No permission scope expansion.
- No shell allowlist relaxation.

Continue to use in isolated/sandboxed environments according to `SECURITY.md`.

## 📦 Compatibility

- Python 3.10+
- Linux
