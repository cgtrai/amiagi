# amiagi v0.1.4

Release focused on communication protocol, runtime stability, UX polish, and landing page.

## Highlights

### Multi-actor communication protocol
- Implemented full addressed-block communication protocol for LLM actors (Polluks, Kastor, Sponsor, Koordynator).
- Added `parse_addressed_blocks()`, `panels_for_target()`, `is_sponsor_readable()` in `communication_protocol.py`.
- Implemented addressed-block routing to correct Textual panels (e.g. `[Kastor -> Sponsor]` → user panel).
- Added configurable `communication_rules.json` with missing-header thresholds, reminder templates, and consultation round limits.
- Added Polluks → Kastor consultation round support with configurable max rounds.
- Added unaddressed-turn tracking with reminder injection for protocol enforcement.

### Runtime bug fixes
- **Idle/interrupt fix**: Model now correctly detects when it asks the user a question (`_model_response_awaits_user()`), pauses the plan, and suspends the watchdog until the user responds. Increased `INTERRUPT_AUTORESUME_IDLE_SECONDS` from 120→180s.
- **Missing tools workflow fix**: Extended `_canonical_tool_name()` with alias map (`file_read→read_file`, `dir_list→list_dir`, etc.), per-tool correction tracking (max 2 attempts), and forced tool-creation plan after correction exhaustion.
- **Supervisor routing fix**: `[Kastor -> Sponsor]` messages now correctly route to the user's main panel, not just the supervisor panel.

### Context-aware `/help`
- Textual mode shows only Textual-relevant commands; CLI mode shows only CLI-relevant commands.
- Mode-specific headers: "Komendy (textual):" and "Komendy (CLI):".

### Landing page & branding
- Designed ASCII art logo for AmIAgi displayed on startup in both CLI and Textual.
- Added randomized MOTD (Message of the Day) pool with 8 humorous/thematic quotes.
- Cleaned up startup output: removed verbose model info, "Tryb Textual aktywny", redundant command hints.
- Single clean message: "Wpisz /help, aby zobaczyć dostępne komendy."

### Input UX
- Added user message queue system for messages submitted while router cycle is in progress.
- Queue position feedback and drain-on-idle behavior.

## Changed

- Supervisor `work_state` field (`WAITING_USER_DECISION`) now pauses plan and suspends watchdog.
- Watchdog resets on new user input (attempts counter, cap notification, suspension flag).
- Corrective prompts now include available tool list to reduce hallucinated tool names.
- `TOOL_CALLING_GUIDE` extended with tool alias documentation and 7-step proactive tool creation workflow.

## Compatibility

- Python: 3.10+
- OS: Linux

## Validation

- Full local test suite: **229 passed**.
- Test growth: 168 (v0.1.3) → 229 (v0.1.4), +61 new tests.

## Safety

No permission policy expansion and no shell allowlist relaxation were introduced in this release.

Use only in isolated/sandboxed environments as described in `SECURITY.md`.
