CREATE TABLE ledger_entries (
    id BIGSERIAL PRIMARY KEY,
    journal_id UUID NOT NULL,
    account_id UUID REFERENCES accounts(id),
    signed_amount NUMERIC(19,2) NOT NULL CHECK (signed_amount <> 0),
    entry_type VARCHAR(32) NOT NULL,
    reversal_of UUID,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_ledger_account_created ON ledger_entries(account_id, created_at DESC);
CREATE INDEX idx_ledger_journal ON ledger_entries(journal_id);

CREATE FUNCTION assert_balanced_journal() RETURNS trigger AS $$
DECLARE target UUID := COALESCE(NEW.journal_id, OLD.journal_id);
BEGIN
    IF (SELECT COALESCE(sum(signed_amount), 0) FROM ledger_entries WHERE journal_id = target) <> 0 THEN
        RAISE EXCEPTION 'unbalanced ledger journal %', target;
    END IF;
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;
CREATE CONSTRAINT TRIGGER ledger_journal_must_balance
AFTER INSERT OR UPDATE OR DELETE ON ledger_entries
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION assert_balanced_journal();

INSERT INTO ledger_entries(journal_id, account_id, signed_amount, entry_type)
SELECT id, id, balance, 'OPENING_CREDIT' FROM accounts WHERE balance <> 0;
INSERT INTO ledger_entries(journal_id, account_id, signed_amount, entry_type)
SELECT id, NULL, -balance, 'SYSTEM_OFFSET' FROM accounts WHERE balance <> 0;
