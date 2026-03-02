# amiagi v0.2.0

Major release: multi-backend LLM support, external API models, per-role model assignment, skills system, and session persistence.

## ✨ Highlights

### Multi-backend model architecture
- New `ChatCompletionClient` protocol — structural interface all LLM backends must satisfy.
- `OpenAIClient` — drop-in replacement for `OllamaClient` supporting OpenAI, OpenRouter, Azure, vLLM, and any OpenAI-compatible endpoint. Zero third-party HTTP dependencies.
- **Per-role model assignment**: Polluks (executor) and Kastor (supervisor) can independently use local Ollama or external API models. Mix and match freely.

### Model selection wizard
- Interactive two-phase wizard at startup for Polluks and Kastor model selection.
- Displays all available models grouped by source (local Ollama + external API).
- Auto-restores previous session configuration on subsequent launches.
- Slash commands (`/help`, `/cls`) work during the wizard.

### Skills system
- Dynamic `SkillsLoader` reads Markdown skill files from `skills/<role>/*.md`.
- Skills injected into system prompt only for API models with large context windows.
- Separate skill directories for Polluks and Kastor.

### Token usage tracking
- Thread-safe `UsageTracker` for API token consumption and cost monitoring.
- New `/api-usage` command with detailed usage summary.
- Live status bar widget in Textual UI.

### Input history & session persistence
- Readline-style up/down arrow input history (persistent across sessions).
- Model-to-role assignments persisted to JSON between sessions.
- Cold start (`--cs`) clears all session data including model config and history.

### Sponsor panel sanitization
- Raw `tool_call` JSON blocks are now filtered from the user-facing panel.
- Three leak paths fixed in supervisor message routing and dialogue polling.

## 🆕 New commands

- `/kastor-model show` — display current Kastor model
- `/kastor-model chose <nr>` — change Kastor model by index
- `/api-usage` — show API token usage and costs
- `/api-key verify` — re-verify OpenAI API key
- `/models current` — now shows both Polluks and Kastor models

## 🧪 Validation

- Full local test suite: **328 passed** (was 229 in v0.1.4, +99 new tests).

## 🔒 Safety

- No permission scope expansion.
- No shell allowlist relaxation.
- API keys masked in all log output.
- No new runtime dependencies.

## 📦 Compatibility

- Python 3.10+
- Linux
