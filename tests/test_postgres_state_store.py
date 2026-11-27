import unittest
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from postgres_state_store import PostgresStateStore


class FakeConnection:
    def __init__(self):
        self.statements = []

    @asynccontextmanager
    async def transaction(self):
        self.statements.append(("BEGIN", ()))
        try:
            yield self
        except Exception:
            self.statements.append(("ROLLBACK", ()))
            raise
        else:
            self.statements.append(("COMMIT", ()))

    async def fetchrow(self, sql, *args):
        self.statements.append((sql, args))
        return {"event_id": args[0]}

    async def execute(self, sql, *args):
        self.statements.append((sql, args))
        return "INSERT 0 1"


class FakeAcquire:
    def __init__(self, conn):
        self.conn = conn

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, *args):
        return False


class FakePool:
    def __init__(self):
        self.conn = FakeConnection()

    def acquire(self):
        return FakeAcquire(self.conn)


class PostgresStateStoreTests(unittest.IsolatedAsyncioTestCase):
    async def test_escalation_and_outbox_insert_share_one_transaction(self):
        pool = FakePool()
        store = PostgresStateStore(pool, worker_id="relay-worker-0001")
        await store.record_escalation_with_outbox(
            event_id="evt-00000001", correlation_id="corr-00000001",
            intake_id="intake-0001", case_id="case-000001",
            idempotency_key="idem-key-0001", division=19,
            target="manual_review", reason_code="RETRY_EXHAUSTED",
            attempts_made=2, recorded_at=datetime.now(timezone.utc),
        )
        statements = pool.conn.statements
        self.assertEqual(statements[0][0], "BEGIN")
        self.assertIn("INSERT INTO intake_events", statements[1][0])
        self.assertIn("INSERT INTO escalation_outbox", statements[2][0])
        self.assertEqual(statements[-1][0], "COMMIT")
        metadata = statements[2][1][9]
        self.assertNotIn("profile", metadata)
        self.assertNotIn("income", metadata)

    async def test_rejects_unsupported_reason_before_database_access(self):
        pool = FakePool()
        store = PostgresStateStore(pool, worker_id="relay-worker-0001")
        with self.assertRaises(ValueError):
            await store.record_escalation_with_outbox(
                event_id="evt-00000001", correlation_id="corr-00000001",
                intake_id="intake-0001", case_id="case-000001",
                idempotency_key="idem-key-0001", division=19,
                target="manual_review", reason_code="private exception text",
                attempts_made=2, recorded_at=datetime.now(timezone.utc),
            )
        self.assertEqual(pool.conn.statements, [])


if __name__ == "__main__":
    unittest.main()
