BEGIN;

CREATE TABLE IF NOT EXISTS intake_events (
    event_id TEXT PRIMARY KEY,
    correlation_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('in_progress', 'completed', 'escalated')),
    escalation_target TEXT NULL CHECK (escalation_target IN ('dlq', 'manual_review')),
    reason_code TEXT NULL CHECK (reason_code IS NULL OR reason_code IN ('RETRY_EXHAUSTED', 'TERMINAL_FAILURE', 'UNHANDLED_FAILURE')),
    attempts_made INTEGER NOT NULL DEFAULT 0 CHECK (attempts_made >= 0),
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_intake_events_correlation_id ON intake_events (correlation_id);
CREATE INDEX IF NOT EXISTS idx_intake_events_status ON intake_events (status);

CREATE TABLE IF NOT EXISTS escalation_outbox (
    outbox_id BIGSERIAL PRIMARY KEY,
    event_id TEXT NOT NULL UNIQUE REFERENCES intake_events (event_id),
    correlation_id TEXT NOT NULL,
    intake_id TEXT NOT NULL,
    case_id TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    division INTEGER NOT NULL CHECK (division BETWEEN 1 AND 21),
    target TEXT NOT NULL CHECK (target IN ('dlq', 'manual_review')),
    reason_code TEXT NOT NULL CHECK (reason_code IN ('RETRY_EXHAUSTED', 'TERMINAL_FAILURE', 'UNHANDLED_FAILURE')),
    attempts_made INTEGER NOT NULL CHECK (attempts_made BETWEEN 1 AND 10),
    last_error TEXT NOT NULL CHECK (last_error IN ('RETRY_EXHAUSTED', 'TERMINAL_FAILURE', 'UNHANDLED_FAILURE')),
    -- Routing metadata only. Never place resident profile or other PII here.
    payload JSONB NOT NULL CHECK (jsonb_typeof(payload) = 'object'),
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'publishing', 'published', 'failed')),
    publish_attempts INTEGER NOT NULL DEFAULT 0 CHECK (publish_attempts >= 0),
    next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    locked_by TEXT NULL,
    locked_at TIMESTAMPTZ NULL,
    published_at TIMESTAMPTZ NULL,
    recorded_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_escalation_outbox_due
    ON escalation_outbox (next_attempt_at, outbox_id)
    WHERE status IN ('pending', 'failed');
CREATE INDEX IF NOT EXISTS idx_escalation_outbox_stale_lease
    ON escalation_outbox (locked_at, outbox_id)
    WHERE status = 'publishing';
CREATE INDEX IF NOT EXISTS idx_escalation_outbox_correlation_id
    ON escalation_outbox (correlation_id);

COMMIT;
