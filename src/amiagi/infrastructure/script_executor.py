from __future__ import annotations

import shlex
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ScriptExecutionResult:
    command: list[str]
    exit_code: int
    stdout: str
    stderr: str


class ScriptExecutor:
    def execute_python(self, script_path: Path, args: list[str], timeout_seconds: int = 120) -> ScriptExecutionResult:
        command = [sys.executable, str(script_path), *args]
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
        return ScriptExecutionResult(
            command=command,
            exit_code=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
        )

    def execute_shell(self, command_text: str, timeout_seconds: int = 120) -> ScriptExecutionResult:
        command = shlex.split(command_text)
        if not command:
            raise ValueError("Empty shell command")
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
        return ScriptExecutionResult(
            command=command,
            exit_code=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
        )
