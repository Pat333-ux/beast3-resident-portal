import hashlib
import hmac
import unittest

from intake_gateway import verify_hmac_signature


class HmacVerificationTests(unittest.TestCase):
    def setUp(self):
        self.secret = "a" * 32
        self.timestamp = "1800000000"
        self.body = b'{"event_id":"evt_12345678"}'
        self.digest = hmac.new(
            self.secret.encode(),
            self.timestamp.encode() + b"." + self.body,
            hashlib.sha256,
        ).hexdigest()

    def test_accepts_valid_recent_signature(self):
        self.assertTrue(
            verify_hmac_signature(
                self.body,
                f"sha256={self.digest}",
                self.timestamp,
                secret=self.secret,
                now_seconds=1_800_000_000,
            )
        )

    def test_rejects_modified_body(self):
        self.assertFalse(
            verify_hmac_signature(
                self.body + b" ",
                f"sha256={self.digest}",
                self.timestamp,
                secret=self.secret,
                now_seconds=1_800_000_000,
            )
        )

    def test_rejects_stale_request(self):
        self.assertFalse(
            verify_hmac_signature(
                self.body,
                f"sha256={self.digest}",
                self.timestamp,
                secret=self.secret,
                now_seconds=1_800_000_400,
            )
        )

    def test_rejects_weak_secret_and_malformed_signature(self):
        self.assertFalse(
            verify_hmac_signature(
                self.body,
                f"sha256={self.digest}",
                self.timestamp,
                secret="short",
                now_seconds=1_800_000_000,
            )
        )
        self.assertFalse(
            verify_hmac_signature(
                self.body,
                "sha256=not-hex",
                self.timestamp,
                secret=self.secret,
                now_seconds=1_800_000_000,
            )
        )


if __name__ == "__main__":
    unittest.main()
