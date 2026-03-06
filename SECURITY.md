# Security Notice

## High-Risk Software Warning

`amiagi` can execute model-generated instructions, scripts, and shell operations. This creates a potentially dangerous runtime profile.

Use this software only if you understand and accept these risks.

## Mandatory Safety Recommendations

- Always run in an isolated **virtual machine** (recommended default).
- Use a dedicated low-privilege OS user.
- Keep the workspace away from personal or production data.
- Disable or strictly limit network access whenever possible.
- Never store cloud credentials, private keys, or secrets in the runtime environment.
- Review logs (`logs/*.jsonl`) regularly during experiments.

## Operational Hardening

- Keep `config/shell_allowlist.json` restrictive.
- Do not grant broad permissions unless strictly needed.
- Prefer read-only workflows first, then escalate permissions minimally.
- Snapshot the VM before high-risk experiments.

## Vault Encryption

Sensitive values (API keys, OAuth tokens, webhook secrets) are stored in an
encrypted vault:

- **At-rest encryption**: AES-256-GCM via the Fernet-compatible scheme.
- **Key derivation**: PBKDF2-HMAC-SHA256 from a master password.
- **Storage**: Database column `dbo.vault_entries.encrypted_value` — never plain text.
- **Access**: Vault read/write restricted to admin roles via RBAC middleware.
- **Rotation**: Re-encrypt all entries by updating the master key (admin CLI).

## Sandbox Isolation

Each agent operates within a dedicated sandbox directory:

- **Per-agent path**: `data/sandboxes/{agent_id}/` — isolated file system scope.
- **Size limits**: Configurable `max_size_bytes` per sandbox (default: 100 MB).
- **Cleanup hooks**: `SandboxMonitor` periodically scans sandboxes, removes stale
  temp files, and alerts on size limit violations.
- **Audit trail**: Every shell command executed inside a sandbox is logged to
  `dbo.shell_executions` with command text, exit code, duration, and block reason.
- **Resource tracking**: File count, total size, and last access timestamp are
  tracked in `dbo.sandbox_metadata`.

## Shell Policy

The shell execution layer enforces a strict allowlist policy:

- **Configuration**: `config/shell_allowlist.json` — defines four categories:
  - `allowed_commands` — commands permitted without restriction (e.g., `ls`, `cat`)
  - `allowed_with_args` — commands permitted only with specific argument patterns
  - `exact_commands` — exact full command strings (e.g., `git status`)
  - `blocked_patterns` — regex patterns that block execution (e.g., `rm -rf /`)
- **Enforcement**: Every shell invocation is checked against the policy before
  execution. Blocked commands are logged with `blocked=true` and `block_reason`.
- **UI editing**: Administrators can view and edit the shell policy from the
  web interface (`/admin/sandboxes` → Shell Policy section, admin-only).
- **Dual mode**: Visual editor (tag-based add/remove) or raw JSON editing.

## Human-in-the-Loop

Agents can request human input via built-in tools:

- **AskHumanTool**: Agent asks a question → appears in operator Inbox →
  operator replies → agent receives answer and continues.
- **ReviewRequestTool**: Agent submits work for review → Inbox notification →
  operator approves/rejects → agent proceeds accordingly.
- Both tools use the web Inbox system with real-time WebSocket push.

## Incident Response

If suspicious behavior is observed:

1. Stop the process immediately.
2. Isolate or destroy the VM.
3. Rotate credentials potentially exposed.
4. Review model I/O and activity logs.

## No Security Guarantee

This project is experimental and provided without security guarantees. Users are fully responsible for safe deployment, isolation, and legal compliance.
