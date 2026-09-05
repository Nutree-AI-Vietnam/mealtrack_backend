---
phase: 2
title: "Validation Plan"
status: pending
priority: P2
effort: "0.5d"
---

# Phase 2: Validation Plan

## Overview

Choose the smallest enforcement point that protects every cache handler while
preserving the current event-specific builders.

## Requirements

- Functional: reject malformed owner context before cache-key construction.
- Functional: reject operations outside the owner-scoped allowlist.
- Reliability: throw validation errors so the Queue message retries/DLQs; do
  not acknowledge unsafe events.
- Compatibility: valid operation names and deletion counts remain unchanged.

## Implementation Steps

1. Prefer a central executor contract that receives the validated owner ID,
   while retaining event-specific parsing for dates and aggregate IDs.
2. Validate UUID-shaped user IDs and aggregate IDs, strict ISO dates, and
   operation count before building or executing operations.
3. Apply `isAllowedCacheOperationName` to each operation with the event owner.
4. Decide whether policy failure should use a typed permanent-validation error
   or the existing generic error path; it must not be silently ignored.
5. Add a producer matrix to tests covering exact keys, wildcard patterns,
   global meal-insight keys, and attempted cross-user patterns.

## Success Criteria

- [ ] One enforcement boundary protects all current cache handlers.
- [ ] Policy checks cannot be bypassed by calling the executor directly.
- [ ] Tests distinguish safe rejection from transient Redis failure.
