# Worker-first meal cache/insight cutover verification

Date: 2026-08-24

Scope: backend compile/lint/test verification in `/Users/alexnguyen/Desktop/Nut/mealtrack_backend` and sibling Worker checks in `/Users/alexnguyen/Desktop/Nut/nutreeai_async`.

## Current Final Status

- Backend CI-aligned unit gate: `2689 passed`, `80.58%` coverage, 51 warnings.
- Worker unit tests: `108 passed` across 27 files.
- Worker staging and production dry-run deploy validation passed.
- Backend compileall, focused Ruff, and `git diff --check`: passed.
- Worker typecheck remains blocked by pre-existing affiliate/notification type
  errors outside this cutover.
- Staging deployment, Queue/Redis live proof, retry/DLQ proof, and live FCM proof
  were not run.

## Initial Verification Before Follow-up Fixes

- `python -m compileall src tests`
  - Result: passed.
- `ruff check $(git diff --name-only --diff-filter=AM HEAD -- src | rg '\.py$')`
  - Result: failed.
  - Note: the first broad Ruff attempt also hit deleted files from the cutover. The corrected run still failed on touched implementation files.
- Focused backend pytest slice
  - Command: `.venv/bin/python3 -m pytest tests/unit/api/test_meal_scan_by_url_insights.py tests/unit/api/test_manual_meal_durable_replay.py tests/unit/api/test_meals_read_translation.py tests/unit/api/test_webhook_handler.py tests/unit/app/events/test_integration_event.py tests/unit/app/graphs/test_meal_analyze_graph_scaffold.py tests/unit/app/handlers/test_catalog_meal_log_handler.py tests/unit/app/handlers/test_meal_recommendation_handlers.py tests/unit/domain/services/test_meal_value_insight_service.py`
  - Result: passed, `97 passed in 1.53s`.
- CI-aligned backend suite
  - Command: `.venv/bin/python3 -m pytest tests/unit --cov=src --cov-fail-under=65`
  - Result: failed with test failures, but coverage gate passed.
  - Totals: `2683 passed, 5 failed, 51 warnings in 107.04s`.
  - Coverage: `80.57%` total.
- Worker tests
  - Command: `npm test`
  - Result: passed, `27 test files, 102 tests`.
  - Command: `npm run typecheck`
  - Result: failed.

## Initial Failed Tests (Resolved)

- Backend regression tests tied to the meal-analyze cutover:
  - `tests/unit/handlers/command_handlers/test_beverage_scan_routing.py::test_packaged_beverage_scan_creates_standard_meal_not_hydration_entry`
  - `tests/unit/handlers/command_handlers/test_scan_by_url_beverage_routing.py::test_scan_by_url_packaged_beverage_creates_standard_meal`
  - `tests/unit/handlers/command_handlers/test_upload_image_consistency.py::test_successful_upload_keeps_ready_scanner_contract_with_backend_calories`
  - Root cause: these paths now publish both the meal-created event and the meal-value-insight event, so assertions expecting one publish now see two publishes. See `src/app/handlers/command_handlers/upload_meal_image_immediately_command_handler.py:371-390` and the analogous scan-by-url path.
  - `tests/unit/handlers/command_handlers/test_scan_by_url_beverage_routing.py::test_scan_by_url_uses_workflow_only_when_graph_enabled`
  - `tests/unit/handlers/command_handlers/test_upload_image_consistency.py::test_upload_uses_workflow_only_when_graph_enabled`
  - Root cause: `MealAnalyzeRuntime` does not accept the `event_bus=` keyword that the cutover handlers pass. See `src/app/graphs/meal_analyze/runtime.py:43-77`, `src/app/handlers/command_handlers/upload_meal_image_immediately_command_handler.py:416-432`, and `src/app/handlers/command_handlers/scan_by_url_command_handler.py:445-462`.

## Initial Build / Lint Status

- `compileall`: success.
- Ruff: failed on touched implementation.
  - The errors were mostly style/import debt in modified files such as `src/app/commands/meal/create_manual_meal_command.py`, `src/app/commands/meal/edit_meal_command.py`, and `src/infra/database/models/__init__.py`.
  - The first Ruff pass also reported deleted-path `E902` noise from the cutover. The corrected pass still failed on existing style issues.
- Backend coverage gate: reached `80.57%`, above the `65%` threshold.
- Worker `npm test`: success.
- Worker `npm run typecheck`: failed on unrelated type-layer files:
  - `src/application/event-handlers/affiliate-event-handler.ts`
  - `src/domain/events/affiliate-events.ts`
  - `src/domain/notifications/notification-renderer.ts`
  - `src/interfaces/cloudflare/queue-consumer-router.ts`
  - `test/affiliate-event-handler.test.ts`

## Initial Classification

- Cutover-caused failures:
  - The 5 failing backend tests are directly tied to the meal-analyze runtime wiring and event fan-out introduced by this cutover.
- Pre-existing dirty-tree / unrelated failures:
  - Ruff findings on touched but not cutover-specific files.
  - Worker typecheck errors in unrelated event/notification files outside the active cache-consumer diff.

## Historical Recommendations

- Remove the stray `event_bus=` argument from `MealAnalyzeRuntime` call sites or add the field if the graph truly needs it.
- Update the beverage/upload expectations to account for the deliberate second publish if that behavior is intended.
- Re-run Ruff on the modified implementation after the style debt in the touched files is either accepted or normalized.
- Fix the Worker type layer separately; it is blocking `npm run typecheck` but does not appear caused by this cutover.

## Historical Next Steps

1. Resolve the `MealAnalyzeRuntime` constructor mismatch.
2. Re-run the 5 failing backend tests.
3. Decide whether the double-publish behavior is intentional and update the assertions if so.
4. Clear the Worker typecheck failures in the sibling repo.

## Historical Unresolved Questions

- Whether the extra meal-value-insight publish in the beverage/upload paths is intended behavior or an accidental duplicate fan-out.
- Whether the Worker typecheck failures are already tracked as separate debt in the sibling repo.

## Follow-up Verification

The five cutover-caused failures were resolved:

- Removed stale `event_bus=` arguments from `MealAnalyzeRuntime` construction.
- Updated scanner/upload assertions to verify the intentional
  `meal.created.v1` plus `meal.value_insight.v1` publication.
- Removed two unused test locals/imports exposed by the cutover edits.

Follow-up results:

- Full backend gate: `2688 passed`, `80.57%` coverage, 51 warnings.
- Scanner/upload/catalog/recommendation regression slice: `39 passed`.
- Backend compileall and `git diff --check`: passed.
- Focused Ruff over the cutover implementation and tests: passed.

## Generic-event architecture update

The final implementation uses one generic `meal.created.v1` or
`meal.updated.v1` event. Nutrition-bearing writes include a bounded
`data.insight` snapshot; the Worker routes the event first through
`MealCacheInvalidationHandler` and then through the application-layer
`MealInsightBusinessHandler`. The standalone `meal.value_insight.v1` path is no
longer part of the runtime contract. Photo-only updates remain cache-neutral
and preserve an existing insight result.

Review follow-up:

- Generic Worker meal invalidation now deletes the UUID-backed
  `meal_insight:{meal_id}` entry.
- Manual-meal Queue publication now runs after UoW exit/commit.
- Catalog meal Queue publication now also runs after UoW exit/commit, while
  idempotent replays do not republish the integration event.
- Insight events include the bounded profile/TDEE context when the configured
  event bus can provide it; lookup failure degrades to an empty context.
- Worker cache writes and invalidations use an atomic per-meal Redis version
  fence; stale insight writes and older invalidation retries cannot overwrite or
  delete a same-mutation/newer result.
- Final backend gate after these fixes: `2689 passed`, `80.58%` coverage.
- Final Worker unit tests: `108 passed`.
- Same-meal Worker queue ordering is covered; empty normalized AI output now
  retries before any cache write, matching the backend parser contract.
- Final review: no blockers found for the fence, retry, timestamp, or empty-result
  paths.
