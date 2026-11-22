BEGIN;

CREATE TABLE IF NOT EXISTS triage_audit_log (
    audit_id TEXT PRIMARY KEY,
    event_id TEXT NOT NULL REFERENCES intake_events (event_id),
    correlation_id TEXT NOT NULL,
    disposition TEXT NOT NULL CHECK (
        disposition IN ('CLAIM_CASE', 'REQUEST_VERIFICATION', 'ROUTE_FSSA_PATHWAY', 'MARK_RESOLVED')
    ),
    actor_id TEXT NOT NULL CHECK (length(actor_id) BETWEEN 1 AND 128),
    actor_role TEXT NOT NULL CHECK (actor_role IN ('CASEWORKER', 'AUDITOR', 'ADMIN')),
    previous_status TEXT NOT NULL CHECK (length(previous_status) BETWEEN 1 AND 64),
    new_status TEXT NOT NULL CHECK (length(new_status) BETWEEN 1 AND 64),
    note TEXT NOT NULL CHECK (length(note) BETWEEN 1 AND 2000),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_triage_audit_log_event_created
    ON triage_audit_log (event_id, created_at DESC, audit_id);
CREATE INDEX IF NOT EXISTS idx_triage_audit_log_correlation_id
    ON triage_audit_log (correlation_id);

-- Prevent accidental mutation through the application database role.
CREATE OR REPLACE FUNCTION reject_triage_audit_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'triage audit entries are append-only';
END;
$$;

DROP TRIGGER IF EXISTS triage_audit_log_immutable ON triage_audit_log;
CREATE TRIGGER triage_audit_log_immutable
    BEFORE UPDATE OR DELETE ON triage_audit_log
    FOR EACH ROW EXECUTE FUNCTION reject_triage_audit_mutation();

COMMIT;
