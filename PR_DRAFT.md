## Summary

This PR delivers v0.1.4 of `amiagi` with communication protocol, runtime stability fixes, and UX polish.

### Update (v0.1.4 scope)

- Implemented multi-actor communication protocol with addressed-block routing (`[Sender -> Receiver]`), unaddressed-turn reminders, and Polluks → Kastor consultation rounds.
- Fixed idle/interrupt logic: model question detection (`_model_response_awaits_user()`), supervisor `WAITING_USER_DECISION` reaction, watchdog reset on new user input, timeout 120→180s.
- Fixed missing-tool workflow: alias map (`file_read→read_file`, `dir_list→list_dir`), per-tool correction tracking (max 2), forced tool-creation plan after exhaustion.
- Fixed supervisor routing: `[Kastor -> Sponsor]` messages now route to user's main panel.
- Context-aware `/help`: Textual shows only Textual commands, CLI shows only CLI commands.
- ASCII art landing page with version/mode indicator and randomized MOTD on startup (CLI + Textual).
- Cleaned startup output: removed verbose model info, "Tryb Textual aktywny", redundant command hints.
- User message queue with position feedback when router cycle is busy.
- Fixed `notes_txt`/`repaired_txt` possibly-unbound static analysis warning in `_poll_supervision_dialogue()`.
- Bumped version to 0.1.4.

### What changed

- `src/amiagi/interfaces/textual_cli.py` — communication protocol routing, landing page, context-aware help, idle/interrupt fixes, tool alias resolution, supervisor routing, message queue, static analysis fix.
- `src/amiagi/interfaces/cli.py` — landing page banner, context-aware help, tool alias resolution, cleaned startup output.
- `src/amiagi/application/communication_protocol.py` — new module: `parse_addressed_blocks()`, `panels_for_target()`, `is_sponsor_readable()`, `load_communication_rules()`.
- `src/amiagi/application/chat_service.py` — `TOOL_CALLING_GUIDE` extended with alias docs and tool-creation workflow.
- `src/amiagi/application/supervisor_service.py` — `SupervisionResult.work_state` field, unknown-tool rule in review prompt.
- `config/communication_rules.json` — new config for protocol thresholds and templates.
- `tests/test_textual_cli.py` — 61 new tests covering all new features.
- Documentation: `README.md`, `README.pl.md`, `RELEASE_NOTES_v0.1.4.md`, `GITHUB_RELEASE_v0.1.4.md`.
- Version bump: `pyproject.toml`, `src/amiagi/__init__.py`.

## Motivation

- Establish structured communication between LLM actors for observability and correctness.
- Fix real-world runtime bugs discovered during extended usage sessions.
- Improve onboarding and startup experience with clean branding.
- Reduce noise in startup messages while keeping essential information accessible via `/help`.

## Validation

- Full test suite run locally:
  - `pytest -q`
  - Result: **229 passed**
- No static analysis errors in modified files.

## Risk assessment

- **Medium risk** — significant runtime flow changes (communication protocol, idle/interrupt, tool resolution).
- All changes are covered by 61 new targeted tests plus full regression suite.
- No permission scope or shell allowlist changes.

## Backward compatibility

- Existing invocation paths remain supported.
- Startup output is cleaner but all information is still accessible via `/help` and `/models show`.
- Communication protocol is additive — works transparently with models that don't use addressed blocks.
