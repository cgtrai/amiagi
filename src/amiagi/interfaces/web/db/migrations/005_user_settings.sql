CREATE TABLE IF NOT EXISTS dbo.user_settings (
    user_id    TEXT PRIMARY KEY,
    settings   JSONB NOT NULL DEFAULT '{}'::jsonb,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);