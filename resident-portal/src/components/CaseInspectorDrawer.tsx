"use client";

import { useState, type FormEvent } from "react";
import type {
  RedactedApplicantSummary,
  TriageAuditEntry,
  TriageDisposition,
  TriageQueueItem,
  TriageRole,
} from "../types/triage";

interface Props {
  eventId: string;
  queueItem: TriageQueueItem | null;
  detail: RedactedApplicantSummary | null;
  auditLog: TriageAuditEntry[];
  role: TriageRole;
  loading: boolean;
  submitting: boolean;
  onClose: () => void;
  onSubmitAction: (eventId: string, correlationId: string, disposition: TriageDisposition, note: string) => Promise<void>;
}

const dispositions: Array<[TriageDisposition, string]> = [
  ["CLAIM_CASE", "Claim case for review"],
  ["REQUEST_VERIFICATION", "Request verification"],
  ["ROUTE_FSSA_PATHWAY", "Route to FSSA pathway"],
  ["MARK_RESOLVED", "Mark triage resolved"],
];

export function CaseInspectorDrawer(props: Props) {
  const { eventId, queueItem, detail, auditLog, role, loading, submitting, onClose, onSubmitAction } = props;
  const [disposition, setDisposition] = useState<TriageDisposition>("CLAIM_CASE");
  const [note, setNote] = useState("");
  const [errorCode, setErrorCode] = useState<string | null>(null);
  const canMutate = role === "CASEWORKER" || role === "ADMIN";

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!canMutate || !queueItem) return;
    const safeNote = note.trim();
    if (!safeNote || safeNote.length > 2000) {
      setErrorCode("NOTE_REQUIRED_OR_TOO_LONG");
      return;
    }
    setErrorCode(null);
    try {
      await onSubmitAction(eventId, queueItem.correlationId, disposition, safeNote);
      setNote("");
    } catch {
      setErrorCode("ACTION_FAILED");
    }
  }

  return (
    <aside aria-label="Case inspector" className="space-y-5 rounded-lg border bg-white p-5">
      <header className="flex items-start justify-between gap-3 border-b pb-3">
        <div>
          <p className="text-xs font-semibold uppercase tracking-wide text-slate-500">Manual-review case</p>
          <h2 className="break-all font-mono text-sm font-bold">{eventId}</h2>
          {queueItem && <p className="break-all font-mono text-xs text-slate-500">Correlation: {queueItem.correlationId}</p>}
        </div>
        <button type="button" onClick={onClose} className="rounded border px-2 py-1 text-xs">Close</button>
      </header>

      {loading ? <p role="status">Loading case details…</p> : detail ? (
        <section aria-label="Applicant summary" className="space-y-3 rounded border bg-slate-50 p-3 text-xs">
          <p><strong>Applicant reference:</strong> {detail.applicantRef}</p>
          <p><strong>County:</strong> {detail.county}</p>
          {detail.householdSizeEstimate !== null && <p><strong>Household size:</strong> {detail.householdSizeEstimate}</p>}
          {role !== "AUDITOR" && detail.residentName && <p><strong>Resident:</strong> {detail.residentName}</p>}
          <p><strong>Contact:</strong> {detail.contactHint}</p>
          <div className="border-t pt-2">
            <p className="font-semibold">Estimated pre-screen only — not an agency determination</p>
            <ul className="mt-1 space-y-1">
              {detail.estimatedPrograms.map((program) => (
                <li key={program.program}>{program.program}: {program.estimatedEligible ? "estimate indicates possible eligibility" : "estimate does not indicate eligibility"}. {program.estimateNote}</li>
              ))}
            </ul>
          </div>
          <div className="border-t pt-2">
            <p className="font-semibold">Verification checklist</p>
            <ul>{detail.requiredVerifications.map((item) => <li key={item}>{item}</li>)}</ul>
          </div>
        </section>
      ) : <p className="text-sm text-slate-600">No detail is available for this role or case.</p>}

      <form onSubmit={submit} className="space-y-3 border-t pt-4">
        <h3 className="text-xs font-bold uppercase tracking-wide">Record case action</h3>
        {!canMutate && <p className="rounded bg-slate-100 p-2 text-xs">Auditor access is read-only.</p>}
        <label className="block text-xs font-medium">
          Disposition
          <select className="mt-1 block w-full rounded border p-2" value={disposition} disabled={!canMutate || submitting} onChange={(e) => setDisposition(e.target.value as TriageDisposition)}>
            {dispositions.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
          </select>
        </label>
        <label className="block text-xs font-medium">
          Audit note (do not enter SSNs, account numbers, or unnecessary sensitive details)
          <textarea className="mt-1 block w-full rounded border p-2" rows={3} maxLength={2000} required disabled={!canMutate || submitting} value={note} onChange={(e) => setNote(e.target.value)} />
        </label>
        {errorCode && <p role="alert">{errorCode}</p>}
        <button className="w-full rounded bg-slate-900 px-3 py-2 text-xs font-semibold text-white disabled:opacity-50" disabled={!canMutate || submitting || !note.trim()} type="submit">
          {submitting ? "Saving…" : "Record action"}
        </button>
      </form>

      <section className="space-y-2 border-t pt-4" aria-label="Audit trail">
        <h3 className="text-xs font-bold uppercase tracking-wide">Audit trail ({auditLog.length})</h3>
        {!auditLog.length ? <p className="text-xs text-slate-500">No actions recorded.</p> : (
          <ol className="space-y-2">
            {auditLog.map((entry) => (
              <li key={entry.auditId} className="rounded border bg-slate-50 p-2 text-xs">
                <p className="font-semibold">{entry.disposition} · {entry.previousStatus} → {entry.newStatus}</p>
                <p className="mt-1 break-words">{entry.note}</p>
                <p className="mt-1 text-slate-500">{entry.actorRole} · {entry.actorId} · {entry.createdAt}</p>
              </li>
            ))}
          </ol>
        )}
      </section>
    </aside>
  );
}
