-- ============================================================
-- Migration 009 (SQLite): Vault & Model Assignments
-- ============================================================

CREATE TABLE IF NOT EXISTS vault_secrets (
    id TEXT PRIMARY KEY,
    agent_id TEXT NOT NULL,
    key TEXT NOT NULL,
    encrypted_value TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    rotated_at TIMESTAMP,
    UNIQUE (agent_id, key)
);

CREATE INDEX IF NOT EXISTS idx_vault_agent
    ON vault_secrets(agent_id);

CREATE TABLE IF NOT EXISTS vault_access_log (
    id TEXT PRIMARY KEY,
    agent_id TEXT NOT NULL,
    key TEXT,
    action TEXT NOT NULL,
    performed_by TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_vault_log_time
    ON vault_access_log(created_at DESC);

CREATE TABLE IF NOT EXISTS model_assignments (
    id TEXT PRIMARY KEY,
    agent_id TEXT NOT NULL UNIQUE,
    model_name TEXT NOT NULL,
    provider TEXT NOT NULL DEFAULT 'ollama',
    assigned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    assigned_by TEXT
);

CREATE TABLE IF NOT EXISTS budget_snapshots (
    id TEXT PRIMARY KEY,
    agent_id TEXT,
    total_cost REAL NOT NULL DEFAULT 0,
    total_tokens INTEGER NOT NULL DEFAULT 0,
    total_requests INTEGER NOT NULL DEFAULT 0,
    snapshot_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_budget_snap_time
    ON budget_snapshots(snapshot_at DESC);
