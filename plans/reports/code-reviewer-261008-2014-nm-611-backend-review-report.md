# NM-611 backend code review (independent reviewer)

Date: 2026-10-08. Worktree `worktrees/mealtrack_backend-nm611-food-search`, branch `fix/nm-611-food-search-speed-local-foods`, base `origin/delivery`. Saved by the lead from the reviewer's message (the reviewer could not write files).

## Scope
- 11 modified files (+681/-378), 11 new src/migration files (1,020 lines), 15 new test files (2,674 lines).
- Read-only checks: lint-imports 4 kept / 0 broken; scoped unit tests 318 + 64 re-run passed; ruff check on 38 changed files clean; M1 statement compiled without a DB.
- Not run: Postgres integration tests, migrations, `ruff format`. Mypy = known ~1046 baseline (2 on changed lines).

## Overall
No Critical/High. Background-task lifecycle, cache correctness, migration reversibility, SQL binding hold up. One new abuse/cost surface (M1), one release-ordering hazard (M2), rest Low.

## Medium

**M1. Unbounded relevance phrase scored on every matching row**
- `src/infra/repositories/food_reference_local_search.py:83-101`; route `src/api/routes/v1/foods.py:52,67` (`q` min_length=1, no max_length).
- WHERE uses `required_food_search_words` (deduped, capped at 6). `_relevance_order` builds `phrase` from every folded word, bound 6 times (3 padded LIKEs, 1 LIKE, 2 `similarity()`). Non-ASCII `icontains(raw_query)` (`:113`) also unbounded.
- `q="chicken " * 1000` → six ~8 KB binds, one LIKE in WHERE; every "chicken" row scored with an 8 KB phrase (pg_trgm re-extracts trigrams per call). Base required whole-query substring match, so such input returned almost instantly. Bounded by catalog size (~2.1K rows) and rate limits → Medium (CPU estimate, no DB run).
- Fix: phrase from the capped list, or truncate `raw_query` (~200 chars) at the top of `build_local_search_statement` (also bounds `:113`). Unit test compiling a 1000-word query, assert bind lengths. Optional `max_length` on `q` (contract change, coordinate with mobile).

**M2. New image cannot run on the pre-migration schema**
- `src/infra/database/models/food_reference_model.py:38` vs `.github/workflows/migrate.yml`, `render.yaml:5-7`, `docs/runbooks/schema-migration.md`.
- ORM maps `name_search`, so every entity load of `food_reference` selects it. Migrations run only from a manually dispatched workflow; no `preDeployCommand`, no startup schema check.
- Image deployed before "Migrate Database" → `UndefinedColumn food_reference.name_search` on every ORM read/write of the table (search, adopt, meal-save lookups, integrity materialize). `_search_local` swallows it (`handler:613-615`) → provider-only; other paths 500. Reverse order is safe (expand-only migrations, narrowed trigger compatible with old code).
- Fix: PR description + release checklist: run "Migrate Database" on staging then prod before deploying ("expand, migrate, deploy code"). Optional: map as `deferred(Column(...))` so a misordered deploy only breaks the search statement (unverified; `name_search` never read in Python).

## Low
- **L1** Background tasks uncapped, no gauge (`food_search_background_completion.py:62-65,98-104`). `_pending` unbounded; `max_in_flight` limits joinability only. Typing storm + slow provider → one task per distinct key, up to 30 s httpx timeout. Same provider concurrency as base, but invisible. Fix: log/gauge `len(_pending)`; past a ceiling skip provider, return local-only with `partial`.
- **L2** Migration 1 imports app code at upgrade time (`20261007093509175128_…py:27-47`). Mitigated by hash pin test (`tests/unit/infra/database/test_food_reference_search_name_sql.py:25-59`). Fix: inline the expression, keep the pin test.
- **L3** Migration 1 locks the table; upgrade path untested. `ADD COLUMN … STORED` rewrite under ACCESS EXCLUSIVE; GIN build non-concurrent in the same transaction. `lock_timeout` 10 s, `statement_timeout` 240 s. ~2.1K rows → sub-second. Chain tests are the known-failing ones; integration tests use `create_all`. Fix: staging first; confirm prod row count; CONCURRENTLY outside the transaction if much larger.
- **L4** Plan docs contradict code: `plan.md:38` "no OFFSET loop", `phase-01-backend-search-path.md:19` "single over-fetch query"; code runs up to 4 OFFSET passes (`food_reference_repository_async.py:72,380-405`). Bound is better than base's `while True`. Fix the docs.
- **L5** Publish-before-adopt: first response has provider rows without `food_reference_id`, cached page has it (`handler:207-212`, `299-302`). Contract-compatible; mostly dormant (FatSecret search candidates lack `metric_serving_amount`). Document so mobile doesn't key identity on that id.
- **L6** Degraded pages cacheable for 1 h (pre-existing). Translation failure → provider searched with untranslated text; local DB error → page without local rows; either cached (`_canonical_query` `handler:537-548`, `_search_local` swallow `:598-616`, cache gates `:213`, `:311-316`). Matters more now local rows are primary for Vietnamese. Fix: error flag from `_search_local`; skip cache write on local failure or non-TRANSLATED outcome.
- **L7** Test gaps: no long/repetitive query case (M1); no lifespan shutdown-order test (`src/api/main.py` ~255-262); repository SQL tests assert string shape, real semantics only in CI `postgres-integration` job (treat as merge gate). Multi-pass loop covered at `tests/unit/infra/repositories/test_food_reference_source_identity.py:106-150`.

## Traced and dropped
- Background tasks referenced in `_pending`, `_finished` logs exceptions; `wait_for(shield(...))` enforces budgets; caller cancel only cancels the wait; fresh UoW per DB op.
- Cache key covers language, mode, limit, hashed query (≤64 chars); region is a function of language; policy/generation namespace captured at request start; nothing partial/timeout/empty cached.
- SQL and Python folding match (đ/Đ, combining marks, duplicate-source guards); `name_search` never NULL; all SQL parameter-bound; folded words carry no LIKE wildcards; FatSecret identity keys exact; empty/symbol-only query → `None`.
- Same-key localization vs provider work (needs a catalog flip within seconds) — dropped as theoretical.
- Pre-existing lock-order inversion (`materialize_reference` vs trigger); dropping `is_verified.desc()` ordering harmless (SQL gate requires `is_verified`).

## Positives
Small extracted modules, no outer I/O in domain; per-waiter deep copies, cross-loop guard, bounded drain with cancel, lifespan drain before publisher; conservative invalidation; real downgrades with `to_regprocedure` and dialect guards; expand-only schema; hash-pinned fold expression; bounded `search_local`; translation cache stores only full translations; stage timings never log query text; `partial` additive.

## Plan status
Keep Phase 3 "In progress" until CI `postgres-integration` is green, M1/M2 decided, mobile review back. Fix L4 doc mismatch.

## Unresolved questions
1. Current prod `food_reference` row count (Aug 2026 plan: 2,142)? Sets migration lock time.
2. Who sequences migrate vs image deploy for staging/prod? Does `/health` touch `food_reference`?
3. Does FatSecret search ever embed servings in prod? (activates adoption + L5)
4. How does mobile handle `partial`; does it key identity on `food_reference_id`?
5. Enforce `q` length at the route (contract change) or in the repository?
6. Newer migration head on `origin/delivery`? (reviewer could not fetch; worktree chain linear from `20261006115245714931`)

**Status:** DONE_WITH_CONCERNS
