import { createHmac, randomUUID, timingSafeEqual } from "node:crypto";
import { NextRequest, NextResponse } from "next/server";
import type { Address, Hex } from "viem";
import { verifyIntakeConsent, type ConsentDomain } from "../../../../auth/eip712Signer";
import type { IntakeEnvelope, SignedConsentPayload } from "../../../../types/portal";

export const runtime = "nodejs";

const MAX_BODY_BYTES = 64 * 1024;
const SAFE_ID = /^[A-Za-z0-9._:-]{8,128}$/;
const SIGNATURE = /^0x[0-9a-fA-F]{130}$/;
const KNOWN_FOCUS_AREAS = new Set([
  "housing", "food", "utilities", "healthcare", "veteran_support",
]);

type ErrorCode =
  | "INVALID_REQUEST"
  | "CONSENT_INVALID"
  | "SERVICE_UNAVAILABLE"
  | "WORKER_DISPATCH_FAILED";

function jsonError(status: number, reasonCode: ErrorCode, correlationId: string) {
  return NextResponse.json(
    { error: "Intake could not be processed.", reasonCode, correlation_id: correlationId },
    { status, headers: { "x-correlation-id": correlationId, "cache-control": "no-store" } },
  );
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isProfile(value: unknown): boolean {
  if (!isRecord(value) || !isRecord(value.location)) return false;
  const { householdSize, grossMonthlyIncomeCents, targetFocusAreas } = value;
  const { state, county, city } = value.location;
  return Number.isSafeInteger(householdSize) && Number(householdSize) >= 1 && Number(householdSize) <= 30
    && Number.isSafeInteger(grossMonthlyIncomeCents) && Number(grossMonthlyIncomeCents) >= 0
    && typeof state === "string" && state.trim().length >= 2 && state.length <= 32
    && typeof county === "string" && county.trim().length >= 2 && county.length <= 64
    && typeof city === "string" && city.trim().length >= 2 && city.length <= 64
    && Array.isArray(targetFocusAreas) && targetFocusAreas.length > 0
    && targetFocusAreas.every((area) => typeof area === "string" && KNOWN_FOCUS_AREAS.has(area));
}

function parseConsent(value: unknown): SignedConsentPayload | null {
  if (!isRecord(value)
    || typeof value.intakeId !== "string"
    || typeof value.consentVersion !== "string"
    || typeof value.purpose !== "string"
    || typeof value.issuedAt !== "string"
    || typeof value.expiresAt !== "string") return null;
  if (!/^\d{1,20}$/.test(value.issuedAt) || !/^\d{1,20}$/.test(value.expiresAt)) return null;
  try {
    return {
      intakeId: value.intakeId,
      consentVersion: value.consentVersion,
      purpose: value.purpose,
      issuedAt: BigInt(value.issuedAt),
      expiresAt: BigInt(value.expiresAt),
    };
  } catch {
    return null;
  }
}

function requiredEnv(name: string): string {
  const value = process.env[name];
  if (!value || !value.trim()) throw new Error("CONFIGURATION_MISSING");
  return value;
}

function makeDomain(): ConsentDomain {
  const chainId = Number(requiredEnv("INTAKE_EIP712_CHAIN_ID"));
  if (!Number.isSafeInteger(chainId) || chainId < 1) throw new Error("CONFIGURATION_INVALID");
  return {
    name: requiredEnv("INTAKE_EIP712_DOMAIN_NAME"),
    version: requiredEnv("INTAKE_EIP712_DOMAIN_VERSION"),
    chainId,
  };
}

function computeHmac(secret: string, timestamp: string, body: string): string {
  return createHmac("sha256", secret).update(`${timestamp}.${body}`, "utf8").digest("hex");
}

function secureEqualHex(expected: string, received: string): boolean {
  if (!/^[0-9a-f]{64}$/.test(received) || !/^[0-9a-f]{64}$/.test(expected)) return false;
  return timingSafeEqual(Buffer.from(expected, "hex"), Buffer.from(received, "hex"));
}

export async function POST(request: NextRequest) {
  const suppliedCorrelationId = request.headers.get("x-correlation-id");
  if (suppliedCorrelationId && !SAFE_ID.test(suppliedCorrelationId)) {
    return jsonError(400, "INVALID_REQUEST", randomUUID());
  }
  const correlationId = suppliedCorrelationId ?? `corr_${randomUUID()}`;
  const eventId = `evt_${randomUUID()}`;
  const contentLength = Number(request.headers.get("content-length") ?? 0);
  if (contentLength > MAX_BODY_BYTES) return jsonError(413, "INVALID_REQUEST", correlationId);

  let rawBody: string;
  let body: unknown;
  try {
    rawBody = await request.text();
    if (Buffer.byteLength(rawBody, "utf8") > MAX_BODY_BYTES) {
      return jsonError(413, "INVALID_REQUEST", correlationId);
    }
    body = JSON.parse(rawBody);
  } catch {
    return jsonError(400, "INVALID_REQUEST", correlationId);
  }

  if (!isRecord(body) || !isRecord(body.intake) || !isRecord(body.consent)
    || typeof body.signature !== "string" || !SIGNATURE.test(body.signature)
    || typeof body.signerAddress !== "string" || !/^0x[0-9a-fA-F]{40}$/.test(body.signerAddress)) {
    return jsonError(400, "INVALID_REQUEST", correlationId);
  }

  const intake = body.intake;
  const consent = parseConsent(body.consent);
  if (intake.schemaVersion !== "1.0.0"
    || typeof intake.intakeId !== "string" || !SAFE_ID.test(intake.intakeId)
    || typeof intake.submittedAt !== "string"
    || Number.isNaN(Date.parse(intake.submittedAt))
    || !isProfile(intake.profile)
    || !consent || consent.intakeId !== intake.intakeId
    || consent.purpose !== "resident_intake_submission") {
    return jsonError(400, "INVALID_REQUEST", correlationId);
  }

  let secret: string;
  let workerUrl: URL;
  let domain: ConsentDomain;
  try {
    secret = requiredEnv("INTAKE_WEBHOOK_SECRET");
    if (Buffer.byteLength(secret, "utf8") < 32) throw new Error("CONFIGURATION_INVALID");
    workerUrl = new URL(requiredEnv("INTAKE_WORKER_URL"));
    if (workerUrl.protocol !== "https:" && workerUrl.hostname !== "localhost" && workerUrl.hostname !== "127.0.0.1") {
      throw new Error("CONFIGURATION_INVALID");
    }
    domain = makeDomain();
  } catch {
    return jsonError(503, "SERVICE_UNAVAILABLE", correlationId);
  }

  const nowSeconds = BigInt(Math.floor(Date.now() / 1000));
  if (consent.issuedAt > nowSeconds + 300n || consent.expiresAt <= nowSeconds
    || consent.expiresAt - consent.issuedAt > 86_400n) {
    return jsonError(401, "CONSENT_INVALID", correlationId);
  }
  let consentValid = false;
  try {
    consentValid = await verifyIntakeConsent(
      domain,
      consent,
      body.signature as Hex,
      body.signerAddress as Address,
    );
  } catch {
    consentValid = false;
  }
  if (!consentValid) return jsonError(401, "CONSENT_INVALID", correlationId);

  const timestamp = Math.floor(Date.now() / 1000).toString();
  const envelope = {
    event_id: eventId,
    correlation_id: correlationId,
    intake_id: intake.intakeId,
    payload: { intake, consent: body.consent, signature: body.signature, signer_address: body.signerAddress },
    attempt: 0,
  };
  const serializedEnvelope = JSON.stringify(envelope);
  const signature = computeHmac(secret, timestamp, serializedEnvelope);

  let response: Response;
  try {
    response = await fetch(workerUrl, {
      method: "POST",
      headers: {
        "content-type": "application/json",
        "x-correlation-id": correlationId,
        "x-event-id": eventId,
        "x-request-timestamp": timestamp,
        "x-signature-256": `sha256=${signature}`,
      },
      body: serializedEnvelope,
      cache: "no-store",
      signal: AbortSignal.timeout(8_000),
    });
  } catch {
    return jsonError(502, "WORKER_DISPATCH_FAILED", correlationId);
  }
  if (!response.ok) return jsonError(502, "WORKER_DISPATCH_FAILED", correlationId);

  let workerResult: unknown;
  try {
    workerResult = await response.json();
  } catch {
    return jsonError(502, "WORKER_DISPATCH_FAILED", correlationId);
  }
  const resultStatus = isRecord(workerResult) ? workerResult.status : undefined;
  const status = resultStatus === "success" || resultStatus === "COMPLETED"
    ? "COMPLETED"
    : resultStatus === "escalated_to_manual_review" || resultStatus === "ESCALATED"
      ? "ESCALATED"
      : "QUEUED";

  // Log only fixed status and opaque trace identifiers, never request/response data.
  console.info(JSON.stringify({ level: "INFO", code: "INTAKE_DISPATCHED", correlation_id: correlationId, event_id: eventId, status }));
  return NextResponse.json(
    { status, eventId, correlationId },
    { status: 202, headers: { "x-correlation-id": correlationId, "cache-control": "no-store" } },
  );
}
