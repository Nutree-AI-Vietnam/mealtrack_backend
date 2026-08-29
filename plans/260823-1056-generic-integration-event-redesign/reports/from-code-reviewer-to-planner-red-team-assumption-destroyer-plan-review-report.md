# Assumption Destroyer review

## Findings

- **Critical — ingress and legacy topology is not present:** `src/infra/adapters/cloudflare_queue_publisher.py:28-65`, `src/infra/services/handlers/__init__.py:51-60`, and `nutreeai_async/wrangler.jsonc:19-49` show separate routing must be designed explicitly. Phases 2 and 3 now include the queue matrix and producer bindings.
- **High — `delivery_id` is absent from current Worker contracts:** `nutreeai_async/src/interfaces/cloudflare/queue-consumer-router.ts:200-217` and `nutreeai_async/src/domain/events/cache-invalidation-event.ts:10-17` do not carry it. Phase 1 now defines a delivery envelope.
- **High — hydration identity is not uniformly UUID-shaped:** `src/domain/model/hydration/hydration_entry.py:10-21` and `src/app/handlers/command_handlers/log_hydration_command_handler.py:101-117` require field-specific validation. Phase 1 now states this.
- **High — queue-disabled mode can skip current specialized persistence:** `src/app/services/cache_invalidation_service.py:57-58` and `src/app/handlers/command_handlers/log_hydration_command_handler.py:119-126` justify separating event creation from dispatch gating. Phase 2 now requires durable canonical creation.
- **High — pending rollback rows need a hold/replay mechanism:** `src/infra/repositories/outbox_repository.py:106-125`, `src/infra/services/outbox_handler_registry.py:17-35`, and `src/infra/repositories/outbox_repository.py:221-275` show current claiming/fallback behavior. Phase 2 now includes per-event pause/hold and Phase 4 defines replay limits.

**Status:** DONE
