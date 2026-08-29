---
phase: 4
title: "Worker Redis Consumer"
status: completed
priority: P1
effort: "1-2d"
dependencies: [3]
---

# Phase 4: Worker Redis Consumer

## Context links

- Phase 1 event contract and provider proof
- Phase 3 Python Queue publisher
- `src/infra/cache/redis_client.py`
- Official consumer ACK/retry docs:
  https://developers.cloudflare.com/queues/configuration/batching-retries/
- Worker runtime docs:
  https://developers.cloudflare.com/workers/configuration/integrations/external-services/

## Overview

Create a small TypeScript Worker that validates the fixed invalidation contract,
deletes approved Redis keys through the provider's HTTP API, and ACKs each
message only after successful completion.
The Worker package and test coverage are implemented in the sibling
`nutreeai_async` repository. The backend keeps only the event contract and
Python publisher integration.
Staging Queue/DLQ enablement remains blocked on external credentials.

## Requirements

- Validate event version, UUID, event type, user identity, operation count,
  allowed key/pattern prefix, and payload size.
- Support only exact key deletion and bounded pattern deletion. No arbitrary
  Redis commands and no cache-value writes.
- Use native `fetch` or a verified Worker-compatible HTTP Redis client; do not
  use TCP-only Node packages in the Worker runtime.
- Call `msg.ack()` only after all operations succeed.
- Call `msg.retry()` for Redis/network/partial-delete failures; configure a DLQ
  for repeated failures and malformed messages.
- Keep structured logs to event ID, operation count, outcome, and latency;
  never log payloads, tokens, or raw cache keys.
- Do not implement HMAC verification, revision fencing, or cache population.

## Suggested deployable layout

```text
nutreeai_async/
  package.json
  tsconfig.json
  wrangler.jsonc
  src/index.ts
  src/event-schema.ts
  src/redis-http-client.ts
  src/cache-operation-executor.ts
  test/...
```

## Related code files

### Sibling repository

- `../nutreeai_async/package.json`
- `../nutreeai_async/tsconfig.json`
- `../nutreeai_async/wrangler.jsonc`
- `../nutreeai_async/src/`
- `../nutreeai_async/test/`

## Implementation steps

1. Scaffold the Worker as an isolated package with pinned dependencies and a
   compatibility date.
2. Implement event and operation validation with prefix, size, match, and
   command-count limits.
3. Implement exact delete and bounded pattern delete through the provider HTTP
   adapter. Retry partial work safely.
4. Implement per-message ACK/retry handling so one failed message does not
   incorrectly ACK or redeliver successful messages.
5. Configure staging Queue consumer, DLQ, Redis namespace, and secrets.
6. Test duplicate, out-of-order, Redis-down, malformed, and slow-pattern cases.

## Todo list

- [x] Scaffold isolated Worker package.
- [x] Add event and operation validation.
- [x] Implement HTTP Redis delete operations.
- [x] Add explicit per-message ACK/retry behavior.
- [x] Define staging/production Queue consumer, DLQ, Redis namespace, and secret
  bindings in Worker configuration.
- [x] Verify failure cases.

## Success criteria

- [x] Valid event is ACKed only after all deletes succeed.
- [x] Redis outage and partial deletion cause retry, not ACK.
- [ ] Poison messages reach the DLQ with deployment evidence.
- [x] Duplicate and out-of-order delete events are harmless.
- [x] Existing cache values are never written or recalculated by the Worker.

## Next steps

Phase 5 runs staging proof and enables one global production path.
