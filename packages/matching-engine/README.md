# `@grant-hub/matching-engine` — Indiana FSSA preliminary screening

This package provides a deterministic, local-only screening prototype for SNAP, TANF, HIP 2.0, and CCDF. It returns preliminary matches, illustrative benefit estimates, and verification checklists; it does not submit applications or make an official eligibility determination.

## Important policy notice

The FPL values, percentage thresholds, deductions, and estimated benefit bands in this prototype are configurable assumptions based on the supplied starter specification. They have not been independently certified as current Indiana FSSA policy. Program rules can vary by household and change over time. Before using these results with residents, have the current rules and calculations reviewed by Indiana FSSA/program experts and label all output as an estimate. A person should not be discouraged from applying based solely on this screening output.

## API

Import `IndianaFssaRulesEngine`, `getMonthlyFPL`, and the profile/result types from `@grant-hub/matching-engine` or `@grant-hub/matching-engine/fssa`.

All income and expense inputs are expressed as monthly dollar amounts. Household size must be at least one, and numeric inputs must be finite and non-negative. `evaluateAll` returns results for each supported program and requested verification documents. Evaluation is deterministic apart from the `evaluatedAt` timestamp and has no network or submission side effects.

## Checks

```sh
pnpm --filter @grant-hub/matching-engine typecheck
pnpm --filter @grant-hub/matching-engine test
```
