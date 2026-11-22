import type { StatusUpdate } from "../types/portal";

const statusLabels = {
  in_progress: "In Progress",
  completed: "Completed",
  manual_review: "Under Review",
} as const;

interface StatusTrackerProps {
  update: StatusUpdate;
}

export function StatusTracker({ update }: StatusTrackerProps) {
  return (
    <section aria-label="Application status" aria-live="polite">
      <h2>{statusLabels[update.status]}</h2>
      <dl>
        <dt>Event ID</dt>
        <dd>{update.event_id}</dd>
        <dt>Correlation ID</dt>
        <dd>{update.correlation_id}</dd>
        <dt>Last updated</dt>
        <dd>
          <time dateTime={update.updatedAt}>{update.updatedAt}</time>
        </dd>
      </dl>
    </section>
  );
}
