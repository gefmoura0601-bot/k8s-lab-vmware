CREATE OR REPLACE FUNCTION account_service.assert_balanced_journal() RETURNS trigger AS $$
DECLARE target UUID := COALESCE(NEW.journal_id, OLD.journal_id);
BEGIN
    IF (
        SELECT COALESCE(sum(signed_amount), 0)
        FROM account_service.ledger_entries
        WHERE journal_id = target
    ) <> 0 THEN
        RAISE EXCEPTION 'unbalanced ledger journal %', target;
    END IF;
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;
