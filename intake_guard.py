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
    RECEIVED = "received"
    NEEDS_INFO = "needs_info"
    READY_FOR_TRIAGE = "ready_for_triage"
    QUEUED_FOR_REVIEW = "queued_for_review"
    MANUAL_REVIEW = "manual_review"
    EXTERNAL_PENDING = "external_pending"
    COMPLETED = "completed"
    REJECTED = "rejected"
    CIRCUIT_PAUSED = "circuit_paused"


ALLOWED_TRANSITIONS: dict[CaseState, frozenset[CaseState]] = {
    CaseState.RECEIVED: frozenset({
        CaseState.READY_FOR_TRIAGE,
        CaseState.NEEDS_INFO,
        CaseState.MANUAL_REVIEW,
        CaseState.REJECTED,
        CaseState.CIRCUIT_PAUSED,
    }),
    CaseState.NEEDS_INFO: frozenset({
        CaseState.READY_FOR_TRIAGE,
        CaseState.REJECTED,
        CaseState.CIRCUIT_PAUSED,
    }),
    CaseState.READY_FOR_TRIAGE: frozenset({
        CaseState.QUEUED_FOR_REVIEW,
        CaseState.MANUAL_REVIEW,
        CaseState.EXTERNAL_PENDING,
        CaseState.REJECTED,
        CaseState.CIRCUIT_PAUSED,
    }),
    CaseState.QUEUED_FOR_REVIEW: frozenset({
        CaseState.READY_FOR_TRIAGE,
        CaseState.MANUAL_REVIEW,
        CaseState.EXTERNAL_PENDING,
        CaseState.COMPLETED,
        CaseState.REJECTED,
        CaseState.CIRCUIT_PAUSED,
    }),
    CaseState.MANUAL_REVIEW: frozenset({
        CaseState.READY_FOR_TRIAGE,
        CaseState.QUEUED_FOR_REVIEW,
        CaseState.EXTERNAL_PENDING,
        CaseState.COMPLETED,
        CaseState.REJECTED,
        CaseState.CIRCUIT_PAUSED,
    }),
    CaseState.EXTERNAL_PENDING: frozenset({
        CaseState.QUEUED_FOR_REVIEW,
        CaseState.MANUAL_REVIEW,
        CaseState.COMPLETED,
        CaseState.REJECTED,
        CaseState.CIRCUIT_PAUSED,
    }),
    CaseState.CIRCUIT_PAUSED: frozenset({
        CaseState.NEEDS_INFO,
        CaseState.READY_FOR_TRIAGE,
        CaseState.QUEUED_FOR_REVIEW,
        CaseState.MANUAL_REVIEW,
        CaseState.EXTERNAL_PENDING,
    }),
    CaseState.COMPLETED: frozenset(),
    CaseState.REJECTED: frozenset(),
}


def assert_valid_transition(source: CaseState, target: CaseState) -> None:
    """Raise ValueError unless target is a permitted successor of source."""
    if target not in ALLOWED_TRANSITIONS.get(source, frozenset()):
        raise ValueError(f"Transition from {source.value} to {target.value} is not allowed")


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