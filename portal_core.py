"""Draft persistence and side-effect-free Phase A resident triage.

This module stores only attachment references and hashes, never raw attachment
bytes. Benefit values and thresholds are illustrative estimates, not an agency
determination or a substitute for current program rules.
"""
from __future__ import annotations

import hashlib
import logging
import re
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator

from intake_guard import HouseholdIntakeV1

logger = logging.getLogger("beast3.portal")

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class DocumentType(str, Enum):
    INCOME_STUB = "income_stub"
    TAX_RETURN = "tax_return"
    UTILITY_BILL = "utility_bill"
    GOVT_ID = "govt_id"
    DD_214 = "dd_214"
    MEDICAL_RECORD = "medical_record"


class AttachmentReference(BaseModel):
    """A pointer to a separately secured file; raw content is never stored here."""

    model_config = ConfigDict(extra="forbid", strict=True)

    attachmentId: str = Field(min_length=8, max_length=128)
    docType: DocumentType
    contentHashSha256: str = Field(min_length=64, max_length=64)
    vaultRef: str = Field(min_length=4, max_length=128)
    uploadedAt: str

    @field_validator("contentHashSha256")
    @classmethod
    def validate_sha256(cls, value: str) -> str:
        if not _SHA256_RE.fullmatch(value):
            raise ValueError("contentHashSha256 must be a lowercase SHA-256 hex digest")
        return value

    @field_validator("uploadedAt")
    @classmethod
    def validate_uploaded_at(cls, value: str) -> str:
        try:
            datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("uploadedAt must be a valid ISO 8601 date-time") from exc
        if "T" not in value:
            raise ValueError("uploadedAt must include a date and time")
        return value


class DraftSession(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    sessionId: str = Field(min_length=8, max_length=128)
    intakeId: str = Field(min_length=8, max_length=128)
    lastSavedAt: str
    completedSections: list[str] = Field(default_factory=list)
    partialProfile: dict[str, Any] = Field(default_factory=dict)
    attachments: list[AttachmentReference] = Field(default_factory=list)


class DraftStore(Protocol):
    """Persistence adapter; production implementations should encrypt at rest."""

    async def save_draft(self, draft: DraftSession) -> None: ...

    async def get_draft(self, session_id: str) -> DraftSession | None: ...


class PhaseATriageResult(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    intakeId: str
    caseId: str
    matchedTracks: list[str]
    missingDocuments: list[DocumentType]
    estimatedBenefitCents: int = Field(ge=0)
    isReadyForDispatcher: bool


def compute_content_hash_sha256(raw_bytes: bytes) -> str:
    """Return a SHA-256 content hash for an attachment without persisting bytes."""
    if not isinstance(raw_bytes, bytes):
        raise TypeError("raw_bytes must be bytes")
    return hashlib.sha256(raw_bytes).hexdigest()


def compute_scrubbed_digest(raw_bytes: bytes) -> str:
    """Return a SHA-256 digest suitable for correlation, not a PII scrubber.

    Hashing does not remove PII from the source. Callers must not log or persist
    the raw payload; use a keyed digest if low-entropy inputs could be guessed.
    """
    return compute_content_hash_sha256(raw_bytes)


class PortalCoreEngine:
    def __init__(self, draft_store: DraftStore):
        self.draft_store = draft_store

    async def save_resident_draft(
        self,
        session_id: str,
        intake_id: str,
        completed_sections: list[str],
        partial_profile: dict[str, Any],
        attachments: list[AttachmentReference],
    ) -> DraftSession:
        """Persist an intermediate form without dispatching or routing it."""
        draft = DraftSession(
            sessionId=session_id,
            intakeId=intake_id,
            lastSavedAt=datetime.now(timezone.utc).isoformat(),
            completedSections=completed_sections,
            partialProfile=partial_profile,
            attachments=attachments,
        )
        await self.draft_store.save_draft(draft)
        logger.info("Draft session saved sessionId=%s intakeId=%s", session_id, intake_id)
        return draft

    def execute_phase_a_triage(
        self,
        intake: HouseholdIntakeV1,
        attachments: list[AttachmentReference],
    ) -> PhaseATriageResult:
        """Calculate preliminary matches and document readiness, without I/O.

        Thresholds and benefit amounts below are illustrative. This function
        performs no external submission, agency lookup, or routing action.
        """
        matched_tracks: list[str] = []
        missing_docs: list[DocumentType] = []
        estimated_benefit = 0

        profile = intake.householdProfile
        annual_income_cents = profile.grossMonthlyIncomeCents * 12

        # Illustrative $25,000 annual ceiling, represented exactly in cents.
        if annual_income_cents <= 2_500_000:
            matched_tracks.extend(["SNAP", "MEDICAID"])
            estimated_benefit += 35_000  # Illustrative estimate: $350/month.

        if profile.monthlyUtilityExpenseCents and profile.monthlyUtilityExpenseCents > 15_000:
            matched_tracks.append("LIHEAP")
            estimated_benefit += 60_000  # Illustrative one-time $600 estimate.

        if profile.isVeteranHousehold and "VA_RELIEF" in intake.requestedTracks:
            matched_tracks.append("VA_RELIEF")

        uploaded_doc_types = {attachment.docType for attachment in attachments}
        if "SNAP" in matched_tracks and DocumentType.INCOME_STUB not in uploaded_doc_types:
            missing_docs.append(DocumentType.INCOME_STUB)
        if "VA_RELIEF" in matched_tracks and DocumentType.DD_214 not in uploaded_doc_types:
            missing_docs.append(DocumentType.DD_214)

        is_ready = not missing_docs
        logger.info(
            "Phase A triage complete intakeId=%s matchedTracks=%s missingDocs=%s ready=%s",
            intake.intakeId,
            matched_tracks,
            missing_docs,
            is_ready,
        )
        return PhaseATriageResult(
            intakeId=intake.intakeId,
            caseId=f"case-{intake.intakeId[7:]}",
            matchedTracks=matched_tracks,
            missingDocuments=missing_docs,
            estimatedBenefitCents=estimated_benefit,
            isReadyForDispatcher=is_ready,
        )
