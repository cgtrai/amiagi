-- Migration 003 (SQLite): Prompts, Search Index, Snippets

-- ── Shared Prompts Library ─────────────────────────────────────
CREATE TABLE IF NOT EXISTS prompts (
    id          TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
    user_id     TEXT NOT NULL,
    title       TEXT NOT NULL,
    template    TEXT NOT NULL,
    tags        TEXT DEFAULT '[]',          -- JSON array
    is_public   INTEGER DEFAULT 0,
    use_count   INTEGER DEFAULT 0,
    created_at  TEXT DEFAULT (datetime('now')),
    updated_at  TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_prompts_user ON prompts(user_id);
CREATE INDEX IF NOT EXISTS idx_prompts_public ON prompts(is_public) WHERE is_public = 1;

-- ── Global Search Index ────────────────────────────────────────
-- NOTE: Full-text search falls back to LIKE in SQLite mode.
-- The content_tsv column is omitted; queries use the content column directly.
CREATE TABLE IF NOT EXISTS search_index (
    id            TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
    entity_type   TEXT NOT NULL,
    entity_id     TEXT NOT NULL,
    title         TEXT NOT NULL,
    content       TEXT DEFAULT '',
    updated_at    TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_search_type ON search_index(entity_type);
CREATE UNIQUE INDEX IF NOT EXISTS idx_search_entity ON search_index(entity_type, entity_id);

-- ── Snippets ───────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS snippets (
    id              TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
    user_id         TEXT NOT NULL,
    content         TEXT NOT NULL,
    tags            TEXT DEFAULT '[]',       -- JSON array
    source_agent    TEXT,
    source_task_id  TEXT,
    created_at      TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_snippets_user ON snippets(user_id);
