# Pull request description — resident portal intake boundary

## Summary

This branch adds a resident-facing Next.js/React portal and a signed intake boundary. The UI captures household and location information, obtains an EIP-712 wallet signature for consent, emits fixed error/status codes, and displays application progress using opaque event and correlation identifiers. The Next.js route validates the submitted shape and consent, then signs the exact worker-envelope bytes with timestamp-bound HMAC-SHA256. The FastAPI gateway verifies the signature in constant time before parsing and hands the validated envelope to an injected application handler.

The Python intake guard validates and quarantines intake payloads without echoing submitted values. Portal Phase A triage is local and side-effect-free, and the outbox relay added here delivers persisted escalation records with at-least-once semantics.

## Outbox contract and operational dependencies

- The escalation transaction must atomically persist the case-state change and outbox record before the direct DLQ publish attempt.
- `OutboxStore.fetch_pending_outbox(limit)` must return stable, oldest-first, unacknowledged records. Concurrent relays need database leases/locking or equivalent claiming to avoid redundant parallel delivery.
- `DLQPublisher.publish_dlq(...)` must be idempotent on `outbox_id`. A publish may succeed while its acknowledgement or subsequent DB mark fails, so the relay can redeliver.
- `OutboxStore.mark_outbox_published(outbox_id=...)` must be durable and idempotent.
- Configure a strong `INTAKE_WEBHOOK_SECRET` (at least 32 UTF-8 bytes), matching EIP-712 domain variables, HTTPS `INTAKE_WORKER_URL`, alerting, dead-letter retention, and shutdown supervision. Never use development fallback secrets.
- The matching engine remains an injected adapter and must be available as a workspace package before installing the frontend workspace.

## Testing

Run Python unit tests with `python -m unittest discover -s tests`. In the frontend workspace run `pnpm --dir resident-portal typecheck` and `pnpm --dir resident-portal test`. Review runtime integration tests against the actual database, broker, wallet, and worker adapter before production deployment.

## Triage checklist

- [ ] Verify TypeScript typecheck, frontend tests, and all Python unit tests.
- [ ] Confirm the workspace contains the intended `@grant-hub/matching-engine` package and its score semantics; eligibility is informational, not an agency determination.
- [ ] Review EIP-712 domain, consent version/purpose/expiry, signer recovery, replay protection, and server-side identity-proofing boundary.
- [ ] Confirm all public ingress is TLS-protected; set request limits, rate limits, authentication/authorization, and secret rotation procedures.
- [ ] Confirm timestamped HMAC format is identical on sender and receiver and reject stale/replayed webhook events with durable event-id deduplication.
- [ ] Ensure application handler connects validated envelopes to the actual case/idempotency store, worker, DLQ publisher, and state updates. The gateway returns unavailable when no handler is injected.
- [ ] Ensure state transition and outbox write are atomic; test broker outage, duplicate delivery, mark failure, concurrent relay instances, and process restart.
- [ ] Configure idempotent `outbox_id` handling and relay ownership/locking, retry monitoring, backlog alerts, retention, and graceful shutdown.
- [ ] Verify logs contain only fixed reason/status codes and opaque identifiers; inspect frontend analytics and error reporting for PII/signatures.
- [ ] Confirm the displayed benefit estimates and program eligibility rules have legal/program-owner approval and are labeled estimates.
- [ ] Verify deployment environment variables, package-manager workspace lockfile, build/start commands, and security review sign-off.

## Not yet implied by this scaffold

The branch defines integration contracts; it does not itself provide production database/broker adapters, relay process supervision/deployment, durable replay protection, rate limiting, or a complete worker handler. Those must be supplied and exercised in the target environment before calling the system production-ready.
