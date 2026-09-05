---
phase: 2
title: "Keep Legacy Outbox Paths Stable"
status: pending
priority: P1
effort: "1.5h"
dependencies: [1]
---

# Phase 2: Keep Legacy Outbox Paths Stable

## Overview

Ringfence this change so only hydration creation bypasses the transactional
outbox. Preserve legacy outbox relay compatibility for old
`hydration.created.v1` rows and leave every non-target durable caller on its
current path.

## Key Insights

- The outbox registry still routes both `cache_invalidation.v1` and
  `hydration.created.v1`
  ([src/infra/services/handlers/__init__.py](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/src/infra/services/handlers/__init__.py:107)).
- The outbox worker claims rows, dispatches outside DB transactions, and
  finalizes status with retry/DLQ logic
  ([src/infra/services/outbox_dispatch_engine.py](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/src/infra/services/outbox_dispatch_engine.py:95)).
- Hydration-adjacent delete/caloric-drink paths still intentionally use
  `cache_invalidation.v1`, not `hydration.created.v1`
  ([src/app/handlers/command_handlers/log_caloric_drink_command_handler.py](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/src/app/handlers/command_handlers/log_caloric_drink_command_handler.py:83),
  [src/app/handlers/command_handlers/delete_hydration_entry_command_handler.py](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/src/app/handlers/command_handlers/delete_hydration_entry_command_handler.py:69),
  [src/app/handlers/command_handlers/delete_meal_command_handler.py](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/src/app/handlers/command_handlers/delete_meal_command_handler.py:76)).

## Requirements

- Functional: keep legacy registry support for `hydration.created.v1` until old
  outbox rows are drained.
- Functional: do not change `nutreeai_async`, `IntegrationEventQueueHandler`,
  or the outbox dispatch engine.
- Functional: preserve current durable callers for `cache_invalidation.v1`,
  `notification_reschedule`, `firebase_account_cleanup`, and affiliate sibling
  outbox rows.
- Non-functional: docs must clearly distinguish "hydration direct post-commit"
  from "all other durable outbox-backed work."

## Architecture

No-touch durable callers to preserve:
- Cache invalidation builder/service:
  [src/app/services/cache_invalidation_service.py](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/src/app/services/cache_invalidation_service.py:49)
- Notification reschedule:
  [src/app/handlers/command_handlers/update_notification_preferences_command_handler.py](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/src/app/handlers/command_handlers/update_notification_preferences_command_handler.py:80),
  [src/app/handlers/command_handlers/update_timezone_command_handler.py](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/src/app/handlers/command_handlers/update_timezone_command_handler.py:68),
  [src/app/handlers/command_handlers/update_language_command_handler.py](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/src/app/handlers/command_handlers/update_language_command_handler.py:55),
  [src/app/handlers/command_handlers/register_fcm_token_command_handler.py](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/src/app/handlers/command_handlers/register_fcm_token_command_handler.py:100)
- Firebase cleanup:
  [src/app/handlers/command_handlers/delete_user_command_handler.py](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/src/app/handlers/command_handlers/delete_user_command_handler.py:94)
- Affiliate sibling outbox:
  [src/api/routes/v1/webhook_subscription_lifecycle.py](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/src/api/routes/v1/webhook_subscription_lifecycle.py:70),
  [src/app/handlers/command_handlers/referral/apply_referral_code_handler.py](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/src/app/handlers/command_handlers/referral/apply_referral_code_handler.py:45),
  [src/infra/database/models/affiliate_event_outbox.py](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/src/infra/database/models/affiliate_event_outbox.py:8)

## Related Code Files

- Do not modify: `src/infra/services/outbox_dispatch_engine.py`
- Do not modify: `src/infra/services/handlers/__init__.py`
- Do not modify: `src/infra/services/handlers/integration_event_queue_handler.py`
- Do not modify: `src/app/services/cache_invalidation_service.py`
- Modify docs only: `docs/system-architecture.md`, `docs/external-services.md`, `docs/troubleshooting.md`

## Implementation Steps

1. Keep the outbox registry mapping for `hydration.created.v1` untouched so any
   pre-cutover rows can still relay through the outbox worker.
2. Do not move caloric-drink, hydration-delete, meal-delete hydration cleanup,
   notification reschedule, firebase cleanup, or affiliate flows off their
   current outbox/sibling-outbox paths.
3. Correct the partially edited docs so they state the final failure behavior:
   hydration commit remains authoritative; post-commit publish failure is
   observable degradation, not a business-write rollback.
4. Call out in the plan/release note that there is no data migration and no
   Worker change.

## Todo List

- [ ] Keep `hydration.created.v1` legacy outbox registration for backlog compatibility
- [ ] Preserve all `cache_invalidation.v1` callers
- [ ] Preserve `notification_reschedule` and `firebase_account_cleanup` enqueue sites
- [ ] Preserve affiliate sibling outbox paths
- [ ] Fix docs so they no longer claim a post-commit publish failure should fail the request

## Success Criteria

- [ ] New hydration writes bypass the transactional outbox; legacy outbox rows still remain processable.
- [ ] No non-hydration durable caller changes file ownership or behavior.
- [ ] Docs accurately describe the split architecture and degraded failure mode.

## Risk Assessment

- High: removing the registry mapping too early would dead-letter old
  `hydration.created.v1` rows.
  Mitigation: keep mapping until backlog is confirmed empty.
- Medium: doc drift could mislead incident response.
  Mitigation: update the three touched docs in the same change as the code hardening.

## Security Considerations

- No payload expansion. Existing envelope validation in the Worker remains
  unchanged
  ([src/infra/services/handlers/integration_event_queue_handler.py](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/src/infra/services/handlers/integration_event_queue_handler.py:35)).
- Keep affiliate and Firebase paths isolated from hydration work.

## Next Steps

- Phase 3 proves the behavior with focused tests and documents rollback.
