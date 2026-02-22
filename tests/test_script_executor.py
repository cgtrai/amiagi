from __future__ import annotations

from pathlib import Path

from amiagi.infrastructure.script_executor import ScriptExecutor


def test_script_executor_runs_python_script(tmp_path: Path) -> None:
    script_path = tmp_path / "hello.py"
    script_path.write_text("print('ok')\n", encoding="utf-8")

    executor = ScriptExecutor()
    result = executor.execute_python(script_path, args=[])

    assert result.exit_code == 0
    assert "ok" in result.stdout


def test_script_executor_runs_shell_command() -> None:
    executor = ScriptExecutor()
    result = executor.execute_shell("echo shell-ok")

    assert result.exit_code == 0
    assert "shell-ok" in result.stdout
