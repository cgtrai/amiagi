# amiagi v0.2.0

Major release: multi-backend LLM support, external API models, per-role model assignment, skills system, input history, and session persistence.

## Highlights

### Multi-backend model architecture

- **`ChatCompletionClient` Protocol** — new structural interface (`typing.Protocol`) that all LLM backends must satisfy: `model` property, `chat()`, `ping()`, `list_models()`. Both `OllamaClient` and the new `OpenAIClient` implement the protocol, making them interchangeable inside `ChatService` and `SupervisorService`.
- **`OpenAIClient`** — drop-in replacement for `OllamaClient` supporting any OpenAI-compatible API endpoint: OpenAI, OpenRouter, Azure, vLLM, and other compatible services. Uses only `urllib.request` (no third-party HTTP libraries). Features built-in retry with exponential backoff, request timeout, API key masking for logs, and automatic token usage tracking.
- **Per-role model assignment** — Polluks (executor) and Kastor (supervisor) can be independently configured with different models from different backends. For example: Polluks on `gpt-5.3-codex` (OpenAI API) and Kastor on `cogito:14b` (local Ollama), or any other combination.

### Model selection wizard

- **Interactive two-phase wizard** at startup — presents a numbered list of all available models (local Ollama + external API), grouped by source, for both Polluks and Kastor roles.
- **Session restore** — on subsequent launches, the wizard auto-restores the previous model configuration if all models are still available. Skips the wizard entirely for a frictionless startup.
- **Commands during wizard** — all `/` commands (`/help`, `/cls`, etc.) work during the wizard without interrupting the selection flow.
- **Friendly prompts** — wizard messages are user-friendly with hints about available commands.

### Skills system

- **`SkillsLoader`** — dynamic loader that reads Markdown skill files from `skills/<role>/*.md` directories. Each `.md` file becomes a named skill.
- **Per-role skills** — Polluks and Kastor have separate skill directories. Skills are injected into the system prompt contextually.
- **API-model conditional** — skills are loaded **only** when an API model with large context window is active. Local Ollama models (14B/8B) skip skills to avoid context overflow.
- **Hot-reload** — `SkillsLoader.reload()` clears the cache for runtime skill updates.

### Token usage tracking

- **`UsageTracker`** — thread-safe, per-session tracker of API token consumption and costs. Records prompt/completion tokens per request, calculates cost from built-in pricing table.
- **`/api-usage` command** — detailed multi-line summary (model, request count, tokens, cumulative cost, last request details).
- **Status bar widget** — `#api_usage_bar` in Textual auto-shows when an API model is active, refreshed every 2 seconds with a compact one-liner (`☁ gpt-5.3-codex │ ⬆ 12.4k ⬇ 3.2k │ $0.23`).

### Input history

- **`InputHistory`** — readline-style history with up/down arrow navigation in the Textual input field. Persists commands to disk (one per line), deduplicates consecutive entries, auto-truncates at 500 entries. Preserves current input as draft during navigation.
- **Configuration**: `AMIAGI_INPUT_HISTORY_PATH` env var, default `./data/input_history.txt`.

### Session model persistence

- **`SessionModelConfig`** — persists Polluks/Kastor model assignments (name + source) to JSON between sessions. Automatically saved after wizard finalization and mid-session model changes.
- **Configuration**: `AMIAGI_MODEL_CONFIG_PATH` env var, default `./data/model_config.json`.

### Sponsor panel sanitization

- **`_sanitize_block_for_sponsor()`** — ensures the user-facing panel (Sponsor) never shows raw `tool_call` JSON blocks. Tool content is stripped using `strip_tool_call_blocks()` and `is_sponsor_readable()` checks. If nothing human-readable remains, content is redirected to `executor_log`. Full (unsanitized) version always preserved in technical logs.
- Fixed three leak paths where formatted tool_call blocks could reach the Sponsor panel: main response rendering, supervisor message routing, and supervision dialogue polling.

## New commands

| Command | Description |
|---------|-------------|
| `/kastor-model show` | Display current Kastor (supervisor) model |
| `/kastor-model chose <nr>` | Change Kastor model by index from the model list |
| `/api-usage` | Show detailed API token usage and costs |
| `/api-key verify` | Re-verify the OpenAI API key (masked output) |
| `/models current` | Now shows both Polluks and Kastor with their assigned models |

## New configuration

| Setting | Env Var | Default |
|---------|---------|---------|
| `openai_api_key` | `OPENAI_API_KEY` | `""` |
| `openai_base_url` | `OPENAI_BASE_URL` | `https://api.openai.com/v1` |
| `openai_request_timeout_seconds` | `OPENAI_REQUEST_TIMEOUT_SECONDS` | `120` |
| `skills_dir` | `AMIAGI_SKILLS_DIR` | `./skills` |
| `input_history_path` | `AMIAGI_INPUT_HISTORY_PATH` | `./data/input_history.txt` |
| `model_config_path` | `AMIAGI_MODEL_CONFIG_PATH` | `./data/model_config.json` |

## Changed

- `/models current` now shows all actors (Polluks + Kastor) with model names and sources, not just the executor model.
- Cold start (`--cs`) now additionally clears `SessionModelConfig` and input history file.
- Model is no longer auto-selected in `main.py` — the wizard handles initial model assignment.
- `OllamaClient` starts with `model=""` placeholder; the wizard or session restore sets the actual model.

## Project structure additions

```text
src/amiagi/
  application/
    model_client_protocol.py   # ChatCompletionClient Protocol
    skills_loader.py           # SkillsLoader + Skill dataclass
  infrastructure/
    openai_client.py           # OpenAIClient (frozen dataclass)
    usage_tracker.py           # UsageTracker + UsageSnapshot
    input_history.py           # InputHistory (readline-style)
    session_model_config.py    # SessionModelConfig persistence
skills/
  polluks/                     # Markdown skills for executor
  kastor/                      # Markdown skills for supervisor
```

## Compatibility

- Python: 3.10+
- OS: Linux
- No new runtime dependencies — `OpenAIClient` uses only `urllib.request`.

## Validation

- Full local test suite: **328 passed**.
- Test growth: 229 (v0.1.4) → 328 (v0.2.0), +99 new tests.

## Safety

- No permission policy expansion.
- No shell allowlist relaxation.
- API keys are masked in all log output (`sk-...abcd` format).
- External API calls use configurable timeout with minimum floor (5s).

Use only in isolated/sandboxed environments as described in `SECURITY.md`.
