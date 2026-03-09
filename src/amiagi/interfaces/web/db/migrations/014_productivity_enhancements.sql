SET search_path TO dbo;

CREATE TABLE IF NOT EXISTS dbo.prompt_usage (
    prompt_id      UUID NOT NULL REFERENCES dbo.prompts(id) ON DELETE CASCADE,
    agent_id       VARCHAR(255) NOT NULL,
    use_count      INT DEFAULT 0,
    last_used_at   TIMESTAMPTZ DEFAULT now(),
    PRIMARY KEY (prompt_id, agent_id)
);

ALTER TABLE dbo.snippets
    ADD COLUMN IF NOT EXISTS pinned BOOLEAN DEFAULT false;