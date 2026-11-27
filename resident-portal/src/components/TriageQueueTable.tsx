"use client";

import type { TriageQueueItem } from "../types/triage";

interface Props {
  items: TriageQueueItem[];
  loading: boolean;
  selectedEventId: string | null;
  onSelect: (eventId: string) => void;
}

export function TriageQueueTable({ items, loading, selectedEventId, onSelect }: Props) {
  if (loading) return <p role="status" className="rounded border p-6">Loading manual-review queue…</p>;
  if (!items.length) return <p className="rounded border p-6">No manual-review cases match this view.</p>;

  return (
    <div className="overflow-x-auto rounded-lg border bg-white">
      <table className="min-w-full divide-y text-left text-xs">
        <thead className="bg-slate-50 font-semibold uppercase tracking-wide text-slate-600">
          <tr>
            <th scope="col" className="px-4 py-3">Event / Correlation ID</th>
            <th scope="col" className="px-4 py-3">Reason</th>
            <th scope="col" className="px-4 py-3">Attempts</th>
            <th scope="col" className="px-4 py-3">Outbox</th>
            <th scope="col" className="px-4 py-3">Assignee</th>
            <th scope="col" className="px-4 py-3">Case</th>
          </tr>
        </thead>
        <tbody className="divide-y text-slate-800">
          {items.map((item) => (
            <tr key={item.eventId} className={item.eventId === selectedEventId ? "bg-blue-50" : "hover:bg-slate-50"}>
              <td className="px-4 py-3 font-mono">
                <div className="font-semibold">{item.eventId}</div>
                <div className="text-slate-500">{item.correlationId}</div>
              </td>
              <td className="px-4 py-3">{item.reasonCode}</td>
              <td className="px-4 py-3">{item.attemptsMade}</td>
              <td className="px-4 py-3 font-mono">{item.outboxStatus ?? "unrecorded"}</td>
              <td className="px-4 py-3">{item.assignedTo ?? "Unassigned"}</td>
              <td className="px-4 py-3">
                <button
                  type="button"
                  aria-current={item.eventId === selectedEventId ? "true" : undefined}
                  onClick={() => onSelect(item.eventId)}
                  className="rounded border px-3 py-1 font-semibold hover:bg-slate-100"
                >
                  Inspect
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
