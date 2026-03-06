-- ============================================================
-- Migration 008 (SQLite): Inbox — Human-in-the-Loop items
-- ============================================================

CREATE TABLE IF NOT EXISTS inbox_items (
    id              TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
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
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    resolved_at     TEXT,
    metadata        TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_inbox_status
    ON inbox_items(status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_inbox_source
    ON inbox_items(source_type, source_id);
