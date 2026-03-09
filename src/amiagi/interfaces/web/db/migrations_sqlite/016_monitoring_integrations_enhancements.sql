ALTER TABLE api_keys
    ADD COLUMN rate_limit_per_min INTEGER;

ALTER TABLE webhooks
    ADD COLUMN last_delivery_status INTEGER;

ALTER TABLE webhooks
    ADD COLUMN last_delivery_at TEXT;

ALTER TABLE webhooks
    ADD COLUMN last_error TEXT;
