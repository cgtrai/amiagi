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
