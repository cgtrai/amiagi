# amiagi v0.1.2

Release focused on runtime usability, operator control, and cleaner user-facing communication.

## Highlights

- Added runtime model management commands in both interfaces:
  - `/models current`
  - `/models show`
  - `/models chose <nr>`
- Added automatic default executor model selection from local Ollama list at startup (first available model, `1/x`).
- Unified command namespace to `/models ...` (removed mixed `/model ...` flow).
- Improved UX of model responses:
  - end-user view now shows plain, readable messages,
  - raw `tool_call` payloads stay in technical logs/panels.
- Preserved parity between standard CLI and Textual UI for model selection/onboarding behavior.

## Compatibility

- Python: 3.10+
- OS: Linux

## Validation

- Full local test suite passed: `165 passed`.
- Targeted regression (CLI + Textual) passed: `55 passed`.

## Safety

No permission scope expansion and no relaxation of shell policy were introduced in this release.

Continue to run in isolated/sandboxed environments as documented in `SECURITY.md`.
