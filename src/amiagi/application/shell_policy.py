from __future__ import annotations

import json
import shlex
from dataclasses import dataclass
from pathlib import Path


_DEFAULT_NO_ARG_COMMANDS = {
    "lscpu",
    "mount",
    "whoami",
    "id",
    "pwd",
    "date",
    "uptime",
    "nproc",
}

_DEFAULT_ARG_SUBSET_COMMANDS: dict[str, set[str]] = {
    "uname": {"-a", "-r", "-s", "-m", "-n", "-v"},
    "free": {"-h", "-m", "-g"},
    "df": {"-h", "-T"},
    "lsblk": {"-a", "-f"},
    "ss": {"-tulpen", "-tulpn", "-tulp", "-tuna", "-lntup"},
}

_DEFAULT_CAT_ALLOWED_FILES = {
    "/proc/cpuinfo",
    "/proc/meminfo",
    "/proc/loadavg",
    "/proc/uptime",
    "/proc/version",
    "/etc/os-release",
}


@dataclass(frozen=True)
class ShellPolicy:
    no_arg_commands: set[str]
    arg_subset_commands: dict[str, set[str]]
    exact_commands: set[tuple[str, ...]]
    ip_allowed_subcommands: set[str]
    cat_allowed_files: set[str]


def default_shell_policy() -> ShellPolicy:
    return ShellPolicy(
        no_arg_commands=set(_DEFAULT_NO_ARG_COMMANDS),
        arg_subset_commands={
            command: set(flags)
            for command, flags in _DEFAULT_ARG_SUBSET_COMMANDS.items()
        },
        exact_commands={
            ("ps", "aux"),
            ("ps", "-ef"),
            ("ollama", "list"),
        },
        ip_allowed_subcommands={"addr", "route", "link"},
        cat_allowed_files=set(_DEFAULT_CAT_ALLOWED_FILES),
    )


def load_shell_policy(policy_path: Path) -> ShellPolicy:
    if not policy_path.exists():
        return default_shell_policy()

    if policy_path.suffix.lower() == ".jsonl":
        payload = _load_jsonl_payload(policy_path)
    else:
        payload = json.loads(policy_path.read_text(encoding="utf-8"))

    if not isinstance(payload, dict):
        raise ValueError("Shell policy payload must be a JSON object.")

    no_arg_commands = set(payload.get("no_arg_commands", []))
    arg_subset_raw = payload.get("arg_subset_commands", {})
    arg_subset_commands = {
        command: set(flags)
        for command, flags in arg_subset_raw.items()
    }
    exact_commands = {
        tuple(command)
        for command in payload.get("exact_commands", [])
        if isinstance(command, list)
    }
    ip_allowed_subcommands = set(payload.get("ip_allowed_subcommands", []))
    cat_allowed_files = set(payload.get("cat_allowed_files", []))

    return ShellPolicy(
        no_arg_commands=no_arg_commands,
        arg_subset_commands=arg_subset_commands,
        exact_commands=exact_commands,
        ip_allowed_subcommands=ip_allowed_subcommands,
        cat_allowed_files=cat_allowed_files,
    )


def _load_jsonl_payload(policy_path: Path) -> dict:
    lines = [line.strip() for line in policy_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    records = [json.loads(line) for line in lines]

    if len(records) == 1 and isinstance(records[0], dict) and (
        "no_arg_commands" in records[0]
        or "arg_subset_commands" in records[0]
        or "exact_commands" in records[0]
        or "ip_allowed_subcommands" in records[0]
        or "cat_allowed_files" in records[0]
    ):
        return records[0]

    payload: dict[str, object] = {
        "no_arg_commands": [],
        "arg_subset_commands": {},
        "exact_commands": [],
        "ip_allowed_subcommands": [],
        "cat_allowed_files": [],
    }
    arg_subset_commands = payload["arg_subset_commands"]
    assert isinstance(arg_subset_commands, dict)

    for record in records:
        if not isinstance(record, dict):
            continue
        record_type = record.get("type")
        if record_type == "no_arg":
            command = record.get("command")
            if isinstance(command, str):
                cast_list = payload["no_arg_commands"]
                assert isinstance(cast_list, list)
                cast_list.append(command)
        elif record_type == "arg_subset":
            command = record.get("command")
            allowed_args = record.get("allowed_args")
            if isinstance(command, str) and isinstance(allowed_args, list):
                arg_subset_commands[command] = allowed_args
        elif record_type == "exact":
            argv = record.get("argv")
            if isinstance(argv, list):
                cast_list = payload["exact_commands"]
                assert isinstance(cast_list, list)
                cast_list.append(argv)
        elif record_type == "ip_subcommand":
            value = record.get("value")
            if isinstance(value, str):
                cast_list = payload["ip_allowed_subcommands"]
                assert isinstance(cast_list, list)
                cast_list.append(value)
        elif record_type == "cat_file":
            value = record.get("path")
            if isinstance(value, str):
                cast_list = payload["cat_allowed_files"]
                assert isinstance(cast_list, list)
                cast_list.append(value)

    return payload


def parse_and_validate_shell_command(
    command_text: str,
    policy: ShellPolicy,
) -> tuple[list[str] | None, str | None]:
    try:
        argv = shlex.split(command_text)
    except ValueError as error:
        return None, f"Niepoprawna składnia polecenia: {error}"

    if not argv:
        return None, "Polecenie jest puste."

    command_name = Path(argv[0]).name
    args = argv[1:]

    if command_name in policy.no_arg_commands:
        if args:
            return None, f"Polecenie '{command_name}' nie przyjmuje argumentów w tym trybie."
        return argv, None

    if command_name in policy.arg_subset_commands:
        allowed_args = policy.arg_subset_commands[command_name]
        if any(arg not in allowed_args for arg in args):
            return None, f"Niedozwolone argumenty dla '{command_name}'."
        return argv, None

    if tuple([command_name, *args]) in policy.exact_commands:
        return argv, None

    if command_name == "ps":
        if tuple([command_name, *args]) in policy.exact_commands:
            return argv, None
        return None, "Dozwolone: 'ps aux' albo 'ps -ef'."

    if command_name == "ip":
        if not args:
            return None, "Dozwolone: 'ip addr', 'ip route', 'ip link' (opcjonalnie 'show')."
        if args[0] not in policy.ip_allowed_subcommands:
            return None, "Dozwolone: 'ip addr', 'ip route', 'ip link'."
        if len(args) == 1:
            return argv, None
        if len(args) == 2 and args[1] == "show":
            return argv, None
        return None, "Dozwolone: 'ip <addr|route|link> [show]'."

    if command_name == "cat":
        if len(args) != 1:
            return None, "Dozwolone: 'cat <plik>' dla wybranych plików systemowych."
        path = args[0]
        if path in policy.cat_allowed_files:
            return argv, None
        return None, "Niedozwolony plik dla 'cat' w trybie bezpiecznym."

    return None, f"Polecenie '{command_name}' nie jest na liście dozwolonych."
