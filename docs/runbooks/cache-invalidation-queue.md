# Cache Invalidation Queue Runbook

Use this runbook for the durable cache-invalidation slice:
business transaction -> outbox row -> Python publisher -> Cloudflare Queue ->
Cloudflare Worker -> Upstash Redis REST delete.

This runbook covers the `cache_invalidation.v1` compatibility path. Hydration
creation also publishes a generic `hydration.created.v1` event through the
same backend outbox; the Worker orchestrator translates it to the same cache
handler. The generic MVP retries the whole ingress message and does not use
D1 delivery state.

Do not use this runbook for HMAC signing, revision fencing, cache-value writes,
local-vs-Cloudflare dual routing, or percentage canaries. Those are intentionally
out of scope for this slice.

## Preconditions

- `CLOUDFLARE_QUEUE_ENABLED` is set the way the target environment intends.
- `CLOUDFLARE_QUEUE_NAME` points to the environment-specific Worker ingress
  queue used by both generic and compatibility events.
- Queue, DLQ, Worker, and Upstash Redis REST credentials are present in the
  deployment environment.
- The outbox worker can reach PostgreSQL and claim `cache_invalidation.v1`
  records.

## Verify the path

1. Confirm the business write committed and an outbox row was created.
2. Confirm the outbox worker published the event to Cloudflare Queue.
3. Confirm the Worker log shows the matching `event_id` and an `ack` outcome.
4. Confirm the target Redis keys or bounded patterns were deleted.

## Failure handling

| Symptom | Expected behavior |
|---|---|
| Queue publication disabled or misconfigured | The outbox row stays retryable or fails per publisher error class. |
| Worker parse or delete failure | Queue retry, then DLQ after configured attempts. |
| Upstash REST outage | Worker retries; no cache value write is attempted. |
| Repeated poison payloads | Inspect the DLQ by `event_id` and the redacted Worker logs. |

For a DLQ replay, use the controlled Queue/DLQ replay mechanism for the
deployed environment and replay the original message by `event_id`. Do not
create a new business write to compensate for a cache-only failure. Record the
replay timestamp and final Worker outcome.

## Rollback

1. Set `CLOUDFLARE_QUEUE_ENABLED=false` to stop new Queue publications.
2. Leave existing outbox rows in place so retries remain possible.
3. If the Worker is misbehaving, disable the consumer or revert the Worker
   deployment instead of changing the business write path.

While publication is disabled, business writes remain authoritative but do not
create new cache-invalidation events. Existing cache entries can therefore stay
stale until their normal TTL or an operator-led cache clear/read-through rebuild;
keep this a short maintenance window and verify freshness after re-enabling.

## Evidence

Record UTC timestamp, environment, event ID, outbox row ID, Queue outcome,
Worker outcome, Redis outcome, and DLQ status. Do not record secrets, raw
payloads, auth headers, or cache values.
