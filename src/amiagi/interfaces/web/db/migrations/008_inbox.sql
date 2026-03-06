-- ============================================================
-- Migration 008 (PostgreSQL): Inbox — Human-in-the-Loop items
-- ============================================================

CREATE TABLE IF NOT EXISTS dbo.inbox_items (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    item_type       TEXT NOT NULL DEFAULT 'gate_approval',
    title           TEXT NOT NULL,
    body            TEXT NOT NULL DEFAULT '',
    source_type     TEXT NOT NULL DEFAULT 'workflow',
    source_id       TEXT,
    node_id         TEXT,
    agent_id        TEXT,
    status          TEXT NOT NULL DEFAULT 'pending',
    priority        INTEGER NOT NULL DEFAULT 0,
    resolution      TEXT,
    resolved_by     TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    resolved_at     TIMESTAMPTZ,
    metadata        JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_inbox_status
    ON dbo.inbox_items(status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_inbox_source
    ON dbo.inbox_items(source_type, source_id);
