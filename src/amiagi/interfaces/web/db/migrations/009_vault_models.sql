-- ============================================================
-- Migration 009 (PostgreSQL): Vault & Model Assignments
-- ============================================================

-- ── Vault secrets ────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS dbo.vault_secrets (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    agent_id        TEXT NOT NULL,
    key             TEXT NOT NULL,
    encrypted_value TEXT NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    rotated_at      TIMESTAMPTZ,
    UNIQUE (agent_id, key)
);

CREATE INDEX IF NOT EXISTS idx_vault_agent
    ON dbo.vault_secrets(agent_id);

-- ── Vault access log ─────────────────────────────────────────
CREATE TABLE IF NOT EXISTS dbo.vault_access_log (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    agent_id        TEXT NOT NULL,
    key             TEXT,
    action          TEXT NOT NULL,
    performed_by    TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_vault_log_time
    ON dbo.vault_access_log(created_at DESC);

-- ── Model assignments (agent → model mapping) ────────────────
CREATE TABLE IF NOT EXISTS dbo.model_assignments (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    agent_id        TEXT NOT NULL UNIQUE,
    model_name      TEXT NOT NULL,
    provider        TEXT NOT NULL DEFAULT 'ollama',
    assigned_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    assigned_by     TEXT
);

-- ── Budget history snapshots ─────────────────────────────────
CREATE TABLE IF NOT EXISTS dbo.budget_snapshots (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    agent_id        TEXT,
    total_cost      NUMERIC(12,6) NOT NULL DEFAULT 0,
    total_tokens    BIGINT NOT NULL DEFAULT 0,
    total_requests  INTEGER NOT NULL DEFAULT 0,
    snapshot_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_budget_snap_time
    ON dbo.budget_snapshots(snapshot_at DESC);
