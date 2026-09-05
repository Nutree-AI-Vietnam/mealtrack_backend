---
title: "Simplify Hydration Direct Queue Delivery"
description: "Finish the hydration-only cutover to direct post-commit Cloudflare Queue publish without disturbing the remaining outbox-backed flows."
status: pending
priority: P2
effort: 5h
branch: "architecture/optimize-architecture"
tags: [backend, hydration, cloudflare, refactor]
blockedBy: []
blocks: [260823-1056-generic-integration-event-redesign]
created: 2026-08-23
createdBy: "ck:plan"
source: skill
---

# Simplify Hydration Direct Queue Delivery

## Overview

Current worktree already started the cutover: `LogHydrationCommandHandler`
publishes `hydration.created.v1` through an injected
`IntegrationEventPublisherPort` after the unit-of-work block exits instead of
enqueueing the transactional outbox
([src/app/handlers/command_handlers/log_hydration_command_handler.py](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/src/app/handlers/command_handlers/log_hydration_command_handler.py:124)).
The composition root also injects `CloudflareQueuePublisher.from_settings()`
into that handler
([src/api/dependencies/event_bus.py](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/src/api/dependencies/event_bus.py:949)).

This plan finishes the hydration-only cutover by hardening post-commit failure
behavior, preserving every non-target outbox path, and aligning tests/docs with
the new contract. `nutreeai_async`, the outbox worker, and legacy
`hydration.created.v1` outbox handling stay in place for compatibility.

Data flow:
`POST /v1/hydration/log`
([src/api/routes/v1/hydration.py](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/src/api/routes/v1/hydration.py:42))
-> `LogHydrationCommand`
([src/app/commands/hydration/log_hydration_command.py](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/src/app/commands/hydration/log_hydration_command.py:10))
-> `LogHydrationCommandHandler`
-> DB commit on `AsyncUnitOfWork.__aexit__`
([src/infra/database/uow_async.py](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/src/infra/database/uow_async.py:148))
-> direct Queue publish through `CloudflareQueuePublisher.publish`
([src/infra/adapters/cloudflare_queue_publisher.py](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/src/infra/adapters/cloudflare_queue_publisher.py:93))
-> unchanged Worker ingress contract.

## Phases

| Phase | Name | Status |
|-------|------|--------|
| 1 | [Harden Post-Commit Hydration Publish](./phase-01-harden-post-commit-hydration-publish.md) | Pending |
| 2 | [Keep Legacy Outbox Paths Stable](./phase-02-keep-legacy-outbox-paths-stable.md) | Pending |
| 3 | [Focused Tests and Rollout Notes](./phase-03-focused-tests-and-rollout-notes.md) | Pending |

## Dependencies

- Blocks `260823-1056-generic-integration-event-redesign`: that in-progress
  plan still needs the smaller hydration-only cutover hardened and verified.
- Keep legacy outbox registry entry for `hydration.created.v1`
  ([src/infra/services/handlers/__init__.py](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/src/infra/services/handlers/__init__.py:107))
  so pre-cutover rows can still drain through the outbox worker.
- Leave non-target durable flows untouched:
  `cache_invalidation.v1`
  ([src/app/services/cache_invalidation_service.py](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/src/app/services/cache_invalidation_service.py:49)),
  `notification_reschedule`
  ([src/app/handlers/command_handlers/update_notification_preferences_command_handler.py](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/src/app/handlers/command_handlers/update_notification_preferences_command_handler.py:80)),
  `firebase_account_cleanup`
  ([src/app/handlers/command_handlers/delete_user_command_handler.py](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/src/app/handlers/command_handlers/delete_user_command_handler.py:94)),
  and affiliate sibling outbox rows
  ([src/infra/database/models/affiliate_event_outbox.py](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/src/infra/database/models/affiliate_event_outbox.py:8)).
