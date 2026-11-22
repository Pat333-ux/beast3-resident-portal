export interface ResidentProfile {
  householdSize: number;
  grossMonthlyIncomeCents: number;
  location: {
    state: string;
    county: string;
    city: string;
  };
  targetFocusAreas: string[];
}

/** Frontend form envelope; a trusted server adapter must validate/map it to the intake schema. */
export interface IntakeEnvelope {
  schemaVersion: "1.0.0";
  intakeId: string;
  correlation_id: string;
  submittedAt: string;
  profile: ResidentProfile;
}

export interface IntakeSubmission {
  intakeId: string;
  event_id: string;
  correlation_id: string;
  status: ApplicationStatus;
}

export type ApplicationStatus = "in_progress" | "completed" | "manual_review";

export interface SignedConsentPayload {
  intakeId: string;
  consentVersion: string;
  purpose: string;
  issuedAt: bigint;
  expiresAt: bigint;
}

export interface StatusUpdate {
  event_id: string;
  correlation_id: string;
  status: ApplicationStatus;
  updatedAt: string;
}
