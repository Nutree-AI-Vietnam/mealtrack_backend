---
date: 2026-10-03
scope: weekly-planner-optimization-batches-1-2
status: focused-checks-passed
---
# Weekly planner batches 1 and 2 verification

## Summary

Focused service/grocery suite: **44 passed**. Repository loader guards: **3 passed**. Dedicated PostgreSQL tests: **8 passed**, 3.13 seconds including database setup. Source unchanged by tester. Updated old fake UoW to implement batch `get_meals`; no production compatibility shim.

## Findings and evidence

- Fresh PostgreSQL sessions load a persisted 14-slot plan with exactly **2 core SELECTs**, for one shared recipe and two mixed recipes. No catalog or pantry tables in these statements. Test `test_plan_read_uses_two_core_selects_in_fresh_session` compares the complete domain aggregate. Pre-change 8-SELECT baseline supplied by integrator; not remeasured here. Compact summaries and full-route counts excluded.
- Bounded validation covers deduplicated replacements/null clears, no catalog reads for soft preferences, selected unlogged recipes plus replacements for hard preferences, logged-only recipe exclusion, and exact `RECIPE_NOT_FOUND`, `RECIPE_SLOT_INELIGIBLE`, `RECIPE_INELIGIBLE` errors.
- Authoritative missing batch recipes yield empty groceries without per-ID fallback.
- Pantry **0/50/2000 g**, checked/do-not-buy/manually-owned flags, day notes and Undo persisted through commit and fresh sessions. Unauthorized plan/pantry reads return no data.
- PostgreSQL concurrent same-revision swaps allow one commit and one `WEEKLY_PLAN_STALE_REVISION`; fresh state has one changed slot and revision increment.
- Logging holds the parent/slot lock while a swap demonstrably waits; the swap rejects the committed logged slot and fresh state retains recipe/logged meal.
- Missing-week concurrent creation under owner/week advisory locks produces one shared plan ID.
- Loader unit guards cover unauthorized parent lookup without slot locks, parent before ordered slot locks, and stable signed 64-bit owner/week advisory keys.

## Review

No confirmed batch1/2 correctness regression in tested scope. Integrator added `populate_existing=True` to locked plan reads after freshness concern; logging already refreshes its locked slot. Owner validation on reused domain plan is explicit. Queue-pool kwargs selected from actual pool class; driver policy unchanged.

Expanded route/pool run initially encountered a concurrent phase5 import error: `weekly_meal_plan_adjustment_provider.py` imported nonexistent `normalize_language` from `timezone_utils`; integrator corrected it. Subsequent full PostgreSQL run passed every collected case.

## Validation boundaries

- Runtime: project Python 3.13.2; isolated local PostgreSQL, independent AsyncSessions; synthetic catalog/user data only. All database URL aliases supplied to same dedicated DB; no external data touched.
- Ruff and syntax compilation pass for all four owned test files.
- Pantry total/remaining/status assertions pass in the full PostgreSQL run. Fixed fixture ordering by stable catalog key; UUID order otherwise selected 100 g or 101 g recipes nondeterministically.
- No deployed endpoint latency, provider calls, aggregate payload bytes, live p95, full unit coverage, compact projection counts, new short-generation flag, or release claims.
- Integration tests require `-m integration`; default pytest config deselects them. Do not overlap PostgreSQL suites because fixture truncates shared tables.

## Recommendations

Final all-PostgreSQL run: **37 passed in 18.07 seconds**, including eight planner cases, three generation transaction cases, catalog projection/publication, preparation recovery and existing PostgreSQL tests. Integrator owns full unit/CI and release gates; see [preparation report](./tester-2026-10-03-catalog-preparation.md).

## Unresolved questions

None for batch1/2 scope; final integration and performance evidence remain pending.
