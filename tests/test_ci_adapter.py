"""Tests for CIAdapter (Phase 10)."""

from __future__ import annotations

import pytest

from amiagi.infrastructure.ci_adapter import CIAdapter, CIConfig, CIRunResult


class TestCIConfig:
    def test_roundtrip(self) -> None:
        cfg = CIConfig(
            github_token="tok",
            repo_owner="owner",
            repo_name="repo",
            default_branch="develop",
        )
        d = cfg.to_dict()
        cfg2 = CIConfig.from_dict(d)
        assert cfg2.repo_owner == "owner"
        assert cfg2.default_branch == "develop"
        assert d["has_token"] is True

    def test_defaults(self) -> None:
        cfg = CIConfig()
        assert cfg.github_token == ""
        assert cfg.default_branch == "main"


class TestCIRunResult:
    def test_to_dict_truncates(self) -> None:
        r = CIRunResult(command="cmd", stdout="x" * 5000)
        d = r.to_dict()
        assert len(d["stdout"]) == 2000


class TestCIAdapter:
    def test_review_pr(self) -> None:
        adapter = CIAdapter()
        result = adapter.review_pr(42)
        assert result.success is True
        assert "42" in result.stdout
        assert result.metadata["pr_number"] == 42

    def test_run_benchmark(self) -> None:
        adapter = CIAdapter()
        result = adapter.run_benchmark("code_gen")
        assert result.success is True
        assert result.metadata["suite"] == "code_gen"

    def test_history(self) -> None:
        adapter = CIAdapter()
        adapter.review_pr(1)
        adapter.run_benchmark("s")
        h = adapter.history()
        assert len(h) == 2
        assert h[0].command.startswith("ci test")  # newest first

    def test_clear_history(self) -> None:
        adapter = CIAdapter()
        adapter.review_pr(1)
        adapter.clear_history()
        assert len(adapter.history()) == 0

    def test_config_property(self) -> None:
        adapter = CIAdapter()
        new_cfg = CIConfig(repo_owner="test")
        adapter.config = new_cfg
        assert adapter.config.repo_owner == "test"

    def test_current_branch(self) -> None:
        # May return empty if not in a git repo, but should not raise
        adapter = CIAdapter()
        branch = adapter.current_branch()
        assert isinstance(branch, str)

    def test_to_dict(self) -> None:
        adapter = CIAdapter()
        d = adapter.to_dict()
        assert "config" in d
        assert "history_count" in d

    def test_run_eval_suite(self) -> None:
        adapter = CIAdapter()
        result = adapter.run_eval_suite("code_quality")
        assert result.success is True
        assert "eval_suite" in result.command
        assert result.metadata["suite"] == "code_quality"

    def test_get_report_empty(self) -> None:
        adapter = CIAdapter()
        report = adapter.get_report()
        assert report.success is True
        assert report.metadata["total"] == 0
        assert report.metadata["passed"] == 0

    def test_get_report_with_history(self) -> None:
        adapter = CIAdapter()
        adapter.review_pr(1)
        adapter.run_benchmark("suite1")
        report = adapter.get_report()
        assert report.metadata["total"] == 2
        assert report.metadata["passed"] == 2
        assert "2 runs" in report.stdout

    def test_get_report_with_failures(self) -> None:
        adapter = CIAdapter()
        adapter._history.append(CIRunResult(command="fail_cmd", success=False, exit_code=1))
        adapter._history.append(CIRunResult(command="ok_cmd", success=True, exit_code=0))
        report = adapter.get_report()
        assert report.metadata["failed"] == 1
        assert report.success is False
