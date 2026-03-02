"""Tests for CIAdapter — GitHub API integration (mocked), review_pr fallback."""

from __future__ import annotations

import json
from typing import Any

from amiagi.infrastructure.ci_adapter import CIAdapter, CIConfig, CIRunResult


# ====================================================================
# Local fallback (no token)
# ====================================================================


def test_review_pr_local_fallback() -> None:
    adapter = CIAdapter(CIConfig())
    result = adapter.review_pr(42)
    assert result.success is True
    assert result.metadata.get("source") == "local"
    assert result.metadata.get("pr_number") == 42


def test_review_pr_local_has_files() -> None:
    adapter = CIAdapter()
    result = adapter.review_pr(1)
    assert "changed_files" in result.metadata


# ====================================================================
# GitHub API helpers (unit tests with stubs)
# ====================================================================


class StubCIAdapter(CIAdapter):
    """CIAdapter subclass that stubs out HTTP calls for testing."""

    def __init__(self, config: CIConfig) -> None:
        super().__init__(config)
        self._api_responses: dict[str, Any] = {}
        self._api_raw_responses: dict[str, str] = {}
        self._posted_comments: list[tuple[str, str]] = []

    def set_api_response(self, path: str, data: Any) -> None:
        self._api_responses[path] = data

    def set_raw_response(self, path: str, text: str) -> None:
        self._api_raw_responses[path] = text

    def _github_api(self, path: str) -> dict[str, Any]:
        if path in self._api_responses:
            resp = self._api_responses[path]
            if isinstance(resp, list):
                return resp  # type: ignore[return-value]
            return resp
        raise RuntimeError(f"No stub for {path}")

    def _github_api_raw(self, path: str, *, accept: str = "") -> str:
        return self._api_raw_responses.get(path, "")

    def _github_post_comment(self, path: str, body: str) -> dict[str, Any]:
        self._posted_comments.append((path, body))
        return {"id": 1}


def _make_config() -> CIConfig:
    return CIConfig(
        github_token="ghp_faketoken",
        repo_owner="testowner",
        repo_name="testrepo",
    )


def test_review_pr_github_api() -> None:
    cfg = _make_config()
    adapter = StubCIAdapter(cfg)
    prefix = "/repos/testowner/testrepo/pulls/10"

    adapter.set_api_response(prefix, {
        "title": "Add feature X",
        "user": {"login": "dev1"},
        "base": {"ref": "main"},
        "head": {"ref": "feature-x"},
        "body": "This adds feature X.",
    })
    adapter.set_raw_response(prefix, "diff --git a/file.py b/file.py\n+new line\n")
    adapter.set_api_response(f"{prefix}/files", [
        {"filename": "file.py"},
        {"filename": "tests/test_file.py"},
    ])

    result = adapter.review_pr(10)
    assert result.success is True
    assert result.metadata["source"] == "github"
    assert result.metadata["pr_number"] == 10
    assert result.metadata["file_count"] == 2
    assert "Add feature X" in result.stdout


def test_review_pr_github_error() -> None:
    cfg = _make_config()
    adapter = StubCIAdapter(cfg)
    # No stub set → will raise RuntimeError
    result = adapter.review_pr(99)
    assert result.success is False
    assert result.metadata["source"] == "github"


def test_post_pr_comment() -> None:
    cfg = _make_config()
    adapter = StubCIAdapter(cfg)
    result = adapter.post_pr_comment(5, "LGTM!")
    assert result.success is True
    assert len(adapter._posted_comments) == 1
    assert adapter._posted_comments[0][1] == "LGTM!"


def test_post_pr_comment_no_config() -> None:
    adapter = CIAdapter()
    result = adapter.post_pr_comment(5, "Should fail")
    assert result.success is False
    assert "config" in result.stderr.lower() or "token" in result.stderr.lower()


# ====================================================================
# Existing methods still work
# ====================================================================


def test_run_benchmark() -> None:
    adapter = CIAdapter()
    result = adapter.run_benchmark("smoke")
    assert result.success is True
    assert "smoke" in result.stdout


def test_run_eval_suite() -> None:
    adapter = CIAdapter()
    result = adapter.run_eval_suite("perf")
    assert result.success is True


def test_get_report_empty() -> None:
    adapter = CIAdapter()
    result = adapter.get_report()
    assert result.success is True  # 0 failed → success
    assert result.metadata["total"] == 0


def test_history_and_clear() -> None:
    adapter = CIAdapter()
    adapter.run_benchmark("a")
    adapter.run_benchmark("b")
    assert len(adapter.history()) == 2
    adapter.clear_history()
    assert len(adapter.history()) == 0
