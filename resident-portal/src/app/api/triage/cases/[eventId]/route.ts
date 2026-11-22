import { NextResponse } from "next/server";
import { authenticateTriageRequest, TriageAuthError } from "../../../../../lib/triageAuth";
import { getTriageRepository } from "../../../../../lib/triageRepository";

export const runtime = "nodejs";

export async function GET(request: Request, context: { params: { eventId: string } }) {
  try {
    const session = authenticateTriageRequest(request);
    const eventId = context.params.eventId;
    if (!/^[A-Za-z0-9._:-]{8,128}$/.test(eventId)) {
      return NextResponse.json({ error: "Invalid case identifier." }, { status: 400, headers: { "cache-control": "no-store" } });
    }
    const result = await getTriageRepository().getCaseDetail({ eventId, countyScope: session.countyScope, role: session.role });
    if (!result) return NextResponse.json({ error: "Case not found." }, { status: 404, headers: { "cache-control": "no-store" } });
    return NextResponse.json({ ...result, role: session.role }, { status: 200, headers: { "cache-control": "no-store", "vary": "cookie, authorization" } });
  } catch (error) {
    const status = error instanceof TriageAuthError ? error.statusCode : 503;
    return NextResponse.json({ error: error instanceof TriageAuthError ? error.message : "Unable to load case details." }, { status, headers: { "cache-control": "no-store" } });
  }
}
