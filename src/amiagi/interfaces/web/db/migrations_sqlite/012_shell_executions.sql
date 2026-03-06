-- ============================================================
-- Migration 012 (SQLite): Shell executions log + sandbox metadata
-- ============================================================

-- ── Shell execution audit log ────────────────────────────────
CREATE TABLE IF NOT EXISTS shell_executions (
    id              TEXT PRIMARY KEY,
    agent_id        TEXT NOT NULL,
    command         TEXT NOT NULL,
    exit_code       INTEGER,
    blocked         INTEGER NOT NULL DEFAULT 0,
    block_reason    TEXT,
    duration_ms     INTEGER,
    stdout_preview  TEXT,
    stderr_preview  TEXT,
    sandbox_id      TEXT,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_shell_exec_agent
    ON shell_executions(agent_id);

CREATE INDEX IF NOT EXISTS idx_shell_exec_created
    ON shell_executions(created_at);

-- ── Sandbox metadata (persistent registry) ───────────────────
CREATE TABLE IF NOT EXISTS sandbox_metadata (
    agent_id        TEXT PRIMARY KEY,
    sandbox_path    TEXT NOT NULL,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_accessed   TIMESTAMP,
    size_bytes      INTEGER DEFAULT 0,
    file_count      INTEGER DEFAULT 0,
    max_size_bytes  INTEGER DEFAULT 268435456,
    notes           TEXT
);
