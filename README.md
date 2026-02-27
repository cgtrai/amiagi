# amiagi

[![CI](https://github.com/cgtrai/amiagi/actions/workflows/ci.yml/badge.svg)](https://github.com/cgtrai/amiagi/actions/workflows/ci.yml)

A local, CLI-first framework for evaluating LLM autonomy in controlled environments.

`amiagi` focuses on reproducible autonomy experiments: tool-calling, permission gating, model I/O audit logs, session continuity, and supervisor-style runtime checks.

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

- Local LLM integration with Ollama
- Layered architecture (`domain`, `application`, `infrastructure`, `interfaces`)
- Persistent memory in SQLite
- Full JSONL audit logs for:
  - model input/output/errors,
  - activity events and intents,
  - supervisor ↔ executor dialogue
- Permission-gated resource access (`disk.*`, `network.*`, `process.exec`)
- Controlled shell policy via allowlist
- Dynamic runtime behavior based on available VRAM
- Runtime model switching from UI/CLI (`/models show`, `/models chose <nr>`, `/models current`)
- Automatic executor model bootstrap (first model from local Ollama list)
- Cleaner end-user responses (tool-call payloads are kept in technical logs, user sees plain text)
- Optional Textual UI parity for model commands and onboarding hints

## Runtime Commands (CLI and Textual)

Model management commands:

- `/cls` — clears the main terminal screen
- `/cls all` — clears terminal screen and scrollback history
- `/models current` — shows currently active executor model
- `/models show` — lists models discovered in local Ollama with index numbers
- `/models chose <nr>` — switches executor model by index from `/models show`

Notes:

- On startup, runtime attempts to select a default executor model automatically (position `1/x` from Ollama list).
- If model list retrieval fails, runtime keeps current model and reports a warning.
- User-facing model output is normalized to readable text, while raw tool traces remain in JSONL/log panels.

## Project Structure

```text
src/amiagi/
  application/      # use-cases and orchestration
  domain/           # domain models
  infrastructure/   # IO, storage, runtime integrations
  interfaces/       # CLI and user interaction layer
tests/              # pytest suite
config/             # shell allowlist policy
data/               # local persistent DB files
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
Latest release notes: [RELEASE_NOTES_v0.1.3.md](RELEASE_NOTES_v0.1.3.md).

## Polish Documentation

Polish documentation is available in [README.pl.md](README.pl.md).
