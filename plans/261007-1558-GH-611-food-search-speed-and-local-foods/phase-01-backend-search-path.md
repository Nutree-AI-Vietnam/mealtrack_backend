# Phase 1 — Backend search path

## Context
- Report: `../reports/debugger-261007-1515-nm-611-food-search-latency-and-local-foods-report.md`
- Handler: `src/app/handlers/query_handlers/search_foods_query_handler.py`
- Repository: `src/infra/repositories/food_reference_repository_async.py` (`search_local`)
- Wiring: `src/api/dependencies/event_bus.py`, `src/api/base_dependencies.py`

## Requirements
- Vietnamese query (`phở bò`, `pho bo`, `banh mi`) finds VN catalog rows by `name_vi`/`name`, accent-insensitive; diacritic-exact matches rank first.
- English behaviour unchanged for top results.
- No LLM or provider call when local results already fill the page with strong matches.
- Provider call bounded by a time budget; never blocks longer than the budget.
- Cache hits stay valid across catalog adoptions (no self-invalidation).

## Steps
1. Fold helper (`src/domain/utils/food_search_text.py`) + tests.
2. Dialect-aware fold expression + generated column `name_search` on `food_reference`; CLI migration with trigram index.
3. Rewrite `search_local`: region list, folded word-prefix match, match rank, SQL-only eligibility status gate, at most 4 bounded OFFSET passes (was unbounded).
4. Handler: local search first; answer at once when ≥5 strong local matches fill the page. Otherwise translation + provider run as one shared background task; the handler waits up to the mode budget (autocomplete 0.5s / search 0.8s with local rows, 3s / 8s without) and answers from local rows (`partial`) when the work runs late; late work warms the cache. Cache key carries limit and mode; stage timings logged.
5. Per-text translation cache decorator around the translation port.
6. Integrity context TTL cache.
7. `materialize_reference`: bump generation only when a cached page could show the row — not for rows already quarantined, policy-version-only changes, or rows inserted by the current transaction.
8. Serving-size trigger function narrowed (CLI migration `20261007163030686049`): still resets the reference and logs an event per row, but bumps only for shown references, never for references created in the same transaction, at most once per reference per transaction. Metadata-bootstrapped databases have no trigger; the migration skips them.
9. Tests: unit (handler, fold, repo SQL, generation bump), Postgres integration (search golden set, trigger).

## Todo
- [x] 1 fold helper
- [x] 2 column + migration
- [x] 3 search_local
- [x] 4 handler
- [x] 5 translation cache
- [x] 6 integrity context TTL
- [x] 7 generation bump narrowing
- [x] 8 trigger narrowing
- [x] 9 tests + checks (ruff, mypy, lint-imports, unit cov ≥65, integration)

## Success criteria
- Unit + integration suites green; golden-set top-5 assertions pass for en and vi.
- Local-only path: no translation/provider call when ≥5 strong local matches.

## Risks
- Generated column rewrite on `food_reference` takes an ACCESS EXCLUSIVE lock during migration (table is small: catalog rows only).
- Trigger change must keep publication fencing for every column that affects nutrition selection.
