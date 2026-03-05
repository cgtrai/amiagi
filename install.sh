#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────
#  amiagi — installation script
#  Checks prerequisites, creates virtualenv, installs deps,
#  configures .env, and optionally pulls Ollama models.
# ──────────────────────────────────────────────────────────────
set -euo pipefail

# ── colours (disabled when not a terminal) ────────────────────
if [[ -t 1 ]]; then
  GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'
  CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'
else
  GREEN=''; YELLOW=''; RED=''; CYAN=''; BOLD=''; NC=''
fi

info()  { printf "${GREEN}[✓]${NC} %s\n" "$*"; }
warn()  { printf "${YELLOW}[!]${NC} %s\n" "$*"; }
error() { printf "${RED}[✗]${NC} %s\n" "$*"; }
step()  { printf "\n${CYAN}${BOLD}── %s${NC}\n" "$*"; }

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="${ROOT_DIR}/.venv"
MIN_PYTHON="3.10"
REQUIRED_PYTHON="python3"

cd "${ROOT_DIR}"

# ── banner ────────────────────────────────────────────────────
printf "${BOLD}"
cat << 'BANNER'

   ╔══════════════════════════════════════╗
   ║        amiagi — installer            ║
   ║   LLM Agent Orchestration Framework  ║
   ╚══════════════════════════════════════╝

BANNER
printf "${NC}"

# ══════════════════════════════════════════════════════════════
#  1. Prerequisites
# ══════════════════════════════════════════════════════════════
step "1/6  Checking prerequisites"

# --- Python ---
if ! command -v "${REQUIRED_PYTHON}" &>/dev/null; then
  error "Python 3 not found. Install Python ${MIN_PYTHON}+ first."
  exit 1
fi

PYTHON_VER=$("${REQUIRED_PYTHON}" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
PYTHON_MAJOR=$("${REQUIRED_PYTHON}" -c "import sys; print(sys.version_info.major)")
PYTHON_MINOR=$("${REQUIRED_PYTHON}" -c "import sys; print(sys.version_info.minor)")

if (( PYTHON_MAJOR < 3 )) || (( PYTHON_MAJOR == 3 && PYTHON_MINOR < 10 )); then
  error "Python ${PYTHON_VER} detected — minimum ${MIN_PYTHON} required."
  exit 1
fi
info "Python ${PYTHON_VER}"

# --- venv module ---
if ! "${REQUIRED_PYTHON}" -c "import venv" &>/dev/null; then
  error "Python venv module not available. Install python3-venv (e.g. sudo apt install python3-venv)."
  exit 1
fi
info "venv module available"

# --- Ollama (optional) ---
if command -v ollama &>/dev/null; then
  OLLAMA_FOUND=true
  info "Ollama found: $(which ollama)"
else
  OLLAMA_FOUND=false
  warn "Ollama not found — you can install it later: curl -fsSL https://ollama.com/install.sh | sh"
fi

# --- GPU (informational) ---
if command -v nvidia-smi &>/dev/null; then
  GPU_MEM=$(nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits 2>/dev/null | head -1 | tr -d ' ')
  if [[ -n "${GPU_MEM}" ]]; then
    GPU_GB=$(( GPU_MEM / 1024 ))
    if (( GPU_GB >= 24 )); then
      info "NVIDIA GPU: ${GPU_GB} GB VRAM"
    else
      warn "NVIDIA GPU: ${GPU_GB} GB VRAM (24 GB+ recommended for 14B models)"
    fi
  fi
else
  warn "nvidia-smi not found — GPU detection skipped"
fi

# --- PostgreSQL (required for web interface) ---
if command -v psql &>/dev/null; then
  PG_VER=$(psql --version 2>/dev/null | grep -oP '\d+' | head -1)
  info "PostgreSQL client found (v${PG_VER})"
else
  warn "PostgreSQL client (psql) not found — required for web interface (--ui web)"
  warn "  Install: sudo apt install postgresql postgresql-client"
  warn "  Then create DB: sudo -u postgres createdb amiagi"
fi

# ══════════════════════════════════════════════════════════════
#  2. Virtual environment
# ══════════════════════════════════════════════════════════════
step "2/6  Setting up virtual environment"

if [[ -d "${VENV_DIR}" ]]; then
  info "Existing .venv found — reusing"
else
  "${REQUIRED_PYTHON}" -m venv "${VENV_DIR}"
  info "Created .venv"
fi

# shellcheck disable=SC1091
source "${VENV_DIR}/bin/activate"
info "Activated .venv ($(python --version))"

# ══════════════════════════════════════════════════════════════
#  3. Dependencies
# ══════════════════════════════════════════════════════════════
step "3/6  Installing dependencies"

python -m pip install --upgrade pip --quiet
python -m pip install -r requirements.txt --quiet
python -m pip install -e . --quiet
info "Runtime dependencies installed"

# --- web interface deps (optional, ask) ---
read -rp "$(printf "${YELLOW}[?]${NC} Install web interface dependencies (PostgreSQL, Starlette)? [Y/n] ")" WEB_CHOICE
WEB_CHOICE="${WEB_CHOICE:-Y}"
if [[ "${WEB_CHOICE}" =~ ^[Yy] ]]; then
  python -m pip install -e ".[web]" --quiet
  info "Web interface dependencies installed"
else
  info "Skipped web dependencies (install later: pip install -e '.[web]')"
fi

# --- dev deps (optional, ask) ---
if [[ -f requirements-dev.txt ]]; then
  read -rp "$(printf "${YELLOW}[?]${NC} Install development dependencies (pytest)? [Y/n] ")" DEV_CHOICE
  DEV_CHOICE="${DEV_CHOICE:-Y}"
  if [[ "${DEV_CHOICE}" =~ ^[Yy] ]]; then
    python -m pip install -r requirements-dev.txt --quiet
    info "Development dependencies installed"
  else
    info "Skipped dev dependencies"
  fi
fi

# ══════════════════════════════════════════════════════════════
#  4. Environment configuration (.env)
# ══════════════════════════════════════════════════════════════
step "4/6  Environment configuration"

if [[ -f .env ]]; then
  info ".env already exists — keeping current configuration"
else
  if [[ -f .env.example ]]; then
    cp .env.example .env
    info "Created .env from .env.example"
  else
    warn ".env.example not found — skipping .env creation"
  fi
fi

# --- required directories ---
for DIR in logs data data/sandboxes data/shared_workspace data/teams data/workflows data/workflow_checkpoints config; do
  mkdir -p "${DIR}"
done
info "Data directories verified"

# ══════════════════════════════════════════════════════════════
#  5. Ollama models (optional)
# ══════════════════════════════════════════════════════════════
step "5/6  Ollama models"

EXECUTOR_MODEL="hf.co/TeichAI/Qwen3-14B-Claude-4.5-Opus-High-Reasoning-Distill-GGUF:Q4_K_M"
SUPERVISOR_MODEL="cogito:14b"

if [[ "${OLLAMA_FOUND}" == "true" ]]; then
  # Check if Ollama server is reachable
  if curl -sf http://127.0.0.1:11434/api/tags &>/dev/null; then
    OLLAMA_RUNNING=true
    info "Ollama server is running"
  else
    OLLAMA_RUNNING=false
    warn "Ollama server not running — start it with: ollama serve"
  fi

  if [[ "${OLLAMA_RUNNING}" == "true" ]]; then
    EXISTING_MODELS=$(ollama list 2>/dev/null | tail -n +2 | awk '{print $1}' || true)

    # Check executor model
    if echo "${EXISTING_MODELS}" | grep -qF "$(echo "${EXECUTOR_MODEL}" | cut -d: -f1)"; then
      info "Executor model already pulled"
    else
      read -rp "$(printf "${YELLOW}[?]${NC} Pull executor model (${EXECUTOR_MODEL})? [Y/n] ")" PULL_EXEC
      PULL_EXEC="${PULL_EXEC:-Y}"
      if [[ "${PULL_EXEC}" =~ ^[Yy] ]]; then
        ollama pull "${EXECUTOR_MODEL}"
        info "Executor model pulled"
      else
        info "Skipped executor model pull"
      fi
    fi

    # Check supervisor model
    if echo "${EXISTING_MODELS}" | grep -qF "$(echo "${SUPERVISOR_MODEL}" | cut -d: -f1)"; then
      info "Supervisor model already pulled"
    else
      read -rp "$(printf "${YELLOW}[?]${NC} Pull supervisor model (${SUPERVISOR_MODEL})? [Y/n] ")" PULL_SUP
      PULL_SUP="${PULL_SUP:-Y}"
      if [[ "${PULL_SUP}" =~ ^[Yy] ]]; then
        ollama pull "${SUPERVISOR_MODEL}"
        info "Supervisor model pulled"
      else
        info "Skipped supervisor model pull"
      fi
    fi
  else
    warn "Skipping model pull — start Ollama first, then run: ollama pull ${EXECUTOR_MODEL}"
  fi
else
  warn "Ollama not installed — model pull skipped"
fi

# ══════════════════════════════════════════════════════════════
#  6. Verification
# ══════════════════════════════════════════════════════════════
step "6/6  Verification"

# Quick import test
if python -c "import amiagi; print(f'amiagi {amiagi.__version__}')" 2>/dev/null; then
  info "Package import OK"
else
  error "Package import failed — check installation"
  exit 1
fi

# CLI entry point
if command -v amiagi &>/dev/null; then
  info "CLI entry point 'amiagi' available"
else
  warn "CLI entry point not found — try: pip install -e ."
fi

# Test suite (quick check)
if command -v pytest &>/dev/null; then
  TEST_COUNT=$(python -m pytest tests/ --collect-only -q 2>/dev/null | tail -1 | grep -oP '\d+(?= test)' || echo "?")
  info "Test suite: ${TEST_COUNT} tests collected"
else
  info "pytest not installed — run: pip install -r requirements-dev.txt"
fi

# ══════════════════════════════════════════════════════════════
#  Done
# ══════════════════════════════════════════════════════════════
printf "\n${GREEN}${BOLD}"
cat << 'DONE'
  ╔══════════════════════════════════════════╗
  ║   Installation complete!                 ║
  ╚══════════════════════════════════════════╝
DONE
printf "${NC}\n"

printf "  ${BOLD}Quick start:${NC}\n"
printf "    source .venv/bin/activate\n"
printf "    amiagi\n\n"
printf "  ${BOLD}With options:${NC}\n"
printf "    amiagi --cold_start       # fresh session\n"
printf "    amiagi --auto             # autonomous mode\n"
printf "    amiagi --lang en          # English interface\n\n"
printf "  ${BOLD}Run tests:${NC}\n"
printf "    pytest\n\n"
printf "  ${BOLD}Documentation:${NC}\n"
printf "    README.md / README.pl.md\n"
printf "    SECURITY.md (read before use!)\n\n"
