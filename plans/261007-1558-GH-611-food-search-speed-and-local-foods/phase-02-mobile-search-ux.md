# Phase 2 — Mobile search UX (nutree_ai)

## Context
- Worktree: `/Users/alexnguyen/Desktop/Nut/nutree/worktrees/nutree_ai-nm611-food-search`
- Backend endpoints (all send `Accept-Language`):
  - `GET /v1/foods/autocomplete?q=&limit=` (limit 1–20, read-only, never adopts provider rows)
  - `GET /v1/foods/search?q=&limit=` (limit 1–50)
  - Response `{results, query, total}` plus `partial: true` when late sources were left out.
- Sheet: `lib/features/meal_creation/presentation/widgets/catalog_food_search_sheet.dart`
- Search state: `application/providers/catalog_food_search_provider.dart` (family on query, mode, language)
- Shortcuts: `application/providers/catalog_food_shortcuts_provider.dart` + `data/datasources/catalog_food_shortcuts_local_store.dart`
- Deadline helper: `lib/core/network/request_deadline.dart`

## Requirements
- Debounced typing; in-flight request cancelled when the query changes.
- Typing uses the read-only autocomplete path; submit / picking a term uses full search.
- Session cache (LRU) so going back to an answered query shows results instantly.
- Idle state shows recents + favorites from local storage (no network).
- Offline: clear error state with retry; local recents still usable.
- Short deadline for search (no long retries); latency analytics.

## Steps
1. Map current flow (screens, providers, repository, Dio interceptors).
2. Search provider: 300 ms debounce in the sheet; family provider per (query, mode, language); per-build `CancelToken` cancelled on dispose; session cache; partial page refetched once after 1.5 s.
3. Idle state: recent picks (device) + foods from recent meals; favorites from favorite meals, falling back to the copy saved on the device.
4. Offline + error state: banner with retry over the user's foods; timeout has its own message.
5. Analytics: started/completed/failed carry `source: catalog`, `search_mode`, `duration_ms`, `cache_hit`, `partial`.
6. build_runner, `flutter analyze`, `flutter test`, guard scripts.

## Decisions and deviations
- **No prefix reuse in the session cache.** Exact repeats (backspacing to an answered query) are instant; refining keeps the earlier results on screen until the new answer arrives. Filtering a shorter query's page could show wrong rows, and AC5 asks only for repeats.
- Cache: 40 entries, 15 min, key = language + mode + normalized query (trim, lowercase, single spaces; diacritics kept). An autocomplete lookup may reuse a stored full-search page. Only full, non-empty pages are stored.
- **Deadlines** cover the whole call including interceptor retries: autocomplete 6 s, search 12 s. A deadline surfaces as `NetworkException` timeout; a caller cancel that comes first stays a Dio cancel (never reported as a timeout or a failure).
- **No automatic retry** on the search and shortcut providers: the error and retry button show at once.
- Loading indicator appears only after 300 ms (`DelayedReveal`); previous results stay visible meanwhile.

## Acceptance criteria mapping
| AC | How | Proof |
|----|-----|-------|
| 1 baseline | Render log latencies | debugger report (p50 ≈5–6 s, p95 ≈9.5–12.8 s) |
| 2 debounce, no stale replace | 300 ms debounce; family provider per query; cancel on leave | sheet test "an older answer never replaces results for a newer query" |
| 3 p95 targets | backend local-first path + budgets; mobile cache | backend tests; real p95 needs prod measurement after deploy |
| 4 recents/favorites instantly | device store + loaded meal lists, no request | sheet "shows the user's foods at once, without a request"; shortcuts "answer at once from the device" |
| 5 repeats instant | session cache | provider "a query answered earlier shows at once"; sheet "going back to an answered query" |
| 6 loader after 300 ms, results stay | `DelayedReveal` + previous value kept | sheet "earlier results stay, and progress shows after a moment" |
| 7 no relevance regression | backend golden set en/vi | backend integration tests |
| 8 offline message + retry | error banner, shortcuts stay | sheet "offline shows a message and retry…", "a timeout says so" |
| 9 verified | analyze, tests, guards, review | final report |

## Todo
- [x] 1 map flow
- [x] 2 controller
- [x] 3 idle state
- [x] 4 offline state
- [x] 5 analytics
- [x] 6 checks

## Risks
- Design-system cross-layer guard is over its limit on the base branch already (167 vs 166); this branch adds none.
- AC3 end-to-end p95 can only be confirmed with production traffic.
