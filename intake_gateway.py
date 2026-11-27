"""Authenticated intake webhook boundary with replay and database idempotency checks."""
from __future__ import annotations

import hashlib
import hmac
import inspect
import json
import os
import re
import time
from typing import Any, Awaitable, Callable

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, ValidationError

app = FastAPI()
_SAFE_ID = re.compile(r"^[A-Za-z0-9._:-]{8,128}$")
_SIGNATURE = re.compile(r"^sha256=([0-9a-f]{64})$")
_REPLAY_WINDOW_SECONDS = 300


class IntakeWebhookEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    event_id: str = Field(min_length=8, max_length=128)
    correlation_id: str = Field(min_length=8, max_length=128)
    intake_id: str = Field(min_length=8, max_length=128)
    payload: dict[str, Any]
    attempt: int = Field(ge=0, le=10)

    @classmethod
    def from_json_bytes(cls, raw: bytes) -> "IntakeWebhookEnvelope":
        return cls.model_validate(json.loads(raw))

    def validate_identifiers(self) -> None:
        for field in ("event_id", "correlation_id", "intake_id"):
            if not _SAFE_ID.fullmatch(getattr(self, field)):
                raise ValueError("unsafe identifier")


Handler = Callable[[IntakeWebhookEnvelope], Any | Awaitable[Any]]


def verify_hmac_signature(
    payload_bytes: bytes,
    signature_header: str | None,
    timestamp_header: str | None,
    *,
    secret: str | bytes,
    now_seconds: int | None = None,
    max_clock_skew_seconds: int = _REPLAY_WINDOW_SECONDS,
) -> bool:
    """Verify timestamp-bound HMAC-SHA256 with a strict replay window."""
    if not signature_header or not timestamp_header or not timestamp_header.isascii():
        return False
    if not timestamp_header.isdigit() or len(timestamp_header) > 12:
        return False
    match = _SIGNATURE.fullmatch(signature_header)
    if not match:
        return False
    timestamp = int(timestamp_header)
    now = int(time.time()) if now_seconds is None else now_seconds
    if abs(now - timestamp) > max_clock_skew_seconds:
        return False
    secret_bytes = secret.encode("utf-8") if isinstance(secret, str) else secret
    if len(secret_bytes) < 32:
        return False
    expected = hmac.new(secret_bytes, timestamp_header.encode("ascii") + b"." + payload_bytes, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, match.group(1))


def derive_event_id(intake_id: str, issued_at: str, consent_signature: str) -> str:
    digest = hashlib.sha256(f"{intake_id}:{issued_at}:{consent_signature}".encode("utf-8")).hexdigest()[:32]
    return f"evt_{digest}"


async def claim_event(db_pool: Any, event_id: str, correlation_id: str) -> tuple[bool, dict[str, Any] | None]:
    """Atomically reserve a new event; return False and existing status on duplicates."""
    async with db_pool.acquire() as connection:
        inserted = await connection.fetchrow(
            """INSERT INTO intake_events (event_id, correlation_id, status, attempts_made)
               VALUES ($1, $2, 'in_progress', 0)
               ON CONFLICT (event_id) DO NOTHING
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


@app.post("/api/v1/intake")
async def receive_intake(request: Request) -> dict[str, Any]:
    secret = os.getenv("INTAKE_WEBHOOK_SECRET", "")
    if len(secret.encode("utf-8")) < 32:
        raise HTTPException(status_code=503, detail="Intake gateway unavailable")

    correlation_header = request.headers.get("x-correlation-id", "")
    event_header = request.headers.get("x-event-id", "")
    timestamp_header = request.headers.get("x-request-timestamp", "")
    if not _SAFE_ID.fullmatch(correlation_header) or not _SAFE_ID.fullmatch(event_header):
        raise HTTPException(status_code=400, detail="Invalid intake request")

    raw = await request.body()
    if len(raw) > 64 * 1024:
        raise HTTPException(status_code=413, detail="Intake request too large")
    if not verify_hmac_signature(raw, request.headers.get("x-signature-256"), timestamp_header, secret=secret):
        raise HTTPException(status_code=401, detail="Invalid request signature")

    try:
        envelope = IntakeWebhookEnvelope.from_json_bytes(raw)
        envelope.validate_identifiers()
    except (ValidationError, ValueError, TypeError, json.JSONDecodeError):
        raise HTTPException(status_code=400, detail="Invalid intake request") from None
    if envelope.correlation_id != correlation_header or envelope.event_id != event_header:
        raise HTTPException(status_code=400, detail="Invalid intake request")

    consent = envelope.payload.get("consent")
    signature = envelope.payload.get("signature")
    if not isinstance(consent, dict) or not isinstance(signature, str):
        raise HTTPException(status_code=400, detail="Invalid intake request")
    issued_at = consent.get("issuedAt")
    expires_at = consent.get("expiresAt")
    if (consent.get("intakeId") != envelope.intake_id or not isinstance(issued_at, str)
        or not issued_at.isdigit() or not isinstance(expires_at, str) or not expires_at.isdigit()
        or not re.fullmatch(r"0x[0-9a-fA-F]{130}", signature)):
        raise HTTPException(status_code=400, detail="Invalid intake request")
    now = int(time.time())
    issued = int(issued_at)
    if abs(now - issued) > _REPLAY_WINDOW_SECONDS or int(expires_at) <= now:
        raise HTTPException(status_code=403, detail="Request outside allowed time window")
    if derive_event_id(envelope.intake_id, issued_at, signature) != envelope.event_id:
        raise HTTPException(status_code=400, detail="Invalid intake request")

    db_pool = getattr(request.app.state, "db_pool", None)
    if db_pool is None:
        raise HTTPException(status_code=503, detail="Intake state store unavailable")
    try:
        is_new, existing = await claim_event(db_pool, envelope.event_id, envelope.correlation_id)
    except Exception:
        raise HTTPException(status_code=503, detail="Intake state store unavailable") from None
    if not is_new:
        if existing is None or existing["correlation_id"] != envelope.correlation_id:
            raise HTTPException(status_code=409, detail="Event conflicts with an existing submission")
        current = existing["status"]
        if current in {"completed", "escalated"}:
            return {
                "status": current.upper(), "event_id": envelope.event_id,
                "correlation_id": envelope.correlation_id, "cached": True,
                "reason_code": existing.get("reason_code"),
            }
        if current == "in_progress":
            raise HTTPException(status_code=409, detail="Event is currently in progress")
        raise HTTPException(status_code=409, detail="Event conflicts with an existing submission")

    handler: Handler | None = getattr(request.app.state, "intake_handler", None)
    if handler is None:
        # Release reservation so a configured handler can accept a later retry.
        try:
            async with db_pool.acquire() as connection:
                await connection.execute(
                    "DELETE FROM intake_events WHERE event_id=$1 AND correlation_id=$2 AND status='in_progress'",
                    envelope.event_id, envelope.correlation_id,
                )
        except Exception:
            pass
        raise HTTPException(status_code=503, detail="Intake worker unavailable")

    try:
        result = handler(envelope)
        if inspect.isawaitable(result):
            result = await result
    except Exception:
        # Do not expose error text or payload; reservation remains to prevent duplicate execution.
        raise HTTPException(status_code=502, detail="Intake processing failed") from None

    status_value = result.get("status", "QUEUED") if isinstance(result, dict) else "QUEUED"
    if status_value not in {"QUEUED", "COMPLETED", "ESCALATED", "success", "escalated_to_manual_review"}:
        status_value = "QUEUED"
    return {"status": status_value, "event_id": envelope.event_id,
            "correlation_id": envelope.correlation_id, "cached": False}
