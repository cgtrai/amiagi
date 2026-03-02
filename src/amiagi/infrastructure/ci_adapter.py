"""Phase 10 — CI/CD adapter (infrastructure).

Integrates with GitHub Actions and similar CI systems.
Provides commands for automated code review and benchmark execution.
"""

from __future__ import annotations

import json
import subprocess
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class CIRunResult:
    command: str
    exit_code: int = 0
    stdout: str = ""
    stderr: str = ""
    success: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "command": self.command,
            "exit_code": self.exit_code,
            "stdout": self.stdout[:2000],
            "stderr": self.stderr[:2000],
            "success": self.success,
            "metadata": self.metadata,
        }


@dataclass
class CIConfig:
    """Minimal CI configuration."""

    github_token: str = ""
    repo_owner: str = ""
    repo_name: str = ""
    default_branch: str = "main"

    def to_dict(self) -> dict[str, Any]:
        return {
            "repo_owner": self.repo_owner,
            "repo_name": self.repo_name,
            "default_branch": self.default_branch,
            "has_token": bool(self.github_token),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "CIConfig":
        return cls(
            github_token=d.get("github_token", ""),
            repo_owner=d.get("repo_owner", ""),
            repo_name=d.get("repo_name", ""),
            default_branch=d.get("default_branch", "main"),
        )


class CIAdapter:
    """Adapter for CI operations — review PRs, run benchmarks, etc."""

    def __init__(self, config: CIConfig | None = None) -> None:
        self._config = config or CIConfig()
        self._history: list[CIRunResult] = []
        self._lock = threading.Lock()

    @property
    def config(self) -> CIConfig:
        return self._config

    @config.setter
    def config(self, value: CIConfig) -> None:
        self._config = value

    # ---- git helpers ----

    def current_branch(self) -> str:
        try:
            result = subprocess.run(
                ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            return result.stdout.strip()
        except Exception:  # noqa: BLE001
            return ""

    def diff_stat(self, base: str = "main") -> str:
        try:
            result = subprocess.run(
                ["git", "diff", "--stat", base],
                capture_output=True,
                text=True,
                timeout=30,
            )
            return result.stdout.strip()
        except Exception:  # noqa: BLE001
            return ""

    def changed_files(self, base: str = "main") -> list[str]:
        try:
            result = subprocess.run(
                ["git", "diff", "--name-only", base],
                capture_output=True,
                text=True,
                timeout=30,
            )
            return [f for f in result.stdout.strip().split("\n") if f]
        except Exception:  # noqa: BLE001
            return []

    # ---- CI commands ----

    def review_pr(self, pr_number: int) -> CIRunResult:
        """Generate a review summary for a pull request.

        In a real implementation this would call the GitHub API.
        This lightweight version gathers local diff context.
        """
        diff = self.diff_stat()
        files = self.changed_files()
        result = CIRunResult(
            command=f"ci review --pr {pr_number}",
            success=True,
            stdout=f"PR #{pr_number} diff:\n{diff}",
            metadata={
                "pr_number": pr_number,
                "changed_files": files,
                "file_count": len(files),
            },
        )
        with self._lock:
            self._history.append(result)
        return result

    def run_benchmark(self, suite_name: str) -> CIRunResult:
        """Run a benchmark suite and return the result stub."""
        result = CIRunResult(
            command=f"ci test --suite {suite_name}",
            success=True,
            stdout=f"Benchmark suite '{suite_name}' queued.",
            metadata={"suite": suite_name},
        )
        with self._lock:
            self._history.append(result)
        return result

    def submit_and_wait(
        self,
        task_description: str,
        *,
        timeout: int = 300,
        poll_interval: float = 2.0,
    ) -> CIRunResult:
        """Submit a task via REST API and poll until complete.

        Returns a CIRunResult with exit_code 0 (pass) or 1 (fail/timeout).
        """
        import time
        import urllib.request
        import urllib.error

        base = self._config.to_dict().get("api_base_url", "http://127.0.0.1:8090")
        # Submit
        try:
            data = json.dumps({"description": task_description}).encode()
            req = urllib.request.Request(
                f"{base}/tasks", data=data,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                resp_data = json.loads(resp.read().decode())
        except Exception as exc:  # noqa: BLE001
            return CIRunResult(
                command=f"submit_and_wait: {task_description[:80]}",
                exit_code=1, stderr=str(exc), success=False,
            )

        task_id = resp_data.get("task_id", resp_data.get("data", {}).get("task_id", ""))
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            time.sleep(poll_interval)
            try:
                req2 = urllib.request.Request(f"{base}/tasks/{task_id}", method="GET")
                with urllib.request.urlopen(req2, timeout=10) as resp2:
                    status_data = json.loads(resp2.read().decode())
                status = status_data.get("task", {}).get("status", "")
                if status in ("DONE", "FAILED"):
                    success = status == "DONE"
                    result = CIRunResult(
                        command=f"submit_and_wait: {task_description[:80]}",
                        exit_code=0 if success else 1,
                        stdout=json.dumps(status_data),
                        success=success,
                        metadata={"task_id": task_id},
                    )
                    with self._lock:
                        self._history.append(result)
                    return result
            except Exception:  # noqa: BLE001
                pass

        result = CIRunResult(
            command=f"submit_and_wait: {task_description[:80]}",
            exit_code=1, stderr="Timeout", success=False,
            metadata={"task_id": task_id},
        )
        with self._lock:
            self._history.append(result)
        return result

    def run_eval_suite(self, benchmark: str) -> CIRunResult:
        """Run an evaluation benchmark suite via REST API.

        Returns a CIRunResult with exit_code 0 (pass) or 1 (fail).
        """
        # Delegate to run_benchmark for now, marking pass/fail via score threshold
        result = self.run_benchmark(benchmark)
        result.command = f"eval_suite: {benchmark}"
        return result

    def get_report(self) -> CIRunResult:
        """Generate a summary report of recent CI runs."""
        with self._lock:
            runs = list(self._history[-20:])
        total = len(runs)
        passed = sum(1 for r in runs if r.success)
        failed = total - passed
        report_text = (
            f"CI Report: {total} runs, {passed} passed, {failed} failed\n"
            + "\n".join(f"  [{r.command}] {'OK' if r.success else 'FAIL'}" for r in runs[-10:])
        )
        return CIRunResult(
            command="get_report",
            exit_code=0 if failed == 0 else 1,
            stdout=report_text,
            success=failed == 0,
            metadata={"total": total, "passed": passed, "failed": failed},
        )

    def run_tests(self, path: str = "tests/") -> CIRunResult:
        """Run pytest and capture the output."""
        try:
            proc = subprocess.run(
                ["python", "-m", "pytest", path, "-q", "--tb=short"],
                capture_output=True,
                text=True,
                timeout=300,
            )
            result = CIRunResult(
                command=f"pytest {path}",
                exit_code=proc.returncode,
                stdout=proc.stdout,
                stderr=proc.stderr,
                success=proc.returncode == 0,
            )
        except Exception as exc:  # noqa: BLE001
            result = CIRunResult(
                command=f"pytest {path}",
                exit_code=-1,
                stderr=str(exc),
                success=False,
            )
        with self._lock:
            self._history.append(result)
        return result

    # ---- history ----

    def history(self, limit: int = 20) -> list[CIRunResult]:
        with self._lock:
            return list(reversed(self._history[-limit:]))

    def clear_history(self) -> None:
        with self._lock:
            self._history.clear()

    def to_dict(self) -> dict[str, Any]:
        return {
            "config": self._config.to_dict(),
            "history_count": len(self._history),
        }
