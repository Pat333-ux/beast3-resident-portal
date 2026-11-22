import asyncio
import logging
import unittest
from unittest.mock import patch

from intake_guard import CaseState
from retry_escalation_worker import (
    NonRetryableError,
    TransientError,
    execute_with_retry_and_escalation,
)


class MemoryDLQ:
    def __init__(self):
        self.entries = []

    async def publish_dlq(self, **entry):
        self.entries.append(entry)


class MemoryStateStore:
    def __init__(self):
        self.updates = []

    async def update_state(self, **update):
        self.updates.append(update)


class RetryEscalationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.dlq = MemoryDLQ()
        self.store = MemoryStateStore()
        self.common = dict(
            intake_id="intake-0001", case_id="case-0001",
            idempotency_key="idem-key-0001", division=19,
            current_state=CaseState.QUEUED_FOR_REVIEW,
            dlq_sink=self.dlq, state_store=self.store,
            scrubbed_digest="a" * 32,
        )

    async def test_success_does_not_escalate(self):
        async def task():
            return {"ok": True}

        result = await execute_with_retry_and_escalation(task_fn=task, **self.common)
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["attempts"], 1)
        self.assertEqual(self.dlq.entries, [])
        self.assertEqual(self.store.updates, [])

    async def test_transient_failures_retry_within_attempt_limit_then_escalate(self):
        calls = 0

        async def task():
            nonlocal calls
            calls += 1
            raise TransientError("private payload must not appear in output")

        with patch("retry_escalation_worker.asyncio.sleep", new_callable=unittest.mock.AsyncMock):
            result = await execute_with_retry_and_escalation(
                task_fn=task, max_attempts=3, base_backoff_seconds=0, **self.common
            )
        self.assertEqual(calls, 3)
        self.assertEqual(result["status"], "escalated_to_manual_review")
        self.assertEqual(result["attempts"], 3)
        self.assertEqual(self.store.updates[0]["target_state"], CaseState.MANUAL_REVIEW)
        self.assertEqual(self.dlq.entries[0]["last_error"], "RETRY_EXHAUSTED")

    async def test_terminal_failure_skips_retry_and_scrubs_error_text(self):
        calls = 0

        async def task():
            nonlocal calls
            calls += 1
            raise NonRetryableError("user@example.com")

        result = await execute_with_retry_and_escalation(task_fn=task, **self.common)
        self.assertEqual(calls, 1)
        self.assertEqual(result["reason"], "TERMINAL_FAILURE")
        self.assertNotIn("user@example.com", str(result))
        self.assertNotIn("user@example.com", str(self.dlq.entries))

    async def test_unhandled_error_is_terminal(self):
        calls = 0

        async def task():
            nonlocal calls
            calls += 1
            raise RuntimeError("sensitive exception")

        result = await execute_with_retry_and_escalation(task_fn=task, **self.common)
        self.assertEqual(calls, 1)
        self.assertEqual(result["reason"], "UNHANDLED_FAILURE")

    async def test_unsafe_identifier_is_rejected(self):
        with self.assertRaises(ValueError):
            await execute_with_retry_and_escalation(
                task_fn=lambda: asyncio.sleep(0),
                **{**self.common, "intake_id": "user@example.com"},
            )


if __name__ == "__main__":
    unittest.main()
