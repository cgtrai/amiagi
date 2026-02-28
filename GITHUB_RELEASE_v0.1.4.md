# amiagi v0.1.4

Major runtime update: communication protocol, stability fixes, UX polish, and ASCII art landing page.

## ✨ Highlights

### Multi-actor communication protocol
- Full addressed-block communication protocol for LLM actors (Polluks, Kastor, Sponsor, Koordynator).
- Configurable `communication_rules.json` with missing-header thresholds, reminders, and consultation rounds.
- Addressed-block routing to correct Textual panels (e.g. `[Kastor -> Sponsor]` → user panel).

### Runtime stability fixes
- **Idle/interrupt**: Model question detection pauses plan and suspends watchdog until user responds. Timeout 120→180s.
- **Tool alias resolution**: `file_read→read_file`, `dir_list→list_dir` etc. with per-tool correction tracking (max 2), then forced tool-creation plan.
- **Supervisor routing**: `[Kastor -> Sponsor]` now correctly reaches user's main panel.

### UX improvements
- Context-aware `/help` — shows only commands relevant to active interface mode.
- ASCII art landing page with version/mode indicator and randomized MOTD on startup (CLI + Textual).
- Cleaned startup output: no verbose model info, just the banner and `/help` hint.
- User message queue with position feedback when router is busy.

## 🧪 Validation

- Full local test suite: **229 passed** (was 168 in v0.1.3, +61 new tests).

## 🔒 Safety

- No permission scope expansion.
- No shell allowlist relaxation.

Continue to use in isolated/sandboxed environments according to `SECURITY.md`.

## 📦 Compatibility

- Python 3.10+
- Linux
