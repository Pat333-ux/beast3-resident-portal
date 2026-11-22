import { afterEach, describe, expect, it } from "vitest";
import { createHmac } from "node:crypto";
import { assertCanMutateTriage, authenticateTriageRequest, TriageAuthError } from "../src/lib/triageAuth";

const originalSecret = process.env.TRIAGE_SESSION_SECRET;
const secret = "test-secret-with-more-than-thirty-two-bytes";
process.env.TRIAGE_SESSION_SECRET = secret;

afterEach(() => {
  if (originalSecret === undefined) delete process.env.TRIAGE_SESSION_SECRET;
  else process.env.TRIAGE_SESSION_SECRET = originalSecret;
});

function token(role: string, overrides: Record<string, unknown> = {}) {
  const now = Math.floor(Date.now() / 1000);
  const payload = Buffer.from(JSON.stringify({ actorId: "actor-123", role, countyScope: "Vigo", iat: now, exp: now + 300, ...overrides })).toString("base64url");
  const sig = createHmac("sha256", secret).update(payload).digest("hex");
  return `${payload}.${sig}`;
}

describe("triage server authentication", () => {
  it("verifies signed session claims and county scope", () => {
    const request = new Request("https://portal.example/api/triage/queue", { headers: { authorization: `Bearer ${token("CASEWORKER")}` } });
    expect(authenticateTriageRequest(request)).toMatchObject({ actorId: "actor-123", role: "CASEWORKER", countyScope: "Vigo" });
  });

  it("rejects tampered, expired, and missing-exp tokens", () => {
    const good = token("AUDITOR");
    const tampered = `${good.slice(0, -1)}0`;
    for (const value of [tampered, token("AUDITOR", { exp: Math.floor(Date.now() / 1000) - 1 }), token("AUDITOR", { exp: undefined })]) {
      const request = new Request("https://portal.example/api/triage/queue", { headers: { authorization: `Bearer ${value}` } });
      expect(() => authenticateTriageRequest(request)).toThrow(TriageAuthError);
    }
  });

  it("enforces read-only auditor role for actions", () => {
    const request = new Request("https://portal.example/api/triage/queue", { headers: { authorization: `Bearer ${token("AUDITOR")}` } });
    const session = authenticateTriageRequest(request);
    expect(() => assertCanMutateTriage(session)).toThrowError(/read-only/);
  });
});
