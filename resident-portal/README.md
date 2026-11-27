# @beast3/resident-portal

A standalone Next.js/React 18 and TypeScript resident intake UI package. It provides wallet-signed consent, a grant-matcher adapter hook, a privacy-conscious intake form, and a correlation-aware status view.

## Setup

This package is a pnpm workspace member. From the repository root, install with `pnpm install`, then run `pnpm --dir resident-portal typecheck` or `pnpm --dir resident-portal test`. The workspace dependency `@grant-hub/matching-engine` must be available as a sibling workspace package under `packages/*`; its API is intentionally isolated behind the `GrantMatcher` interface in `src/hooks/useGrantEligibility.ts` so the adapter can map the package's actual exported API without guessing.

## EIP-712 consent

`signIntakeConsent` asks a connected wallet to sign typed data with a configured domain (`name`, `version`, `chainId`) and a consent payload that includes intake ID, consent version, purpose, issue time, and expiry. Configure domain values consistently with the verifier. Signing typed data is a wallet message operation and sends no transaction, so it requires no gas. It proves control of an address only; it is not identity verification, an authorization decision, or an EIP-2612 token permit. EIP-2612 is for token allowance permits and must only be added if the application has a specific token contract integration.

Never place private keys in the browser app. Consent verification and identity proofing should be performed by a trusted server component as appropriate; enforce expiry and replay protection server-side (for example, persist consumed consent nonces or use one-time intake IDs).

## Grant eligibility and pipeline integration

Inject an implementation of `GrantMatcher` backed by `@grant-hub/matching-engine` into `useGrantEligibility`. The hook sorts valid normalized scores (0–1) in descending order. Confirm score semantics and local/state program rules with the matching-engine package; displayed results are pre-qualification estimates, not benefit guarantees or agency determinations.

`IntakeForm` emits an `IntakeEnvelope` with a client-generated `correlation_id`. The trusted intake adapter must validate and map the UI model to `HouseholdIntakeV1`, then return server-assigned `event_id` and the canonical `correlation_id`. Feed those values and pipeline status updates into `StatusTracker`; do not fabricate event IDs in the browser. The existing retry/escalation worker must publish authenticated status events for this to be real-time; this UI component alone does not establish a subscription.

## Privacy controls

The form does not log profile values or analytics events. Analytics should emit only fixed status/error codes and correlation identifiers, never income, location, attachment contents, wallet signatures, or raw request/response bodies. Use TLS and a trusted server-side handler with access controls and encryption at rest. Client-side encryption requires a defined recipient-key and rotation protocol; do not treat hashing as encryption or PII scrubbing. A wallet signature is sensitive and should not be sent to analytics.

## Development

```sh
pnpm --dir resident-portal typecheck
pnpm --dir resident-portal test
```
