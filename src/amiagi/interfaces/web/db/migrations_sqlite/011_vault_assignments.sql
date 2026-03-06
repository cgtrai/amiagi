-- ============================================================
-- Migration 011 (SQLite): Vault secret assignments
-- ============================================================

-- ── Vault assignments (m2m: secrets ↔ agents/skills) ─────────
CREATE TABLE IF NOT EXISTS vault_assignments (
    id              TEXT PRIMARY KEY,
    secret_agent_id TEXT NOT NULL,
    secret_key      TEXT NOT NULL,
    entity_type     TEXT NOT NULL CHECK (entity_type IN ('agent', 'skill')),
    entity_id       TEXT NOT NULL,
    assigned_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    assigned_by     TEXT,
    UNIQUE (secret_agent_id, secret_key, entity_type, entity_id)
);

CREATE INDEX IF NOT EXISTS idx_vault_assign_entity
    ON vault_assignments(entity_type, entity_id);

CREATE INDEX IF NOT EXISTS idx_vault_assign_secret
    ON vault_assignments(secret_agent_id, secret_key);
