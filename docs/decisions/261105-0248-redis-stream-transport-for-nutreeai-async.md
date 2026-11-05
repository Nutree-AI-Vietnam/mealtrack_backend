# Decision: Redis stream transport for nutreeai_async

**Status:** Accepted for the backend producer. Worker consumer pending.

**Date:** 2026-11-05

**Supersedes (once switched):** the Cloudflare Queue ingress in
[260822-1631-cloudflare-async-cache-worker-proposal.md](./260822-1631-cloudflare-async-cache-worker-proposal.md)

## Context

`nutree_backend` (web service), `nutreeai_async` (background worker) and the
`nutree` Key Value instance all run on Render in Oregon. Events still leave
Render twice to reach the worker:

```text
nutree_backend --HTTPS publish--> api.cloudflare.com Queue <--HTTPS pull/ack-- nutreeai_async
```

- Render bills both legs as outbound traffic: the 2026 plans include 5 GB
  (Hobby), 25 GB (Pro) or 1 TB (Scale) a month, then $0.15 per GB. A publish
  costs 707 B for a water log, 1,771 B for a typical meal and 6,304 B for the
  largest meal, plus about 620 B for a new TLS handshake after 30 s idle.
- Every publish, pull and ack is a call to the Cloudflare client API, which
  allows 1,200 requests per 5 minutes per user/token and blocks every call on
  that token for 5 minutes when exceeded.
- The publish crosses the internet after commit with a 3–10 s timeout; a failed
  call loses the event.

Traffic between Render services in the same region is not billed. A stream in
Redis on the private network takes the event path off the bill entirely.

Render metrics, 7 days to 2026-11-05:

| Resource | Measurement |
|---|---|
| `nutree_backend` outbound | about 1.8 GB a day, all traffic included |
| `nutreeai_async` outbound | about 20–25 GB a day, following user activity |
| `nutree` Key Value | free plan: 25 MB RAM with 18–26 MB used, 28–44 of 50 connections, persistence off, `allkeys_lru`, Valkey 8.1.4, reachable from `0.0.0.0/0` |

The worker handles a few events a minute. Queue calls at that rate cannot
account for 20–25 GB a day, so most of the worker's traffic comes from its
handlers and still needs tracing. Moving off the Queue removes the Queue calls,
not that traffic.

## Decision

The backend appends each integration event to a Redis stream on Render's
private network. `nutreeai_async` reads it through a consumer group with
blocking reads. The backend switches with
`INTEGRATION_EVENT_TRANSPORT=redis_stream`; the default stays
`cloudflare_queue` until the worker consumes the stream.

```text
nutree_backend --XADD--> events Key Value (private network) <--XREADGROUP BLOCK-- nutreeai_async
```

The stream gets its own Key Value instance, set as
`INTEGRATION_EVENT_REDIS_URL`. `nutree` cannot hold it: it is full, so the
stream would push out cache entries or be evicted itself under
`allkeys_lru`, and without persistence a restart drops every queued event. A
queue wants `noeviction` and persistence, which a cache does not.

## Producer contract (backend, implemented)

| Item | Value |
|---|---|
| Redis | `INTEGRATION_EVENT_REDIS_URL` (internal URL); blank falls back to `REDIS_URL` |
| Key | `nutreeai_async:events:<ENVIRONMENT>`, e.g. `nutreeai_async:events:production` |
| Command | `XADD <key> MAXLEN ~ <INTEGRATION_EVENT_STREAM_MAXLEN> * event <json>` |
| Field `event` | The `IntegrationEvent` envelope as compact UTF-8 JSON: the same object the Queue message body carried |
| Cap | `INTEGRATION_EVENT_STREAM_MAXLEN`, default 20,000. Past the cap, the oldest entries are trimmed whether or not they were delivered. |
| Timing | After commit, on a background task. Failures are logged and never fail the write, as with the Queue. |
| Duplicates | Possible: insight recovery re-publishes, and clients retry writes. Handlers stay idempotent by `event_id`. |

Code: `src/infra/adapters/redis_stream_event_publisher.py`, selected in
`src/bootstrap/integration_services.py`. The publisher keeps its own blocking
pool of up to 4 connections per process, opened on demand. A write burst waits
for a free connection instead of failing; a Redis outage fails each publish
within the 3 s connect timeout.

Sizing: a typical meal event is about 1.4 KB and the largest about 6.8 KB. A
full 20,000-entry backlog is about 28 MB typical and at most about 136 MB.
With the worker reading continuously, the stream normally holds only in-flight
entries.

## Consumer contract (nutreeai_async, to implement)

The worker is a long-running process on the same private network, so it reads
with blocking calls instead of polling:

1. At start-up, create the group from the start of the stream:
   `XGROUP CREATE <key> nutreeai_async 0 MKSTREAM` (ignore `BUSYGROUP`).
   Use `0`, not `$`, so events published before the first run are consumed.
2. Read on a dedicated connection; a blocked connection cannot run other
   commands: `XREADGROUP GROUP nutreeai_async <instance> COUNT 10 BLOCK 5000 STREAMS <key> >`.
   Use the Render instance ID as the consumer name. The call returns as soon as
   an event arrives, so there is no polling traffic or delay.
3. Hand each envelope to the existing integration-event router with bounded
   concurrency. On success, send `XACK` and `XDEL` in one pipeline; Valkey has
   no `XACKDEL`.
4. Every 30 s, reclaim stalled work:
   `XAUTOCLAIM <key> nutreeai_async <instance> <visibility_ms> 0-0 COUNT 10`.
   This retries failures and entries held by an instance that died during a
   deploy. Set the visibility timeout above the slowest handler.
5. Dead letters: when `XPENDING <key> nutreeai_async IDLE <visibility_ms> - + 10`
   reports `deliveries >= max_attempts`, run `XADD <key>:dlq * event <json> error <class>`,
   then `XACK` and `XDEL` the original. Take `max_attempts` from today's Queue
   `max_retries`.
6. On SIGTERM, stop reading, finish in-flight entries and exit. Anything left
   unacknowledged is reclaimed by step 4.
7. Keep the Cloudflare pull consumer until the backend has switched in that
   environment.

## Rollout

1. Merge the backend change. Nothing changes: the default is `cloudflare_queue`.
2. Create a paid Key Value instance for events in Oregon: `noeviction`,
   persistence on (journal plus snapshot), no external access. Starter (256 MB,
   250 connections) is far more than the stream needs.
3. Ship the worker's stream consumer, reading `INTEGRATION_EVENT_REDIS_URL`,
   alongside the Queue consumer.
4. Preprod or staging: set `INTEGRATION_EVENT_REDIS_URL` and
   `INTEGRATION_EVENT_TRANSPORT=redis_stream` on the backend and redeploy. Log a
   meal, then check `XINFO GROUPS <key>`, the worker ack log for its
   `event_id`, the `meal_insight:<meal_id>` key and an empty `XPENDING`.
5. Production: same switch. To roll back, set `cloudflare_queue` and redeploy;
   the worker still consumes the Queue.
6. After a stable week: delete `CloudflareQueuePublisher`, the
   `CLOUDFLARE_QUEUE_*` settings and this switch; remove the worker's pull
   consumer; delete the Queue and its DLQ.

## Consequences

Gains:

- The event path stops costing bandwidth: a publish and its delivery stay on
  Render's private network.
- Neither the backend nor the worker calls the Cloudflare API for events, so
  publishes, pulls and acks no longer share its rate limit.
- Blocking reads deliver an event within milliseconds; the Queue pull loop
  waits for its next poll.
- Backlog, in-flight work and dead letters are visible with `XLEN`,
  `XPENDING` and `XRANGE`.

Costs and risks:

- The worker takes over retry, visibility timeout and dead-lettering, which
  Cloudflare Queues provided.
- Durability rests on the events instance's persistence. With journal fsync
  every second, a crash can lose up to a second of events.
- Event delivery depends on that Redis. During an outage, events published in
  that window are lost and logged. The write-path cache purge
  (`write_cache_invalidation.py`) and insight recovery
  (`meal_insight_recovery.py`) are unaffected.
- During a worker outage longer than the cap, trimming drops the oldest
  undelivered events, as Queue retention expiry would. Meal insights recover on
  read and cache invalidation is a second pass. Firebase account cleanup and
  subscription receipts have no automatic recovery.
