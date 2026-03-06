-- ============================================================
-- Migration 012 (PostgreSQL): Shell executions log + sandbox metadata
-- ============================================================

-- ── Shell execution audit log ────────────────────────────────
CREATE TABLE IF NOT EXISTS dbo.shell_executions (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    agent_id        TEXT NOT NULL,
    command         TEXT NOT NULL,
    exit_code       INTEGER,
    blocked         BOOLEAN NOT NULL DEFAULT false,
    block_reason    TEXT,
    duration_ms     INTEGER,
    stdout_preview  TEXT,
    stderr_preview  TEXT,
    sandbox_id      TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_shell_exec_agent
    ON dbo.shell_executions(agent_id);

CREATE INDEX IF NOT EXISTS idx_shell_exec_created
    ON dbo.shell_executions(created_at DESC);

CREATE INDEX IF NOT EXISTS idx_shell_exec_blocked
    ON dbo.shell_executions(blocked) WHERE blocked = true;

-- ── Sandbox metadata (persistent registry) ───────────────────
CREATE TABLE IF NOT EXISTS dbo.sandbox_metadata (
    agent_id        TEXT PRIMARY KEY,
    sandbox_path    TEXT NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_accessed   TIMESTAMPTZ,
    size_bytes      BIGINT DEFAULT 0,
    file_count      INTEGER DEFAULT 0,
    max_size_bytes  BIGINT DEFAULT 268435456,
    notes           TEXT
);

CREATE INDEX IF NOT EXISTS idx_sandbox_meta_size
    ON dbo.sandbox_metadata(size_bytes DESC);
