ALTER TABLE accounts
    ADD COLUMN cpf_fingerprint VARCHAR(64),
    ADD COLUMN cpf_last4 VARCHAR(4);

ALTER TABLE accounts
    ADD CONSTRAINT ck_accounts_cpf_pair CHECK (
        (cpf_fingerprint IS NULL AND cpf_last4 IS NULL)
        OR (cpf_fingerprint IS NOT NULL AND cpf_last4 ~ '^[0-9]{4}$')
    );

CREATE UNIQUE INDEX uq_accounts_cpf_fingerprint
    ON accounts(cpf_fingerprint)
    WHERE cpf_fingerprint IS NOT NULL;
