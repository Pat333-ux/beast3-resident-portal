"""asyncpg-backed StateStore and leased PostgreSQL outbox implementation."""
from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any
from uuid import uuid4

import asyncpg

from outbox_relay import OutboxRecord

_SAFE_ID = re.compile(r"^[A-Za-z0-9._:-]{8,128}$")
_REASONS = {"RETRY_EXHAUSTED", "TERMINAL_FAILURE", "UNHANDLED_FAILURE"}
_TARGETS = {"dlq", "manual_review"}

_CLAIM_SQL = """
WITH claimed AS (
    SELECT outbox_id FROM escalation_outbox
    WHERE ((status IN ('pending', 'failed') AND next_attempt_at <= NOW())
       OR (status = 'publishing' AND locked_at < NOW() - ($3 * INTERVAL '1 second')))
    ORDER BY next_attempt_at ASC, outbox_id ASC
    FOR UPDATE SKIP LOCKED LIMIT $1
)
UPDATE escalation_outbox AS o
SET status='publishing', locked_by=$2, locked_at=NOW(),
    publish_attempts=o.publish_attempts+1, updated_at=NOW()
FROM claimed WHERE o.outbox_id=claimed.outbox_id
RETURNING o.outbox_id, o.intake_id, o.case_id, o.idempotency_key, o.division,
          o.reason_code, o.attempts_made, o.last_error, o.payload, o.publish_attempts
"""


class PostgresStateStore:
    def __init__(self, pool: asyncpg.Pool, *, worker_id: str | None = None, lease_seconds: int = 300):
        if lease_seconds < 1:
            raise ValueError("lease_seconds must be positive")
        self.pool = pool
        self.worker_id = worker_id or f"relay-{uuid4()}"
        if not _SAFE_ID.fullmatch(self.worker_id):
            raise ValueError("worker_id must be a safe identifier")
        self.lease_seconds = lease_seconds

    @staticmethod
    def _validate_id(value: str, field: str) -> None:
        if not isinstance(value, str) or not _SAFE_ID.fullmatch(value):
            raise ValueError(f"{field} must be a safe identifier")

    async def claim_event(self, *, event_id: str, correlation_id: str) -> tuple[bool, dict[str, Any] | None]:
        """Atomically reserve a new event row for exactly one request handler."""
        self._validate_id(event_id, "event_id")
        self._validate_id(correlation_id, "correlation_id")
        async with self.pool.acquire() as connection:
            inserted = await connection.fetchrow(
                """INSERT INTO intake_events (event_id, correlation_id, status, attempts_made)
                   VALUES ($1,$2,'in_progress',0) ON CONFLICT (event_id) DO NOTHING
                   RETURNING event_id""",
                event_id, correlation_id,
            )
            if inserted:
                return True, None
            row = await connection.fetchrow(
                """SELECT event_id, correlation_id, status, escalation_target, reason_code
                   FROM intake_events WHERE event_id=$1""",
                event_id,
            )
            return False, dict(row) if row else None

    async def mark_in_progress(self, *, event_id: str, correlation_id: str, attempt: int) -> None:
        self._validate_id(event_id, "event_id")
        self._validate_id(correlation_id, "correlation_id")
        if isinstance(attempt, bool) or not isinstance(attempt, int) or attempt < 0:
            raise ValueError("attempt must be a non-negative integer")
        await self.pool.execute(
            """INSERT INTO intake_events (event_id, correlation_id, status, attempts_made)
               VALUES ($1,$2,'in_progress',$3) ON CONFLICT (event_id) DO UPDATE SET
                 correlation_id=EXCLUDED.correlation_id,
                 status=CASE WHEN intake_events.status IN ('completed','escalated')
                             THEN intake_events.status ELSE 'in_progress' END,
                 attempts_made=GREATEST(intake_events.attempts_made,EXCLUDED.attempts_made),
                 updated_at=NOW()""", event_id, correlation_id, attempt,
        )

    async def mark_completed(self, *, event_id: str, correlation_id: str) -> None:
        self._validate_id(event_id, "event_id")
        self._validate_id(correlation_id, "correlation_id")
        await self.pool.execute(
            "UPDATE intake_events SET status='completed',updated_at=NOW() WHERE event_id=$1 AND correlation_id=$2",
            event_id, correlation_id,
        )

    async def record_escalation_with_outbox(
        self, *, event_id: str, correlation_id: str, intake_id: str, case_id: str,
        idempotency_key: str, division: int, target: str, reason_code: str,
        attempts_made: int, recorded_at: datetime, scrubbed_digest: str | None = None,
    ) -> None:
        for field, value in (("event_id", event_id), ("correlation_id", correlation_id),
                             ("intake_id", intake_id), ("case_id", case_id),
                             ("idempotency_key", idempotency_key)):
            self._validate_id(value, field)
        if target not in _TARGETS or reason_code not in _REASONS:
            raise ValueError("unsupported escalation target or reason code")
        if isinstance(division, bool) or not isinstance(division, int) or not 1 <= division <= 21:
            raise ValueError("division must be from 1 through 21")
        if isinstance(attempts_made, bool) or not isinstance(attempts_made, int) or not 1 <= attempts_made <= 10:
            raise ValueError("attempts_made must be from 1 through 10")
        if scrubbed_digest is not None and not re.fullmatch(r"[A-Fa-f0-9]{16,128}", scrubbed_digest):
            raise ValueError("scrubbed_digest must be a hexadecimal digest")
        metadata: dict[str, Any] = {
            "event_id": event_id, "correlation_id": correlation_id, "target": target,
            "reason_code": reason_code, "attempts_made": attempts_made,
            "recorded_at": recorded_at.isoformat(),
        }
        if scrubbed_digest is not None:
            metadata["scrubbed_digest"] = scrubbed_digest

        async with self.pool.acquire() as connection:
            async with connection.transaction():
                event = await connection.fetchrow(
                    """INSERT INTO intake_events
                         (event_id,correlation_id,status,escalation_target,reason_code,attempts_made,recorded_at,updated_at)
                       VALUES ($1,$2,'escalated',$3,$4,$5,$6,NOW())
                       ON CONFLICT (event_id) DO UPDATE SET correlation_id=EXCLUDED.correlation_id,
                         status='escalated',escalation_target=EXCLUDED.escalation_target,
                         reason_code=EXCLUDED.reason_code,
                         attempts_made=GREATEST(intake_events.attempts_made,EXCLUDED.attempts_made),updated_at=NOW()
                       WHERE intake_events.status <> 'completed' RETURNING event_id""",
                    event_id, correlation_id, target, reason_code, attempts_made, recorded_at,
                )
                if event is None:
                    return
                await connection.execute(
                    """INSERT INTO escalation_outbox
                         (event_id,correlation_id,intake_id,case_id,idempotency_key,division,target,reason_code,
                          attempts_made,last_error,payload,status,next_attempt_at,recorded_at,created_at,updated_at)
                       VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$8,$10::jsonb,'pending',NOW(),$11,NOW(),NOW())
                       ON CONFLICT (event_id) DO NOTHING""",
                    event_id, correlation_id, intake_id, case_id, idempotency_key, division,
                    target, reason_code, attempts_made, json.dumps(metadata), recorded_at,
                )

    async def fetch_pending_outbox(self, *, limit: int) -> list[OutboxRecord]:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 1000:
            raise ValueError("limit must be from 1 through 1000")
        async with self.pool.acquire() as connection:
            rows = await connection.fetch(_CLAIM_SQL, limit, self.worker_id, self.lease_seconds)
        return [OutboxRecord(
            outbox_id=str(row["outbox_id"]), intake_id=row["intake_id"], case_id=row["case_id"],
            idempotency_key=row["idempotency_key"], division=row["division"], reason=row["reason_code"],
            attempts=row["attempts_made"], last_error=row["last_error"],
            scrubbed_digest=row["payload"].get("scrubbed_digest") if isinstance(row["payload"], dict) else None,
            publish_attempts=row["publish_attempts"],
        ) for row in rows]

    async def mark_outbox_published(self, *, outbox_id: str) -> None:
        if not outbox_id.isdigit():
            raise ValueError("outbox_id must be numeric")
        result = await self.pool.execute(
            """UPDATE escalation_outbox SET status='published',published_at=NOW(),locked_by=NULL,
                      locked_at=NULL,updated_at=NOW()
               WHERE outbox_id=$1::bigint AND locked_by=$2 AND status='publishing'""",
            outbox_id, self.worker_id,
        )
        if result.endswith(" 0"):
            raise RuntimeError("outbox lease lost before acknowledgement")

    async def mark_outbox_failed(self, *, outbox_id: str, backoff_seconds: float) -> None:
        if not outbox_id.isdigit() or backoff_seconds < 0:
            raise ValueError("invalid outbox failure settlement")
        await self.pool.execute(
            """UPDATE escalation_outbox SET status='failed',next_attempt_at=NOW()+($3*INTERVAL '1 second'),
                      locked_by=NULL,locked_at=NULL,updated_at=NOW()
               WHERE outbox_id=$1::bigint AND locked_by=$2 AND status='publishing'""",
            outbox_id, self.worker_id, backoff_seconds,
        )
