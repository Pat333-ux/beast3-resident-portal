# Pull request description — resident portal intake boundary

## Summary

This branch adds a resident-facing Next.js/React portal, EIP-712 consent signing, an HMAC-authenticated FastAPI intake boundary, and a PostgreSQL-backed event state/outbox adapter. The browser emits a strict intake model and opaque correlation identifier; the server validates consent and profile structure, generates the event ID, and signs the exact downstream envelope with timestamp-bound HMAC-SHA256. Python verifies HMAC before parsing, and only an injected application handler can pass validated submissions into the processing pipeline.

Escalation state and a PII-minimized outbox record are persisted atomically. `OutboxRelay` claims batches via PostgreSQL `FOR UPDATE SKIP LOCKED`, publishes at-least-once, marks acknowledged rows, reschedules publish failures with backoff, and recovers stale leases.

## Persistence and delivery contract

- Apply `sql/001_intake_state_and_outbox.sql` before starting the API or relay.
- `PostgresStateStore.record_escalation_with_outbox(...)` commits the event state and routing-only outbox metadata in one transaction. Resident profile data is not serialized into the outbox.
- Outbox claims are leased to a worker ID. `mark_outbox_published` and `mark_outbox_failed` only settle records owned by that worker.
- Delivery is at-least-once. `DLQPublisher` and downstream consumers must deduplicate on stable `outbox_id`/`event_id`; an acknowledgement can be lost after a successful publish.
- Run a strong `INTAKE_WEBHOOK_SECRET` of at least 32 bytes, matching EIP-712 domain variables, HTTPS `INTAKE_WORKER_URL`, database credentials with least privilege, secret rotation, alerts, retention, and shutdown supervision.
- The application-specific intake handler and production DLQ/broker implementation must be injected; they are not provisioned by this branch.

## Testing

Run `python -m unittest discover -s tests`. Frontend checks: `pnpm --dir resident-portal typecheck` and `pnpm --dir resident-portal test`. Validate PostgreSQL migration, concurrent claims, lease recovery, rollback, and real broker idempotency in integration tests before deployment.

## Triage checklist

- [ ] Verify TypeScript typecheck, frontend tests, and all Python unit tests.
- [ ] Review EIP-712 domain, consent version/purpose/expiry, signer recovery, replay protection, and identity-proofing boundary.
- [ ] Confirm TLS ingress, request/rate limits, HMAC timestamp skew and durable event replay protection.
- [ ] Verify application handler links validated intake to the idempotency store, worker, state store, and DLQ publisher.
- [ ] Apply migration and verify DB grants, backups, retention, and schema rollback strategy.
- [ ] Confirm escalation state/outbox atomicity, payload PII minimization, `SKIP LOCKED` multi-worker claiming, ownership checks, stale lease recovery, and retry backoff.
- [ ] Test broker outage, duplicate delivery, ack loss, process restart, and concurrent relays; confirm publisher idempotency.
- [ ] Inspect logs and analytics for raw intake values, signatures, or exception text.
- [ ] Confirm matching-engine workspace availability and approved program rules; present eligibility as estimates, not determinations.
- [ ] Confirm environment variables, deployment supervision, monitoring, and security review sign-off.

Production readiness still requires concrete deployment configuration, integration testing against the target PostgreSQL/broker, durable request replay protection, rate limiting, and operator procedures.
