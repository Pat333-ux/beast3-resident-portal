"""Strict intake validation and race-safe portal dispatch boundary.

This module deliberately stores only correlation metadata and scrubbed references.
Raw intake data must not be sent to logs or quarantine records.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator


class CaseState(str, Enum):
    NEEDS_INFO = "needs_info"
    READY_FOR_TRIAGE = "ready_for_triage"
    QUEUED_FOR_REVIEW = "queued_for_review"
    EXTERNAL_PENDING = "external_pending"
    COMPLETED = "completed"
    REJECTED = "rejected"
    CIRCUIT_PAUSED = "circuit_paused"
    MANUAL_REVIEW = "manual_review"


ALLOWED_TRANSITIONS: dict[CaseState, frozenset[CaseState]] = {
    CaseState.NEEDS_INFO: frozenset({CaseState.READY_FOR_TRIAGE, CaseState.REJECTED, CaseState.CIRCUIT_PAUSED}),
    CaseState.READY_FOR_TRIAGE: frozenset({CaseState.QUEUED_FOR_REVIEW, CaseState.MANUAL_REVIEW, CaseState.EXTERNAL_PENDING, CaseState.REJECTED, CaseState.CIRCUIT_PAUSED}),
    CaseState.QUEUED_FOR_REVIEW: frozenset({CaseState.READY_FOR_TRIAGE, CaseState.MANUAL_REVIEW, CaseState.EXTERNAL_PENDING, CaseState.REJECTED, CaseState.CIRCUIT_PAUSED}),
    CaseState.MANUAL_REVIEW: frozenset({CaseState.READY_FOR_TRIAGE, CaseState.EXTERNAL_PENDING, CaseState.COMPLETED, CaseState.REJECTED, CaseState.CIRCUIT_PAUSED}),
    CaseState.EXTERNAL_PENDING: frozenset({CaseState.COMPLETED, CaseState.MANUAL_REVIEW, CaseState.REJECTED, CaseState.CIRCUIT_PAUSED}),
    CaseState.COMPLETED: frozenset(),
    CaseState.REJECTED: frozenset(),
    CaseState.CIRCUIT_PAUSED: frozenset({CaseState.NEEDS_INFO, CaseState.READY_FOR_TRIAGE, CaseState.QUEUED_FOR_REVIEW, CaseState.MANUAL_REVIEW, CaseState.EXTERNAL_PENDING}),
}


def assert_valid_transition(source: CaseState, target: CaseState) -> None:
    if target not in ALLOWED_TRANSITIONS[source]:
        raise ValueError(f"Invalid case state transition: {source.value} -> {target.value}")


class Jurisdiction(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    state: str = Field(min_length=2, max_length=32)
    county: str = Field(min_length=2, max_length=64)
    municipality: str | None = Field(default=None, max_length=64)


class HouseholdProfile(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    householdSize: int = Field(ge=1, le=30)
    grossMonthlyIncomeCents: int = Field(ge=0)
    liquidAssetsCents: int = Field(ge=0)
    monthlyUtilityExpenseCents: int | None = Field(default=None, ge=0)
    hasSeniorOrDisability: bool | None = None
    isVeteranHousehold: bool | None = None


class PiiReference(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    scrubbedDigest: str = Field(min_length=16, max_length=128)
    vaultRef: str = Field(min_length=4, max_length=128)


class HouseholdIntakeV1(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    schemaVersion: str = Field(pattern=r"^1\.0\.0$")
    intakeId: str = Field(min_length=8, max_length=128)
    idempotencyKey: str = Field(min_length=8, max_length=128)
    submittedAt: str
    jurisdiction: Jurisdiction
    householdProfile: HouseholdProfile
    requestedTracks: list[str] = Field(min_length=1)
    piiReference: PiiReference | None = None

    @field_validator("submittedAt")
    @classmethod
    def validate_submitted_at(cls, value: str) -> str:
        try:
            datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("must be a valid ISO 8601 date-time") from exc
        if "T" not in value:
            raise ValueError("must include a date and time")
        return value

    @field_validator("requestedTracks")
    @classmethod
    def validate_tracks(cls, value: list[str]) -> list[str]:
        allowed = {
            "SNAP", "MEDICAID", "LIHEAP", "FSSA", "VA_RELIEF", "IHCDA_HOUSING",
            "CREDIT_REPAIR", "HOME_LOAN", "EDUCATION_FAFSA", "GRANTS", "CARE_AUTOMATION",
        }
        if len(set(value)) != len(value):
            raise ValueError("requestedTracks must contain unique values")
        invalid = [track for track in value if track not in allowed]
        if invalid:
            raise ValueError("requestedTracks contains an unsupported track")
        return value


# JSON-safe record stored atomically with the idempotency key.
class StoredCase:
    def __init__(self, case_id: str, current_state: CaseState, previous_state: CaseState | None = None):
        self.case_id = case_id
        self.current_state = current_state
        self.previous_state = previous_state


class IdempotencyStore(Protocol):
    async def get(self, key: str) -> StoredCase | None: ...

    async def put_if_absent(self, key: str, record: StoredCase) -> bool: ...


class QuarantineSink(Protocol):
    async def record(self, entry: dict[str, Any]) -> None: ...


def _candidate_identifier(candidate: Any, name: str, fallback: str) -> str:
    if isinstance(candidate, dict):
        value = candidate.get(name)
        if isinstance(value, str) and 8 <= len(value) <= 128:
            return value
    return fallback


def _safe_digest(candidate: Any) -> str | None:
    if not isinstance(candidate, dict):
        return None
    pii = candidate.get("piiReference")
    if not isinstance(pii, dict):
        return None
    digest = pii.get("scrubbedDigest")
    return digest if isinstance(digest, str) and 16 <= len(digest) <= 128 else None


def _format_validation_errors(exc: ValidationError) -> list[str]:
    # Exclude Pydantic's input/value fields so raw PII is never echoed into DLQ data.
    return [
        f"{'.'.join(str(part) for part in error['loc']) or 'root'}: {error['msg']}"
        for error in exc.errors(include_input=False)
    ]


async def process_portal_submission(
    *,
    raw_payload: Any,
    case_id: str,
    idempotency_store: IdempotencyStore,
    quarantine_sink: QuarantineSink,
    has_missing_verification_docs: bool = False,
    circuit_breaker_active: bool = False,
) -> dict[str, Any]:
    """Validate, quarantine, deduplicate, then initialize the case state.

    A production IdempotencyStore must implement ``put_if_absent`` atomically
    (for example, with a unique database constraint) across all workers.
    """
    candidate = raw_payload if isinstance(raw_payload, dict) else {}
    intake_id = _candidate_identifier(candidate, "intakeId", "unknown-intake")
    idempotency_key = _candidate_identifier(candidate, "idempotencyKey", "unknown-idempotency-key")

    try:
        intake = HouseholdIntakeV1.model_validate(raw_payload)
    except (ValidationError, TypeError) as exc:
        errors = _format_validation_errors(exc) if isinstance(exc, ValidationError) else ["root: payload must be an object"]
        await quarantine_sink.record({
            "intakeId": intake_id,
            "idempotencyKey": idempotency_key,
            "reason": "SCHEMA_VALIDATION_FAILED",
            "errors": errors,
            "scrubbedDigest": _safe_digest(candidate),
        })
        return {
            "status": "quarantined", "intakeId": intake_id,
            "idempotencyKey": idempotency_key,
            "reason": "SCHEMA_VALIDATION_FAILED", "errors": errors,
        }

    if not isinstance(case_id, str) or not 8 <= len(case_id) <= 128:
        reason = "INVALID_CASE_ID"
        errors = ["caseId: must be a string between 8 and 128 characters"]
        await quarantine_sink.record({
            "intakeId": intake.intakeId,
            "idempotencyKey": intake.idempotencyKey,
            "reason": reason,
            "errors": errors,
            "scrubbedDigest": intake.piiReference.scrubbedDigest if intake.piiReference else None,
        })
        return {"status": "quarantined", "intakeId": intake.intakeId,
                "idempotencyKey": intake.idempotencyKey, "reason": reason, "errors": errors}

    existing = await idempotency_store.get(intake.idempotencyKey)
    if existing is not None:
        return {
            "status": "deduplicated", "intakeId": intake.intakeId,
            "caseId": existing.case_id, "idempotencyKey": intake.idempotencyKey,
            "currentState": existing.current_state.value,
        }

    base_state = CaseState.NEEDS_INFO if has_missing_verification_docs else CaseState.READY_FOR_TRIAGE
    initial_state = CaseState.CIRCUIT_PAUSED if circuit_breaker_active else base_state
    previous_state = base_state if circuit_breaker_active else None
    if circuit_breaker_active:
        assert_valid_transition(base_state, CaseState.CIRCUIT_PAUSED)

    created = await idempotency_store.put_if_absent(
        intake.idempotencyKey,
        StoredCase(case_id, initial_state, previous_state),
    )
    if not created:
        raced = await idempotency_store.get(intake.idempotencyKey)
        return {
            "status": "deduplicated", "intakeId": intake.intakeId,
            "caseId": raced.case_id if raced else case_id,
            "idempotencyKey": intake.idempotencyKey,
            "currentState": raced.current_state.value if raced else initial_state.value,
        }

    return {
        "status": "accepted", "intakeId": intake.intakeId, "caseId": case_id,
        "idempotencyKey": intake.idempotencyKey, "initialState": initial_state.value,
        "previousState": previous_state.value if previous_state else None,
        "scrubbedDigest": intake.piiReference.scrubbedDigest if intake.piiReference else None,
    }
