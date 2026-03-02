# amiagi

[![CI](https://github.com/cgtrai/amiagi/actions/workflows/ci.yml/badge.svg)](https://github.com/cgtrai/amiagi/actions/workflows/ci.yml)

A local, CLI-first framework for evaluating LLM autonomy in controlled environments.

`amiagi` focuses on reproducible autonomy experiments: tool-calling, permission gating, model I/O audit logs, session continuity, and supervisor-style runtime checks. Supports both local Ollama models and external API backends (OpenAI, OpenRouter, Azure, vLLM) with per-role model assignment.

## Safety Disclaimer (Read First)

This project can execute model-generated code and shell commands. Treat it as **high risk**.

- Use with the **highest caution**.
- Run only inside an **isolated virtual machine** (or equivalent sandboxed environment).
- Do not connect it to production systems, sensitive data, or privileged credentials.
- You are fully responsible for runtime isolation, network policy, and access control.

See [SECURITY.md](SECURITY.md) for mandatory safety recommendations.

## License and Usage Scope

This repository is released for:

- **Non-commercial use only**
- **Scientific and research use only**

Commercial usage is not permitted.

See [LICENSE](LICENSE) for full terms.

## Key Capabilities

### Model backends

- **Local LLM integration** with Ollama (any GGUF model)
- **External API support** via `OpenAIClient` — OpenAI, OpenRouter, Azure, vLLM, or any OpenAI-compatible endpoint
- **Per-role model assignment** — Polluks (executor) and Kastor (supervisor) can independently use local or API models
- **Interactive model selection wizard** at startup with session restore
- **Session persistence** — model-to-role assignments saved between sessions (`SessionModelConfig`)
- **Token usage tracking** for API models with real-time cost display (`UsageTracker`)

### Skills

- **Dynamic skill loading** — Markdown skill files from `skills/<role>/*.md` injected into system prompt
- **API-model conditional** — skills loaded only for large-context API models; local models skip to avoid context overflow
- **Per-role skill directories** — separate `skills/polluks/` and `skills/kastor/` directories

### Architecture & runtime

- Layered architecture (`domain`, `application`, `infrastructure`, `interfaces`)
- `ChatCompletionClient` protocol — structural interface all backends must satisfy
- Persistent memory in SQLite
- Full JSONL audit logs for:
  - model input/output/errors,
  - activity events and intents,
  - supervisor ↔ executor dialogue
- Permission-gated resource access (`disk.*`, `network.*`, `process.exec`)
- Controlled shell policy via allowlist
- Dynamic runtime behavior based on available VRAM
- Multi-actor communication protocol with addressed-block routing, unaddressed-turn reminders, and consultation rounds
- Deep tool-call resolution flow with iteration cap protection (`resolve_tool_calls`, max 15 steps)
- Tool name alias resolution (`file_read→read_file`, `dir_list→list_dir`) with per-tool correction tracking
- Adaptive supervisor watchdog with attempt caps/cooldown and plan-aware reactivation checks

### User experience

- **Readline-style input history** (up/down arrows) with persistent file-backed storage
- **Sponsor panel sanitization** — raw `tool_call` JSON is filtered from user-facing panel; technical details preserved in executor logs
- Runtime model switching from UI/CLI (`/models show`, `/models chose <nr>`, `/models current`)
- Kas tor model management (`/kastor-model show`, `/kastor-model chose <nr>`)
- API usage monitoring (`/api-usage`) and key verification (`/api-key verify`)
- Explicit multi-actor runtime visibility (Router, Polluks, Kastor, Terminal) in Textual status panel
- Directional supervision lanes in logs (`POLLUKS→KASTOR`, `KASTOR→ROUTER`) for clearer handoff tracing
- Interrupt-safe conversational mode in Textual (identity-aware reply + user decision follow-up)
- ASCII art landing page with randomized MOTD on startup (both CLI and Textual)
- Context-aware `/help` — shows only commands relevant to the active interface mode
- User message queue with position feedback when router cycle is busy

## Runtime Commands (CLI and Textual)

Model management commands:

- `/cls` — clears the main terminal screen
- `/cls all` — clears terminal screen and scrollback history
- `/models current` — shows both Polluks and Kastor with their assigned models and sources
- `/models show` — lists all available models (local Ollama + external API) with index numbers
- `/models chose <nr>` — switches Polluks (executor) model by index from `/models show`
- `/kastor-model show` — displays current Kastor (supervisor) model and source
- `/kastor-model chose <nr>` — switches Kastor model by index from the model list

API and usage commands:

- `/api-usage` — shows detailed API token usage, cost breakdown, and request count
- `/api-key verify` — re-verifies the OpenAI API key with masked output

Operational and diagnostics commands:

- `/queue-status` — shows model queue status and VRAM policy decision context
- `/capabilities [--network]` — checks tool/backend readiness (optionally includes network reachability)
- `/show-system-context [text]` — displays current system prompt/context used for model call
- `/goal-status` (alias: `/goal`) — shows goal/stage snapshot from `notes/main_plan.json`

Textual-focused actor/runtime commands:

- `/router-status` — shows actor states and runtime routing status
- `/idle-until <ISO8601|off>` — schedules/clears watchdog idle window

Notes:

- On startup, an interactive wizard guides model selection for both Polluks and Kastor.
- Previous model configuration is auto-restored if all models are still available.
- User-facing model output is sanitized: raw `tool_call`/JSON payloads are filtered from the Sponsor panel and preserved in technical logs.
- Input history (up/down arrows) persists across sessions.

## Current Runtime Behavior (Polluks/Kastor/Router)

- **Per-role backends**: Polluks and Kastor can use different models from different providers (Ollama, OpenAI, OpenRouter, etc.).
- **Skills injection**: when an API model is active, role-specific skills from `skills/<role>/` are injected into the system prompt.
- Textual interruptions are now decision-driven: after interrupt handling, runtime explicitly asks whether to continue, stop, or start a new task.
- Identity queries in interrupt mode are handled deterministically (Polluks identity response), avoiding accidental tool-flow drift.
- Auto-resume is blocked while identity decision is still pending, preventing unwanted continuation.
- Idle watchdog reactivation checks include actionable-plan context, not only passive turn counters.
- If tool-call resolution reaches iteration cap with unresolved calls, runtime emits explicit warning and marks router as stalled for visibility.
- Multi-actor communication protocol enforces addressed blocks (`[Sender -> Receiver]`), with automatic reminders for unaddressed turns and configurable consultation rounds.
- Supervisor `[Kastor -> Sponsor]` messages are routed to the user's main panel with sanitized content (no raw `tool_call` JSON).
- Unknown tool names are resolved via alias map; after max correction attempts, runtime forces a tool-creation plan.

## Project Structure

```text
src/amiagi/
  application/      # use-cases, orchestration, protocols
    model_client_protocol.py  # ChatCompletionClient Protocol
    skills_loader.py          # SkillsLoader + Skill dataclass
    communication_protocol.py # addressed-block routing, sanitization
  domain/           # domain models
  infrastructure/   # IO, storage, runtime integrations
    openai_client.py          # OpenAIClient (OpenAI-compatible API)
    usage_tracker.py          # UsageTracker + UsageSnapshot
    input_history.py          # readline-style input history
    session_model_config.py   # session model persistence
  interfaces/       # CLI and user interaction layer
tests/              # pytest suite (328 tests)
config/             # shell allowlist policy
skills/             # per-role Markdown skill files
  polluks/          # executor skills
  kastor/           # supervisor skills
data/               # local persistent DB, history, model config
logs/               # JSONL runtime and model logs
```

## Requirements

- Linux environment
- Python 3.10+
- Local Ollama server (`http://127.0.0.1:11434`)
- NVIDIA GPU with **minimum 24 GB VRAM**

## Install Ollama and Models

Install Ollama (Linux):

```bash
curl -fsSL https://ollama.com/install.sh | sh
```

Start local Ollama server:

```bash
ollama serve
```

Pull the models used by this project:

```bash
ollama pull hf.co/TeichAI/Qwen3-14B-Claude-4.5-Opus-High-Reasoning-Distill-GGUF:Q4_K_M
ollama pull cogito:14b
```

Recommended `.env` model settings:

```env
OLLAMA_MODEL=hf.co/TeichAI/Qwen3-14B-Claude-4.5-Opus-High-Reasoning-Distill-GGUF:Q4_K_M
AMIAGI_SUPERVISOR_MODEL=cogito:14b
```

## Installation

### Recommended for GitHub users (auto-create virtual environment)

```bash
bash scripts/setup_venv.sh
source .venv/bin/activate
```

### Optional (Conda environment with your own name)

```bash
source "$(conda info --base)/etc/profile.d/conda.sh"
conda create -n <your_env_name> python=3.10 -y
conda activate <your_env_name>
pip install --upgrade pip
pip install -r requirements.txt
pip install -e .
```

For development and tests:

```bash
pip install -r requirements-dev.txt
```

### Alternative (new local virtual environment)

```bash
python -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
pip install -e .
```

## Configure Environment

Copy `.env.example` to `.env` and adjust values if needed.

```bash
cp .env.example .env
```

### External API models (optional)

To use OpenAI-compatible API models, set these in your `.env`:

```env
OPENAI_API_KEY=sk-your-api-key-here
OPENAI_BASE_URL=https://api.openai.com/v1    # or OpenRouter, Azure, etc.
OPENAI_REQUEST_TIMEOUT_SECONDS=120
```

The model selection wizard at startup will offer both local Ollama and external API models.

### Skills directory (optional)

Custom skills can be added as Markdown files in `skills/<role>/`:

```env
AMIAGI_SKILLS_DIR=./skills
```

Skills are loaded only for API models with large context windows.

## Run

If you use the local `.venv`, activate it first:

```bash
source .venv/bin/activate
```

If you use Conda, activate your environment first:

```bash
source "$(conda info --base)/etc/profile.d/conda.sh"
conda activate <your_env_name>
```

Preferred CLI command:

```bash
amiagi
```

Backward-compatible command:

```bash
amiagi
```

Alternative module launch:

```bash
python -m main
```

Useful runtime modes:

```bash
python -m main --cold_start
python -m main --auto
python -m main --cold_start --auto
```

## Tests

```bash
pytest
```

## Continuous Integration

GitHub Actions workflow runs the full test suite on every push and pull request.

- Workflow file: `.github/workflows/ci.yml`
- Python versions: 3.10, 3.11, 3.12

## Notes on Naming

The package code namespace remains `amiagi` for compatibility with existing imports. The distribution and project identity are now `amiagi`.

## Contributing

Contribution guidelines are available in [CONTRIBUTING.md](CONTRIBUTING.md).

## Release Process

Pre-release checklist is available in [RELEASE_CHECKLIST.md](RELEASE_CHECKLIST.md).
Current unreleased changes: [RELEASE_NOTES_UNRELEASED.md](RELEASE_NOTES_UNRELEASED.md).
Latest release notes: [RELEASE_NOTES_v0.2.0.md](RELEASE_NOTES_v0.2.0.md).

## Polish Documentation

Polish documentation is available in [README.pl.md](README.pl.md).
