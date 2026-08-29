---
phase: 1
title: "Minimal Contract and Provider Proof"
status: in-progress
priority: P1
effort: "1d"
dependencies: []
---

# Phase 1: Minimal Contract and Provider Proof

## Context links

- [Local scout](./research/local-outbox-cache-scout.md)
- [Cloudflare research](./research/cloudflare-queue-platform-research.md)
- `src/app/services/cache_invalidation_service.py`
- `src/infra/database/models/outbox_event.py`
- `src/infra/cache/cache_service.py`
- `src/infra/cache/redis_client.py`

## Overview

Lock the smallest infrastructure-only event and verify that the deployed Redis
provider can be reached from a Worker. Do not introduce a new revision model or
change cache writers. The local contract and allowlist work are in place; the
remaining gate is external provider proof, which is blocked on credentials.

## Requirements

- Define `cache_invalidation.v1` with an exact `event_type`, `event_id`,
  `user_id`, `occurred_at`, and a bounded list of exact `delete_key` or bounded
  `delete_pattern` operations.
- Allow only the existing MealTrack cache-key prefixes; reject arbitrary Redis
  commands, unknown operation types, invalid patterns, and oversized events.
- Keep the internal event limit at 32 KB, below Cloudflare Queue's platform
  limit, without placing meal, nutrition, email, or provider payloads on Queue.
- Prove the deployed Redis provider exposes an HTTP-compatible API for exact
  deletion and bounded pattern deletion. No Worker enablement without this
  provider proof.
- Inventory every `after_meal_write` caller and its UoW boundary before Phase 2.

## Related code files

### Create

- `plans/260822-1730-cloudflare-async-cache-projection-worker/reports/phase-01-contract-scout.md`

### Read / verify

- All `after_meal_write` call sites under `src/app/handlers/` and
  `src/app/graphs/meal_analyze/nodes.py`.
- `src/domain/cache/cache_keys.py` and current cache invalidation operation
  coverage.
- Cache provider configuration in `src/infra/config/settings.py`.

## Implementation steps

1. Record the meal-write caller matrix: file/line, route or command, UoW
   boundary, mutation, and whether the event can be enqueued before commit.
2. Extract the current invalidation key expansion into a pure operation builder
   without changing its key set or date behavior.
3. Specify contract fixtures for valid, duplicate, malformed, oversized, and
   unsupported-operation events. No HMAC or revision metadata is required.
4. Run a staging provider spike for exact delete and bounded pattern delete
   through the actual Worker-compatible HTTP interface.
5. Record the provider decision, operation limits, and all caller exclusions in
   the phase report.

## Todo list

- [x] Complete the meal-write/UoW caller matrix.
- [x] Lock the minimal event schema and operation allowlist.
- [ ] Prove Redis HTTP exact-delete and bounded-pattern-delete support.
- [x] Record exclusions and provider decision.

## Success criteria

- [x] Every live meal-write caller is classified before Phase 2.
- [x] Operation fixtures cover duplicate delivery and malformed input.
- [ ] Provider HTTP proof passes or Worker production is explicitly blocked.
- [x] No cache-population or revision-fencing work is included.

## Next steps

Phase 2 creates the event inside the existing business transaction.
