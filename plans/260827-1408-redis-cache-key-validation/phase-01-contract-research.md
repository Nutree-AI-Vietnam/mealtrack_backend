---
phase: 1
title: "Contract Research"
status: pending
priority: P2
effort: "0.5d"
---

# Phase 1: Contract Research

## Overview

Map all Worker cache-operation producers, their event context, and the Redis
execution boundary. Confirm which fields are trusted from the backend envelope
and which require local validation.

## Related Code Files

- Read: `/Users/alexnguyen/Desktop/Nut/nutreeai_async/src/domain/cache/cache-key-policy.ts`
- Read: `/Users/alexnguyen/Desktop/Nut/nutreeai_async/src/domain/cache/delete-cache-operations.ts`
- Read: `/Users/alexnguyen/Desktop/Nut/nutreeai_async/src/domain/cache/cache-invalidation-builders.ts`
- Read: `/Users/alexnguyen/Desktop/Nut/nutreeai_async/src/application/caching/`
- Read: `/Users/alexnguyen/Desktop/Nut/nutreeai_async/src/domain/events/`

## Implementation Steps

1. Inventory every call to `deleteCacheOperations` and record the owning
   `user_id`, dates, aggregate IDs, and operation family.
2. Compare each generated key/pattern with the current allowlist and identify
   any legitimate exceptions such as the global `meal_insight:{meal_id}` key.
3. Trace generic routing to confirm validation happens before any builder or
   Redis call.
4. Record the exact malformed-input behavior expected by Queue retry/DLQ.

## Success Criteria

- [ ] Complete producer-to-executor matrix exists in the phase report.
- [ ] No cache operation reaches Redis without validated context.
- [ ] Legitimate current key families are explicitly covered.
