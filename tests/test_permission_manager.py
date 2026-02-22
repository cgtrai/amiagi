from __future__ import annotations

from amiagi.interfaces.cli import _network_resource_for_model
from amiagi.interfaces.permission_manager import PermissionManager


def test_permission_manager_denies_on_no() -> None:
    manager = PermissionManager(input_fn=lambda _: "n", output_fn=lambda _: None)
    assert manager.request("network.internet", "test") is False


def test_permission_manager_allow_all_skips_next_prompts() -> None:
    answers = iter(["all"])
    manager = PermissionManager(input_fn=lambda _: next(answers), output_fn=lambda _: None)

    assert manager.request("disk.read", "test") is True
    assert manager.request("network.internet", "test") is True


def test_permission_manager_remembers_granted_resource_in_session() -> None:
    prompts: list[str] = []

    def _input(prompt: str) -> str:
        prompts.append(prompt)
        return "tak"

    manager = PermissionManager(input_fn=_input, output_fn=lambda _: None)

    assert manager.request("disk.read", "pierwszy odczyt") is True
    assert manager.request("disk.read", "drugi odczyt") is True
    assert len(prompts) == 1


def test_permission_manager_asks_again_for_different_resource() -> None:
    prompts: list[str] = []
    answers = iter(["tak", "tak"])

    def _input(prompt: str) -> str:
        prompts.append(prompt)
        return next(answers)

    manager = PermissionManager(input_fn=_input, output_fn=lambda _: None)

    assert manager.request("disk.read", "odczyt") is True
    assert manager.request("disk.write", "zapis") is True
    assert len(prompts) == 2


def test_network_resource_is_local_for_loopback() -> None:
    assert _network_resource_for_model("http://127.0.0.1:11434") == "network.local"
    assert _network_resource_for_model("http://localhost:11434") == "network.local"


def test_network_resource_is_internet_for_remote_host() -> None:
    assert _network_resource_for_model("https://example.com") == "network.internet"


def test_permission_manager_camera_microphone_hooks() -> None:
    answers = iter(["tak", "yes"])
    manager = PermissionManager(input_fn=lambda _: next(answers), output_fn=lambda _: None)

    assert manager.request_camera_access() is True
    assert manager.request_microphone_access() is True


def test_permission_manager_clipboard_hooks() -> None:
    answers = iter(["t", "y"])
    manager = PermissionManager(input_fn=lambda _: next(answers), output_fn=lambda _: None)

    assert manager.request_clipboard_read() is True
    assert manager.request_clipboard_write() is True
