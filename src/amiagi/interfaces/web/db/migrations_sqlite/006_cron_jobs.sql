-- Migration 006 (SQLite): Cron Jobs

CREATE TABLE IF NOT EXISTS cron_jobs (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL DEFAULT '',
    cron_expr   TEXT NOT NULL DEFAULT '* * * * *',
    task_title  TEXT NOT NULL DEFAULT '',
    task_description TEXT NOT NULL DEFAULT '',
    enabled     INTEGER NOT NULL DEFAULT 1,
    last_run    TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
