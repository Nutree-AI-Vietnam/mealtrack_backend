---
title: "Cloudflare Queue Platform Research"
type: research
status: complete
date: 2026-08-22
---

# Cloudflare Queue Platform Research

## Verified platform constraints

- Cloudflare's HTTP publish endpoint accepts messages from Python or any HTTP
  client. It requires an API token with `Queues Edit` permission and returns a
  successful response when the Queue accepts the message:
  https://developers.cloudflare.com/queues/examples/publish-to-a-queue-via-http/
- Queue consumers deliver batches. Individual `ack()` and `retry()` calls are
  available; explicitly acknowledging successful messages prevents a later
  failure in the same batch from redelivering those messages:
  https://developers.cloudflare.com/queues/configuration/batching-retries/
- A consumer retry limit and dead-letter queue are configured on the consumer.
  Without a DLQ, repeatedly failing messages are eventually discarded:
  https://developers.cloudflare.com/queues/configuration/dead-letter-queues/
- Current queue limits include 128 KB maximum message size, 100-message maximum
  consumer batch, up to 14 days retention on paid plans, and configurable
  consumer concurrency. Internal events should stay well below the platform
  limit (target <=32 KB) and must not carry meal payloads:
  https://developers.cloudflare.com/queues/platform/limits/
- Cloudflare Workers run in a V8-based runtime; libraries that depend on
  `fs`, `http/net`, or other Node runtime behavior may not translate directly.
  External credentials belong in Wrangler secrets:
  https://developers.cloudflare.com/workers/configuration/integrations/external-services/
- Workers Logs, traces, built-in metrics, and OpenTelemetry export are
  available, but custom metrics export is not currently supported through OTel.
  The first version should use structured Worker logs plus Cloudflare queue/
  Worker metrics and backend metrics:
  https://developers.cloudflare.com/workers/observability/

## Architecture decisions for the plan

1. Keep the existing Python outbox worker as the relay in v1. The API request
   must not depend on Cloudflare availability after the database commit. Queue
   publish latency is an operational SLO measured from outbox creation; a
   later post-commit wake-up can reduce polling latency without changing
   correctness.
2. Use a dedicated Queue API token, not the existing Workers AI token. Store
   the token only in backend runtime secrets; do not place it in event payloads
   or Worker logs.
3. Start with a push consumer and explicit per-message acknowledgements. Set a
   bounded initial batch/concurrency configuration and tune from staging
   backlog/latency evidence.
4. Treat Cloudflare consumer retries/DLQ and PostgreSQL outbox retries as two
   distinct failure domains. PostgreSQL marks the event complete after Queue
   acceptance; Redis failure is handled by Queue retry/DLQ.
5. Use a fetch-based Redis adapter only after a provider proof. The Worker must
   support the operations needed by the event contract, including an atomic
   revision-fence operation and bounded deletion. If the current Redis provider
   cannot provide an HTTP endpoint and atomic fence operation, the plan stops at
   staging rather than silently weakening ordering guarantees.

## Worker contract

The Worker accepts a versioned, infrastructure-only envelope:

```json
{
  "schema_version": 1,
  "event_id": "uuid",
  "event_type": "cache_projection.invalidate",
  "aggregate": {"type": "user_nutrition", "id": "opaque-user-id"},
  "projection_revision": 219,
  "operations": [{"op": "delete_key", "key": "user:..."}],
  "occurred_at": "2026-08-22T10:10:20Z",
  "signature": "base64-hmac-over-canonical-envelope"
}
```

Unknown schema versions, unsupported operations, oversized messages, invalid
key/pattern namespaces, signature mismatches, and missing revision metadata are
not successful work; they should be retried to the configured DLQ with redacted
structured logs. The signature secret is separate from the Queue API token.

## Unresolved integration proof

The repository config uses generic Redis TCP URLs and documents Upstash as an
example. The plan must not assume that the deployed provider's TCP endpoint is
usable from Workers or that an HTTP API supports the required atomic Lua
operation. Phase 1 owns this proof and records the provider-specific decision.
