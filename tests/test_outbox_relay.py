import asyncio
import unittest

from outbox_relay import OutboxRecord, OutboxRelay


class MemoryOutboxStore:
    def __init__(self, records):
        self.records = list(records)
        self.published = []

    async def fetch_pending_outbox(self, *, limit):
        return self.records[:limit]

    async def mark_outbox_published(self, *, outbox_id):
        self.published.append(outbox_id)
        self.records = [record for record in self.records if record.outbox_id != outbox_id]


class MemoryPublisher:
    def __init__(self):
        self.entries = []
        self.fail = False

    async def publish_dlq(self, **entry):
        if self.fail:
            raise RuntimeError("do not leak this exception")
        self.entries.append(entry)


def record(outbox_id="outbox-0001"):
    return OutboxRecord(
        outbox_id=outbox_id,
        intake_id="intake-0001",
        case_id="case-00001",
        idempotency_key="idem-key-0001",
        division=19,
        reason="RETRY_EXHAUSTED",
        attempts=3,
        last_error="RETRY_EXHAUSTED",
        scrubbed_digest="a" * 32,
    )


class OutboxRelayTests(unittest.IsolatedAsyncioTestCase):
    async def test_publish_then_mark(self):
        store = MemoryOutboxStore([record()])
        publisher = MemoryPublisher()
        relay = OutboxRelay(store=store, publisher=publisher, poll_interval_seconds=0.01)

        self.assertEqual(await relay.relay_once(), 1)
        self.assertEqual(publisher.entries[0]["outbox_id"], "outbox-0001")
        self.assertEqual(store.published, ["outbox-0001"])
        self.assertEqual(store.records, [])

    async def test_publish_failure_leaves_record_pending(self):
        store = MemoryOutboxStore([record()])
        publisher = MemoryPublisher()
        publisher.fail = True
        relay = OutboxRelay(store=store, publisher=publisher)

        self.assertEqual(await relay.relay_once(), 0)
        self.assertEqual(store.published, [])
        self.assertEqual(len(store.records), 1)

    async def test_ack_failure_keeps_record_for_idempotent_redelivery(self):
        class AckFailureStore(MemoryOutboxStore):
            async def mark_outbox_published(self, *, outbox_id):
                raise RuntimeError("private error text")

        store = AckFailureStore([record()])
        publisher = MemoryPublisher()
        relay = OutboxRelay(store=store, publisher=publisher)

        self.assertEqual(await relay.relay_once(), 0)
        self.assertEqual(len(publisher.entries), 1)
        self.assertEqual(len(store.records), 1)

    async def test_invalid_record_is_not_published_or_marked(self):
        invalid = OutboxRecord(
            outbox_id="unsafe id", intake_id="intake-0001", case_id="case-00001",
            idempotency_key="idem-key-0001", division=19, reason="RETRY_EXHAUSTED",
            attempts=1, last_error="RETRY_EXHAUSTED",
        )
        store = MemoryOutboxStore([invalid])
        publisher = MemoryPublisher()
        relay = OutboxRelay(store=store, publisher=publisher)

        self.assertEqual(await relay.relay_once(), 0)
        self.assertEqual(publisher.entries, [])
        self.assertEqual(store.published, [])

    async def test_run_stops_when_event_is_set(self):
        store = MemoryOutboxStore([])
        relay = OutboxRelay(store=store, publisher=MemoryPublisher(), poll_interval_seconds=0.01)
        stop = asyncio.Event()
        task = asyncio.create_task(relay.run(stop))
        await asyncio.sleep(0.02)
        stop.set()
        await asyncio.wait_for(task, timeout=1)


if __name__ == "__main__":
    unittest.main()
