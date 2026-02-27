# amiagi v0.1.1

Incremental release focused on supervisor-worker runtime reliability and observability.

## Highlights

- Executor role prompt aligned with autonomous runtime role to remove contradictory framing.
- Passive-turn accounting fixed so supervisor/autonomy-repaired actionable `tool_call` responses are not counted as idle.
- Supervisor review criteria hardened to reject declarative “done” claims without hard evidence (`TOOL_RESULT` / artifacts).
- Optional Textual UI (`--ui textual`) added with 3-pane layout:
  - left: user ↔ examined model,
  - right top: supervisor technical dialogue,
  - right bottom: executor technical dialogue to supervisor.

## Compatibility

- Python: 3.10+
- OS: Linux

## Verification

- Full local test suite passed: `139 passed`.

## Safety

No permission model or tool policy changes were introduced.  
Use in isolated/sandboxed environments as described in `SECURITY.md`.

## Post-release updates (2026-02-27)

- Textual runtime now applies supervisor refinement directly in the user-turn path, not only in auxiliary flows.
- Added passive-streak corrective stage in both Textual and standard CLI (`user_turn_passive_streak`) to reduce repeated non-actionable model replies.
- Added full tool-call continuation in Textual (`tool_call -> TOOL_RESULT -> follow-up ask`) to enforce execution continuity.
- Added unknown-tool recovery path in Textual (e.g., unsupported tool names) with supervisor corrective stage and safe fallback.
- Added progress guard in Textual to detect stale/invalid plan state and request a concrete next action.
- Added periodic supervisor watchdog in Textual to trigger nudges after inactivity and force next operational steps.
- Extended regression coverage for the above scenarios in:
  - `tests/test_textual_cli.py`
  - `tests/test_cli_runtime_flow.py`
  - `tests/test_main_interrupt.py`
