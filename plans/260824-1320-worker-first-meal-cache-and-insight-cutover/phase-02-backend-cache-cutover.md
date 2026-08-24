---
phase: 2
title: "Backend Cache Cutover"
status: completed
priority: P1
effort: "1.5d"
dependencies: [1]
---

# Phase 2: Backend Cache Cutover

## Overview

Treat meal cache invalidation as the existing direct-Queue feature. This phase
validated that the Worker-owned generic meal path remains separate from the new
insight event and that the dirty branch does not reintroduce the removed
outbox/cache-worker flow.

## Key Insights

- `MealCacheInvalidationHandler` ACKs missing-context events instead of retrying:
  if `user_id` or `meal_date` is absent, it logs `meal_context_missing_acknowledged`
  and returns (`../nutreeai_async/src/application/event-handlers/meal-cache-invalidation-handler.ts:32-52`).
  That is a stale-cache bug, not a transport failure.
- The current meal publishers are already broad. Live emitters found in code:
  `create_manual_meal_command_handler.py:102-116,212-225`,
  `edit_meal_command_handler.py:217-233,411-427`,
  `upload_meal_image_immediately_command_handler.py:376-389`,
  `scan_by_url_command_handler.py:321-334,407-420`,
  `meal_catalog/log_catalog_meal_command_handler.py:152-165`,
  `meal_recommendation/log_recommended_meal_command_handler.py:83-100`,
  `meal_suggestion/save_meal_suggestion_command_handler.py:126-139`,
  `add_custom_ingredient_command_handler.py:59-72`,
  `attach_meal_photo_command_handler.py:78-91`,
  `delete_meal_photo_command_handler.py:62-75`,
  `delete_meal_command_handler.py:145-160`,
  and the meal-analyze graph publish node
  (`src/app/graphs/meal_analyze/nodes.py:430-440`) which is read-only in this phase.
- The older cache-worker plan still references deleted backend files such as
  `src/app/services/cache_invalidation_service.py` and deleted handler tests,
  so Phase 2 must not reuse that file list.

## Requirements

- Functional: every meal event that should invalidate meal-derived projections
  must include `data.user_id` and `data.meal_date`; updates that move a meal
  across dates must also include `data.old_meal_date`.
- Functional: keep the existing direct publish after successful commit; do not
  reintroduce transactional outbox writes for this slice.
- Functional: preserve ancillary `meal.updated.v1` emitters for custom
  ingredients and meal-photo changes.
- Non-functional: do not widen scope into insight generation or Worker cache
  write-back; those belong to Phases 3 and 4.

## Architecture

```text
backend command handler
  -> commit meal change
  -> publish meal.created|updated|deleted.v1 with user_id + meal_date context
  -> Worker IntegrationEventRouter
  -> MealCacheInvalidationHandler
  -> buildMealInvalidationOperations
  -> Redis delete operations
```

## Related Code Files

- Modify:
  `src/app/handlers/command_handlers/create_manual_meal_command_handler.py`
- Modify:
  `src/app/handlers/command_handlers/edit_meal_command_handler.py`
- Modify:
  `src/app/handlers/command_handlers/upload_meal_image_immediately_command_handler.py`
- Modify:
  `src/app/handlers/command_handlers/scan_by_url_command_handler.py`
- Modify:
  `src/app/handlers/command_handlers/meal_catalog/log_catalog_meal_command_handler.py`
- Modify:
  `src/app/handlers/command_handlers/meal_recommendation/log_recommended_meal_command_handler.py`
- Modify:
  `src/app/handlers/command_handlers/meal_suggestion/save_meal_suggestion_command_handler.py`
- Modify:
  `src/app/handlers/command_handlers/add_custom_ingredient_command_handler.py`
- Modify:
  `src/app/handlers/command_handlers/attach_meal_photo_command_handler.py`
- Modify:
  `src/app/handlers/command_handlers/delete_meal_photo_command_handler.py`
- Modify:
  `src/app/handlers/command_handlers/delete_meal_command_handler.py`
- Read-only validation:
  `src/app/graphs/meal_analyze/nodes.py`
- Tests:
  `tests/unit/handlers/command_handlers/test_create_manual_meal_command_handler.py`,
  `tests/unit/handlers/command_handlers/test_meal_delete_command_handlers.py`,
  `tests/unit/handlers/command_handlers/test_attach_meal_photo_command_handler.py`,
  `tests/unit/app/handlers/test_catalog_meal_log_handler.py`,
  `tests/unit/app/handlers/test_meal_recommendation_handlers.py`

## Implementation Steps

1. Convert the publisher assertions in each owned backend handler test into
   contract assertions for `event_type`, `user_id`, `meal_date`, and
   `old_meal_date` where applicable.
2. Normalize any handler that still omits date context or relies on fallback
   aggregate IDs so the Worker never ACKs a context-free meal event.
3. Keep the publish point post-commit for all owned handlers. If a handler
   currently publishes inside the UoW, move that publish to the same pattern
   already used by hydration and the image handlers.
4. Do not touch `src/app/graphs/meal_analyze/nodes.py` here; record its meal
   event publish as an existing compatible emitter and leave insight work to Phase 3.
5. Update tests so the direct-Queue contract, not deleted outbox assertions, is
   the acceptance gate.

## Todo List

- [x] Re-verify the direct Queue meal-event path remains compatible
- [x] Preserve post-commit publication and existing meal context fields
- [x] Remove stale outbox expectations from focused backend tests
- [x] Re-verify graph meal publish remains compatible without editing it here

## Success Criteria

- [x] Existing meal emitters publish the context required by `MealCacheInvalidationHandler`.
- [x] No Phase 2 file reintroduces or depends on the deleted outbox stack.
- [x] Focused backend tests cover direct event publication.
- [x] The graph meal publish path is explicitly verified as compatible.

## Risk Assessment

- Medium likelihood / High impact: one missed emitter silently leaves stale
  cache because the Worker ACKs missing-context events.
  Mitigation: exhaustive test inventory and per-emitter assertions.
- Low likelihood / High impact: moving a publish point before commit makes the
  Worker invalidate cache for rolled-back writes.
  Mitigation: keep direct publish strictly post-commit.
- Low likelihood / Medium impact: backend tests still reference deleted outbox
  behavior and mask the new contract.
  Mitigation: rewrite assertions to target event payloads only.

## Security Considerations

- Event payload tests must not snapshot tokens, raw meal images, or cache keys.
- Keep meal cache events ID/date scoped; do not add nutrition blobs to this path.

## Next Steps

Phase 3 owns the insight trigger surfaces and may read these emitters but should
not reopen their file ownership.

## Unresolved Questions

- None.
