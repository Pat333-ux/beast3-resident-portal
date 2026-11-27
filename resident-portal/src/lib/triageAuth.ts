import { createHmac, timingSafeEqual } from "node:crypto";
import type { TriageRole } from "../types/triage";

export interface VerifiedTriageSession {
  actorId: string;
  role: TriageRole;
  countyScope: string;
  credential: "bearer" | "cookie";
}

export class TriageAuthError extends Error {
  constructor(message: string, public readonly statusCode: number = 401) {
    super(message);
    this.name = "TriageAuthError";
  }
}

const ROLES = new Set<TriageRole>(["CASEWORKER", "AUDITOR", "ADMIN"]);
const SAFE_CLAIM = /^[\p{L}\p{N} ._'@:-]{1,128}$/u;
const SAFE_COUNTY = /^[\p{L}\p{N} .'-]{2,64}$/u;

function unauthorized(): never {
  throw new TriageAuthError("Authentication required.", 401);
}

function readToken(request: Request): { token: string; credential: "bearer" | "cookie" } {
  const authorization = request.headers.get("authorization");
  if (authorization) {
    const match = /^Bearer ([A-Za-z0-9._-]+)$/.exec(authorization);
    if (!match) unauthorized();
    return { token: match[1], credential: "bearer" };
  }
  const cookies = request.headers.get("cookie") ?? "";
  const cookie = cookies.split(";").map((part) => part.trim())
    .find((part) => part.startsWith("beast3_triage_session="));
  if (!cookie) unauthorized();
  try {
    return { token: decodeURIComponent(cookie.slice("beast3_triage_session=".length)), credential: "cookie" };
  } catch {
    unauthorized();
  }
}

export function authenticateTriageRequest(request: Request): VerifiedTriageSession {
  const secret = process.env.TRIAGE_SESSION_SECRET ?? "";
  if (Buffer.byteLength(secret, "utf8") < 32) {
    throw new TriageAuthError("Triage authentication is unavailable.", 503);
  }
  const { token, credential } = readToken(request);
  const parts = token.split(".");
  if (parts.length !== 2 || !/^[A-Za-z0-9_-]+$/.test(parts[0]) || !/^[0-9a-f]{64}$/.test(parts[1])) unauthorized();

  const [encodedPayload, providedHex] = parts;
  const expected = createHmac("sha256", secret).update(encodedPayload, "utf8").digest();
  const provided = Buffer.from(providedHex, "hex");
  if (provided.length !== expected.length || !timingSafeEqual(provided, expected)) unauthorized();

  let claims: unknown;
  try {
    const decoded = Buffer.from(encodedPayload, "base64url");
    if (decoded.toString("base64url") !== encodedPayload) unauthorized();
    claims = JSON.parse(decoded.toString("utf8"));
  } catch {
    unauthorized();
  }
  if (typeof claims !== "object" || claims === null || Array.isArray(claims)) unauthorized();
  const payload = claims as Record<string, unknown>;
  const now = Math.floor(Date.now() / 1000);
  if (!Number.isSafeInteger(payload.exp) || (payload.exp as number) <= now) unauthorized();
  if (!Number.isSafeInteger(payload.iat) || (payload.iat as number) > now + 60) unauthorized();
  if ((payload.exp as number) - (payload.iat as number) > 12 * 60 * 60) unauthorized();
  if (typeof payload.actorId !== "string" || !SAFE_CLAIM.test(payload.actorId)) unauthorized();
  if (typeof payload.role !== "string" || !ROLES.has(payload.role as TriageRole)) {
    throw new TriageAuthError("Triage role is not permitted.", 403);
  }
  if (typeof payload.countyScope !== "string" || !SAFE_COUNTY.test(payload.countyScope)) unauthorized();

  return {
    actorId: payload.actorId,
    role: payload.role as TriageRole,
    countyScope: payload.countyScope,
    credential,
  };
}

export function assertCanMutateTriage(session: VerifiedTriageSession): void {
  if (session.role !== "CASEWORKER" && session.role !== "ADMIN") {
    throw new TriageAuthError("This role is read-only.", 403);
  }
}

export function assertSameOriginMutation(request: Request, session: VerifiedTriageSession): void {
  if (session.credential !== "cookie") return;
  const origin = request.headers.get("origin");
  if (!origin || origin !== new URL(request.url).origin) {
    throw new TriageAuthError("Cross-origin request rejected.", 403);
  }
}
