# Cache Invalidation Queue Runbook

Use this runbook for the cache-invalidation slice:
business transaction -> direct Python publisher -> Cloudflare Queue ->
Cloudflare Worker -> Upstash Redis REST delete.

This runbook covers the `cache_invalidation.v1` compatibility path. Hydration
creation also publishes a generic `hydration.created.v1` event through the
same direct Queue publisher; the Worker orchestrator translates it to the same cache
handler. The generic MVP retries the whole ingress message and does not use
D1 delivery state.

Do not use this runbook for HMAC signing, revision fencing, cache-value writes,
local-vs-Cloudflare dual routing, or percentage canaries. Those are intentionally
out of scope for this slice.

## Preconditions

- `CLOUDFLARE_QUEUE_NAME` points to the environment-specific Worker ingress
  queue used by both generic and compatibility events.
- Queue, DLQ, Worker, and Upstash Redis REST credentials are present in the
  deployment environment.
- The backend has `CLOUDFLARE_QUEUE_NAME` plus valid Cloudflare credentials. Set
  Queue-specific account/token variables for a dedicated credential, or leave
  them blank to reuse `CLOUDFLARE_ACCOUNT_ID` and `CLOUDFLARE_API_TOKEN`. A
  reused token must have both Queue and Workers AI permissions.

## Verify the path

1. Confirm the business write committed and the API logged Queue acceptance.
2. Confirm the publisher sent the event to Cloudflare Queue.
3. Confirm the Worker log shows the matching `event_id` and an `ack` outcome.
4. Confirm the target Redis keys or bounded patterns were deleted.

## Failure handling

| Symptom | Expected behavior |
|---|---|
| Queue publication misconfigured | The publish fails with a configuration error; the database write remains authoritative. |
| Worker parse or delete failure | Queue retry, then DLQ after configured attempts. |
| Upstash REST outage | Worker retries; no cache value write is attempted. |
| Repeated poison payloads | Inspect the DLQ by `event_id` and the redacted Worker logs. |

For a DLQ replay, use the controlled Queue/DLQ replay mechanism for the
deployed environment and replay the original message by `event_id`. Do not
create a new business write to compensate for a cache-only failure. Record the
replay timestamp and final Worker outcome.

## Rollback

1. If the Worker is misbehaving, disable the consumer or revert the Worker
   deployment instead of changing the business write path.

While the consumer is disabled, business writes remain authoritative but cache
events accumulate in Queue retry/DLQ handling. Verify freshness after restoring
the Worker consumer.

## Evidence

Record UTC timestamp, environment, event ID, Queue outcome,
Worker outcome, Redis outcome, and DLQ status. Do not record secrets, raw
payloads, auth headers, or cache values.
