ALTER TABLE accounts
    ADD COLUMN account_number VARCHAR(10),
    ADD COLUMN password_hash VARCHAR(100),
    ADD COLUMN failed_login_attempts INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN locked_until TIMESTAMPTZ;

CREATE UNIQUE INDEX uq_accounts_account_number
    ON accounts(account_number)
    WHERE account_number IS NOT NULL;
