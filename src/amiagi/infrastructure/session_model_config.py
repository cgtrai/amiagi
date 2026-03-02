"""Persist and restore LLM model-to-role assignments between sessions.

Stores a simple JSON file so the wizard can be skipped on restart.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

_DEFAULT_PATH = Path("./data/model_config.json")


@dataclass
class SessionModelConfig:
    """Snapshot of which model is assigned to which role."""

    polluks_model: str = ""
    polluks_source: str = "ollama"  # "ollama" | "openai"
    kastor_model: str = ""
    kastor_source: str = "ollama"

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def save(self, path: Path = _DEFAULT_PATH) -> None:
        """Write config to *path* as JSON."""
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            data = {
                "polluks_model": self.polluks_model,
                "polluks_source": self.polluks_source,
                "kastor_model": self.kastor_model,
                "kastor_source": self.kastor_source,
            }
            path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        except Exception:
            pass

    @staticmethod
    def load(path: Path = _DEFAULT_PATH) -> SessionModelConfig | None:
        """Load config from *path*.  Returns *None* if missing or corrupt."""
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return SessionModelConfig(
                polluks_model=data.get("polluks_model", ""),
                polluks_source=data.get("polluks_source", "ollama"),
                kastor_model=data.get("kastor_model", ""),
                kastor_source=data.get("kastor_source", "ollama"),
            )
        except Exception:
            return None

    @staticmethod
    def clear(path: Path = _DEFAULT_PATH) -> None:
        """Delete the config file if it exists."""
        try:
            if path.exists():
                path.unlink()
        except Exception:
            pass
