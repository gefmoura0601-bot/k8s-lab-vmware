CREATE TABLE cards (
    id UUID PRIMARY KEY,
    account_id UUID NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    card_type VARCHAR(10) NOT NULL CHECK (card_type IN ('DEBIT', 'CREDIT')),
    form_factor VARCHAR(10) NOT NULL CHECK (form_factor IN ('VIRTUAL', 'PHYSICAL')),
    status VARCHAR(12) NOT NULL CHECK (status IN ('ACTIVE', 'BLOCKED', 'CANCELLED')),
    pan_fingerprint VARCHAR(64) NOT NULL UNIQUE,
    expiry_month INTEGER NOT NULL CHECK (expiry_month BETWEEN 1 AND 12),
    expiry_year INTEGER NOT NULL CHECK (expiry_year BETWEEN 2020 AND 9999),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    version BIGINT NOT NULL DEFAULT 0,
    CONSTRAINT uq_cards_account_type_factor UNIQUE (account_id, card_type, form_factor)
);

CREATE INDEX idx_cards_account_created ON cards(account_id, created_at DESC);

CREATE TABLE card_credit_lines (
    account_id UUID PRIMARY KEY REFERENCES accounts(id) ON DELETE CASCADE,
    credit_limit NUMERIC(19,2) NOT NULL CHECK (credit_limit > 0),
    used_amount NUMERIC(19,2) NOT NULL DEFAULT 0 CHECK (used_amount >= 0),
    version BIGINT NOT NULL DEFAULT 0,
    CONSTRAINT ck_card_credit_line_limit CHECK (used_amount <= credit_limit)
);

CREATE TABLE card_payments (
    payment_id UUID PRIMARY KEY,
    card_id UUID REFERENCES cards(id) ON DELETE CASCADE,
    account_id UUID REFERENCES accounts(id) ON DELETE CASCADE,
    merchant_id VARCHAR(80) NOT NULL,
    merchant_name VARCHAR(120) NOT NULL,
    order_reference VARCHAR(120) NOT NULL,
    amount NUMERIC(19,2) NOT NULL CHECK (amount > 0),
    currency VARCHAR(3) NOT NULL,
    payment_type VARCHAR(10) NOT NULL CHECK (payment_type IN ('DEBIT', 'CREDIT')),
    card_type VARCHAR(10) CHECK (card_type IN ('DEBIT', 'CREDIT')),
    installments INTEGER NOT NULL CHECK (installments BETWEEN 1 AND 12),
    status VARCHAR(12) NOT NULL CHECK (status IN ('CAPTURED', 'DECLINED')),
    authorization_code VARCHAR(6),
    decline_code VARCHAR(40),
    request_fingerprint VARCHAR(64) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT ck_card_payment_result CHECK (
        (status = 'CAPTURED' AND authorization_code IS NOT NULL AND decline_code IS NULL)
        OR (status = 'DECLINED' AND authorization_code IS NULL AND decline_code IS NOT NULL)
    )
);

CREATE INDEX idx_card_payments_account_created
    ON card_payments(account_id, created_at DESC);
CREATE INDEX idx_card_payments_card_created
    ON card_payments(card_id, created_at DESC);
