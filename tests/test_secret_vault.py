"""Tests for SecretVault (Phase 7)."""

from __future__ import annotations

from pathlib import Path

import pytest

from amiagi.infrastructure.secret_vault import SecretVault


@pytest.fixture()
def vault(tmp_path: Path) -> SecretVault:
    return SecretVault(vault_path=tmp_path / "vault.json")


class TestSecretVault:
    def test_set_and_get(self, vault: SecretVault) -> None:
        vault.set_secret("agent1", "api_key", "sk-12345")
        assert vault.get_secret("agent1", "api_key") == "sk-12345"

    def test_get_nonexistent_key(self, vault: SecretVault) -> None:
        assert vault.get_secret("agent1", "nope") is None

    def test_get_nonexistent_agent(self, vault: SecretVault) -> None:
        assert vault.get_secret("nope", "key") is None

    def test_delete_secret(self, vault: SecretVault) -> None:
        vault.set_secret("a1", "k", "v")
        assert vault.delete_secret("a1", "k")
        assert vault.get_secret("a1", "k") is None

    def test_delete_nonexistent(self, vault: SecretVault) -> None:
        assert not vault.delete_secret("a1", "nope")

    def test_list_keys(self, vault: SecretVault) -> None:
        vault.set_secret("a1", "key1", "v1")
        vault.set_secret("a1", "key2", "v2")
        keys = vault.list_keys("a1")
        assert set(keys) == {"key1", "key2"}

    def test_list_keys_empty(self, vault: SecretVault) -> None:
        assert vault.list_keys("unknown") == []

    def test_delete_agent(self, vault: SecretVault) -> None:
        vault.set_secret("a1", "k1", "v1")
        vault.set_secret("a1", "k2", "v2")
        assert vault.delete_agent("a1")
        assert vault.list_keys("a1") == []

    def test_delete_agent_nonexistent(self, vault: SecretVault) -> None:
        assert not vault.delete_agent("nope")

    def test_agent_isolation(self, vault: SecretVault) -> None:
        vault.set_secret("a1", "key", "secret_a1")
        vault.set_secret("a2", "key", "secret_a2")
        assert vault.get_secret("a1", "key") == "secret_a1"
        assert vault.get_secret("a2", "key") == "secret_a2"

    def test_persistence(self, tmp_path: Path) -> None:
        path = tmp_path / "vault.json"
        v1 = SecretVault(vault_path=path)
        v1.set_secret("a1", "token", "abc123")
        # Simulate restart
        v2 = SecretVault(vault_path=path)
        assert v2.get_secret("a1", "token") == "abc123"

    def test_overwrite_secret(self, vault: SecretVault) -> None:
        vault.set_secret("a1", "key", "old")
        vault.set_secret("a1", "key", "new")
        assert vault.get_secret("a1", "key") == "new"

    def test_obfuscation_not_plaintext(self, tmp_path: Path) -> None:
        path = tmp_path / "vault.json"
        v = SecretVault(vault_path=path)
        v.set_secret("a1", "password", "super_secret_pass")
        raw = path.read_text()
        assert "super_secret_pass" not in raw

    def test_unicode_secret(self, vault: SecretVault) -> None:
        vault.set_secret("a1", "greeting", "cześć świat 🌍")
        assert vault.get_secret("a1", "greeting") == "cześć świat 🌍"
