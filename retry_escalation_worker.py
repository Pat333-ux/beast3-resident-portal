"""Safe retry, dead-letter, and manual-review escalation worker."""
from __future__ import annotations

import asyncio
import logging
import random
import re
from typing import Any, Awaitable, Callable, Protocol

from intake_guard import CaseState, assert_valid_transition

logger = logging.getLogger("beast3.escalation")
_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9._:-]{8,128}$")
_DIGEST_RE = re.compile(r"^[A-Fa-f0-9]{16,128}$")


class TransientError(Exception):
    """An explicitly classified transient downstream failure that may be retried."""


class NonRetryableError(Exception):
    """A terminal failure. Exception text is deliberately never propagated."""


class DLQSink(Protocol):
    async def publish_dlq(
        self,
        *,
        intake_id: str,
        case_id: str,
        idempotency_key: str,
        division: int,
        reason: str,
        attempts: int,
        last_error: str,
        scrubbed_digest: str | None = None,
    ) -> None: ...


class StateStore(Protocol):
    async def update_state(
        self,
        *,
        case_id: str,
        idempotency_key: str,
        target_state: CaseState,
        reason: str,
    ) -> None: ...


def _validate_correlation(value: str, field: str) -> None:
    if not isinstance(value, str) or not _IDENTIFIER_RE.fullmatch(value):
        raise ValueError(f"{field} must be a safe identifier")


async def execute_with_retry_and_escalation(
    *,
    task_fn: Callable[[], Awaitable[Any]],
    intake_id: str,
    case_id: str,
    idempotency_key: str,
    division: int,
    current_state: CaseState,
    dlq_sink: DLQSink,
    state_store: StateStore,
    scrubbed_digest: str | None = None,
    max_attempts: int = 5,
    base_backoff_seconds: float = 1.0,
    max_backoff_seconds: float = 30.0,
) -> dict[str, Any]:
    """Run task with bounded jittered retries, otherwise escalate and publish DLQ.

    Only ``TransientError`` is retried. Terminal and unexpected exceptions are
    short-circuited without exposing exception text. ``max_attempts`` includes
    the initial call and is strictly bounded to 1..10.
    """
    _validate_correlation(intake_id, "intake_id")
    _validate_correlation(case_id, "case_id")
    _validate_correlation(idempotency_key, "idempotency_key")
    if not isinstance(division, int) or isinstance(division, bool) or not 1 <= division <= 21:
        raise ValueError("division must be an integer from 1 through 21")
    if not isinstance(max_attempts, int) or isinstance(max_attempts, bool) or not 1 <= max_attempts <= 10:
        raise ValueError("max_attempts must be an integer from 1 through 10")
    if base_backoff_seconds < 0 or max_backoff_seconds < 0:
        raise ValueError("backoff limits must not be negative")
    if scrubbed_digest is not None and not _DIGEST_RE.fullmatch(scrubbed_digest):
        raise ValueError("scrubbed_digest must be a hexadecimal digest")

    attempts = 0
    failure_code = "RETRY_EXHAUSTED"
    while attempts < max_attempts:
        attempts += 1
        try:
            result = await task_fn()
            logger.info(
                "Task completed intakeId=%s caseId=%s idempotencyKey=%s scrubbedDigest=%s",
                intake_id, case_id, idempotency_key, scrubbed_digest or "none",
            )
            return {
                "status": "success",
                "intakeId": intake_id,
                "caseId": case_id,
                "idempotencyKey": idempotency_key,
                "attempts": attempts,
                "result": result,
            }
        except TransientError:
            failure_code = "RETRY_EXHAUSTED"
            if attempts >= max_attempts:
                break
            ceiling = min(base_backoff_seconds * (2 ** (attempts - 1)), max_backoff_seconds)
            await asyncio.sleep(random.uniform(0.0, ceiling))
        except NonRetryableError:
            failure_code = "TERMINAL_FAILURE"
            break
        except Exception:
            # Unclassified errors are terminal by default (fail closed).
            failure_code = "UNHANDLED_FAILURE"
            break

    logger.error(
        "Task escalated intakeId=%s caseId=%s idempotencyKey=%s scrubbedDigest=%s",
        intake_id, case_id, idempotency_key, scrubbed_digest or "none",
    )
    assert_valid_transition(current_state, CaseState.MANUAL_REVIEW)
    await state_store.update_state(
        case_id=case_id,
        idempotency_key=idempotency_key,
        target_state=CaseState.MANUAL_REVIEW,
        reason=failure_code,
    )
    await dlq_sink.publish_dlq(
        intake_id=intake_id,
        case_id=case_id,
        idempotency_key=idempotency_key,
        division=division,
        reason=failure_code,
        attempts=attempts,
        last_error=failure_code,
        scrubbed_digest=scrubbed_digest,
    )
    return {
        "status": "escalated_to_manual_review",
        "intakeId": intake_id,
        "caseId": case_id,
        "idempotencyKey": idempotency_key,
        "division": division,
        "currentState": CaseState.MANUAL_REVIEW.value,
        "attempts": attempts,
        "reason": failure_code,
        "scrubbedDigest": scrubbed_digest,
    }
