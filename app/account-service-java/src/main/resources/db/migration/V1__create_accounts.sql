CREATE TABLE accounts (
    id UUID PRIMARY KEY,
    owner_name VARCHAR(120) NOT NULL,
    balance NUMERIC(19,2) NOT NULL CHECK (balance >= 0),
    created_at TIMESTAMPTZ NOT NULL,
    version BIGINT NOT NULL DEFAULT 0
);

CREATE TABLE processed_transfers (
    transaction_id UUID PRIMARY KEY,
    source_account_id UUID NOT NULL REFERENCES accounts(id),
    destination_account_id UUID NOT NULL REFERENCES accounts(id),
    amount NUMERIC(19,2) NOT NULL CHECK (amount > 0),
    processed_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX idx_processed_transfers_source ON processed_transfers(source_account_id);
CREATE INDEX idx_processed_transfers_destination ON processed_transfers(destination_account_id);
