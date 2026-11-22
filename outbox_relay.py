"""At-least-once relay for durable escalation outbox records."""
from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from typing import Protocol

logger = logging.getLogger("beast3.outbox")
_SAFE_ID = re.compile(r"^[A-Za-z0-9._:-]{8,128}$")
_ALLOWED_REASONS = {"RETRY_EXHAUSTED", "TERMINAL_FAILURE", "UNHANDLED_FAILURE"}


@dataclass(frozen=True, slots=True)
class OutboxRecord:
    outbox_id: str
    intake_id: str
    case_id: str
    idempotency_key: str
    division: int
    reason: str
    attempts: int
    last_error: str
    scrubbed_digest: str | None = None
    publish_attempts: int = 0

    def validate(self) -> None:
        for field in ("outbox_id", "intake_id", "case_id", "idempotency_key"):
            value = getattr(self, field)
            if not isinstance(value, str) or not _SAFE_ID.fullmatch(value):
                raise ValueError(f"{field} must be a safe identifier")
        if isinstance(self.division, bool) or not isinstance(self.division, int) or not 1 <= self.division <= 21:
            raise ValueError("division must be an integer from 1 through 21")
        if self.reason not in _ALLOWED_REASONS or self.last_error not in _ALLOWED_REASONS:
            raise ValueError("outbox failure code is not allowed")
        if isinstance(self.attempts, bool) or not isinstance(self.attempts, int) or not 1 <= self.attempts <= 10:
            raise ValueError("attempts must be an integer from 1 through 10")
        if self.scrubbed_digest is not None and not re.fullmatch(r"[A-Fa-f0-9]{16,128}", self.scrubbed_digest):
            raise ValueError("scrubbed_digest must be a hexadecimal digest")


class OutboxStore(Protocol):
    async def fetch_pending_outbox(self, *, limit: int) -> list[OutboxRecord]: ...
    async def mark_outbox_published(self, *, outbox_id: str) -> None: ...
    async def mark_outbox_failed(self, *, outbox_id: str, backoff_seconds: float) -> None: ...


class DLQPublisher(Protocol):
    async def publish_dlq(self, *, outbox_id: str, intake_id: str, case_id: str,
                          idempotency_key: str, division: int, reason: str,
                          attempts: int, last_error: str,
                          scrubbed_digest: str | None = None) -> None: ...


class OutboxRelay:
    def __init__(self, *, store: OutboxStore, publisher: DLQPublisher,
                 batch_size: int = 100, poll_interval_seconds: float = 1.0,
                 operation_timeout_seconds: float = 10.0,
                 max_backoff_seconds: float = 300.0) -> None:
        if not 1 <= batch_size <= 1000:
            raise ValueError("batch_size must be between 1 and 1000")
        if poll_interval_seconds <= 0 or operation_timeout_seconds <= 0 or max_backoff_seconds <= 0:
            raise ValueError("timeouts, polling interval, and max backoff must be positive")
        self.store = store
        self.publisher = publisher
        self.batch_size = batch_size
        self.poll_interval_seconds = poll_interval_seconds
        self.operation_timeout_seconds = operation_timeout_seconds
        self.max_backoff_seconds = max_backoff_seconds

    async def relay_once(self) -> int:
        records = await asyncio.wait_for(self.store.fetch_pending_outbox(limit=self.batch_size),
                                         timeout=self.operation_timeout_seconds)
        delivered = 0
        for record in records:
            published = False
            try:
                record.validate()
                await asyncio.wait_for(self.publisher.publish_dlq(
                    outbox_id=record.outbox_id, intake_id=record.intake_id,
                    case_id=record.case_id, idempotency_key=record.idempotency_key,
                    division=record.division, reason=record.reason, attempts=record.attempts,
                    last_error=record.last_error, scrubbed_digest=record.scrubbed_digest,
                ), timeout=self.operation_timeout_seconds)
                published = True
                await asyncio.wait_for(self.store.mark_outbox_published(outbox_id=record.outbox_id),
                                       timeout=self.operation_timeout_seconds)
                delivered += 1
                logger.info("Outbox record delivered outboxId=%s", record.outbox_id)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                # A failed publish is rescheduled. Failed marking stays leased for stale-lease recovery,
                # because the broker may already have accepted the record.
                if not published:
                    delay = min(2 ** min(max(record.publish_attempts, 0), 16), self.max_backoff_seconds)
                    settle = getattr(self.store, "mark_outbox_failed", None)
                    if settle is not None:
                        try:
                            await asyncio.wait_for(settle(outbox_id=record.outbox_id, backoff_seconds=delay),
                                                   timeout=self.operation_timeout_seconds)
                        except Exception:
                            pass
                logger.warning("Outbox delivery deferred outboxId=%s failureCode=%s",
                               record.outbox_id,
                               "TIMEOUT" if isinstance(exc, asyncio.TimeoutError) else "DELIVERY_OR_ACK_FAILURE")
        return delivered

    async def run(self, stop_event: asyncio.Event) -> None:
        while not stop_event.is_set():
            try:
                delivered = await self.relay_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.warning("Outbox poll failed failureCode=STORE_UNAVAILABLE")
                delivered = 0
            if delivered == 0:
                try:
                    await asyncio.wait_for(stop_event.wait(), timeout=self.poll_interval_seconds)
                except asyncio.TimeoutError:
                    pass
