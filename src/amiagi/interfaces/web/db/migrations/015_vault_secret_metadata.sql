ALTER TABLE dbo.vault_secrets
    ADD COLUMN IF NOT EXISTS secret_type TEXT NOT NULL DEFAULT 'api_key';

ALTER TABLE dbo.vault_secrets
    ADD COLUMN IF NOT EXISTS expires_at TIMESTAMPTZ;

ALTER TABLE dbo.vault_secrets
    ADD COLUMN IF NOT EXISTS last_access_at TIMESTAMPTZ;