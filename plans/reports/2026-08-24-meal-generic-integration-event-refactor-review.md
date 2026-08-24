# Review: meal.created/meal.updated insight refactor

Date: 2026-08-24

Work context: `/Users/alexnguyen/Desktop/Nut/mealtrack_backend`
Worker sibling: `/Users/alexnguyen/Desktop/Nut/nutreeai_async`
Reports: `/Users/alexnguyen/Desktop/Nut/mealtrack_backend/plans/reports/`
Plans: `/Users/alexnguyen/Desktop/Nut/mealtrack_backend/plans/`

## Current state

- Backend is already mid-cutover. `publish_meal_event(...)` now embeds
  `data.insight` inside generic `meal.created.v1` / `meal.updated.v1`
  (`src/app/events/meal/meal_events.py:137-186`), and 11 producer call sites
  already use it:
  `src/app/graphs/meal_analyze/nodes.py:391`,
  `src/app/handlers/command_handlers/create_manual_meal_command_handler.py:107,212`,
  `src/app/handlers/command_handlers/edit_meal_command_handler.py:222,411`,
  `src/app/handlers/command_handlers/meal_catalog/log_catalog_meal_command_handler.py:93`,
  `src/app/handlers/command_handlers/meal_recommendation/log_recommended_meal_command_handler.py:90`,
  `src/app/handlers/command_handlers/meal_suggestion/save_meal_suggestion_command_handler.py:130`,
  `src/app/handlers/command_handlers/scan_by_url_command_handler.py:314,397`,
  `src/app/handlers/command_handlers/upload_meal_image_immediately_command_handler.py:369`.
- Three `meal.updated.v1` producers still bypass the new helper and emit plain
  update events with no insight snapshot:
  `src/app/handlers/command_handlers/add_custom_ingredient_command_handler.py:59-72`,
  `src/app/handlers/command_handlers/attach_meal_photo_command_handler.py:78-91`,
  `src/app/handlers/command_handlers/delete_meal_photo_command_handler.py:62-75`.
- Backend read path is already cache-only. `GET /v1/meals/{meal_id}` and
  `GET /v1/meals/{meal_id}/value-insights` only read `meal_insight:{meal_id}`
  through `MealValueInsightService`; they do not run AI locally
  (`src/api/routes/v1/meals_read.py:170-224`,
  `src/domain/services/meal_value_insight_service.py:18-50`).
- Worker has not completed the cutover. Generic routing already supports
  multiple handlers per event type and retries the whole event on one handler
  failure (`../nutreeai_async/src/interfaces/cloudflare/integration-event-router.ts:35-165`,
  `../nutreeai_async/test/integration-event-router.test.ts:39-164`), but meal
  insights still hang off a legacy special parser/branch:
  `../nutreeai_async/src/interfaces/cloudflare/queue-consumer-router.ts:29-32,131-153,295-323,550-612`,
  `../nutreeai_async/src/domain/events/meal-value-insight-event.ts:28-41,150-211`,
  `../nutreeai_async/src/infrastructure/event-handlers/meal-value-insight-event-handler.ts:104-199`.
- Current active plan still assumes the special event stays authoritative
  (`plans/260824-1320-worker-first-meal-cache-and-insight-cutover/plan.md:37-44,74-75`).

## Main gaps / failure modes

1. High / High: current branch can strand insights.
   Generic meal invalidation already deletes `meal_insight:{meal_id}` for meal
   UUID events (`../nutreeai_async/src/domain/cache/cache-invalidation-builders.ts:14-80`,
   `../nutreeai_async/src/application/event-handlers/meal-cache-invalidation-handler.ts:61-88`).
   If the Worker ignores `data.insight`, or a producer sends plain
   `meal.updated.v1`, the read path stays `generating`.

2. High / Medium: partial backend producer migration.
   The three plain `meal.updated.v1` producers above do not regenerate insight
   even though at least `add_custom_ingredient` changes nutrition
   (`src/app/handlers/command_handlers/add_custom_ingredient_command_handler.py:46-57`).

3. Medium / High: payload-size regression.
   Generic integration events are capped at 32 KiB
   (`src/domain/events/integration_event.py:12-18,44-52`).
   The refactor is safe only if `data.insight` stays within that limit.

4. Medium / Medium: stale tests and plan docs still describe two-message flow.
   Backend tests still reference removed special-event helpers
   (`tests/unit/app/events/test_integration_event.py:251-329`) and graph tests
   still expect two publishes (`tests/unit/app/graphs/test_meal_analyze_graph_scaffold.py:203-263`).
   Worker queue tests still model one `meal.created.v1` plus one
   `meal.value_insight.v1` for the same meal
   (`../nutreeai_async/test/queue-consumer-router.test.ts:50-158`).

## Recommended implementation plan

### Phase 1: Worker compatibility first

- Add a generic meal-insight adapter/parser in Worker domain/application code
  that reads `event.data.insight` from `IntegrationEvent`.
- Refactor current AI/cache/push logic out of the interface-special path into an
  application handler, e.g. `MealInsightProjectionHandler`, depending only on
  `AIPort`, `CacheWritePort`, and `PushNotificationPort`.
- Register handlers as:
  - `meal.created.v1` -> `MealCacheInvalidationHandler`, `MealInsightProjectionHandler`
  - `meal.updated.v1` -> `MealCacheInvalidationHandler`, `MealInsightProjectionHandler`
  - `meal.deleted.v1` -> `MealCacheInvalidationHandler`
- Keep the legacy `meal.value_insight.v1` branch temporarily during this phase
  as rollback compatibility. Do not delete it until backend producer cleanup and
  tests are green.

Files: only `../nutreeai_async/src/**` and Worker tests.

### Phase 2: finish backend producer migration

- Migrate the remaining plain `meal.updated.v1` producers to
  `publish_meal_event(...)`:
  - `src/app/handlers/command_handlers/add_custom_ingredient_command_handler.py`
  - `src/app/handlers/command_handlers/attach_meal_photo_command_handler.py`
  - `src/app/handlers/command_handlers/delete_meal_photo_command_handler.py`
- Minimal recommendation: use the existing optional `event_bus` lookup only
  where already wired. For the three remaining update producers, an empty
  `user_context` is acceptable for first cut because it preserves correctness
  without widening constructor/composition surface.
- Add/adjust backend unit coverage so every meal create/update producer now
  publishes exactly one generic event, never a second legacy insight event.

Files: only backend producer files plus focused backend tests.

### Phase 3: remove legacy special-event path

- Delete Worker special parsing and routing for `meal.value_insight.v1`.
- Delete legacy Worker handler/tests that accept the standalone event.
- Replace stale backend tests that import `MealValueInsightRequestedEvent` /
  `publish_meal_value_insight_event` with generic-event contract tests around
  `MealInsightSnapshot` nested under `data.insight`.
- Update `plans/260824-1320-worker-first-meal-cache-and-insight-cutover/plan.md`
  so it no longer claims the special event is authoritative.

Files: backend event-contract tests/docs plus Worker legacy parser/router/handler/tests.

### Phase 4: verification and rollout

- Local proof:
  - backend focused unit suite for event producers and read path
  - Worker unit suite for generic routing, retry, cache write, FCM best-effort
- Integration proof:
  - one created meal, one updated meal, one deleted meal traced through publish
    -> Worker handler sequence -> Redis `meal_insight:{meal_id}` -> GET status
- Staging proof:
  - same traces with real Queue + Redis
  - one forced handler failure proving whole-event retry / DLQ behavior

## Acceptance criteria

- Backend source emits no `meal.value_insight.v1` message. Every insight-bearing
  meal mutation publishes one generic `meal.created.v1` or `meal.updated.v1`
  event with `data.insight`.
- All three remaining plain update producers are either migrated to
  `publish_meal_event(...)` or explicitly documented as insight-neutral and do
  not delete `meal_insight:{meal_id}`. Recommendation: migrate all three for
  the first cut.
- Worker generic router is the only live meal-insight ingress. It invokes two
  handlers for `meal.created.v1` / `meal.updated.v1`, ACKs only after both
  succeed, and retries the whole event on failure.
- `GET /v1/meals/{meal_id}/value-insights` remains cache-only and returns:
  - `generating` before Worker cache write
  - `fresh` after Worker cache write
  - no local AI fallback
- Insight cache key and JSON shape stay backward compatible:
  `meal_insight:{meal_id}` and the same parsed structure consumed by
  `MealValueInsightService`.
- Generic integration event payload with nested `data.insight` stays under the
  32 KiB envelope limit; unit coverage proves the bounded snapshot.

## Test matrix

- Backend unit:
  - `tests/unit/app/events/test_integration_event.py`
  - `tests/unit/app/graphs/test_meal_analyze_graph_scaffold.py`
  - `tests/unit/handlers/command_handlers/test_create_manual_meal_command_handler.py`
  - `tests/unit/handlers/command_handlers/test_attach_meal_photo_command_handler.py`
  - `tests/unit/handlers/command_handlers/test_upload_image_consistency.py`
  - `tests/unit/handlers/command_handlers/test_scan_by_url_beverage_routing.py`
  - `tests/unit/app/handlers/test_catalog_meal_log_handler.py`
  - `tests/unit/app/handlers/test_meal_recommendation_handlers.py`
  - `tests/unit/app/handlers/test_meal_suggestion_cqrs_handlers.py`
  - add focused coverage for `add_custom_ingredient` / `delete_meal_photo` if absent
- Worker unit:
  - `../nutreeai_async/test/integration-event-router.test.ts`
  - `../nutreeai_async/test/queue-consumer-router.test.ts`
  - `../nutreeai_async/test/meal-cache-invalidation-handler.test.ts`
  - replace `../nutreeai_async/test/meal-value-insight-event-handler.test.ts`
    with generic-event insight-handler coverage
  - `../nutreeai_async/test/cache-operation-executor.test.ts`
- Staging/manual:
  - create meal -> generic event -> cache invalidation -> insight cache write
  - update meal -> old insight invalidated -> new insight written
  - delete meal -> insight key removed, no insight handler run

## Rollback / migration strategy

- Phase 1 is additive. Keep Worker support for legacy `meal.value_insight.v1`
  until generic handler path is green.
- Complete backend producer cleanup before deleting the legacy Worker branch.
- Remove legacy parser/handler/tests last. If rollout pauses mid-way, Worker can
  support both payload shapes without data migration because Redis key/value
  contract stays unchanged.

## Recommendation

Proceed. The architecture target is sound and the Worker already has the key
primitive needed: one generic event can fan out to multiple handlers. The
lowest-risk sequence is Worker generic compatibility first, backend producer
completion second, legacy-path deletion last.

Status: DONE

## Implementation follow-up

The recommended cutover was completed in the shared worktrees with the final
generic-event shape: backend meal create/update events carry an optional
bounded `data.insight` snapshot, and the Worker fans one event out to cache
invalidation plus the application-layer `MealInsightBusinessHandler`. The
legacy standalone event route, parser, and infrastructure handler were removed.
Insight-neutral photo updates preserve an existing insight cache entry.
