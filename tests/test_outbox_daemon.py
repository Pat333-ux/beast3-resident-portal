import asyncio
import unittest

from outbox_daemon import OutboxRelayDaemon


class MemoryPublisher:
    def __init__(self, fail=False):
        self.fail = fail

    async def publish_escalation(self, payload, target):
        await asyncio.sleep(0)
        if self.fail:
            raise RuntimeError("private broker exception")


class MemoryConnection:
    async def execute(self, query, *args):
        return "UPDATE 1"

    async def fetchval(self, query, *args):
        return 0


class FakeRecord(dict):
    pass


class OutboxDaemonTests(unittest.IsolatedAsyncioTestCase):
    async def test_success_marks_published(self):
        pool = None
        daemon = OutboxRelayDaemon(pool, MemoryPublisher())
        conn = MemoryConnection()
        record = FakeRecord(
            event_id="evt-12345678",
            correlation_id="corr-12345678",
            target="dlq",
            payload={"event_id": "evt-12345678", "correlation_id": "corr-12345678"},
            publish_attempts=1,
        )
        self.assertTrue(await daemon.process_single_entry(conn, record))

    async def test_broker_failure_reschedules(self):
        daemon = OutboxRelayDaemon(None, MemoryPublisher(fail=True))
        conn = MemoryConnection()
        record = FakeRecord(
            event_id="evt-12345678",
            correlation_id="corr-12345678",
            target="manual_review",
            payload={"event_id": "evt-12345678"},
            publish_attempts=2,
        )
        self.assertFalse(await daemon.process_single_entry(conn, record))


if __name__ == "__main__":
    unittest.main()
