"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { CaseInspectorDrawer } from "../../components/CaseInspectorDrawer";
import { TriageQueueTable } from "../../components/TriageQueueTable";
import type {
  RedactedApplicantSummary,
  TriageAuditEntry,
  TriageDisposition,
  TriageQueueItem,
  TriageRole,
  TriageSessionView,
} from "../../types/triage";

const DEFAULT_SESSION: TriageSessionView = { actorId: "", role: "AUDITOR", countyScope: "Assigned county" };

function isRole(value: unknown): value is TriageRole {
  return value === "CASEWORKER" || value === "ADMIN" || value === "AUDITOR";
}

export default function TriageDashboardPage() {
  const [session, setSession] = useState(DEFAULT_SESSION);
  const [items, setItems] = useState<TriageQueueItem[]>([]);
  const [detail, setDetail] = useState<RedactedApplicantSummary | null>(null);
  const [auditLog, setAuditLog] = useState<TriageAuditEntry[]>([]);
  const [reasonFilter, setReasonFilter] = useState("ALL");
  const [search, setSearch] = useState("");
  const [selectedEventId, setSelectedEventId] = useState<string | null>(null);
  const [loadingQueue, setLoadingQueue] = useState(true);
  const [loadingDetail, setLoadingDetail] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [errorCode, setErrorCode] = useState<string | null>(null);

  const loadQueue = useCallback(async () => {
    setLoadingQueue(true);
    setErrorCode(null);
    try {
      const response = await fetch("/api/triage/queue?target=manual_review", { cache: "no-store" });
      if (!response.ok) throw new Error("QUEUE_UNAVAILABLE");
      const data: unknown = await response.json();
      if (!data || typeof data !== "object") throw new Error("QUEUE_INVALID");
      const record = data as { items?: unknown; session?: unknown };
      if (!Array.isArray(record.items)) throw new Error("QUEUE_INVALID");
      setItems(record.items as TriageQueueItem[]);
      if (record.session && typeof record.session === "object") {
        const actor = record.session as Partial<TriageSessionView>;
        if (typeof actor.actorId === "string" && typeof actor.countyScope === "string" && isRole(actor.role)) {
          setSession({ actorId: actor.actorId, countyScope: actor.countyScope, role: actor.role });
        }
      }
    } catch {
      setErrorCode("QUEUE_UNAVAILABLE");
      setItems([]);
    } finally {
      setLoadingQueue(false);
    }
  }, []);

  const loadCase = useCallback(async (eventId: string) => {
    setSelectedEventId(eventId);
    setLoadingDetail(true);
    setDetail(null);
    setAuditLog([]);
    try {
      const response = await fetch(`/api/triage/cases/${encodeURIComponent(eventId)}`, { cache: "no-store" });
      if (!response.ok) throw new Error("CASE_UNAVAILABLE");
      const data = await response.json();
      setDetail(data.summary ?? null);
      setAuditLog(Array.isArray(data.auditLog) ? data.auditLog : []);
    } catch {
      setErrorCode("CASE_UNAVAILABLE");
    } finally {
      setLoadingDetail(false);
    }
  }, []);

  useEffect(() => { void loadQueue(); }, [loadQueue]);

  async function submitAction(eventId: string, correlationId: string, disposition: TriageDisposition, note: string) {
    if (session.role === "AUDITOR") throw new Error("READ_ONLY");
    setSubmitting(true);
    try {
      const response = await fetch(`/api/triage/cases/${encodeURIComponent(eventId)}/actions`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ eventId, correlationId, disposition, note }),
      });
      if (!response.ok) throw new Error("ACTION_REJECTED");
      await Promise.all([loadQueue(), loadCase(eventId)]);
    } finally {
      setSubmitting(false);
    }
  }

  const reasons = useMemo(() => ["ALL", ...new Set(items.map((item) => item.reasonCode).filter(Boolean)).values()].sort(), [items]);
  const filtered = useMemo(() => {
    const query = search.trim().toLowerCase();
    return items.filter((item) => (reasonFilter === "ALL" || item.reasonCode === reasonFilter)
      && (!query || [item.eventId, item.correlationId, item.reasonCode].some((value) => value.toLowerCase().includes(query))));
  }, [items, reasonFilter, search]);
  const unpublished = items.filter((item) => item.outboxStatus !== null && item.outboxStatus !== "published").length;

  return (
    <main className="mx-auto max-w-7xl space-y-6 p-6">
      <header className="flex flex-wrap items-end justify-between gap-4 border-b pb-4">
        <div>
          <p className="text-xs font-semibold uppercase tracking-wide text-slate-500">BEAST 3.0 · Municipal task force · {session.countyScope}</p>
          <h1 className="text-2xl font-bold">Manual Review Triage</h1>
          <p className="mt-1 text-sm text-slate-600">Program pre-screens are estimates only, not official eligibility determinations.</p>
        </div>
        <div className="flex items-center gap-3">
          <span className="text-xs">{session.actorId || "Authenticated operator"} · {session.role}</span>
          <button type="button" onClick={() => void loadQueue()} className="rounded border px-3 py-2 text-xs font-semibold">Refresh</button>
        </div>
      </header>

      {errorCode && <div role="alert" className="rounded border border-red-300 bg-red-50 px-4 py-3 text-sm">{errorCode}</div>}
      <section aria-label="Queue summary" className="grid gap-4 sm:grid-cols-2">
        <div className="rounded border bg-white p-4"><p className="text-xs text-slate-500">Manual-review cases</p><p className="text-2xl font-bold">{filtered.length}</p></div>
        <div className="rounded border bg-white p-4"><p className="text-xs text-slate-500">Outbox not published</p><p className="text-2xl font-bold text-amber-700">{unpublished}</p></div>
      </section>
      <section aria-label="Queue filters" className="flex flex-wrap gap-3">
        <label className="sr-only" htmlFor="triage-search">Search trace IDs or reason</label>
        <input id="triage-search" type="search" value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Search event ID, correlation ID, reason" className="w-full max-w-md rounded border px-3 py-2 text-sm" />
        <label className="text-xs">Reason
          <select value={reasonFilter} onChange={(event) => setReasonFilter(event.target.value)} className="ml-2 rounded border p-2">
            {reasons.map((reason) => <option key={reason} value={reason}>{reason}</option>)}
          </select>
        </label>
      </section>
      <div className="grid gap-6 lg:grid-cols-12">
        <div className={selectedEventId ? "lg:col-span-7" : "lg:col-span-12"}>
          <TriageQueueTable items={filtered} loading={loadingQueue} selectedEventId={selectedEventId} onSelect={(id) => void loadCase(id)} />
        </div>
        {selectedEventId && <div className="lg:col-span-5"><CaseInspectorDrawer
          eventId={selectedEventId}
          queueItem={items.find((item) => item.eventId === selectedEventId) ?? null}
          detail={detail}
          auditLog={auditLog}
          role={session.role}
          loading={loadingDetail}
          submitting={submitting}
          onClose={() => setSelectedEventId(null)}
          onSubmitAction={submitAction}
        /></div>}
      </div>
    </main>
  );
}
