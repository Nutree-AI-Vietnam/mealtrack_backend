---
phase: 3
title: "Python Queue Publisher"
status: completed
priority: P1
effort: "1d"
dependencies: [2]
---

# Phase 3: Python Queue Publisher

## Context links

- Phase 2 transactional event contract
- `src/infra/services/outbox_dispatch_engine.py`
- `src/infra/services/handlers/__init__.py`
- `src/cron/outbox_worker.py`
- `src/infra/config/settings.py`, `.env.example`
- Official HTTP publish contract:
  https://developers.cloudflare.com/queues/examples/publish-to-a-queue-via-http/

## Overview

Add one Python publisher for `cache_invalidation.v1` events using Cloudflare's
HTTP Queue endpoint. The existing outbox worker owns retries and the API never
waits for Queue or Redis.
This publisher and its tests are implemented in the working tree.
Staging publish evidence remains blocked on external credentials.

## Requirements

- POST `{ "body": <event> }` with a dedicated Queue API token and bounded
  timeout.
- Accept only the documented successful response shape; treat timeout, 429,
  and 5xx as retryable.
- Surface invalid credentials/configuration without leaking headers or payloads.
- Queue acceptance marks the existing outbox row `COMPLETED`; it does not claim
  Redis processing completed.
- Preserve existing outbox handlers and statuses. Do not add HMAC signing or
  per-event destination routing.
- Use a single global Queue-enabled configuration for deployed environments;
  local tests inject a fake publisher rather than implementing a second route.

## Related code files

### Create

- `src/infra/adapters/cloudflare_queue_publisher.py`
- `src/infra/services/handlers/cache_invalidation_queue_handler.py`
- `tests/unit/infra/adapters/test_cloudflare_queue_publisher.py`
- `tests/unit/infra/services/handlers/test_cache_invalidation_queue_handler.py`

### Modify

- `src/infra/config/settings.py`
- `.env.example`
- `src/infra/services/handlers/__init__.py`
- `src/cron/outbox_worker.py` if configuration injection is required
- Existing outbox publisher/handler tests

## Implementation steps

1. Add Queue URL/name, account reference, dedicated token, timeout, and
   enablement settings. Keep them separate from Workers AI settings.
2. Implement an injected `httpx` publisher with redacted errors and no payload
   logging.
3. Register the cache event handler without changing unrelated outbox routes.
4. Verify Queue 200, timeout, 429, 5xx, invalid-token, and malformed-success
   responses through focused tests.
5. Publish one non-sensitive event to a staging Queue and preserve its
   `event_id` in backend logs/metrics.

## Todo list

- [x] Add Queue settings and secret names.
- [x] Implement publisher and error classification.
- [x] Register the cache event handler.
- [x] Verify existing outbox retry/completion behavior.
- [ ] Publish one staging event. (blocked on credentials)

## Success criteria

- [x] Queue acceptance completes only the matching outbox event.
- [x] Transient publish failures retry through the existing outbox worker.
- [x] No API response path depends on Queue availability.
- [x] Existing outbox handler tests remain green.
- [ ] Staging publication is proven with a real Queue event.

## Next steps

Phase 4 implements the fixed-operation Worker consumer and DLQ behavior.
