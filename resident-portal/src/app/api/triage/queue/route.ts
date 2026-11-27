import { NextResponse } from "next/server";
import { authenticateTriageRequest, TriageAuthError } from "../../../../lib/triageAuth";
import { getTriageRepository, sessionForQueue } from "../../../../lib/triageRepository";

export const runtime = "nodejs";

export async function GET(request: Request) {
  try {
    const session = authenticateTriageRequest(request);
    const url = new URL(request.url);
    const requestedCounty = url.searchParams.get("county")?.trim();
    if (requestedCounty && requestedCounty.toLocaleLowerCase() !== session.countyScope.toLocaleLowerCase()) {
      return NextResponse.json({ error: "County scope denied." }, { status: 403, headers: { "cache-control": "no-store" } });
    }
    const reasonCode = url.searchParams.get("reasonCode")?.trim() || undefined;
    const search = url.searchParams.get("q")?.trim() || undefined;
    if (reasonCode && !["RETRY_EXHAUSTED", "TERMINAL_FAILURE", "UNHANDLED_FAILURE"].includes(reasonCode)) {
      return NextResponse.json({ error: "Invalid filter." }, { status: 400 });
    }
    if (search && search.length > 128) return NextResponse.json({ error: "Invalid filter." }, { status: 400 });
    const items = await getTriageRepository().listManualReviewQueue({ county: session.countyScope, reasonCode, search });
    return NextResponse.json({ items, session: sessionForQueue(session), county: session.countyScope }, {
      status: 200, headers: { "cache-control": "no-store", "vary": "cookie, authorization" },
    });
  } catch (error) {
    const status = error instanceof TriageAuthError ? error.statusCode : 503;
    return NextResponse.json({ error: error instanceof TriageAuthError ? error.message : "Unable to load triage queue." }, { status, headers: { "cache-control": "no-store" } });
  }
}
