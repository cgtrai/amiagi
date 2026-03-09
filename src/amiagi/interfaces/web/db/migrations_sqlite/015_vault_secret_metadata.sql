ALTER TABLE vault_secrets ADD COLUMN secret_type TEXT NOT NULL DEFAULT 'api_key';
ALTER TABLE vault_secrets ADD COLUMN expires_at TIMESTAMP;
ALTER TABLE vault_secrets ADD COLUMN last_access_at TIMESTAMP;