import { NextResponse } from "next/server";
import { assertCanMutateTriage, assertSameOriginMutation, authenticateTriageRequest, TriageAuthError } from "../../../../../../lib/triageAuth";
import { getTriageRepository } from "../../../../../../lib/triageRepository";
import type { TriageDisposition } from "../../../../../../types/triage";

export const runtime = "nodejs";
const VALID = new Set<TriageDisposition>(["CLAIM_CASE", "REQUEST_VERIFICATION", "ROUTE_FSSA_PATHWAY", "MARK_RESOLVED"]);

export async function POST(request: Request, context: { params: { eventId: string } }) {
  try {
    const session = authenticateTriageRequest(request);
    assertCanMutateTriage(session);
    assertSameOriginMutation(request, session);

    const eventId = context.params.eventId;
    if (!/^[A-Za-z0-9._:-]{8,128}$/.test(eventId)) {
      return NextResponse.json({ error: "Invalid case identifier." }, { status: 400, headers: { "cache-control": "no-store" } });
    }
    const contentLength = Number(request.headers.get("content-length") ?? 0);
    if (contentLength > 8192) return NextResponse.json({ error: "Request too large." }, { status: 413 });
    let body: unknown;
    try {
      const text = await request.text();
      if (Buffer.byteLength(text, "utf8") > 8192) return NextResponse.json({ error: "Request too large." }, { status: 413 });
      body = JSON.parse(text);
    } catch {
      return NextResponse.json({ error: "Invalid request." }, { status: 400 });
    }
    if (typeof body !== "object" || body === null || Array.isArray(body)) {
      return NextResponse.json({ error: "Invalid request." }, { status: 400 });
    }
    const payload = body as Record<string, unknown>;
    const correlationId = payload.correlationId;
    const disposition = payload.disposition;
    const note = payload.note;
    if (typeof correlationId !== "string" || !/^[A-Za-z0-9._:-]{8,128}$/.test(correlationId)
      || typeof disposition !== "string" || !VALID.has(disposition as TriageDisposition)
      || typeof note !== "string" || !note.trim() || note.length > 2000) {
      return NextResponse.json({ error: "Invalid action." }, { status: 400, headers: { "cache-control": "no-store" } });
    }

    const result = await getTriageRepository().recordAuditAction({
      eventId, correlationId, disposition: disposition as TriageDisposition, note,
      actorId: session.actorId, actorRole: session.role, countyScope: session.countyScope,
    });
    if (!result) return NextResponse.json({ error: "Case not found." }, { status: 404, headers: { "cache-control": "no-store" } });
    return NextResponse.json(result, { status: 201, headers: { "cache-control": "no-store" } });
  } catch (error) {
    const status = error instanceof TriageAuthError ? error.statusCode : 503;
    return NextResponse.json({ error: error instanceof TriageAuthError ? error.message : "Unable to record triage action." }, { status, headers: { "cache-control": "no-store" } });
  }
}
