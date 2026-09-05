---
phase: 1
title: "Harden Post-Commit Hydration Publish"
status: pending
priority: P1
effort: "2h"
dependencies: []
---

# Phase 1: Harden Post-Commit Hydration Publish

## Overview

Finish the hydration-only direct publish path in the current worktree. Keep the
DB write authoritative, publish only after commit, and make post-commit Queue
failure observable without turning a committed hydration write into a false 5xx.

## Key Insights

- Current branch state already removed `uow.outbox.enqueue(...)` from the
  hydration handler and moved publish after the unit-of-work block
  ([src/app/handlers/command_handlers/log_hydration_command_handler.py](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/src/app/handlers/command_handlers/log_hydration_command_handler.py:124)).
- `AsyncUnitOfWork.__aexit__` commits on clean exit, so code after the `async
  with` runs post-commit
  ([src/infra/database/uow_async.py](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/src/infra/database/uow_async.py:160)).
- The repo already has a clean domain-side port:
  `IntegrationEventPublisherPort`
  ([src/domain/ports/integration_event_publisher_port.py](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/src/domain/ports/integration_event_publisher_port.py:8)).
- The current branch still lets publisher exceptions escape after commit, which
  would surface as request failure despite persisted SQL state.

## Requirements

- Functional: `POST /v1/hydration/log` keeps returning the existing success
  payload when the hydration row commits, whether direct Queue publish succeeds
  or fails afterward.
- Functional: new hydration writes must not create a transactional outbox row.
- Functional: `event_publisher=None` remains an explicit no-publish path for
  environments where Queue publication is disabled.
- Non-functional: the application layer depends only on
  `IntegrationEventPublisherPort`, never on a Cloudflare-specific type.
- Non-functional: no reopen/retry/rollback attempt after commit.

## Architecture

Write path:
`src/api/routes/v1/hydration.py:42` -> `LogHydrationCommand`
-> `LogHydrationCommandHandler.handle`
([src/app/handlers/command_handlers/log_hydration_command_handler.py](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/src/app/handlers/command_handlers/log_hydration_command_handler.py:41))
-> persist meal + hydration row
([src/app/handlers/command_handlers/log_hydration_command_handler.py](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/src/app/handlers/command_handlers/log_hydration_command_handler.py:88))
-> build `HydrationCreatedEvent`
([src/app/handlers/command_handlers/log_hydration_command_handler.py](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/src/app/handlers/command_handlers/log_hydration_command_handler.py:124))
-> commit on UoW exit
-> call `IntegrationEventPublisherPort.publish(payload)`
-> `CloudflareQueuePublisher.publish`
([src/infra/adapters/cloudflare_queue_publisher.py](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/src/infra/adapters/cloudflare_queue_publisher.py:93))
-> unchanged Worker ingress queue.

Failure behavior:
- Commit failure: raise as today; publisher is never called.
- Queue publish failure after commit: catch in handler, record structured
  observability with `event_id`, `aggregate_id`, `environment`, and
  `error_type`, then return the normal hydration response.
- Disabled publisher: skip direct publish and return success; rely on explicit
  config/docs, not silent outbox fallback.

## Related Code Files

- Modify: `src/app/handlers/command_handlers/log_hydration_command_handler.py`
- Modify: `src/api/dependencies/event_bus.py`
- Keep/track: `src/domain/ports/integration_event_publisher_port.py`
- Read-only dependency: `src/infra/adapters/cloudflare_queue_publisher.py`
- Read-only dependency: `src/infra/database/uow_async.py`

## Implementation Steps

1. Keep `HydrationCreatedEvent` assembly inside the transaction, but keep
   `publish(...)` strictly after the `async with self.uow` block.
2. Wrap post-commit publish in a narrow `try/except Exception` at the handler
   boundary so transport failures do not escape into HTTP after SQL commit.
3. Record failure via existing app observability helpers; do not import
   Cloudflare exception classes into the handler.
4. Keep `event_publisher` injection in the composition root
   ([src/api/dependencies/event_bus.py](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/src/api/dependencies/event_bus.py:949))
   so infrastructure stays outside the application layer.

## Todo List

- [ ] Preserve current post-commit ordering in `LogHydrationCommandHandler`
- [ ] Convert post-commit publish failure from request error to observable degradation
- [ ] Keep Queue-disabled path explicit and documented
- [ ] Avoid adding a new result type or fallback queue abstraction unless tests prove the simple port is insufficient

## Success Criteria

- [ ] A successful hydration request commits SQL and returns the existing payload without creating a new outbox row.
- [ ] A post-commit publish exception does not roll back the hydration row and does not turn the response into a false failure.
- [ ] A failing DB commit still aborts the request and never calls the publisher.

## Risk Assessment

- High: side effects can be lost because the outbox retry layer is removed for
  this one path.
  Mitigation: explicit observability, clear docs/runbook, legacy outbox handler
  left intact for old rows, and rollback-by-revert with no schema migration.
- Medium: catching failures too broadly could hide programming bugs.
  Mitigation: emit `error_type` and event identifiers; keep tests for commit
  failure vs publish failure distinct.

## Security Considerations

- Keep payload unchanged from `IntegrationEvent.to_payload()`
  ([src/domain/events/integration_event.py](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/src/domain/events/integration_event.py:54)).
- Never log raw headers, tokens, or request bodies; only IDs and error type.

## Next Steps

- Phase 2 freezes all non-target durable paths and corrects docs.
- Phase 3 adds regression tests and the rollback/verification checklist.
