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
  limit of 1,200 requests per 5 minutes per user/token, and going
  over it blocks every API call on that token for 5 minutes. When the Queue
  token falls back to `CLOUDFLARE_API_TOKEN`, Queue publishes go out on the
  same token as Workers AI calls.
- The publish crosses the internet after commit, with a 3–10 s timeout. A failed
  call loses the event (the documented MVP trade-off).
- Queue-specific configuration (`CLOUDFLARE_QUEUE_ID` vs the queue name, token
  permissions) has repeatedly blocked rollout.

The backend and the Worker already share one Upstash Redis: the backend through
`REDIS_URL`, the Worker through Upstash REST for cache deletes and
`meal_insight:*` writes.

## Decision

The backend appends each integration event to a Redis stream in that shared
Redis. `nutreeai_async` reads the stream through a consumer group. The backend
switches with `INTEGRATION_EVENT_TRANSPORT=redis_stream`. The default stays
`cloudflare_queue` until the Worker consumes the stream.

```text
committed write -> RedisStreamEventPublisher -> XADD (REDIS_URL) -> Worker consumer group
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
pool of 10 connections. A write burst waits for a free connection instead of
failing; a Redis outage fails each publish within the 3 s connect timeout.

Sizing: a typical meal event is about 1.4 KB. The largest meal shape (8
ingredients with micros) is about 6.8 KB. A full 20,000-entry backlog is about
28 MB typical and at most about 136 MB.

## Consumer contract (nutreeai_async, to implement)

Use a Cron Trigger consumer (`* * * * *`). A scheduled invocation gets 15
minutes of wall time, the same as a Queue consumer, which covers the 15–40 s
insight AI call. A fetch handler's `ctx.waitUntil()` only gets 30 s after the
response, so do not run the AI handler from a request.

1. Once per environment, create the group from the start of the stream:
   `XGROUP CREATE <key> nutreeai_async 0 MKSTREAM` (ignore `BUSYGROUP`).
   Use `0`, not `$`, so events published before the first run are consumed.
2. Each cron invocation loops for about 55 s:
   - Reclaim stalled work: `XAUTOCLAIM <key> nutreeai_async worker <visibility_ms> 0-0 COUNT 10`.
     Acknowledge the deleted IDs it returns.
   - Read new work: `XREADGROUP GROUP nutreeai_async worker COUNT 10 STREAMS <key> >`.
   - Parse `event` and hand the envelope to the existing integration-event router,
     the same path the Queue consumer uses. The Upstash SDK may already have
     deserialized the JSON value, so accept a string or an object.
   - On success, `XACKDEL <key> nutreeai_async IDS 1 <id>` (or `XACK` then `XDEL`).
   - On failure, leave the entry pending. `XAUTOCLAIM` retries it after the
     visibility timeout.
   - Poison: when `XPENDING <key> nutreeai_async IDLE <visibility_ms> - + 10`
     reports `deliveries >= max_attempts`, run `XADD <key>:dlq * event <json> error <class>`,
     then `XACKDEL` the original.
   - Sleep 1–2 s after an empty read, and stop starting new work at about 50 s.
3. Cap in-flight entries (for example 10) to respect OpenAI rate limits and the
   per-invocation subrequest limit.
4. Set the visibility timeout above the slowest handler (for example 2 minutes
   for 40 s AI calls). Take `max_attempts` from today's Queue `max_retries`.
5. Keep the Queue consumer until the backend has switched in that environment.

Idle polling costs about one Upstash command per second with `XAUTOCLAIM`
every loop, or about 0.6 per second with `XAUTOCLAIM` every 15 s: roughly
1.5–2.6 million commands a month. Check that against the Upstash plan.

Later, if needed: the backend can ring a doorbell (an authenticated, best-effort
`POST` to the Worker after `XADD`) to cut latency below the poll interval or
stop idle polling. The AI handler must still not run inside `waitUntil()`.

## Rollout

1. Merge the backend change. Nothing changes: the default is `cloudflare_queue`.
2. Confirm **eviction is disabled** on the Upstash database behind `REDIS_URL`
   (Upstash's default). With eviction on, the optimistic-volatile policy can
   evict keys without a TTL, the stream included, once memory is full.
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

- Less outbound traffic per event. Measured bytes sent out of Render
  (application data plus TLS record):

  | Event | Cloudflare Queue | Redis stream |
  |---|---|---|
  | `hydration.created.v1` | 707 B | 415 B |
  | `meal.created.v1`, typical | 1,771 B | 1,479 B |
  | `meal.created.v1`, largest | 6,304 B | 6,012 B |

  Add about 620 B per Cloudflare publish after 30 s idle; the Redis connection
  stays open. The event itself still leaves Render once: Upstash is outside
  Render, and a stream in Render Key Value read by the Worker on Cloudflare
  would be billed the same way. The remaining levers are fewer events (drop
  events whose only Worker job repeats the backend's synchronous cache purge)
  and smaller ones (deflate plus base64 makes meal events 32–50% smaller).
  Decide on compression before the Worker consumes the stream.
- No per-event Cloudflare API call, no shared API rate-limit budget, and no
  Queue credentials on the backend.
- A publish is one Redis round-trip on a pooled connection instead of an HTTPS
  call with a multi-second timeout.
- Backlog, in-flight work and dead letters are visible with `XLEN`,
  `XPENDING` and `XRANGE` in the Redis the team already runs.

Costs and risks:

- The Worker takes over retry, visibility timeout and dead-lettering, which
  Cloudflare Queues provided.
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
