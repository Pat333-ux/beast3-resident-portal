import { Pool } from "pg";
import type {
  RedactedApplicantSummary,
  TriageAuditEntry,
  TriageDisposition,
  TriageQueueItem,
  TriageRole,
} from "../types/triage";
import type { VerifiedTriageSession } from "./triageAuth";

export interface TriageRepository {
  listManualReviewQueue(params: { county: string; reasonCode?: string; search?: string }): Promise<TriageQueueItem[]>;
  getCaseDetail(params: { eventId: string; countyScope: string; role: TriageRole }): Promise<{
    queueItem: TriageQueueItem;
    summary: RedactedApplicantSummary;
    auditLog: TriageAuditEntry[];
  } | null>;
  recordAuditAction(params: {
    eventId: string; correlationId: string; disposition: TriageDisposition; note: string;
    actorId: string; actorRole: TriageRole; countyScope: string;
  }): Promise<{ auditEntry: TriageAuditEntry; updatedQueueItem: TriageQueueItem } | null>;
}

const SAFE_ID = /^[A-Za-z0-9._:-]{8,128}$/;
const REASONS = new Set(["RETRY_EXHAUSTED", "TERMINAL_FAILURE", "UNHANDLED_FAILURE"]);
const DISPOSITIONS = new Set<TriageDisposition>([
  "CLAIM_CASE", "REQUEST_VERIFICATION", "ROUTE_FSSA_PATHWAY", "MARK_RESOLVED",
]);

let pool: Pool | undefined;
function databasePool(): Pool {
  if (pool) return pool;
  const connectionString = process.env.DATABASE_URL;
  if (!connectionString) throw new Error("DATABASE_UNAVAILABLE");
  pool = new Pool({ connectionString, max: 10, idleTimeoutMillis: 30_000, connectionTimeoutMillis: 5_000 });
  return pool;
}

function mapQueue(row: Record<string, unknown>): TriageQueueItem {
  return {
    eventId: String(row.event_id),
    correlationId: String(row.correlation_id),
    status: row.status as TriageQueueItem["status"],
    escalationTarget: row.escalation_target as TriageQueueItem["escalationTarget"],
    reasonCode: String(row.reason_code ?? ""),
    attemptsMade: Number(row.attempts_made),
    recordedAt: new Date(String(row.recorded_at)).toISOString(),
    updatedAt: new Date(String(row.updated_at)).toISOString(),
    outboxStatus: (row.outbox_status ?? null) as TriageQueueItem["outboxStatus"],
    outboxPublishAttempts: Number(row.publish_attempts ?? 0),
    assignedTo: row.assigned_to === null || row.assigned_to === undefined ? null : String(row.assigned_to),
    county: String(row.county),
  };
}

const QUEUE_SELECT = `
  SELECT e.event_id, e.correlation_id, e.status, e.escalation_target,
         e.reason_code, e.attempts_made, e.recorded_at, e.updated_at,
         e.assigned_to, e.county, o.status AS outbox_status,
         COALESCE(o.publish_attempts, 0) AS publish_attempts
  FROM intake_events e
  LEFT JOIN escalation_outbox o ON o.event_id = e.event_id
  WHERE e.status='escalated' AND e.escalation_target='manual_review' AND e.county=$1
`;

export class PgTriageRepository implements TriageRepository {
  async listManualReviewQueue(params: { county: string; reasonCode?: string; search?: string }): Promise<TriageQueueItem[]> {
    const values: unknown[] = [params.county];
    let sql = QUEUE_SELECT;
    if (params.reasonCode) {
      values.push(params.reasonCode);
      sql += ` AND e.reason_code=$${values.length}`;
    }
    if (params.search) {
      values.push(`%${params.search.replace(/[\\%_]/g, "\\$&")}%`);
      sql += ` AND (e.event_id ILIKE $${values.length} ESCAPE '\\\\' OR e.correlation_id ILIKE $${values.length} ESCAPE '\\\\' OR e.reason_code ILIKE $${values.length} ESCAPE '\\\\')`;
    }
    sql += " ORDER BY e.updated_at DESC LIMIT 500";
    const result = await databasePool().query(sql, values);
    return result.rows.map(mapQueue);
  }

  async getCaseDetail(params: { eventId: string; countyScope: string; role: TriageRole }) {
    if (!SAFE_ID.test(params.eventId)) return null;
    const db = databasePool();
    const caseResult = await db.query(`${QUEUE_SELECT} AND e.event_id=$2 LIMIT 1`, [params.countyScope, params.eventId]);
    if (!caseResult.rows[0]) return null;
    const queueItem = mapQueue(caseResult.rows[0]);
    const auditResult = await db.query(
      `SELECT audit_id,event_id,correlation_id,disposition,actor_id,actor_role,note,previous_status,new_status,created_at
       FROM triage_audit_log WHERE event_id=$1 ORDER BY created_at ASC,audit_id ASC LIMIT 500`,
      [params.eventId],
    );
    const auditLog: TriageAuditEntry[] = auditResult.rows.map((row) => ({
      auditId: String(row.audit_id), eventId: String(row.event_id), correlationId: String(row.correlation_id),
      disposition: row.disposition as TriageDisposition, actorId: String(row.actor_id),
      actorRole: row.actor_role as TriageRole, note: String(row.note),
      previousStatus: String(row.previous_status), newStatus: String(row.new_status),
      createdAt: new Date(String(row.created_at)).toISOString(),
    }));
    // No resident-PII source is configured in this repository. Fail closed for all roles
    // rather than inventing or loading unencrypted private data.
    const summary: RedactedApplicantSummary = {
      applicantRef: `APP-${params.eventId.replace(/^evt_/i, "").slice(0, 6).toUpperCase()}`,
      county: queueItem.county,
      householdSizeEstimate: null,
      contactHint: "[REDACTED]",
      estimatedPrograms: [],
      requiredVerifications: [],
    };
    return { queueItem, summary, auditLog };
  }

  async recordAuditAction(params: {
    eventId: string; correlationId: string; disposition: TriageDisposition; note: string;
    actorId: string; actorRole: TriageRole; countyScope: string;
  }) {
    if (!SAFE_ID.test(params.eventId) || !SAFE_ID.test(params.correlationId)
      || !DISPOSITIONS.has(params.disposition) || !params.note.trim() || params.note.length > 2000) {
      throw new Error("INVALID_ACTION");
    }
    const db = databasePool();
    const client = await db.connect();
    try {
      await client.query("BEGIN");
      const locked = await client.query(
        `SELECT event_id,correlation_id,status,escalation_target,reason_code,attempts_made,
                recorded_at,updated_at,assigned_to,county,triage_status
         FROM intake_events WHERE event_id=$1 AND correlation_id=$2 AND county=$3 FOR UPDATE`,
        [params.eventId, params.correlationId, params.countyScope],
      );
      const row = locked.rows[0];
      if (!row || row.status !== "escalated" || row.escalation_target !== "manual_review") {
        await client.query("ROLLBACK");
        return null;
      }
      const previousStatus = String(row.triage_status ?? "open");
      const nextStatus: Record<TriageDisposition, string> = {
        CLAIM_CASE: "claimed", REQUEST_VERIFICATION: "verification_requested",
        ROUTE_FSSA_PATHWAY: "routed_fssa", MARK_RESOLVED: "resolved",
      };
      const newStatus = nextStatus[params.disposition];
      const assignedTo = params.disposition === "CLAIM_CASE" ? params.actorId : row.assigned_to;
      const updated = await client.query(
        `UPDATE intake_events SET triage_status=$2,assigned_to=$3,updated_at=NOW()
         WHERE event_id=$1 RETURNING event_id,correlation_id,status,escalation_target,reason_code,
           attempts_made,recorded_at,updated_at,assigned_to,county,triage_status`,
        [params.eventId, newStatus, assignedTo],
      );
      const auditId = `audit-${crypto.randomUUID()}`;
      const inserted = await client.query(
        `INSERT INTO triage_audit_log
          (audit_id,event_id,correlation_id,disposition,actor_id,actor_role,note,previous_status,new_status)
         VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)
         RETURNING created_at`,
        [auditId, params.eventId, params.correlationId, params.disposition, params.actorId,
          params.actorRole, params.note.trim(), previousStatus, newStatus],
      );
      await client.query("COMMIT");
      return {
        auditEntry: {
          auditId, eventId: params.eventId, correlationId: params.correlationId,
          disposition: params.disposition, actorId: params.actorId, actorRole: params.actorRole,
          note: params.note.trim(), previousStatus, newStatus,
          createdAt: new Date(inserted.rows[0].created_at).toISOString(),
        },
        updatedQueueItem: mapQueue(updated.rows[0]),
      };
    } catch (error) {
      await client.query("ROLLBACK");
      throw error;
    } finally {
      client.release();
    }
  }
}

export function getTriageRepository(): TriageRepository {
  return new PgTriageRepository();
}

export function sessionForQueue(session: VerifiedTriageSession) {
  return { actorId: session.actorId, role: session.role, countyScope: session.countyScope };
}
