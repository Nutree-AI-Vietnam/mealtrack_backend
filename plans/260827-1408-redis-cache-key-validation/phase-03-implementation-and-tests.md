---
phase: 3
title: "Implementation And Tests"
status: pending
priority: P2
effort: "1d"
---

# Phase 3: Implementation And Tests

## Overview

Implement the selected validation boundary in `nutreeai_async`, then verify
all cache handlers and Queue retry behavior. This phase is intentionally
pending approval after the contract and validation design are reviewed.

## Related Code Files

- Modify: `/Users/alexnguyen/Desktop/Nut/nutreeai_async/src/domain/cache/delete-cache-operations.ts`
- Modify: `/Users/alexnguyen/Desktop/Nut/nutreeai_async/src/domain/cache/cache-key-policy.ts`
- Modify: affected event parsers/handlers under
  `/Users/alexnguyen/Desktop/Nut/nutreeai_async/src/`
- Add or modify: focused tests under
  `/Users/alexnguyen/Desktop/Nut/nutreeai_async/test/`

## Implementation Steps

1. Add typed validation errors and central enforcement.
2. Update every executor call site with validated owner context.
3. Add rejection tests for wildcard injection, invalid dates, wrong owner
   prefixes, unsupported key families, and operation-count overflow.
4. Add positive tests for every current cache family.
5. Run `npm test`, `npm run typecheck`, and the Worker deployment dry-run.

## Success Criteria

- [ ] Unsafe operations fail before Redis calls.
- [ ] All Worker tests pass.
- [ ] Typecheck passes.
- [ ] No valid cache invalidation behavior regresses.

## Risk Assessment

- An overly strict regex can stop legitimate invalidation; mitigate with a
  producer matrix and positive tests for every builder.
- Rejecting malformed events may increase DLQ volume; monitor event-type and
  validation-error metrics after deployment.
