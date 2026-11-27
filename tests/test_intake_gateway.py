import hashlib
import hmac
import unittest
from unittest.mock import AsyncMock, patch

from intake_gateway import claim_event, derive_event_id, verify_hmac_signature


class HmacVerificationTests(unittest.TestCase):
    def setUp(self):
        self.secret = "a" * 32
        self.timestamp = "1800000000"
        self.body = b'{"event_id":"evt_12345678"}'
        self.digest = hmac.new(self.secret.encode(), self.timestamp.encode() + b"." + self.body, hashlib.sha256).hexdigest()

    def test_accepts_valid_recent_signature(self):
        self.assertTrue(verify_hmac_signature(self.body, f"sha256={self.digest}", self.timestamp,
                                              secret=self.secret, now_seconds=1_800_000_000))

    def test_rejects_modified_body(self):
        self.assertFalse(verify_hmac_signature(self.body + b" ", f"sha256={self.digest}", self.timestamp,
                                               secret=self.secret, now_seconds=1_800_000_000))

    def test_rejects_stale_request(self):
        self.assertFalse(verify_hmac_signature(self.body, f"sha256={self.digest}", self.timestamp,
                                               secret=self.secret, now_seconds=1_800_000_400))

    def test_rejects_weak_secret_and_malformed_signature(self):
        self.assertFalse(verify_hmac_signature(self.body, f"sha256={self.digest}", self.timestamp,
                                               secret="short", now_seconds=1_800_000_000))
        self.assertFalse(verify_hmac_signature(self.body, "sha256=not-hex", self.timestamp,
                                               secret=self.secret, now_seconds=1_800_000_000))

    def test_deterministic_event_id(self):
        self.assertEqual(derive_event_id("intake-1234", "1800000000", "0xabc"),
                         derive_event_id("intake-1234", "1800000000", "0xabc"))
        self.assertNotEqual(derive_event_id("intake-1234", "1800000000", "0xabc"),
                            derive_event_id("intake-1234", "1800000001", "0xabc"))


class IdempotencyClaimTests(unittest.IsolatedAsyncioTestCase):
    async def test_atomically_claims_new_event(self):
        connection = AsyncMock()
        connection.fetchrow = AsyncMock(return_value={"event_id": "evt-12345678"})

        class Acquire:
            async def __aenter__(self): return connection
            async def __aexit__(self, *args): return False

        pool = type("Pool", (), {"acquire": lambda self: Acquire()})()
        created, existing = await claim_event(pool, "evt-12345678", "corr-12345678")
        self.assertTrue(created)
        self.assertIsNone(existing)
        self.assertIn("ON CONFLICT (event_id) DO NOTHING", connection.fetchrow.await_args.args[0])

    async def test_returns_existing_status_on_duplicate(self):
        connection = AsyncMock()
        connection.fetchrow = AsyncMock(side_effect=[None, {
            "event_id": "evt-12345678", "correlation_id": "corr-12345678", "status": "completed",
            "escalation_target": None, "reason_code": None,
        }])

        class Acquire:
            async def __aenter__(self): return connection
            async def __aexit__(self, *args): return False

        pool = type("Pool", (), {"acquire": lambda self: Acquire()})()
        created, existing = await claim_event(pool, "evt-12345678", "corr-12345678")
        self.assertFalse(created)
        self.assertEqual(existing["status"], "completed")


if __name__ == "__main__":
    unittest.main()
