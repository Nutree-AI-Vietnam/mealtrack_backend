# NM-611 mobile review: catalog food search (Flutter)

Reviewer: code-reviewer | Date: 2026-10-08 | Read-only (no edits to code, no flutter test/analyze/build_runner)

## Scope
- Worktree: `/Users/alexnguyen/Desktop/Nut/nutree/worktrees/nutree_ai-nm611-food-search` (branch `fix/nm-611-food-search-speed`, base `main` 512476b0), uncommitted changes.
- Plan used for AC mapping: `/Users/alexnguyen/Desktop/Nut/worktrees/mealtrack_backend-nm611-food-search/plans/261007-1558-GH-611-food-search-speed-and-local-foods/phase-02-mobile-search-ux.md` (AC2, AC4, AC5, AC6, AC8).
- Rules checked: CLAUDE.md, docs/conventions.md, docs/architecture.md, docs/contracts.md ("Catalog food search").
- Checks run: `dart analyze` per file on 9 changed lib/test files = 0 issues. Backend route read for limits/scoping only (`src/api/routes/v1/foods.py`); backend review is a separate agent.
- Flag: rollout gated by PostHog `catalog_food_search_enabled` (default false, fail-closed), so blast radius is limited.

## Verdict
No Critical, no High. 2 Medium, 4 Low, 3 Info. Core behaviour (stale results, lifecycle, cache, errors, shortcuts) traced and correct. Fix M1/M2 before or right after merge; both are small.

---

## Findings (ranked)

### M1. MEDIUM, tests: data-source / repository seam is unasserted
- Where:
  - `test/features/meal_creation/data/datasources/food_api_data_source_test.dart:86` (`isNotNull`), `:89-104`, `:106-114`
  - `test/features/meal_creation/helpers/fake_catalog_food_search.dart:36` (`FakeCatalogFoodRepository implements FoodRepository`)
  - `lib/features/meal_creation/data/repositories/food_repository.dart:33-47` (no test: `test/features/meal_creation/data/repositories/` does not exist)
- Defect:
  - `:86` is always true: `sendWithDeadline` always hands the API its own inner token.
  - `:89-104` ends in `expect(future, completion(anything))`; passes whether or not the caller cancel is forwarded.
  - `:106-114` assert the constants only, not that 6 s is applied to autocomplete and 12 s to search.
  - Provider and sheet tests use the fake repository, so they never reach `sendWithDeadline` or the repository mapping.
- Failure scenario: delete `cancelToken: cancelToken` at `food_api_data_source.dart:35` or `:44`; or swap the deadlines at `:29`/`:38`; or drop `partial: response.partial` at `food_repository.dart:47`. Every test stays green. In prod: old requests are not cancelled on query change (AC2; burns the 60/30 per-minute backend limits), deadlines silently change, partial pages get cached and never refetched (AC5/AC3 regression).
- Fix:
  1. Extend `FakeApiService` with a Completer-backed pending call. After `caller.cancel()` + `await pumpEventQueue()`, assert `fakeApiService.searchFoodsCancelToken!.isCancelled` and that the future throws a `DioException` of type cancel.
  2. `fakeAsync` with a never-completing fake: autocomplete still pending at 5.9 s, `NetworkException` with `isTimeout` at 6 s; search still pending at 6 s, timeout at 12 s.
  3. `FoodRepository.searchCatalog` test with a fake data source returning `partial: true` and `false`; assert `FoodSearchPage.partial` and mapped results.

### M2. MEDIUM, analytics (prod only): `errorType` is obfuscated and cannot tell timeout from offline
- Where: `lib/features/meal_creation/application/providers/catalog_food_search_provider.dart:107`
- Defect: `errorType: fromException(error).runtimeType.toString()`.
  1. Prod releases build with `--obfuscate --split-debug-info` (`scripts/shorebird-release.sh:214,229`), so `runtimeType.toString()` is a minified name that changes between builds.
  2. Offline and timeout are both `NetworkException`, so the new `isTimeout` split (used for UI copy at `catalog_food_search_error_banner.dart:17,41`) is lost in analytics.
- Failure scenario: PostHog `manual_food_search_failed.error_type` shows junk values per release. During the flagged rollout nobody can split timeout vs offline vs server by `error_type`. Mitigation, so non-blocking: `duration_ms` is sent too, and timeouts cluster at about 6000/12000 ms.
- Same pattern pre-exists at 6 call sites (`food_search_provider.dart:64`, `manual_meal_save_service.dart:139,317,327`, `meal_edit_screen.dart:470`, `meal_share_sheet.dart:379`). Do not copy it into new code; optionally share one helper.
- Fix (stable labels; `errorCode` is `NETWORK_ERROR` / status-code string):
  ```dart
  String catalogFoodSearchErrorLabel(Object error) {
    final e = fromException(error);
    if (e is NetworkException) return e.isTimeout ? 'timeout' : 'offline';
    return e.errorCode ?? 'unknown';
  }
  ```
  Add a provider test asserting `timeout` vs `offline` on the failed event.

### L3. LOW, analytics: caller cancel during 503 back-off is reported as a failure
- Where: `catalog_food_search_provider.dart:34-35,104` with `lib/core/network/interceptors/retry_on_503_interceptor.dart:51-53`
- Defect: after the back-off, a cancelled token makes the interceptor call `handler.next(err)` with the ORIGINAL 503 error, not a cancel. `isCatalogFoodSearchCancel` is false, so `trackManualFoodSearchFailed` fires. Contract (docs/contracts.md): cancels send no failure event.
- Trace: user types "pho"; request in flight; backend returns 503; interceptor waits 0.5-5 s; user types more; the provider is disposed and `cancelToken.cancel()` runs; delay returns early; `:52` is true; the 503 `ServerException` (type badResponse) goes up; `sendWithDeadline` has `timedOut == false` so it rethrows; the catch at `:104` is not a cancel, so a failed event is sent.
- Impact: nothing shown to the user (provider already disposed). Inflates the failed count, only while the backend is returning 503 (deploy windows).
- Fix: `if (!cancelToken.isCancelled && !isCatalogFoodSearchCancel(error))` at `:104`. The per-build token is cancelled only by dispose; the deadline cancels the inner token, so real timeouts are still reported.

### L4. LOW, convention: `Theme.of` text style instead of `AppTypography`
- Where: `lib/features/meal_creation/presentation/widgets/catalog_food_search_idle_sections.dart:155` (`Theme.of(context).textTheme.labelLarge?.copyWith(...)`)
- Defect: skips `AppTypography.*(languageCode: ...)` (conventions + CLAUDE.md), so section titles ignore the per-language font (vi/ja/zh).
- It is the only newly authored instance: `titleLarge`/`titleMedium` in `sheet_header.dart:35` / `results_body.dart:48` moved from the old sheet (HEAD lines 227, 426) and `catalog_food_result_card.dart:82,94,102` are untouched by this diff.
- Fix: `AppTypography.captionMedium(languageCode: context.languageCode, color: context.colors.textSecondary)` (or `bodyMedium`), pick the size to match design.

### L5. LOW, test nits
- `test/.../catalog_food_search_provider_test.dart:83-102`: name "a full answer is kept, so asking again needs no request" asserts only `cache.lookup(...)` and `cacheHit == false`; it never asks again (repeat is covered at `:35`). Rename or add the second read.
- Sheet level: `_onFoodSelected` -> `_rememberPick` (details path, `catalog_food_search_sheet.dart:154`) is untested; only the quick-add path is.
- Local store "loads and parses" test asserts only `name`.
- Session-cache test: heavy boilerplate, weak `keyFor` format assertion.

### L6. LOW / INFO, noise: corrupt shortcut entries are logged on every load
- Where: `lib/features/meal_creation/data/datasources/catalog_food_shortcuts_local_store.dart:44-47,57-60`
- Behaviour is correct (corrupt blob or entry = miss, never throws). Each failure goes to `ErrorLogger` on every load until the next `record()` or favorites save rewrites the JSON. Possible Sentry noise for a rare user. Optional: log once per session.

### I7. INFO, design trade-off: `name:` identity key collapses brands
- `lib/features/meal_creation/domain/services/food_search_result_identity.dart:17`. Same-name foods with different brands merge in recents/favorites. Deliberate: meal-derived foods have `brand: null` (`meal_food_search_result_mapper.dart`). Confirm product intent.

### I8. INFO: persistence side effect inside provider build
- `catalog_food_shortcuts_provider.dart:72` `unawaited(store.saveFavoriteFoods(favoriteFoods))`. Idempotent (unchanged JSON skipped), errors swallowed. Fine; a listener would be cleaner.

### I9. INFO: backend rate limits
- Autocomplete 60/min, search 30/min (`foods.py`). 300 ms debounce makes sustained breach unrealistic; cancelled requests still count server-side. 429 shows the generic server banner + retry (no dedicated copy). Limiter key (per user/IP vs global) is for the backend review.

---

## Verified correct (traced, no action)
- Stale results: body watches only the current-key autoDispose provider (`catalog_food_search_body.dart:51-54`); the old request is cancelled on dispose (`provider:80-83`); `_lastShown` is shown only while the new key loads and is cleared under min length or on error (`body:70,75-80,86`). Test: sheet test `:161-182`.
- Lifecycle: debounce Timer cancelled in dispose/submit/clear/term (`sheet:80,90,109,115`); `mounted` guards on every async continuation; refetch Timer cancelled on dispose (`provider:81`); delayed `state =` guarded by `buildRef.mounted` (`:118,:122`).
- `sendWithDeadline` (`request_deadline.dart`): cancel-first vs timer-first ordering, pre-cancelled caller token, Timer always cancelled in `finally`. Dio 5.11 `CancelToken.cancel` is idempotent (no throw on double cancel; debug-only warning log), so the accepted "cancel after deadline" case is harmless.
- Cache: key language|mode|normalized query; stores only `!partial && results.isNotEmpty` (`provider:93`); autocomplete may reuse a search page; LRU 40, 15 min; partial gets one refetch after 1.5 s and the full refetch result is stored by the same rule.
- Errors: timeout vs offline vs server mapping (`error_banner:15-21,40-41`); no provider auto-retry (`provider:26,42`); retry = `ref.invalidate` and keeps old error while loading (`body:74-80,94`); started/completed/failed counted once per build; cache hit sends started + completed (0 ms, `cacheHit true`).
- Shortcuts: corrupt JSON = miss; dedupe + limits (6 shown, 12 stored); concurrent `record()` safe (SharedPreferences cache updates synchronously); account switch wipes keys via `AccountStorageCoordinator._purgeAndUnbind`.
- Security/privacy: analytics send `queryLength` only, never query text; session cache holds global (non-user) catalog rows (backend search has no user id).
- Conventions/l10n/a11y: ARBs complete in 7 locales; `Semantics` header/liveRegion/progress label; no `toStringAsFixed`/hardcoded colors/fonts added; presentation does not import `data/*`; application->data mapper import allowed by architecture.md and has precedents.

## Dropped after tracing (not defects)
- Cross-account cache or shortcut leak (purge covers it; cache rows are global).
- "Search failures no longer logged" (old path never logged; ErrorLogger drops Network/Server exceptions).
- Language key mismatch in cache key (refuted).
- application->data import (allowed).

## Positive observations
- Stale-result safety by construction (one watched key, cancel on dispose) instead of sequence counters.
- Small focused widgets; docs (`architecture.md`, `contracts.md`) updated with the behaviour.
- Fail-safe local store; analytics carry duration/cache/partial without PII.
- Sheet and provider tests use fake time, real assertions, no `pumpAndSettle`, nothing that can hang.

## Plan follow-ups
- Phase-02 todo items 1-5 look complete in code. Item 6 (build_runner, analyze, test, guard scripts) not re-run by reviewer (full run in progress in the worktree). AC3 p95 needs prod measurement after deploy.
- Known and accepted items (cross-layer guard 167 vs 166, file sizes, dead code, `toResponse()` density, `sendWithDeadline` post-deadline cancel reporting) not re-reported; no new failure mode found for them.

## Metrics
- Lint: `dart analyze` 0 issues on 9 files (per-file). Full analyze/tests not run by reviewer.
- Coverage: not measured.

## Unresolved questions
1. Fix L3 (cancel during 503 back-off) in this PR (1 line) or follow-up?
2. Share one stable `errorType` helper across the 6 pre-existing call sites, or only fix the catalog provider?
3. Backend limiter key and 429 UX: per user/IP or global? Needs the backend review.
4. Are brand-distinct recents wanted (affects the `name:` identity key)?
5. Who checks AC3 p95 after deploy, and with which PostHog insight / `[RES-id] elapsed=` logs?
