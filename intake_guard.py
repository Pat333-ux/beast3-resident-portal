"""Canonical case states and state-transition validation for BEAST 3.0."""
from __future__ import annotations

from enum import Enum


class CaseState(str, Enum):
    RECEIVED = "received"
    READY_FOR_TRIAGE = "ready_for_triage"
    QUEUED_FOR_REVIEW = "queued_for_review"
    CIRCUIT_PAUSED = "circuit_paused"
    EXTERNAL_PENDING = "external_pending"
    MANUAL_REVIEW = "manual_review"
    COMPLETED = "completed"


ALLOWED_TRANSITIONS: dict[CaseState, frozenset[CaseState]] = {
    CaseState.RECEIVED: frozenset({CaseState.READY_FOR_TRIAGE, CaseState.MANUAL_REVIEW}),
    CaseState.READY_FOR_TRIAGE: frozenset({CaseState.QUEUED_FOR_REVIEW, CaseState.MANUAL_REVIEW}),
    CaseState.QUEUED_FOR_REVIEW: frozenset({
        CaseState.CIRCUIT_PAUSED, CaseState.EXTERNAL_PENDING,
        CaseState.MANUAL_REVIEW, CaseState.COMPLETED,
    }),
    CaseState.CIRCUIT_PAUSED: frozenset({CaseState.QUEUED_FOR_REVIEW, CaseState.MANUAL_REVIEW}),
    CaseState.EXTERNAL_PENDING: frozenset({CaseState.QUEUED_FOR_REVIEW, CaseState.MANUAL_REVIEW, CaseState.COMPLETED}),
    CaseState.MANUAL_REVIEW: frozenset({CaseState.QUEUED_FOR_REVIEW, CaseState.COMPLETED}),
    CaseState.COMPLETED: frozenset(),
}


def assert_valid_transition(source: CaseState, target: CaseState) -> None:
    """Raise ValueError unless target is a permitted successor of source."""
    if target not in ALLOWED_TRANSITIONS.get(source, frozenset()):
        raise ValueError(f"Transition from {source.value} to {target.value} is not allowed")
