-- ================================================================
-- Migration 003: Prompts, Search Index, Snippets
-- Faza 12 — User productivity tables
-- ================================================================

SET search_path TO dbo;

-- ── Shared Prompts Library ─────────────────────────────────────

CREATE TABLE IF NOT EXISTS dbo.prompts (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     UUID NOT NULL,
    title       VARCHAR(255) NOT NULL,
    template    TEXT NOT NULL,
    tags        TEXT[] DEFAULT '{}',
    is_public   BOOLEAN DEFAULT false,
    use_count   INT DEFAULT 0,
    created_at  TIMESTAMPTZ DEFAULT now(),
    updated_at  TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_prompts_user ON dbo.prompts(user_id);
CREATE INDEX IF NOT EXISTS idx_prompts_tags ON dbo.prompts USING GIN(tags);
CREATE INDEX IF NOT EXISTS idx_prompts_public ON dbo.prompts(is_public) WHERE is_public = true;

-- ── Global Search Index ────────────────────────────────────────

CREATE TABLE IF NOT EXISTS dbo.search_index (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    entity_type   VARCHAR(50) NOT NULL,   -- 'agent', 'task', 'file', 'prompt', 'skill', 'snippet'
    entity_id     VARCHAR(255) NOT NULL,
    title         VARCHAR(500) NOT NULL,
    content       TEXT DEFAULT '',
    content_tsv   tsvector,
    updated_at    TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_search_tsv ON dbo.search_index USING GIN(content_tsv);
CREATE INDEX IF NOT EXISTS idx_search_type ON dbo.search_index(entity_type);
CREATE UNIQUE INDEX IF NOT EXISTS idx_search_entity ON dbo.search_index(entity_type, entity_id);

-- Auto-update tsvector
CREATE OR REPLACE FUNCTION dbo.search_index_tsv_trigger() RETURNS trigger AS $$
BEGIN
    NEW.content_tsv := to_tsvector('english', COALESCE(NEW.title, '') || ' ' || COALESCE(NEW.content, ''));
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_search_index_tsv ON dbo.search_index;
CREATE TRIGGER trg_search_index_tsv
    BEFORE INSERT OR UPDATE ON dbo.search_index
    FOR EACH ROW EXECUTE FUNCTION dbo.search_index_tsv_trigger();

-- ── Snippets ───────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS dbo.snippets (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID NOT NULL,
    content         TEXT NOT NULL,
    tags            TEXT[] DEFAULT '{}',
    source_agent    VARCHAR(100),
    source_task_id  VARCHAR(255),
    created_at      TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_snippets_user ON dbo.snippets(user_id);
CREATE INDEX IF NOT EXISTS idx_snippets_tags ON dbo.snippets USING GIN(tags);
