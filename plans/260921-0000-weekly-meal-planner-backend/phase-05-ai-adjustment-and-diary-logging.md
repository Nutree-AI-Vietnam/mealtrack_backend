---
phase: 5
title: "AI Adjustment And Diary Logging"
status: pending
priority: P1
effort: "4-6d"
dependencies: [3, 4]
mode: deep
---

# Phase 5: AI Adjustment And Diary Logging

## Overview

Add Ask Nutree as a safe structured proposal flow and connect a planned slot to
the existing meal diary without duplicating nutrition or meal persistence.

## Context Links

- Handoff sections 5.3 and 5.5
- Structured AI patterns: `src/infra/adapters/meal_generation_service.py`,
  `src/infra/services/ai/`, `src/domain/services/ai_output_validation_service.py`
- Existing catalog log path: `src/app/services/catalog_meal_log_service.py`,
  `src/app/services/recommended_meal_materialization_service.py`
- Existing event publication: `src/app/handlers/command_handlers/meal_catalog/`

## Requirements

- `POST /v1/meal-plans/{plan_id}/ai-prompt` accepts a bounded prompt and returns
  explanation, summary, proposed slot changes, and a proposed plan projection.
- LLM output is structured, schema-validated, bounded to existing catalog IDs,
  and revalidated by deterministic diet/time/dislike/allergy policy. Invalid or
  unavailable provider output fails without changing the plan.
- The proposal is ephemeral/read-only. Applying changes requires the normal
  owner-scoped PATCH and its lifecycle/idempotency rules; no direct AI mutation.
- `POST /v1/meal-plans/{plan_id}/slots/{slot_id}/log` validates slot ownership,
  date/type, selected recipe, and portion multiplier 0.5/1/1.5/2; it creates a
  normal `Meal`, scales the logged nutrition by portion only, and links the slot
  to `logged_meal_id`.
- Logging is idempotent and transaction-safe. Replays return the original meal;
  duplicate/incompatible logs do not create a second meal.
- Existing meal event/integration publication remains after commit; translation
  persistence stays best-effort at its existing boundary.

## Architecture

Use an application command for AI proposal orchestration and a provider port
backed by the existing structured-generation service with a dedicated model
purpose. The prompt contains only the bounded plan/recipe context required for
the diff. A pure validator resolves every proposed recipe to the same catalog
snapshot and recomputes policy constraints. Diary logging reuses
`RecommendedMealMaterializationService`, adding a portion multiplier at the
domain materialization boundary rather than adding client macros. Weekly
logging must claim/finalize `weekly_meal_plan_slots` directly; it must not
accidentally use the existing three-day recommendation slot lookup.

## Related Code Files

| Action | Files |
|---|---|
| Create | `src/app/commands/meal_planner/ai_adjust_meal_plan_command.py`, `log_meal_plan_slot_command.py` |
| Create | matching handlers, AI proposal domain DTO/validator, and response mappers |
| Create | `src/infra/services/ai/weekly_meal_plan_adjustment.py` or the existing provider-port equivalent |
| Modify | `src/api/routes/v1/meal_plans.py`, request/response schemas, event-bus registration |
| Modify | `src/app/services/catalog_meal_log_service.py` and materialization seam for portion scaling |
| Reuse | `src/infra/repositories/meal_write_operation_repository_async.py` for log idempotency inside the weekly UoW |
| Extend | `tests/unit/app/`, `tests/unit/domain/`, `tests/unit/api/`, integration meal/log tests |

## Implementation Steps

1. Define the structured proposal schema: bounded explanation, slot changes,
   old/new catalog IDs, action, and server-generated summary. Reject unknown
   fields/IDs and cap changed slots/context size.
2. Add provider adapter wiring using existing OpenAI/Cloudflare routing and
   error semantics. Never persist raw prompt/provider output or send it to
   analytics; log only bounded outcome metrics.
3. Add deterministic post-validation and ensure AI output cannot bypass
   confirmed-plan or allergy-safety policy.
4. Add a weekly-slot claim/finalize path in the weekly repository and extend the
   catalog materialization command with a typed portion multiplier and
   idempotency fingerprint that includes plan, slot, date, type, and multiplier.
5. Materialize the normal meal with canonical food references, derive calories
   from scaled macros, finalize the weekly slot link in the same transaction,
   then publish the existing meal-created event after commit.

## Tests Before

- Characterize current catalog log idempotency, materialization, canonical
  `food_reference_id`, and event publication behavior.
- Lock AI provider failure mapping and ensure no plan mutation occurs on failure.

## Tests After / Scenario Matrix

| Scenario | Expected evidence |
|---|---|
| Valid AI diff | Only existing catalog recipes; deterministic summary |
| Unknown/invalid AI recipe | 422/503; no SQL plan mutation |
| Prompt too large | 413/422 before provider call |
| Provider timeout/failure | Stable 503; no raw prompt/output persistence |
| Confirmed plan AI request | Same lifecycle conflict as PATCH |
| Portion 0.5/1/1.5/2 | Nutrition and calories scale from backend macros |
| Invalid multiplier/date/type | 422; no meal or slot link |
| Duplicate log/replay | One meal, stable replay payload |
| Concurrent log | One winner; no duplicate diary meal |
| Post-commit event failure | Meal/slot transaction remains correct; existing boundary reports failure |

## Function / Interface Checklist

- AI provider interface is replaceable by a narrow fake in unit tests.
- Proposal validator shares catalog/policy code with generation and PATCH.
- Portion scaling occurs before backend calorie derivation and never trusts
  request calories/macros.
- Weekly slot claim/finalization and meal creation share one UoW/transaction;
  the existing three-day recommendation repository is not involved.

## Dependency Map

- Depends on Phase 3 persistence and Phase 4 API schemas/repositories.
- Blocks end-to-end weekly planner readiness.
- Must not change standalone `/v1/meal-catalog/{id}/log` behavior except for
  deliberate shared-service regression fixes.

## Success Criteria

- [ ] AI returns safe, structured, non-mutating diffs with stable failure paths.
- [ ] Slot logging creates one canonical normal meal with correct portion math
  and durable slot linkage.
- [ ] Existing catalog logging and meal event flows remain green.

## Risk Assessment

AI is an untrusted suggestion source and diary logging is a data-integrity
boundary. Mitigate with structured output, catalog-ID allowlists,
post-validation, one transaction, idempotency, and no raw prompt persistence.

## Security Considerations

Apply prompt/body size limits, redact user identifiers, avoid prompt/provider
payload logs, enforce owner scope before loading plan context, and treat recipe
text as untrusted model context.

## Next Steps

Run full migration/API/concurrency verification and keep writes behind rollout
control until production content is ready.
