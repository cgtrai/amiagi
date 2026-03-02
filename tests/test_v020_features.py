"""Tests for v0.2.0 features: UsageTracker, OpenAIClient, SkillsLoader,
model_client_protocol, wizard commands, and config additions."""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, cast
from types import SimpleNamespace

import pytest

# --------------------------------------------------------------------------
# UsageTracker
# --------------------------------------------------------------------------
from amiagi.infrastructure.usage_tracker import UsageTracker, UsageSnapshot, _format_tokens


class TestFormatTokens:
    def test_below_thousand(self) -> None:
        assert _format_tokens(42) == "42"

    def test_thousands(self) -> None:
        assert _format_tokens(12_400) == "12.4k"

    def test_millions(self) -> None:
        assert _format_tokens(2_500_000) == "2.5M"


class TestUsageTracker:
    def test_initial_snapshot_is_zero(self) -> None:
        tracker = UsageTracker()
        snap = tracker.snapshot()
        assert snap.request_count == 0
        assert snap.total_prompt_tokens == 0
        assert snap.total_cost_usd == 0.0

    def test_record_single_request(self) -> None:
        tracker = UsageTracker()
        tracker.record("gpt-5-mini", prompt_tokens=1000, completion_tokens=500)
        snap = tracker.snapshot()
        assert snap.request_count == 1
        assert snap.total_prompt_tokens == 1000
        assert snap.total_completion_tokens == 500
        assert snap.model == "gpt-5-mini"
        # Cost: (1000 * 0.40 + 500 * 1.60) / 1_000_000 = 0.0012
        assert abs(snap.total_cost_usd - 0.0012) < 1e-8

    def test_record_accumulates(self) -> None:
        tracker = UsageTracker()
        tracker.record("gpt-5.3-codex", prompt_tokens=500, completion_tokens=200)
        tracker.record("gpt-5.3-codex", prompt_tokens=300, completion_tokens=100)
        snap = tracker.snapshot()
        assert snap.request_count == 2
        assert snap.total_prompt_tokens == 800
        assert snap.total_completion_tokens == 300
        # Last request values
        assert snap.last_request_prompt_tokens == 300
        assert snap.last_request_completion_tokens == 100

    def test_unknown_model_has_zero_cost(self) -> None:
        tracker = UsageTracker()
        tracker.record("unknown-model", prompt_tokens=1000, completion_tokens=500)
        snap = tracker.snapshot()
        assert snap.total_cost_usd == 0.0

    def test_format_status_line_empty_when_no_requests(self) -> None:
        tracker = UsageTracker()
        assert tracker.format_status_line() == ""

    def test_format_status_line_after_record(self) -> None:
        tracker = UsageTracker()
        tracker.record("gpt-5-mini", prompt_tokens=12_400, completion_tokens=3200)
        line = tracker.format_status_line()
        assert "gpt-5-mini" in line
        assert "⬆" in line
        assert "$" in line

    def test_format_detailed_empty_when_no_requests(self) -> None:
        tracker = UsageTracker()
        detailed = tracker.format_detailed()
        assert "Brak" in detailed

    def test_format_detailed_after_record(self) -> None:
        tracker = UsageTracker()
        tracker.record("gpt-5.3-codex", prompt_tokens=500, completion_tokens=200)
        detailed = tracker.format_detailed()
        assert "gpt-5.3-codex" in detailed
        assert "Zapytania:" in detailed
        assert "1" in detailed

    def test_thread_safety(self) -> None:
        tracker = UsageTracker()
        errors: list[Exception] = []

        def worker() -> None:
            try:
                for _ in range(100):
                    tracker.record("gpt-5-mini", prompt_tokens=10, completion_tokens=5)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        snap = tracker.snapshot()
        assert snap.request_count == 400
        assert snap.total_prompt_tokens == 4000
        assert snap.total_completion_tokens == 2000


# --------------------------------------------------------------------------
# model_client_protocol
# --------------------------------------------------------------------------
from amiagi.application.model_client_protocol import ChatCompletionClient


class TestModelClientProtocol:
    def test_ollama_client_satisfies_protocol(self) -> None:
        from amiagi.infrastructure.ollama_client import OllamaClient

        client = OllamaClient(base_url="http://localhost:11434", model="test")
        assert isinstance(client, ChatCompletionClient)

    def test_openai_client_satisfies_protocol(self) -> None:
        from amiagi.infrastructure.openai_client import OpenAIClient

        client = OpenAIClient(api_key="sk-test123", model="gpt-5-mini")
        assert isinstance(client, ChatCompletionClient)

    def test_plain_object_does_not_satisfy_protocol(self) -> None:
        class NotAClient:
            pass

        assert not isinstance(NotAClient(), ChatCompletionClient)


# --------------------------------------------------------------------------
# OpenAIClient (unit tests, no real API calls)
# --------------------------------------------------------------------------
from amiagi.infrastructure.openai_client import (
    OpenAIClient,
    OpenAIClientError,
    SUPPORTED_OPENAI_MODELS,
    mask_api_key,
)


class TestMaskApiKey:
    def test_normal_key(self) -> None:
        assert mask_api_key("sk-abc123456xyz") == "sk-...6xyz"

    def test_short_key(self) -> None:
        assert mask_api_key("short") == "***"

    def test_empty_key(self) -> None:
        assert mask_api_key("") == "***"


class TestOpenAIClient:
    def test_supported_models_list(self) -> None:
        assert "gpt-5.3-codex" in SUPPORTED_OPENAI_MODELS
        assert "gpt-5-mini" in SUPPORTED_OPENAI_MODELS

    def test_is_api_client_marker(self) -> None:
        client = OpenAIClient(api_key="sk-test", model="gpt-5-mini")
        assert client._is_api_client is True

    def test_frozen_dataclass(self) -> None:
        client = OpenAIClient(api_key="sk-test", model="gpt-5-mini")
        with pytest.raises(AttributeError):
            client.model = "other"  # type: ignore[misc]

    def test_replace_creates_new_instance(self) -> None:
        client = OpenAIClient(api_key="sk-test", model="gpt-5-mini")
        new_client = replace(client, model="gpt-5.3-codex")
        assert new_client.model == "gpt-5.3-codex"
        assert client.model == "gpt-5-mini"

    def test_ping_fails_without_real_server(self) -> None:
        client = OpenAIClient(
            api_key="sk-test",
            model="gpt-5-mini",
            base_url="http://127.0.0.1:1",  # no server here
            request_timeout_seconds=1,
        )
        assert client.ping() is False

    def test_list_models_returns_supported_models_on_error(self) -> None:
        client = OpenAIClient(
            api_key="sk-test",
            model="gpt-5-mini",
            base_url="http://127.0.0.1:1",
            request_timeout_seconds=1,
        )
        # When can't connect, list_models should return the supported list
        models = client.list_models()
        assert isinstance(models, list)

    def test_usage_tracker_integration(self) -> None:
        tracker = UsageTracker()
        client = OpenAIClient(
            api_key="sk-test",
            model="gpt-5-mini",
            usage_tracker=tracker,
        )
        assert client.usage_tracker is tracker


# --------------------------------------------------------------------------
# SkillsLoader
# --------------------------------------------------------------------------
from amiagi.application.skills_loader import SkillsLoader, Skill


class TestSkillsLoader:
    def test_load_for_role_empty_dir(self, tmp_path: Path) -> None:
        loader = SkillsLoader(skills_dir=tmp_path / "skills")
        skills = loader.load_for_role("polluks")
        assert skills == []

    def test_load_for_role_reads_md_files(self, tmp_path: Path) -> None:
        role_dir = tmp_path / "skills" / "polluks"
        role_dir.mkdir(parents=True)
        (role_dir / "web_research.md").write_text("# Web Research\nContent here.", encoding="utf-8")
        (role_dir / "python_dev.md").write_text("# Python Dev\nMore content.", encoding="utf-8")

        loader = SkillsLoader(skills_dir=tmp_path / "skills")
        skills = loader.load_for_role("polluks")
        assert len(skills) == 2
        names = [s.name for s in skills]
        assert "python_dev" in names
        assert "web_research" in names
        assert all(s.role == "polluks" for s in skills)

    def test_load_for_role_caches(self, tmp_path: Path) -> None:
        role_dir = tmp_path / "skills" / "kastor"
        role_dir.mkdir(parents=True)
        (role_dir / "review.md").write_text("Review skill", encoding="utf-8")

        loader = SkillsLoader(skills_dir=tmp_path / "skills")
        first = loader.load_for_role("kastor")
        second = loader.load_for_role("kastor")
        assert first is second

    def test_reload_clears_cache(self, tmp_path: Path) -> None:
        role_dir = tmp_path / "skills" / "kastor"
        role_dir.mkdir(parents=True)
        (role_dir / "review.md").write_text("V1", encoding="utf-8")

        loader = SkillsLoader(skills_dir=tmp_path / "skills")
        first = loader.load_for_role("kastor")
        assert first[0].content == "V1"

        (role_dir / "review.md").write_text("V2", encoding="utf-8")
        loader.reload()
        second = loader.load_for_role("kastor")
        assert second[0].content == "V2"

    def test_list_available(self, tmp_path: Path) -> None:
        kastor_dir = tmp_path / "skills" / "kastor"
        kastor_dir.mkdir(parents=True)
        (kastor_dir / "a.md").write_text("A", encoding="utf-8")
        polluks_dir = tmp_path / "skills" / "polluks"
        polluks_dir.mkdir(parents=True)
        (polluks_dir / "b.md").write_text("B", encoding="utf-8")
        (polluks_dir / "c.md").write_text("C", encoding="utf-8")

        loader = SkillsLoader(skills_dir=tmp_path / "skills")
        avail = loader.list_available()
        assert "kastor" in avail
        assert "polluks" in avail
        assert avail["kastor"] == ["a"]
        assert avail["polluks"] == ["b", "c"]

    def test_ignores_non_md_files(self, tmp_path: Path) -> None:
        role_dir = tmp_path / "skills" / "polluks"
        role_dir.mkdir(parents=True)
        (role_dir / "good.md").write_text("good", encoding="utf-8")
        (role_dir / "bad.txt").write_text("bad", encoding="utf-8")

        loader = SkillsLoader(skills_dir=tmp_path / "skills")
        skills = loader.load_for_role("polluks")
        assert len(skills) == 1
        assert skills[0].name == "good"


# --------------------------------------------------------------------------
# ChatService.is_api_model() and skills integration
# --------------------------------------------------------------------------
from amiagi.application.chat_service import ChatService


class TestChatServiceApiModel:
    def _make_service(self, *, is_api: bool = False, tmp_path: Path | None = None) -> ChatService:
        class _FakeClient:
            def __init__(self, *, api: bool = False) -> None:
                self.model = "test"
                self.base_url = "http://localhost"
                self._is_api_client = api

            def chat(self, messages, system_prompt, num_ctx=None):
                return "ok"

            def ping(self):
                return True

            def list_models(self):
                return ["test"]

        from amiagi.infrastructure.memory_repository import MemoryRepository
        db_path = (tmp_path or Path("/tmp")) / "test.db"
        repo = MemoryRepository(db_path)
        return ChatService(
            memory_repository=repo,
            ollama_client=_FakeClient(api=is_api),
        )

    def test_is_api_model_false_for_ollama(self, tmp_path: Path) -> None:
        svc = self._make_service(is_api=False, tmp_path=tmp_path)
        assert svc.is_api_model() is False

    def test_is_api_model_true_for_openai(self, tmp_path: Path) -> None:
        svc = self._make_service(is_api=True, tmp_path=tmp_path)
        assert svc.is_api_model() is True

    def test_model_client_property(self, tmp_path: Path) -> None:
        svc = self._make_service(tmp_path=tmp_path)
        assert svc.model_client is svc.ollama_client

    def test_skills_not_loaded_for_local_model(self, tmp_path: Path) -> None:
        skills_dir = tmp_path / "skills" / "polluks"
        skills_dir.mkdir(parents=True)
        (skills_dir / "test_skill.md").write_text("# Test Skill", encoding="utf-8")

        svc = self._make_service(is_api=False, tmp_path=tmp_path)
        svc.skills_loader = SkillsLoader(skills_dir=tmp_path / "skills")
        prompt = svc.build_system_prompt("hello")
        assert "Test Skill" not in prompt

    def test_skills_loaded_for_api_model(self, tmp_path: Path) -> None:
        skills_dir = tmp_path / "skills" / "polluks"
        skills_dir.mkdir(parents=True)
        (skills_dir / "test_skill.md").write_text("# Test Skill\nSkill content here", encoding="utf-8")

        svc = self._make_service(is_api=True, tmp_path=tmp_path)
        svc.skills_loader = SkillsLoader(skills_dir=tmp_path / "skills")
        prompt = svc.build_system_prompt("hello")
        assert "test_skill" in prompt
        assert "Skill content here" in prompt


# --------------------------------------------------------------------------
# Config additions
# --------------------------------------------------------------------------
from amiagi.config import Settings


class TestConfigNewFields:
    def test_default_openai_fields(self) -> None:
        s = Settings()
        assert s.openai_api_key == ""
        assert s.openai_base_url == "https://api.openai.com/v1"
        assert s.openai_request_timeout_seconds == 120
        assert s.skills_dir == Path("./skills")

    def test_from_env_reads_openai_api_key(self, monkeypatch) -> None:
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test-key-here")
        s = Settings.from_env()
        assert s.openai_api_key == "sk-test-key-here"

    def test_from_env_reads_skills_dir(self, monkeypatch) -> None:
        monkeypatch.setenv("AMIAGI_SKILLS_DIR", "/custom/skills")
        s = Settings.from_env()
        assert s.skills_dir == Path("/custom/skills")


# --------------------------------------------------------------------------
# Textual CLI wizard and command tests
# --------------------------------------------------------------------------

_TEXTUAL_AVAILABLE = False
_AmiagiTextualApp: Any = None

try:
    from amiagi.interfaces.textual_cli import _AmiagiTextualApp

    _TEXTUAL_AVAILABLE = True
except Exception:
    pass


class DummyPermissionManager:
    allow_all = False
    granted_once: set = set()


@pytest.mark.skipif(not _TEXTUAL_AVAILABLE, reason="textual not installed")
class TestTextualWizardAndCommands:
    def _make_app(self, tmp_path: Path, *, models: list[str] | None = None) -> Any:
        _models = models or ["model-a", "model-b"]

        class _Client:
            base_url = "http://127.0.0.1:11434"

            def __init__(self) -> None:
                self.model = ""

            def list_models(self) -> list[str]:
                return _models

            def chat(self, messages, system_prompt, num_ctx=None):
                return "ok"

            def ping(self):
                return True

        class _ChatSvc:
            def __init__(self) -> None:
                self.ollama_client = _Client()
                self.supervisor_service = None
                self.work_dir = tmp_path / "work"
                self.memory_repository = type("R", (), {"recent_messages": lambda s, limit=6: []})()

        return _AmiagiTextualApp(
            chat_service=cast(Any, _ChatSvc()),
            supervisor_dialogue_log_path=tmp_path / "dial.jsonl",
            permission_manager=cast(Any, DummyPermissionManager()),
            shell_policy_path=tmp_path / "shell_allowlist.json",
        )

    def test_wizard_builds_combined_model_list(self, tmp_path: Path) -> None:
        app = self._make_app(tmp_path, models=["qwen3:14b"])
        combined = app._build_wizard_model_list()
        names = [n for n, s in combined]
        assert "qwen3:14b" in names
        assert "gpt-5.3-codex" in names
        assert "gpt-5-mini" in names

    def test_format_wizard_model_list_contains_ollama_and_api(self, tmp_path: Path) -> None:
        app = self._make_app(tmp_path, models=["llama3:8b"])
        combined = app._build_wizard_model_list()
        text = app._format_wizard_model_list(combined)
        assert "Ollama" in text
        assert "API" in text
        assert "☁" in text

    def test_models_show_includes_api_models(self, tmp_path: Path) -> None:
        app = self._make_app(tmp_path)
        outcome = app._handle_cli_like_commands("/models show")
        merged = "\n".join(outcome.messages)
        assert "MODELE POLLUKSA" in merged
        assert "gpt-5.3-codex" in merged

    def test_kastor_model_command_without_supervisor(self, tmp_path: Path) -> None:
        app = self._make_app(tmp_path)
        outcome = app._handle_cli_like_commands("/kastor-model show")
        assert outcome.handled is True
        assert "nieaktywny" in "\n".join(outcome.messages).lower()

    def test_api_usage_empty(self, tmp_path: Path) -> None:
        app = self._make_app(tmp_path)
        outcome = app._handle_cli_like_commands("/api-usage")
        assert outcome.handled is True
        assert "Brak" in "\n".join(outcome.messages)

    def test_api_key_verify_no_key(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        app = self._make_app(tmp_path)
        app._settings = None
        outcome = app._handle_cli_like_commands("/api-key verify")
        assert outcome.handled is True
        assert "Brak" in "\n".join(outcome.messages)

    def test_wizard_phase_initial(self, tmp_path: Path) -> None:
        app = self._make_app(tmp_path)
        assert app._wizard_phase == 0
        assert app._model_configured is False


# =====================================================================
# InputHistory
# =====================================================================

from amiagi.infrastructure.input_history import InputHistory


class TestInputHistory:
    def test_add_and_navigate(self, tmp_path: Path) -> None:
        path = tmp_path / "history.txt"
        h = InputHistory(path)
        h.add("first")
        h.add("second")
        h.add("third")
        assert h.older() == "third"
        assert h.older() == "second"
        assert h.older() == "first"
        assert h.older() == "first"  # stays at oldest
        assert h.newer() == "second"
        assert h.newer() == "third"
        assert h.newer() == ""  # back to draft

    def test_persists_to_file(self, tmp_path: Path) -> None:
        path = tmp_path / "history.txt"
        h1 = InputHistory(path)
        h1.add("alpha")
        h1.add("beta")
        # Recreate — should load from file
        h2 = InputHistory(path)
        assert h2.entries == ["alpha", "beta"]
        assert h2.older() == "beta"

    def test_deduplicates_consecutive(self, tmp_path: Path) -> None:
        path = tmp_path / "history.txt"
        h = InputHistory(path)
        h.add("repeat")
        h.add("repeat")
        assert h.entries == ["repeat"]

    def test_empty_input_ignored(self, tmp_path: Path) -> None:
        path = tmp_path / "history.txt"
        h = InputHistory(path)
        h.add("")
        h.add("   ")
        assert h.entries == []

    def test_preserves_draft(self, tmp_path: Path) -> None:
        path = tmp_path / "history.txt"
        h = InputHistory(path)
        h.add("cmd1")
        h.add("cmd2")
        entry = h.older(current_text="my draft")
        assert entry == "cmd2"
        h.older()
        result = h.newer()
        assert result == "cmd2"
        result = h.newer()
        assert result == "my draft"

    def test_max_entries(self, tmp_path: Path) -> None:
        path = tmp_path / "history.txt"
        h = InputHistory(path, max_entries=3)
        for i in range(10):
            h.add(f"cmd{i}")
        assert len(h.entries) == 3
        assert h.entries == ["cmd7", "cmd8", "cmd9"]


# =====================================================================
# SessionModelConfig
# =====================================================================

from amiagi.infrastructure.session_model_config import SessionModelConfig


class TestSessionModelConfig:
    def test_save_and_load(self, tmp_path: Path) -> None:
        path = tmp_path / "model_config.json"
        cfg = SessionModelConfig(
            polluks_model="qwen3:14b",
            polluks_source="ollama",
            kastor_model="gpt-5-mini",
            kastor_source="openai",
        )
        cfg.save(path)
        loaded = SessionModelConfig.load(path)
        assert loaded is not None
        assert loaded.polluks_model == "qwen3:14b"
        assert loaded.kastor_model == "gpt-5-mini"
        assert loaded.kastor_source == "openai"

    def test_load_missing_returns_none(self, tmp_path: Path) -> None:
        path = tmp_path / "nonexistent.json"
        assert SessionModelConfig.load(path) is None

    def test_clear_removes_file(self, tmp_path: Path) -> None:
        path = tmp_path / "model_config.json"
        SessionModelConfig(polluks_model="x").save(path)
        assert path.exists()
        SessionModelConfig.clear(path)
        assert not path.exists()

    def test_load_corrupt_returns_none(self, tmp_path: Path) -> None:
        path = tmp_path / "model_config.json"
        path.write_text("{bad json", encoding="utf-8")
        assert SessionModelConfig.load(path) is None

    def test_config_new_settings_fields(self) -> None:
        from amiagi.config import Settings
        s = Settings()
        assert s.input_history_path == Path("./data/input_history.txt")
        assert s.model_config_path == Path("./data/model_config.json")


# --------------------------------------------------------------------------
# Sponsor panel tool_call sanitization
# --------------------------------------------------------------------------
from amiagi.application.communication_protocol import strip_tool_call_blocks, is_sponsor_readable


class TestStripToolCallBlocks:
    """Verify strip_tool_call_blocks removes fenced and bare tool JSON."""

    def test_fenced_block_removed(self) -> None:
        text = 'Odpowiedź\n```tool_call\n{"tool":"read_file","args":{"path":"a.py"}}\n```\nKoniec'
        result = strip_tool_call_blocks(text)
        assert "tool_call" not in result
        assert "read_file" not in result
        assert "Odpowiedź" in result
        assert "Koniec" in result

    def test_bare_json_removed(self) -> None:
        text = 'Oto wynik: {"tool": "run_shell", "args": {"command": "ls"}} kontynuuję'
        result = strip_tool_call_blocks(text)
        assert '"tool"' not in result
        assert "kontynuuję" in result

    def test_plain_text_unchanged(self) -> None:
        text = "Zwykły tekst bez narzędzi."
        assert strip_tool_call_blocks(text) == text

    def test_only_tool_block_returns_empty(self) -> None:
        text = '```tool_call\n{"tool":"read_file","args":{"path":"x"}}\n```'
        assert strip_tool_call_blocks(text) == ""


class TestIsSponsorReadable:
    """Verify is_sponsor_readable rejects JSON-heavy and tool-like content."""

    def test_plain_text_readable(self) -> None:
        assert is_sponsor_readable("Wszystko gotowe.") is True

    def test_many_braces_unreadable(self) -> None:
        assert is_sponsor_readable('{"a":{"b":{"c":1}}}') is False

    def test_tool_marker_unreadable(self) -> None:
        assert is_sponsor_readable('"tool": "read_file"') is False

    def test_empty_readable(self) -> None:
        assert is_sponsor_readable("") is True


class TestSanitizeBlockForSponsor:
    """Integration test for _sanitize_block_for_sponsor via the app class.

    We instantiate the Textual app minimally and call the helper directly.
    """

    def _make_app(self) -> Any:
        """Create a minimal app instance for testing the sanitization helper."""
        from unittest.mock import MagicMock, patch
        from amiagi.interfaces.textual_cli import _AmiagiTextualApp

        mock_service = MagicMock()
        mock_service.supervisor_service = None

        with patch.object(_AmiagiTextualApp, "__init__", lambda self_arg: None):
            app: Any = _AmiagiTextualApp.__new__(_AmiagiTextualApp)

        # Minimal attributes needed for _sanitize_block_for_sponsor
        app._log_buffer = []  # list[tuple[str, str]]

        def fake_append_log(widget_id: str, message: str) -> None:
            app._log_buffer.append((widget_id, message))

        app._append_log = fake_append_log
        return app

    def test_pure_tool_call_returns_none(self) -> None:
        app = self._make_app()
        content = '```tool_call\n{"tool":"read_file","args":{"path":"x.py"}}\n```'
        result = app._sanitize_block_for_sponsor(content, "[Polluks -> Sponsor]")
        assert result is None
        # Should have redirected to executor_log + supervisor_log
        panels = [panel for panel, _ in app._log_buffer]
        assert "executor_log" in panels
        assert "supervisor_log" in panels

    def test_mixed_content_returns_stripped(self) -> None:
        app = self._make_app()
        content = 'Przeczytałem plik.\n```tool_call\n{"tool":"read_file","args":{"path":"x"}}\n```'
        result = app._sanitize_block_for_sponsor(content, "[Polluks -> Sponsor]")
        assert result is not None
        assert "tool_call" not in result
        assert "Przeczytałem plik." in result
        # Full version echoed to executor_log
        executor_entries = [msg for panel, msg in app._log_buffer if panel == "executor_log"]
        assert any("tool_call" in e for e in executor_entries)

    def test_plain_text_passes_through(self) -> None:
        app = self._make_app()
        content = "Zadanie wykonane pomyślnie."
        result = app._sanitize_block_for_sponsor(content, "")
        assert result == content
        assert app._log_buffer == []
