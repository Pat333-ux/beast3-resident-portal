# Production outbox relay daemon

Run the daemon with `DATABASE_URL` and a production broker adapter. This branch's
`ConsoleMockPublisher` is only a local validation implementation and must not be
used in production.

```sh
export DATABASE_URL='postgresql://...'
export WORKER_ID='relay-worker-vigo-1'
export OUTBOX_BATCH_SIZE='50'
export OUTBOX_POLL_INTERVAL='1'
export OUTBOX_MAX_BACKOFF='300'
export HEALTH_PORT='8080'
python outbox_daemon.py
```

Endpoints:

- `/healthz` and `/ready`: process/database health (database pool failure is tracked in the polling loop)
- `/metrics`: opaque operational counters and polling depth

The relay claims leased PostgreSQL rows with `FOR UPDATE SKIP LOCKED`, publishes
at least once, and reschedules failures with bounded exponential backoff plus
jitter. Publishers must deduplicate broker deliveries by `outbox_id` or
`event_id`. Stop the process with SIGINT/SIGTERM; in-flight broker calls have a
30-second timeout.
