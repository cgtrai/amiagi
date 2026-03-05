-- 006_cron_jobs.sql — Scheduled (cron) tasks table

CREATE TABLE IF NOT EXISTS dbo.cron_jobs (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL DEFAULT '',
    cron_expr   TEXT NOT NULL DEFAULT '* * * * *',
    task_title  TEXT NOT NULL DEFAULT '',
    task_description TEXT NOT NULL DEFAULT '',
    enabled     BOOLEAN NOT NULL DEFAULT TRUE,
    last_run    TIMESTAMPTZ,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
