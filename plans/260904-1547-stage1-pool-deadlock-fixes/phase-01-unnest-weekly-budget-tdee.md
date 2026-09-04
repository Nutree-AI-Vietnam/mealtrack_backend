---
phase: 1
title: Unnest weekly budget TDEE
status: completed
effort: M
---

# Phase 1: Unnest weekly budget TDEE

## Overview

Stop `GetWeeklyBudgetQueryHandler` from calling `GetUserTdeeQueryHandler` while holding an open `AsyncUnitOfWork`. Mirror the daily-macros fix: resolve TDEE once **before** the UoW, pass the result into create/sync/policy paths.

Nested call sites today (all inside `async with uow`):
- `_current_target_policy` (today + tomorrow preview)
- `_create_weekly_budget`
- `_sync_targets_if_stale`

## Implementation Steps

1. Add `_resolve_tdee(user_id)` that calls `GetUserTdeeQueryHandler` with `cache_service`.
2. In `handle()`, call `_resolve_tdee` **before** `async with uow`.
3. Use TDEE `profile_target_revision`, `bmr`, macros, and `(macro_preset, is_custom)` policy from that result.
4. Change `_create_weekly_budget` / `_sync_targets_if_stale` to take `tdee_result` — no nested handler calls.
5. Remove or demote `_current_target_policy` to a pure extractor from the TDEE dict.
6. Add unit test: while weekly-budget UoW is open, `GetUserTdeeQueryHandler` is never constructed/called (or assert TDEE handle is only called once, before UoW enter).
7. Update existing weekly-budget unit tests to pass `tdee_result` into create/sync helpers.

## Success Criteria

- [ ] No `GetUserTdeeQueryHandler.handle` while weekly-budget UoW session is open
- [ ] Existing weekly budget unit tests pass
- [ ] New consolidation test proves single pre-UoW TDEE call
