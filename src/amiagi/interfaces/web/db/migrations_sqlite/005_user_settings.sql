CREATE TABLE IF NOT EXISTS user_settings (
    user_id    TEXT PRIMARY KEY,
    settings   TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);