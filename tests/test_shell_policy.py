from __future__ import annotations

from pathlib import Path

from amiagi.application.shell_policy import (
    default_shell_policy,
    load_shell_policy,
    parse_and_validate_shell_command,
)


def test_allows_resource_info_commands() -> None:
    policy = default_shell_policy()
    argv, error = parse_and_validate_shell_command("uname -a", policy)
    assert error is None
    assert argv == ["uname", "-a"]

    argv, error = parse_and_validate_shell_command("ollama list", policy)
    assert error is None
    assert argv == ["ollama", "list"]


def test_rejects_mutating_or_non_allowlisted_commands() -> None:
    policy = default_shell_policy()
    _, error = parse_and_validate_shell_command("rm -rf /tmp/x", policy)
    assert error is not None

    _, error = parse_and_validate_shell_command("ip addr add 1.2.3.4/24 dev lo", policy)
    assert error is not None


def test_rejects_disallowed_cat_file() -> None:
    policy = default_shell_policy()
    _, error = parse_and_validate_shell_command("cat /etc/passwd", policy)
    assert error is not None


def test_load_shell_policy_from_json(tmp_path: Path) -> None:
    policy_path = tmp_path / "policy.json"
    policy_path.write_text(
        """
{
  "no_arg_commands": ["date"],
  "arg_subset_commands": {},
  "exact_commands": [["echo", "ok"]],
  "ip_allowed_subcommands": ["addr"],
  "cat_allowed_files": ["/proc/version"]
}
""".strip(),
        encoding="utf-8",
    )

    policy = load_shell_policy(policy_path)
    argv, error = parse_and_validate_shell_command("echo ok", policy)

    assert error is None
    assert argv == ["echo", "ok"]


def test_load_shell_policy_from_jsonl(tmp_path: Path) -> None:
    policy_path = tmp_path / "policy.jsonl"
    policy_path.write_text(
        "\n".join(
            [
                '{"type":"no_arg","command":"date"}',
                '{"type":"exact","argv":["echo","ok"]}',
                '{"type":"ip_subcommand","value":"addr"}',
                '{"type":"cat_file","path":"/proc/version"}',
            ]
        ),
        encoding="utf-8",
    )

    policy = load_shell_policy(policy_path)
    argv, error = parse_and_validate_shell_command("echo ok", policy)

    assert error is None
    assert argv == ["echo", "ok"]
