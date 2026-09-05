---
phase: 3
title: "Focused Tests and Rollout Notes"
status: pending
priority: P1
effort: "1.5h"
dependencies: [1, 2]
---

# Phase 3: Focused Tests and Rollout Notes

## Overview

Add only the tests needed to prove the hydration-only cutover is ordered,
observable, and isolated. Then document the rollback path and rollout proof
needed before calling the slice complete.

## Key Insights

- The current handler test already moved from outbox assertions to direct
  publisher assertions
  ([tests/unit/handlers/command_handlers/test_log_hydration_command_handler.py](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/tests/unit/handlers/command_handlers/test_log_hydration_command_handler.py:36)),
  but it does not yet prove commit ordering or degraded failure behavior.
- Queue transport and Worker-envelope tests already exist and should stay
  focused:
  [tests/unit/infra/adapters/test_cloudflare_queue_publisher.py](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/tests/unit/infra/adapters/test_cloudflare_queue_publisher.py:23),
  [tests/unit/infra/services/handlers/test_integration_event_queue_handler.py](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/tests/unit/infra/services/handlers/test_integration_event_queue_handler.py:32).
- Cache invalidation outbox tests already guard the non-target
  `cache_invalidation.v1` contract
  ([tests/unit/app/services/test_cache_invalidation_outbox.py](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/tests/unit/app/services/test_cache_invalidation_outbox.py:12)).

## Requirements

- Functional: prove publish happens only after successful `__aexit__` completion.
- Functional: prove commit failure skips publish.
- Functional: prove post-commit publish failure returns the normal hydration
  payload and emits observability.
- Non-functional: keep validation to targeted unit tests; do not run broad
  unscoped `pytest`.

## Architecture

Test matrix:
- Unit: `LogHydrationCommandHandler` ordering, no-outbox path, disabled
  publisher, publish failure, commit failure.
- Unit: existing `CloudflareQueuePublisher` classification remains transport
  truth.
- Unit: existing `IntegrationEventQueueHandler` remains legacy outbox/backlog
  truth.
- Regression/no-touch: `cache_invalidation.v1` tests stay green to prove this
  plan did not widen.

## Related Code Files

- Modify: `tests/unit/handlers/command_handlers/test_log_hydration_command_handler.py`
- Optional modify: `tests/unit/infra/services/test_outbox_dispatch_engine.py` only if one compatibility assertion is needed
- Read-only validation: `tests/unit/infra/adapters/test_cloudflare_queue_publisher.py`
- Read-only validation: `tests/unit/infra/services/handlers/test_integration_event_queue_handler.py`
- Read-only validation: `tests/unit/app/services/test_cache_invalidation_outbox.py`

## Implementation Steps

1. Extend the fake UoW used by the hydration handler test so it records enter,
   exit, and commit-complete ordering; assert publisher runs after exit.
2. Add a commit-failure test where `__aexit__` raises and the publisher is not
   called.
3. Add a publish-failure test where the publisher raises after commit and the
   handler still returns the normal payload while observability is emitted.
4. Keep transport/Worker tests targeted; only extend them if the direct path
   changes shared payload shape, which this plan explicitly avoids.
5. Run the narrow validation command:
   `pytest tests/unit/handlers/command_handlers/test_log_hydration_command_handler.py tests/unit/infra/adapters/test_cloudflare_queue_publisher.py tests/unit/infra/services/handlers/test_integration_event_queue_handler.py tests/unit/app/services/test_cache_invalidation_outbox.py -q`
6. Rollback plan: restore `uow.outbox.enqueue(...)` in
   `LogHydrationCommandHandler`, remove direct publisher injection from
   `event_bus.py`, and revert the three docs. No schema or Worker rollback is
   required.

## Todo List

- [ ] Add post-commit ordering assertion
- [ ] Add commit-failure skips-publish assertion
- [ ] Add publish-failure degrades-but-succeeds assertion
- [ ] Keep validation command targeted to unit paths
- [ ] Document rollback steps in the implementation PR/notes
- [ ] Mark staging/live proof separate from local/unit proof

## Success Criteria

- [ ] Focused handler tests cover success, commit failure, disabled publisher, and publish failure.
- [ ] Existing queue publisher and Worker handler tests still pass unchanged or with only compatibility assertions.
- [ ] Rollback is one code revert with no migration or Worker change.
- [ ] The implementation report clearly separates local unit proof from future staging/live queue proof.

## Risk Assessment

- Medium: tests may falsely prove ordering if the fake UoW does not model
  `__aexit__` correctly.
  Mitigation: make the fake explicitly record exit before publish.
- Medium: staged rollout may be mistaken for complete after local unit proof.
  Mitigation: keep staging/live queue evidence as an explicit follow-up gate.

## Security Considerations

- Test assertions must avoid logging secrets or raw auth headers.
- Validation stays local/unit; no live queue or provider credentials required.

## Next Steps

- After implementation, collect local unit evidence first.
- Treat staging hydration log -> Queue -> Worker -> Redis proof as a separate
  rollout checkpoint.
