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

## Incident Response

If suspicious behavior is observed:

1. Stop the process immediately.
2. Isolate or destroy the VM.
3. Rotate credentials potentially exposed.
4. Review model I/O and activity logs.

## No Security Guarantee

This project is experimental and provided without security guarantees. Users are fully responsible for safe deployment, isolation, and legal compliance.
