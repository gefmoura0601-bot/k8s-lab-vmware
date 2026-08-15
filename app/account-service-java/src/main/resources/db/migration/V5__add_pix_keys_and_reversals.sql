CREATE TABLE pix_keys (
    pix_key UUID PRIMARY KEY,
    account_id UUID NOT NULL UNIQUE REFERENCES accounts(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE processed_transfers ADD COLUMN reversal_of UUID NULL;
CREATE UNIQUE INDEX uq_processed_transfer_reversal
    ON processed_transfers(reversal_of) WHERE reversal_of IS NOT NULL;
ALTER TABLE processed_transfers
    ADD CONSTRAINT fk_processed_transfer_reversal
    FOREIGN KEY (reversal_of) REFERENCES processed_transfers(transaction_id);
