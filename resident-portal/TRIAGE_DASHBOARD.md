# Municipal task force triage dashboard scaffold

The `/triage` page is a PII-minimized operator UI for manual-review escalations. It expects authenticated server endpoints at `/api/triage/queue`, `/api/triage/cases/{eventId}`, and `/api/triage/cases/{eventId}/actions`.

## Security contract for the API adapter

- Derive the actor identity, role, and county scope from the authenticated server session. Do not trust client-supplied role or actor headers; the UI deliberately does not send them or allow role switching.
- Filter the queue server-side to `escalation_target = 'manual_review'` and enforce county scope server-side. The client-side filter is for display only.
- The queue must return trace IDs, reason/attempt metadata, and outbox state without resident PII.
- Only CASEWORKER and ADMIN may fetch the authorized applicant-detail view or submit dispositions. AUDITOR responses must be redacted server-side. UI redaction is not an authorization boundary.
- Validate action event/correlation IDs against the stored case, enforce state transitions transactionally, and append an immutable audit row in the same transaction. Do not accept client actor role/ID as authority.
- Notes can themselves contain sensitive data. Apply access controls, retention limits, and operational policy; never emit notes or applicant detail to application logs or analytics.
- Treat every program result as an estimate pending FSSA/caseworker verification.

## Database migration

Apply `sql/002_triage_audit_log.sql` after `sql/001_intake_state_and_outbox.sql`. The audit table records actor, role, disposition, trace IDs, previous/new state, note, and timestamp. A database trigger rejects updates and deletes; production DB roles should additionally receive INSERT/SELECT only on the audit table.

## Integration status

This change supplies the TypeScript contracts, dashboard components, and audit schema. It does not create the authenticated API handlers or identity/session middleware: those need to be implemented with the deployment's trusted authentication provider and database adapter before the dashboard can load operational records.
