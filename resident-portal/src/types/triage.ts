export type TriageRole = "CASEWORKER" | "AUDITOR" | "ADMIN";
export type EscalationTarget = "dlq" | "manual_review";
export type IntakeEventStatus = "in_progress" | "completed" | "escalated";
export type OutboxDeliveryStatus = "pending" | "publishing" | "published" | "failed";
export type TriageDisposition =
  | "CLAIM_CASE"
  | "REQUEST_VERIFICATION"
  | "ROUTE_FSSA_PATHWAY"
  | "MARK_RESOLVED";

export interface TriageQueueItem {
  eventId: string;
  correlationId: string;
  status: IntakeEventStatus;
  escalationTarget: EscalationTarget;
  reasonCode: string;
  attemptsMade: number;
  recordedAt: string;
  updatedAt: string;
  outboxStatus: OutboxDeliveryStatus | null;
  outboxPublishAttempts: number;
  assignedTo: string | null;
  county: string;
}

export interface EstimatedProgram {
  program: "SNAP" | "TANF" | "HIP_2_0" | "CCDF";
  estimatedEligible: boolean;
  estimateNote: string;
}

export interface RedactedApplicantSummary {
  applicantRef: string;
  county: string;
  householdSizeEstimate: number | null;
  residentName?: string;
  contactHint: string;
  estimatedPrograms: EstimatedProgram[];
  requiredVerifications: string[];
}

export interface TriageAuditEntry {
  auditId: string;
  eventId: string;
  correlationId: string;
  disposition: TriageDisposition;
  actorId: string;
  actorRole: TriageRole;
  note: string;
  previousStatus: string;
  newStatus: string;
  createdAt: string;
}

export interface TriageActionPayload {
  eventId: string;
  correlationId: string;
  disposition: TriageDisposition;
  note: string;
}

/** Display-only identity returned by an authenticated server endpoint. */
export interface TriageSessionView {
  actorId: string;
  role: TriageRole;
  countyScope: string;
}
