---
phase: 3
title: "Backend Insight Publishing"
status: completed
priority: P1
effort: "2d"
dependencies: [1, 2]
---

# Phase 3: Backend Insight Publishing

## Overview

Replace FastAPI's process-local meal insight spawning with the optional
`data.insight` snapshot on the generic meal Queue event, while preserving the
existing trigger surfaces and response timing.

## Key Insights

- The former process-local helper has been removed. Committed meal-write
  handlers now publish one generic event with a bounded `data.insight` snapshot
  when nutrition is available.
- Runtime trigger inventory is exact, not approximate:
  `src/api/routes/v1/meals_read.py:190-198,254-262`,
  `src/api/routes/v1/meals_edit.py:201-209`,
  `src/api/routes/v1/meals_analyze.py:127-135`,
  `src/api/routes/v1/meals_manual_text.py:217-225`,
  `src/api/routes/v1/meal_scan_by_url.py:165-174`,
  `src/app/graphs/meal_analyze/nodes.py:153-189`,
  and the catalog callback in
  `src/api/dependencies/event_bus.py:746-774`.
- The backend reader now uses the Worker-owned `meal_insight:{meal_id}` key and
  never invokes AI or schedules background work.
- The Worker insight contract already supports AI generation and optional push,
  but it is a special queue payload, not the generic `IntegrationEvent`
  envelope (`../nutreeai_async/src/domain/events/meal-value-insight-event.ts:28-41,150-211`).

## Requirements

- Functional: replace all runtime uses of `schedule_value_insight_generation`
  with direct Queue publication.
- Functional: preserve post-write prewarm coverage for meal creation/edit paths.
- Functional: publish only after the meal is committed.
- Functional: include normalized language, bounded profile/TDEE context when the
  event bus can provide it, and the authoritative nutrition and ingredient
  snapshot required by the Worker.
- Functional: keep `tokens` optional; absence of tokens must not block cache generation.
- Non-functional: do not port backend cache hashing into the Worker.
- Non-functional: do not make request success depend on queue acceptance or AI completion.

## Architecture

```text
write/read trigger
  -> compact user context
  -> publish meal.created.v1 or meal.updated.v1 with data.insight
  -> return response immediately
```

Chosen backend rule:
- The backend keeps language normalization and bounded user-context lookup.
- The Worker derives `meal_insight:{meal_id}` and owns the seven-day TTL.

## Related Code Files

- Modified: `src/app/events/meal/meal_events.py`
- Modified: meal command handlers and graph publication node under
  `src/app/handlers/` and `src/app/graphs/meal_analyze/`
- Modified: `src/api/routes/v1/meals_read.py` and meal write routes
- Modified: `src/domain/services/meal_value_insight_service.py`
- Removed: the process-local insight schedulers and
  `src/domain/ports/meal_insight_ai_port.py`
- Tests: `tests/unit/app/events/test_integration_event.py`,
  `tests/unit/domain/services/test_meal_value_insight_service.py`,
  `tests/unit/app/graphs/test_meal_analyze_graph_scaffold.py`

## Implementation Steps

1. Add the validated bounded insight snapshot to the generic meal event.
2. Publish the generic event after committed meal writes across all existing write
   paths, including graph and catalog/recommendation flows.
3. Make the backend insight service a cache-only reader and remove its local AI
   and scheduler dependencies.
4. Remove graph/runtime and route plumbing that existed only for local insight
   generation.
5. Extend focused tests to assert the event contract and cache-only behavior.

## Todo List

- [x] Replace `spawn(...)`-based helper logic with publish logic
- [x] Remove the old scheduler and AI port
- [x] Replace write-side trigger sites with direct event publication
- [x] Remove graph/runtime task-manager wiring used only for insights
- [x] Add focused backend tests for event publication, profile context, and
  cache-only reads

## Success Criteria

- [x] No runtime path still calls `BackgroundTaskManager.spawn(...)` for meal insights.
- [x] Write-side triggers publish one generic meal event with the Worker insight snapshot.
- [x] Route responses remain non-blocking and read the Worker-owned cache namespace.
- [x] Backend tests cover write-side publication, profile context, and cache-only
  reader behavior.

## Risk Assessment

- Medium likelihood / Medium impact: duplicate publish sources can create duplicate
  AI work before a result is cached.
  Mitigation: generic meal invalidation keeps stale results from being served;
  lease-based dedupe remains a follow-up if duplicate delivery becomes material.
- Medium likelihood / High impact: backend publishes a cache key that does not
  match the read path.
  Mitigation: keep the fixed `meal_insight:{meal_id}` namespace in both the
  cache-only reader and Worker contract, and assert it in tests.
- Medium likelihood / Medium impact: removing task-manager plumbing breaks graph
  runtime assumptions.
  Mitigation: keep the existing graph tests and update them to assert publish
  rather than spawned coroutine ownership.

## Security Considerations

- Do not embed raw auth headers, Firebase claims, or image URLs in the insight event.
- Keep `user_context` restricted to the compact safe subset built in
  `src/app/events/meal/meal_events.py`.
- If token enrichment is later added, only active device tokens belong in the payload.

## Next Steps

Phase 4 owns the Worker-side cache write, invalidation, and optional push behavior.

## Unresolved Questions

- None.
