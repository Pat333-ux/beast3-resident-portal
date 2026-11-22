"""Production-oriented PostgreSQL outbox relay daemon."""
from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import random
import signal
import socket
import sys
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, Protocol, Sequence

import asyncpg

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s [%(levelname)s] [outbox-daemon] %(message)s",
)
logger = logging.getLogger("outbox_daemon")

DATABASE_URL = os.getenv("DATABASE_URL", "")
WORKER_ID = os.getenv("WORKER_ID", f"relay-worker-{socket.gethostname()}-{os.getpid()}")
BATCH_SIZE = max(1, min(int(os.getenv("OUTBOX_BATCH_SIZE", "50")), 1000))
POLL_INTERVAL_SECONDS = max(0.05, float(os.getenv("OUTBOX_POLL_INTERVAL", "1.0")))
MAX_BACKOFF_SECONDS = max(1, int(os.getenv("OUTBOX_MAX_BACKOFF", "300")))
HEALTH_PORT = int(os.getenv("HEALTH_PORT", "8080"))
LEASE_SECONDS = max(60, int(os.getenv("OUTBOX_LEASE_SECONDS", "300")))

DAEMON_METRICS: dict[str, Any] = {
    "is_healthy": True,
    "last_poll_time": 0.0,
    "published_count": 0,
    "failed_count": 0,
    "recovered_leases_count": 0,
    "pending_depth": 0,
}


class PublisherProtocol(Protocol):
    async def publish_escalation(self, payload: dict[str, Any], target: str) -> None: ...


class ConsoleMockPublisher:
    """Only suitable for local validation; production must inject a real publisher."""

    async def publish_escalation(self, payload: dict[str, Any], target: str) -> None:
        event_id = payload.get("event_id") if isinstance(payload, dict) else None
        logger.info("Outbox publish accepted target=%s eventId=%s", target, event_id)


class HealthMetricsHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path in ("/healthz", "/ready"):
            healthy = bool(DAEMON_METRICS["is_healthy"])
            self.send_response(200 if healthy else 503)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"status": "ok" if healthy else "unhealthy"}).encode())
        elif self.path == "/metrics":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(DAEMON_METRICS).encode())
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format: str, *args: Any) -> None:
        return


class OutboxRelayDaemon:
    def __init__(self, db_pool: asyncpg.Pool, publisher: PublisherProtocol) -> None:
        self.db_pool = db_pool
        self.publisher = publisher
        self.running = False

    async def claim_batch(self, conn: asyncpg.Connection) -> Sequence[asyncpg.Record]:
        claim_query = """
            WITH stale_count AS (
                SELECT COUNT(*) AS count
                FROM escalation_outbox
                WHERE status = 'publishing'
                  AND locked_at < NOW() - ($3 * INTERVAL '1 second')
            ), claimed AS (
                SELECT outbox_id
                FROM escalation_outbox
                WHERE (
                    (status IN ('pending', 'failed') AND next_attempt_at <= NOW())
                    OR (status = 'publishing' AND locked_at < NOW() - ($3 * INTERVAL '1 second'))
                )
                ORDER BY next_attempt_at ASC, outbox_id ASC
                FOR UPDATE SKIP LOCKED
                LIMIT $1
            )
            UPDATE escalation_outbox AS o
            SET status='publishing', locked_by=$2, locked_at=NOW(),
                publish_attempts=o.publish_attempts + 1, updated_at=NOW()
            FROM claimed
            WHERE o.outbox_id=claimed.outbox_id
            RETURNING o.outbox_id, o.event_id, o.correlation_id, o.target,
                      o.reason_code, o.attempts_made, o.publish_attempts,
                      o.payload, o.recorded_at,
                      (o.status = 'publishing') AS was_already_claimed
        """
        # Count stale claims separately because RETURNING cannot safely identify pre-update state.
        stale_before = await conn.fetchval(
            "SELECT COUNT(*) FROM escalation_outbox WHERE status='publishing' "
            "AND locked_at < NOW() - ($1 * INTERVAL '1 second')",
            LEASE_SECONDS,
        )
        rows = await conn.fetch(claim_query, BATCH_SIZE, WORKER_ID, LEASE_SECONDS)
        DAEMON_METRICS["recovered_leases_count"] += stale_before or 0
        return rows

    async def mark_published(self, conn: asyncpg.Connection, event_id: str, correlation_id: str) -> bool:
        result = await conn.execute(
            """UPDATE escalation_outbox
               SET status='published', published_at=NOW(), locked_by=NULL,
                   locked_at=NULL, updated_at=NOW()
               WHERE event_id=$1 AND correlation_id=$2
                 AND status='publishing' AND locked_by=$3""",
            event_id, correlation_id, WORKER_ID,
        )
        return not result.endswith(" 0")

    async def reschedule_failure(
        self, conn: asyncpg.Connection, event_id: str,
        correlation_id: str, publish_attempts: int,
    ) -> bool:
        base = min(MAX_BACKOFF_SECONDS, math.pow(2, max(0, publish_attempts)))
        jitter = random.uniform(0.1, 0.5) * base
        delay = int(base + jitter)
        result = await conn.execute(
            """UPDATE escalation_outbox
               SET status='failed', next_attempt_at=NOW()+($3*INTERVAL '1 second'),
                   locked_by=NULL, locked_at=NULL, updated_at=NOW()
               WHERE event_id=$1 AND correlation_id=$2
                 AND status='publishing' AND locked_by=$4""",
            event_id, correlation_id, delay, WORKER_ID,
        )
        return not result.endswith(" 0")

    async def update_pending_depth(self, conn: asyncpg.Connection) -> None:
        depth = await conn.fetchval(
            "SELECT COUNT(*) FROM escalation_outbox WHERE status IN ('pending','failed','publishing')"
        )
        DAEMON_METRICS["pending_depth"] = depth or 0

    async def process_single_entry(self, conn: asyncpg.Connection, record: asyncpg.Record) -> bool:
        event_id = record["event_id"]
        correlation_id = record["correlation_id"]
        target = record["target"]
        attempts = record["publish_attempts"]
        payload_value = record["payload"]
        payload = payload_value if isinstance(payload_value, dict) else json.loads(payload_value)

        try:
            await asyncio.wait_for(
                self.publisher.publish_escalation(payload=payload, target=target),
                timeout=30.0,
            )
            if await self.mark_published(conn, event_id, correlation_id):
                DAEMON_METRICS["published_count"] += 1
                return True
        except asyncio.TimeoutError:
            logger.warning("Outbox publish timed out eventId=%s", event_id)
        except Exception:
            logger.warning("Outbox publish failed eventId=%s failureCode=BROKER_UNAVAILABLE", event_id)

        await self.reschedule_failure(conn, event_id, correlation_id, attempts)
        DAEMON_METRICS["failed_count"] += 1
        return False

    async def run_loop(self) -> None:
        self.running = True
        logger.info("Outbox relay loop started workerId=%s", WORKER_ID)
        while self.running:
            DAEMON_METRICS["last_poll_time"] = time.time()
            try:
                async with self.db_pool.acquire() as conn:
                    records = await self.claim_batch(conn)
                    if records:
                        for record in records:
                            if not self.running:
                                break
                            await self.process_single_entry(conn, record)
                        await self.update_pending_depth(conn)
                    else:
                        await asyncio.sleep(POLL_INTERVAL_SECONDS)
            except asyncio.CancelledError:
                raise
            except Exception:
                DAEMON_METRICS["is_healthy"] = False
                logger.warning("Outbox polling cycle failed failureCode=STORE_UNAVAILABLE")
                await asyncio.sleep(POLL_INTERVAL_SECONDS * 2)
            else:
                DAEMON_METRICS["is_healthy"] = True

    def stop(self) -> None:
        logger.info("Stopping outbox relay loop")
        self.running = False


def start_health_server(port: int) -> HTTPServer:
    server = HTTPServer(("0.0.0.0", port), HealthMetricsHandler)
    logger.info("Health server listening port=%s", port)
    return server


async def main() -> None:
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL is required")
    pool = await asyncpg.create_pool(dsn=DATABASE_URL, min_size=2, max_size=10, command_timeout=60)
    daemon = OutboxRelayDaemon(pool, ConsoleMockPublisher())
    health_server = start_health_server(HEALTH_PORT)
    health_thread = asyncio.get_running_loop().run_in_executor(None, health_server.serve_forever)
    stop_event = asyncio.Event()

    def request_stop() -> None:
        daemon.stop()
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, request_stop)

    relay_task = asyncio.create_task(daemon.run_loop())
    try:
        await stop_event.wait()
        await relay_task
    finally:
        await pool.close()
        health_server.shutdown()
        await health_thread
        logger.info("Outbox daemon shutdown complete")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(0)
