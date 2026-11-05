# Decision: Redis stream transport for nutreeai_async

**Status:** Accepted for the backend producer. Worker consumer pending.

**Date:** 2026-11-05

**Supersedes (once switched):** the Cloudflare Queue ingress in
[260822-1631-cloudflare-async-cache-worker-proposal.md](./260822-1631-cloudflare-async-cache-worker-proposal.md)

## Context

Integration events reach `nutreeai_async` like this today:

```text
committed write -> CloudflareQueuePublisher -> api.cloudflare.com (Queue REST) -> Queue -> Worker
```

- Every event is an HTTPS request from Render to `api.cloudflare.com`, and
  Render bills that outbound traffic: the 2026 plans include 5 GB (Hobby),
  25 GB (Pro) or 1 TB (Scale) a month, then $0.15 per GB. The request adds
  292 bytes of HTTP framing to every event. When a server process has not
  published for 30 s, it also pays a fresh TLS handshake of about 620 bytes.
- Every event is one call to the Cloudflare client API. That API has a global
  limit of 1,200 requests per 5 minutes per user/token, and going over it
  blocks every API call on that token for 5 minutes. When the Queue token falls
  back to `CLOUDFLARE_API_TOKEN`, Queue publishes go out on the same token as
  Workers AI calls.
- The publish crosses the internet after commit, with a 3–10 s timeout. A
  failed call loses the event (the documented MVP trade-off).
- Queue-specific configuration (`CLOUDFLARE_QUEUE_ID` vs the queue name, token
  permissions) has repeatedly blocked rollout.

The backend's Redis is a Render Key Value instance (`REDIS_URL`) on Render's
private network, where traffic between services in the same region is not
billed. The Worker's cache deletes and `meal_insight:*` writes must land in
that same instance. Older backend docs describe the Worker's Redis access as
Upstash REST: confirm which Redis the Worker writes to before rollout. If it is
not this instance, the backend never sees the Worker's insights or purges.

## Decision

The backend appends each integration event to a Redis stream in its Key Value
instance. `nutreeai_async` reads the stream through a consumer group over the
instance's external URL. The backend switches with
`INTEGRATION_EVENT_TRANSPORT=redis_stream`. The default stays
`cloudflare_queue` until the Worker consumes the stream.

```text
committed write -> RedisStreamEventPublisher -> XADD over the private network (free)
                -> Render Key Value <- Worker consumer group over the external URL (TLS)
```

## Producer contract (backend, implemented)

| Item | Value |
|---|---|
| Key | `nutreeai_async:events:<ENVIRONMENT>`, e.g. `nutreeai_async:events:production` |
| Command | `XADD <key> MAXLEN ~ <INTEGRATION_EVENT_STREAM_MAXLEN> * event <json>` |
| Field `event` | The `IntegrationEvent` envelope as compact UTF-8 JSON: the same object the Queue message body carried |
| Cap | `INTEGRATION_EVENT_STREAM_MAXLEN`, default 20,000. Past the cap, the oldest entries are trimmed whether or not they were delivered. |
| Timing | After commit, on a background task. Failures are logged and never fail the write, as with the Queue. |
| Duplicates | Possible: insight recovery re-publishes, and clients retry writes. Handlers stay idempotent by `event_id`. |

Code: `src/infra/adapters/redis_stream_event_publisher.py`, selected in
`src/bootstrap/integration_services.py`. The publisher keeps its own blocking
pool of up to 4 connections, opened on demand. A write burst waits for a free
connection instead of failing; a Redis outage fails each publish within the
3 s connect timeout.

Sizing: a typical meal event is about 1.4 KB. The largest meal shape (8
ingredients with micros) is about 6.8 KB. A full 20,000-entry backlog is about
28 MB typical and at most about 136 MB.

## Consumer contract (nutreeai_async, to implement)

Use a Cron Trigger consumer (`* * * * *`). A scheduled invocation gets 15
minutes of wall time, the same as a Queue consumer, which covers the 15–40 s
insight AI call. A fetch handler's `ctx.waitUntil()` only gets 30 s after the
response, so do not run the AI handler from a request.

Render Key Value has no REST API. The Worker connects to the instance's
external URL (`rediss://`, TLS and password) with a TCP socket (`connect()`
from `cloudflare:sockets`) and a small RESP client. Open one connection per
cron run and keep it for the whole loop. Every new TLS connection sends the
server's certificate out of Render, a few KB that Render bills, so one
connection per event would cost more than the Queue publish it replaces.

1. Once per environment, create the group from the start of the stream:
   `XGROUP CREATE <key> nutreeai_async 0 MKSTREAM` (ignore `BUSYGROUP`).
   Use `0`, not `$`, so events published before the first run are consumed.
2. Each cron invocation loops for about 55 s:
   - Reclaim stalled work: `XAUTOCLAIM <key> nutreeai_async worker <visibility_ms> 0-0 COUNT 10`
     (Valkey or Redis 6.2+; on older Redis 6 use `XPENDING` then `XCLAIM`).
     Acknowledge the deleted IDs it returns.
   - Read new work: `XREADGROUP GROUP nutreeai_async worker COUNT 10 STREAMS <key> >`.
   - Parse `event` (a JSON string) and hand the envelope to the existing
     integration-event router, the same path the Queue consumer uses.
   - On success, send `XACK` and `XDEL` for the entry in one pipeline. Valkey 8
     has no `XACKDEL`.
   - On failure, leave the entry pending. `XAUTOCLAIM` retries it after the
     visibility timeout.
   - Poison: when `XPENDING <key> nutreeai_async IDLE <visibility_ms> - + 10`
     reports `deliveries >= max_attempts`, run `XADD <key>:dlq * event <json> error <class>`,
     then `XACK` and `XDEL` the original.
   - Sleep 1–2 s after an empty read, and stop starting new work at about 50 s.
3. Cap in-flight entries (for example 10) to respect OpenAI rate limits and the
   per-invocation subrequest limit.
4. Set the visibility timeout above the slowest handler (for example 2 minutes
   for 40 s AI calls). Take `max_attempts` from today's Queue `max_retries`.
5. Keep the Queue consumer until the backend has switched in that environment.

An empty poll costs 27 billed bytes (a 5-byte reply plus its TLS record):
about 35 MB a month at one poll every 2 s.

A doorbell (the backend calling the Worker after `XADD`) would cut latency
below the poll interval, but each doorbell-started run opens its own Key Value
connection and costs more billed bytes than the Queue publish. Keep polling
unless latency requires otherwise.

## Rollout

1. Merge the backend change. Nothing changes: the default is `cloudflare_queue`.
2. Check the Key Value instance behind `REDIS_URL`:
   - **Eviction policy.** `allkeys-lru`, Render's suggestion for caches, can
     evict the stream. `volatile-lru` or `volatile-ttl` keeps it only if every
     cache key carries a TTL. Otherwise give the stream a separate `noeviction`
     instance, which Render recommends for queues.
   - **Persistence.** Use a paid instance with persistence on. Free instances
     keep nothing across a restart.
   - **External access.** Allow Cloudflare's published IP ranges
     (<https://www.cloudflare.com/ips/>), not `0.0.0.0/0`.
   - **Version and connections.** Valkey 8 or Redis 6.2+; the plan's connection
     limit must cover up to 4 more per backend process and one per Worker run.
3. Deploy the Worker stream consumer to staging, keeping the Queue consumer.
4. Staging: set `INTEGRATION_EVENT_TRANSPORT=redis_stream` on Render and
   redeploy. Log a meal, then check `XINFO GROUPS <key>` for the entry, the
   Worker ack log for its `event_id`, the `meal_insight:<meal_id>` key, and an
   empty `XPENDING`.
5. Production: same switch. To roll back, set `cloudflare_queue` and redeploy;
   the Worker still consumes the Queue.
6. After a stable week: delete `CloudflareQueuePublisher`, the
   `CLOUDFLARE_QUEUE_*` settings and this switch; remove the Worker's Queue
   consumer; delete the Queue and its DLQ.

## Consequences

Gains:

- Less billed traffic per event. The `XADD` travels over the private network
  and is free; Render bills what the Worker reads back out. Measured bytes
  billed per event (application data plus TLS records):

  | Event | Cloudflare publish | Key Value replies to the Worker (read, ack, delete) |
  |---|---|---|
  | `hydration.created.v1` | 707 B | 458 B |
  | `meal.created.v1`, typical | 1,771 B | 1,522 B |
  | `meal.created.v1`, largest | 6,304 B | 6,055 B |

  The Cloudflare path also pays about 620 B per publish after 30 s idle. The
  event itself is still billed once because its consumer runs on Cloudflare.
  The remaining levers are fewer events (drop events whose only Worker job
  repeats the backend's synchronous cache purge) and smaller ones (deflate plus
  base64 makes meal events 32–50% smaller). Decide on compression before the
  Worker consumes the stream.
- No per-event Cloudflare API call, no shared API rate-limit budget, and no
  Queue credentials on the backend.
- A publish is one round-trip on the private network instead of an HTTPS call
  with a multi-second timeout.
- Backlog, in-flight work and dead letters are visible with `XLEN`,
  `XPENDING` and `XRANGE` in the Redis the team already runs.

Costs and risks:

- The Worker takes over retry, visibility timeout and dead-lettering, which
  Cloudflare Queues provided.
- The Key Value instance must accept connections from Cloudflare (allow list,
  TLS, password), unless the Worker already connects this way.
- Event delivery now depends on Redis. During a Redis outage, events published
  in that window are lost and logged; before, only caches degraded. The
  write-path cache purge (`write_cache_invalidation.py`) and insight recovery
  (`meal_insight_recovery.py`) are unaffected.
- Latency is bounded by the poll interval (1–2 s), close to the Queue's batch
  timeout.
- During a Worker outage longer than the cap, trimming drops the oldest
  undelivered events, as Queue retention expiry would. Meal insights recover on
  read and cache invalidation is a second pass. Firebase account cleanup and
  subscription receipts have no automatic recovery.
