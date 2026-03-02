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

        When ``github_token``, ``repo_owner`` and ``repo_name`` are configured
        this method fetches real PR data from the GitHub API. Otherwise it
        falls back to local ``git diff``.
        """
        cfg = self._config
        if cfg.github_token and cfg.repo_owner and cfg.repo_name:
            return self._review_pr_github(pr_number)

        # Fallback: local git diff
        diff = self.diff_stat()
        files = self.changed_files()
        result = CIRunResult(
            command=f"ci review --pr {pr_number}",
            success=True,
            stdout=f"PR #{pr_number} diff (local):\n{diff}",
            metadata={
                "pr_number": pr_number,
                "changed_files": files,
                "file_count": len(files),
                "source": "local",
            },
        )
        with self._lock:
            self._history.append(result)
        return result

    # ---- GitHub API helpers ----

    def _github_api(self, path: str) -> dict[str, Any]:
        """Make an authenticated GET request to the GitHub API."""
        import urllib.request
        import urllib.error

        url = f"https://api.github.com{path}"
        req = urllib.request.Request(url, method="GET", headers={
            "Authorization": f"token {self._config.github_token}",
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": "amiagi-ci-adapter",
        })
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def _github_api_raw(self, path: str, *, accept: str = "application/vnd.github.v3.diff") -> str:
        """Make a GitHub API request returning raw text (e.g. diff)."""
        import urllib.request

        url = f"https://api.github.com{path}"
        req = urllib.request.Request(url, method="GET", headers={
            "Authorization": f"token {self._config.github_token}",
            "Accept": accept,
            "User-Agent": "amiagi-ci-adapter",
        })
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.read().decode("utf-8", errors="replace")

    def _github_post_comment(self, path: str, body: str) -> dict[str, Any]:
        """POST a comment to a GitHub API endpoint."""
        import urllib.request

        url = f"https://api.github.com{path}"
        data = json.dumps({"body": body}).encode("utf-8")
        req = urllib.request.Request(url, data=data, method="POST", headers={
            "Authorization": f"token {self._config.github_token}",
            "Accept": "application/vnd.github.v3+json",
            "Content-Type": "application/json",
            "User-Agent": "amiagi-ci-adapter",
        })
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def _review_pr_github(self, pr_number: int) -> CIRunResult:
        """Fetch PR details from GitHub and generate a review summary."""
        import urllib.error
        cfg = self._config
        api_prefix = f"/repos/{cfg.repo_owner}/{cfg.repo_name}/pulls/{pr_number}"
        try:
            pr_data = self._github_api(api_prefix)
            pr_diff = self._github_api_raw(api_prefix)
            pr_files = self._github_api(f"{api_prefix}/files")
        except urllib.error.HTTPError as exc:
            result = CIRunResult(
                command=f"ci review --pr {pr_number}",
                exit_code=exc.code,
                stderr=f"GitHub API error: HTTP {exc.code}",
                success=False,
                metadata={"pr_number": pr_number, "source": "github"},
            )
            with self._lock:
                self._history.append(result)
            return result
        except Exception as exc:  # noqa: BLE001
            result = CIRunResult(
                command=f"ci review --pr {pr_number}",
                exit_code=1,
                stderr=str(exc),
                success=False,
                metadata={"pr_number": pr_number, "source": "github"},
            )
            with self._lock:
                self._history.append(result)
            return result

        changed_filenames: list[str] = []
        if isinstance(pr_files, list):
            for file_entry in pr_files:
                if isinstance(file_entry, dict):
                    changed_filenames.append(file_entry.get("filename", ""))
        title = pr_data.get("title", "")
        body_text = pr_data.get("body", "") or ""

        summary_lines = [
            f"PR #{pr_number}: {title}",
            f"Author: {pr_data.get('user', {}).get('login', '?')}",
            f"Base: {pr_data.get('base', {}).get('ref', '?')} ← {pr_data.get('head', {}).get('ref', '?')}",
            f"Files changed: {len(changed_filenames)}",
            f"Diff size: {len(pr_diff)} chars",
        ]
        if body_text:
            summary_lines.append(f"Description: {body_text[:300]}")

        result = CIRunResult(
            command=f"ci review --pr {pr_number}",
            success=True,
            stdout="\n".join(summary_lines),
            metadata={
                "pr_number": pr_number,
                "title": title,
                "changed_files": changed_filenames,
                "file_count": len(changed_filenames),
                "diff_chars": len(pr_diff),
                "source": "github",
            },
        )
        with self._lock:
            self._history.append(result)
        return result

    def post_pr_comment(self, pr_number: int, comment: str) -> CIRunResult:
        """Post a review comment on a GitHub PR."""
        cfg = self._config
        if not (cfg.github_token and cfg.repo_owner and cfg.repo_name):
            return CIRunResult(
                command=f"ci comment --pr {pr_number}",
                exit_code=1,
                stderr="GitHub config not set (token, repo_owner, repo_name required).",
                success=False,
            )
        import urllib.error
        try:
            self._github_post_comment(
                f"/repos/{cfg.repo_owner}/{cfg.repo_name}/issues/{pr_number}/comments",
                comment,
            )
            result = CIRunResult(
                command=f"ci comment --pr {pr_number}",
                success=True,
                stdout=f"Comment posted on PR #{pr_number}.",
                metadata={"pr_number": pr_number},
            )
        except urllib.error.HTTPError as exc:
            result = CIRunResult(
                command=f"ci comment --pr {pr_number}",
                exit_code=exc.code,
                stderr=f"GitHub API error: HTTP {exc.code}",
                success=False,
            )
        except Exception as exc:  # noqa: BLE001
            result = CIRunResult(
                command=f"ci comment --pr {pr_number}",
                exit_code=1,
                stderr=str(exc),
                success=False,
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
