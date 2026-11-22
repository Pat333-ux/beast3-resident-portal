import asyncio
import unittest

from intake_guard import CaseState, StoredCase, assert_valid_transition, process_portal_submission


class MemoryStore:
    def __init__(self):
        self.records = {}
        self.lock = asyncio.Lock()

    async def get(self, key):
        return self.records.get(key)

    async def put_if_absent(self, key, record):
        async with self.lock:
            if key in self.records:
                return False
            self.records[key] = record
            return True


class MemoryQuarantine:
    def __init__(self):
        self.entries = []

    async def record(self, entry):
        self.entries.append(entry)


def valid_payload(**updates):
    payload = {
        "schemaVersion": "1.0.0",
        "intakeId": "intake-0001",
        "idempotencyKey": "idem-key-0001",
        "submittedAt": "2026-11-22T12:00:00Z",
        "jurisdiction": {"state": "IN", "county": "Vigo"},
        "householdProfile": {
            "householdSize": 2,
            "grossMonthlyIncomeCents": 250000,
            "liquidAssetsCents": 100000,
        },
        "requestedTracks": ["SNAP", "LIHEAP"],
    }
    payload.update(updates)
    return payload


class IntakeGuardTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.store = MemoryStore()
        self.quarantine = MemoryQuarantine()

    async def submit(self, payload, **kwargs):
        return await process_portal_submission(
            raw_payload=payload,
            case_id=kwargs.pop("case_id", "case-0001"),
            idempotency_store=self.store,
            quarantine_sink=self.quarantine,
            **kwargs,
        )

    async def test_accepts_valid_payload_without_inventing_defaults(self):
        result = await self.submit(valid_payload())
        self.assertEqual(result["status"], "accepted")
        self.assertEqual(result["initialState"], "ready_for_triage")
        self.assertIsNone(result["previousState"])

    async def test_missing_fields_are_quarantined_not_defaulted(self):
        payload = valid_payload()
        del payload["householdProfile"]["liquidAssetsCents"]
        result = await self.submit(payload)
        self.assertEqual(result["status"], "quarantined")
        self.assertEqual(result["reason"], "SCHEMA_VALIDATION_FAILED")
        self.assertTrue(any("liquidAssetsCents" in error for error in result["errors"]))
        self.assertEqual(len(self.quarantine.entries), 1)

    async def test_schema_version_mismatch_and_unknown_fields_quarantine(self):
        for payload in (valid_payload(schemaVersion="2.0.0"), valid_payload(unexpected="field")):
            result = await self.submit(payload)
            self.assertEqual(result["status"], "quarantined")

    async def test_strict_types_reject_coercion(self):
        payload = valid_payload()
        payload["householdProfile"]["householdSize"] = "2"
        result = await self.submit(payload)
        self.assertEqual(result["status"], "quarantined")

    async def test_duplicate_idempotency_key_returns_existing_case_and_state(self):
        first = await self.submit(valid_payload())
        second = await self.submit(valid_payload(), case_id="case-0002")
        self.assertEqual(first["status"], "accepted")
        self.assertEqual(second["status"], "deduplicated")
        self.assertEqual(second["caseId"], "case-0001")
        self.assertEqual(second["currentState"], "ready_for_triage")

    async def test_concurrent_duplicate_submissions_create_one_case(self):
        results = await asyncio.gather(
            self.submit(valid_payload(), case_id="case-0001"),
            self.submit(valid_payload(), case_id="case-0002"),
        )
        self.assertEqual(sorted(result["status"] for result in results), ["accepted", "deduplicated"])
        self.assertEqual(len(self.store.records), 1)

    async def test_circuit_pause_saves_previous_state_for_recovery(self):
        result = await self.submit(valid_payload(), has_missing_verification_docs=True, circuit_breaker_active=True)
        self.assertEqual(result["initialState"], "circuit_paused")
        self.assertEqual(result["previousState"], "needs_info")
        self.assertEqual(self.store.records["idem-key-0001"].previous_state, CaseState.NEEDS_INFO)

    async def test_transition_guard_rejects_terminal_state_changes(self):
        with self.assertRaises(ValueError):
            assert_valid_transition(CaseState.COMPLETED, CaseState.READY_FOR_TRIAGE)
        assert_valid_transition(CaseState.READY_FOR_TRIAGE, CaseState.CIRCUIT_PAUSED)

    async def test_invalid_payload_does_not_echo_submitted_pii(self):
        payload = valid_payload(secret="raw-sensitive-value")
        result = await self.submit(payload)
        self.assertEqual(result["status"], "quarantined")
        self.assertNotIn("raw-sensitive-value", str(self.quarantine.entries))


if __name__ == "__main__":
    unittest.main()
