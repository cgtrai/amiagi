SET search_path TO dbo;

ALTER TABLE dbo.api_keys
    ADD COLUMN IF NOT EXISTS rate_limit_per_min INT;

ALTER TABLE dbo.webhooks
    ADD COLUMN IF NOT EXISTS last_delivery_status INT,
    ADD COLUMN IF NOT EXISTS last_delivery_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS last_error TEXT;
