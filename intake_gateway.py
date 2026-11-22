"""Authenticated intake webhook boundary for the resident portal.

The gateway verifies the exact raw-body HMAC and timestamp before parsing input.
An application-specific async handler must be installed as
``app.state.intake_handler`` to adapt validated events to the configured worker
and its StateStore/DLQ pipeline. No unsafe development secret is provided.
"""
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


class IntakeWebhookEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    event_id: str = Field(min_length=8, max_length=128)
    correlation_id: str = Field(min_length=8, max_length=128)
    intake_id: str = Field(min_length=8, max_length=128)
    payload: dict[str, Any]
    attempt: int = Field(ge=0, le=10)

    @classmethod
    def from_json_bytes(cls, raw: bytes) -> "IntakeWebhookEnvelope":
        value = json.loads(raw)
        return cls.model_validate(value)

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
    max_clock_skew_seconds: int = 300,
) -> bool:
    """Verify timestamp-bound HMAC-SHA256, rejecting malformed or stale requests."""
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
    signed = timestamp_header.encode("ascii") + b"." + payload_bytes
    expected = hmac.new(secret_bytes, signed, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, match.group(1))


@app.post("/api/v1/intake")
async def receive_intake(request: Request) -> dict[str, str]:
    secret = os.getenv("INTAKE_WEBHOOK_SECRET", "")
    if len(secret.encode("utf-8")) < 32:
        raise HTTPException(status_code=503, detail="Intake gateway unavailable")

    correlation_header = request.headers.get("x-correlation-id", "")
    event_header = request.headers.get("x-event-id", "")
    if not _SAFE_ID.fullmatch(correlation_header) or not _SAFE_ID.fullmatch(event_header):
        raise HTTPException(status_code=400, detail="Invalid intake request")

    raw = await request.body()
    if len(raw) > 64 * 1024:
        raise HTTPException(status_code=413, detail="Intake request too large")
    if not verify_hmac_signature(
        raw,
        request.headers.get("x-signature-256"),
        request.headers.get("x-request-timestamp"),
        secret=secret,
    ):
        raise HTTPException(status_code=401, detail="Invalid request signature")

    try:
        envelope = IntakeWebhookEnvelope.from_json_bytes(raw)
        envelope.validate_identifiers()
    except (ValidationError, ValueError, TypeError, json.JSONDecodeError):
        raise HTTPException(status_code=400, detail="Invalid intake request") from None
    if envelope.correlation_id != correlation_header or envelope.event_id != event_header:
        raise HTTPException(status_code=400, detail="Invalid intake request")

    handler: Handler | None = getattr(request.app.state, "intake_handler", None)
    if handler is None:
        raise HTTPException(status_code=503, detail="Intake worker unavailable")
    try:
        result = handler(envelope)
        if inspect.isawaitable(result):
            result = await result
    except Exception:
        # Do not expose exception messages or request payloads.
        raise HTTPException(status_code=502, detail="Intake processing failed") from None

    status = result.get("status", "QUEUED") if isinstance(result, dict) else "QUEUED"
    if status not in {"QUEUED", "COMPLETED", "ESCALATED", "success", "escalated_to_manual_review"}:
        status = "QUEUED"
    return {
        "status": status,
        "event_id": envelope.event_id,
        "correlation_id": envelope.correlation_id,
    }
