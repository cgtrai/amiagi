CREATE TABLE IF NOT EXISTS prompt_usage (
    prompt_id      TEXT NOT NULL,
    agent_id       TEXT NOT NULL,
    use_count      INTEGER DEFAULT 0,
    last_used_at   TEXT DEFAULT (datetime('now')),
    PRIMARY KEY (prompt_id, agent_id)
);

ALTER TABLE snippets
    ADD COLUMN IF NOT EXISTS pinned INTEGER DEFAULT 0;