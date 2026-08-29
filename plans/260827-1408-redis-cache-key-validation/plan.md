---
title: "Redis Cache Key Validation"
description: "Validate Worker cache operations against the event owner before Redis deletion."
status: pending
priority: P2
branch: "architecture/optimize-architecture"
tags: [security, worker, redis, cache]
blockedBy: []
blocks: []
created: "2026-08-27T07:08:15.443Z"
createdBy: "ck:plan"
source: skill
---

# Redis Cache Key Validation

## Overview

The Worker currently defines an allowlist for cache keys and patterns, but the
executor does not apply it. Event-specific handlers also accept user/date
fields without consistently validating them before building Redis patterns.
This plan adds one central validation boundary so malformed or cross-scope
operations fail and are retried/DLQ'd instead of deleting unintended keys.

This is a separate follow-up. It does not change the accepted Queue-driven
meal-insight architecture or the intentional removal of push/email work.

## Phases

| Phase | Name | Status |
|-------|------|--------|
| 1 | [Contract Research](./phase-01-contract-research.md) | Pending |
| 2 | [Validation Plan](./phase-02-validation-plan.md) | Pending |
| 3 | [Implementation And Tests](./phase-03-implementation-and-tests.md) | Pending |

## Dependencies

- Worker repository: `/Users/alexnguyen/Desktop/Nut/nutreeai_async`.
- Generic event parsers must expose validated owner context before cache-key
  construction.
- Existing cache-key patterns in `src/domain/cache/cache-key-policy.ts` are
  the starting allowlist; legitimate keys must remain unchanged.

## Success Criteria

- [ ] Every cache deletion operation is validated against its event owner and
  operation type before Redis execution.
- [ ] Invalid user IDs, dates, aggregate IDs, and unsupported key patterns
  fail before `SCAN`, `DEL`, or exact deletion.
- [ ] Valid meal, hydration, movement, profile, cheat-day, and saved-suggestion
  operations retain current cache behavior.
- [ ] Tests prove malformed input cannot broaden deletion scope and that Queue
  failures retry rather than ACK.

## Non-Goals

- No new event ledger or exactly-once delivery mechanism.
- No change to the backend Queue publication contract.
- No provider-side Redis ACL redesign.
