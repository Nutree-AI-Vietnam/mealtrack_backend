# NM-611: food search latency and missing local foods (investigation)

- Date: 2026-10-07. Investigation only; no code, config or data changed.
- Repos: `mealtrack_backend` (backend), `nutree/nutree_ai` (mobile).
- Backend line refs were checked against `origin/main` abd2d200. Every cited file is identical on the current branch `fix/weekly-planner-two-slot-and-simple-meals`. Mobile refs point at the current working tree.
- Latency source: Render logs, lines `[RES-…] … elapsed=` (`src/api/middleware/request_logger.py:99-105`). These are server-side only and exclude network time. Sample: 100 `GET /v1/foods/search` lines per day.
- Short names: `H` = `src/app/handlers/query_handlers/search_foods_query_handler.py`, `R` = `src/infra/repositories/food_reference_repository_async.py`, `IR` = `src/infra/repositories/food_reference_integrity_repository.py`.

## 1. Executive summary

- **FatSecret is not the main cause of slowness.** A search with nothing else running takes 1.1–1.7s. The p95 of 10–13s happens when searches overlap and wait for each other. Causes:
  - Up to 4 LLM calls run one after another in each Vietnamese search.
  - DB writes run inside the search request and hold 2 global row locks until commit.
  - The cache invalidates itself on every write.
  - The local query re-runs in a loop.
  - FatSecret is called almost every time (30s timeout) and adds to the total.
- **Local foods go missing because of 4 bugs that stack:**
  1. #496 (commit 143c2c83, 2026-08-16) forces non-English local search to region `US`. No user, English or Vietnamese, can reach the VN-region seed rows.
  2. Local search matches the LLM's English translation, not what the user typed. The normalizer also deletes every non-ASCII letter ('phở bò' becomes 'ph b'), and `name_vi` has no index.
  3. Each Vietnamese search saves serving labels. A DB trigger then sets that food to `integrity_status='unknown'`, and local search hides it. The local catalog shrinks as people use it.
  4. The integrity gate is probably not activated: nothing in the code calls `activate_policy()` (confirm with the first query in §8). The SQL filter and the Python filter disagree, so hidden foods never come back.
- **Correction to what I said earlier.** A food hidden by bug 3 does **not** come back on its next FatSecret-backed search. That only happens when the gate is activated. In pending mode, which production is probably in, it stays hidden for good.
- **The two problems feed each other.** Fewer local hits mean more FatSecret and LLM work per search, which makes search slower.

## 2. AC1 baseline (server-side, `GET /v1/foods/search`)

| Day | p50 | p95 | Max |
|---|---|---|---|
| 10-07 | ≈5.1s | ≈9.5s | 12.83s |
| 10-06 | ≈6.1s | ≈12.8s | 17.54s |
| 10-03 | ≈5.3s | ≈10.2s | 10.8s |

- 5/300 requests took ≤1s; 40/300 took <2s. About 1% were cache hits.
- A search with nothing else running takes 1.1–1.7s. Overlapping bursts take 4–17s and finish about 1s apart, which looks like requests waiting on a shared resource. The global row locks fit that pattern but are not confirmed; see the lock queries in §8.
  - Example: 10-03 09:13:45–09:14:11, 17 responses at 3.8–10.5s each.
- 10-07 07:17: one client ran 3 Vietnamese searches. They made 6 OpenAI calls and 2 FatSecret calls and took 1.668s, 3.814s and 6.342s.
- Whole service: 66–103 responses per day with status 499 (client gave up). 10-06 also had 356 responses with status 500; I did not trace their source or tie them to search.
- Caveats:
  - These are samples, not full days.
  - Server-side only, with no per-stage breakdown: `food_search.operation.latency_ms` is in Sentry, which is not authorized.
  - No client end-to-end numbers.

## 3. Request pipeline: Vietnamese, `/v1/foods/search` (non-autocomplete)

| # | Step | Where | Cost / problem |
|---|---|---|---|
| 1 | Integrity context (own UoW) | `H:66`, `src/api/dependencies/event_bus.py:332-339` | Extra DB round-trip on every request |
| 2 | Redis read | `H:70-126`, `src/domain/cache/cache_keys.py:139-156` | Key = language + query + policy + **global** generation |
| 3 | LLM: translate query vi→en | `H:128`, `H:381-392` | LLM call 1; no translation cache |
| 4 | Local search | `H:129-133` → `R:367` | Region forced to `US` (`H:132`); ASCII-only normalizer; OFFSET loop `R:433-450` |
| 5 | FatSecret | `H:135-144`, `H:339-358`, `src/infra/adapters/fat_secret_service.py:62` | Runs whenever local returns fewer than `limit`; region US, language en; 30s timeout |
| 6 | LLM: localize names | `H:168` | LLM calls 2–3 (batch + repair) |
| 7 | Adopt provider hits (writes) | `H:173` → `H:223-…`, single UoW `H:238` | New rows run `materialize_reference` → generation +1, global locks |
| 8 | LLM: serving labels | `H:174-180` → `src/app/services/serving_label_localizer.py:65-107` | LLM call 4 |
| 9 | Save labels (writes) | `serving_label_localizer.py:154-195` → `src/infra/repositories/food_reference_locale.py:153-176` | Trigger on each changed row: food set to 'unknown', generation +1, 2 global locks, recipe projection jobs |
| 10 | Cache write | `H:185-186` | Written under the generation read in step 1, which steps 7 and 9 already bumped, so the entry is stale before anyone reads it |

- Each LLM call has an 8s timeout with `max_retries=1`, plus a repair batch (`src/infra/adapters/openai_translation_adapter.py`). There is no cache (`src/domain/services/translation/text_translation_service.py`).
- `/v1/foods/autocomplete` (`src/api/routes/v1/foods.py:63-78`) skips steps 7 and 9, so it does no writes.
- English searches skip steps 3, 6, 8 and 9. Adoption is at `H:154`. The cache is written only when FatSecret returned hits (`H:160-163`).

## 4. Root causes: slowness

- **S1. Sequential LLM calls.** A Vietnamese search makes up to 4 calls (query, names ×≤2, labels). Each can take up to 8s plus a retry and a repair. Nothing is cached, and none of the calls run in parallel.
- **S2. FatSecret runs almost every time** (`H:135-138`): local results rarely fill `limit`, and S3/L1–L3 make that worse. There is no time budget and the timeout is 30s.
- **S3. The cache invalidates itself.** The generation is read at `H:66` and bumped by writes in the same request (`IR:236`, plus the serving trigger), and the cache is then written under the old generation. Because the generation is global, any adoption or label write anywhere invalidates everyone's cache. This fits the ~1% hit rate.
- **S4. Writes in the request hold two global row locks until commit:**
  - `food_reference_integrity_control` id=1, taken by the serving trigger and by materialize.
  - `catalog_publication_version` id=1, taken by a statement trigger on every write to any `food_reference*` table (`src/infra/database/catalog_publication_triggers.py:22-27,127`).
  - Concurrent searches therefore run one at a time, and all adoption in a request shares one UoW.
  - For foods used by recipes, a label write also marks recipe projections dirty and inserts `catalog_preparation_jobs` (`catalog_publication_triggers.py:64-67,98-119,166-174`).
- **S5. Cache key gaps.**
  - The key has no `limit` (`H:70-74`), so a cached `limit=5` result is returned truncated to a `limit=10` request (`H:92`).
  - The key has no `autocomplete` flag, so autocomplete results without adopted `food_reference_id`s can be served to `/search`. The downstream impact is unverified.
  - English results that come only from local search are never cached.
- **S6. Integrity-context UoW on every request** (`event_bus.py:332-339`), even on a cache hit.
- **S7. Mobile client** (`nutree_ai`):
  - The catalog sheet runs the full write path on every debounced query: `catalog_food_search_sheet.dart:95` (350ms debounce) → `:205` `mealEditFoodSearchProvider` → `food_search_providers.dart:114` `searchFoods` → `/foods/search`.
  - No `CancelToken` on food search (`api_service.dart:436-443`), so stale keystroke requests keep running.
  - `persistentConnection: isLoopback` (`dio_factory.dart:38,82`) means no keep-alive in production, so every search pays TCP+TLS setup.
  - Receive timeout is 60s (`app_constants.dart:27`), and `retry_on_503_interceptor.dart` retries 503s.
- **S8. Local search loop.** The SQL gate admits verified rows of any status (pending mode). The Python filter then drops anything not 'valid' (`R:944`). The OFFSET loop (`R:433-450`) re-runs the query until it has collected `limit` survivors. The query probably does a sequential scan: `name_vi ILIKE` has no index, so the planner can't combine the OR branches with a BitmapOr. Confirm with EXPLAIN (§8).

## 5. Root causes: missing local foods

- **L1. Region regression from #496.**
  - `H:132` passes `region="US"` for every non-English request.
  - `R:420` filters `region IN (region, 'global')`.
  - Seeds are region `VN`: the scrapers set it, and `upsert_seed` defaults to it (`R:623`).
  - `_region_for_language` (`H:540-543`, vi→VN) is bypassed, and English resolves to `US`.
  - Result: no search path can reach VN-region rows.
- **L2. Vietnamese text can't match.**
  - a. The local query uses the LLM's English translation (`H:128-131`), not the user's text.
  - b. `normalize_food_name` (`src/domain/services/meal_suggestion/ingredient_name_normalizer.py:61`) keeps only `[a-z0-9\s]`:

    | Input | Output |
    |---|---|
    | 'thịt bò tái' | 'th t b t i' |
    | 'cơm chiên trứng' | 'c m chi n tr ng' |
    | 'củ đậu' | 'c u' |
    | 'bánh mì' | 'b nh m' |

    Even the original text would be mangled and match noise.
  - c. `name_vi` is in the match clause (`R:389`) but has no trigram index. Ranking uses similarity on `name` / `name_normalized` (`R:401,414`).
- **L3. A trigger loop hides foods.**
  1. Saving labels also covers local rows (`serving_label_localizer.py:154-195` handles any item with a `food_reference_id`, which is set for local rows at `H:468`).
  2. Each new `name_vi` value fires `trg_food_reference_serving_integrity`. It is unconditional (`migrations/versions/20260815000004_add_food_reference_integrity_state.py:231-274`) and sets `integrity_status='unknown'` with reason `serving_changed`.
  3. Local search then drops the row (`R:944`).
  - Seeded rows have no re-materialize path and never recover.
  - FatSecret-adopted rows in pending mode: `get_by_source_identities` (`R:161-186`, SQL gate only) still returns them, so `H:262-264` treats them as existing and skips re-materializing. They stay hidden permanently.
  - Activated mode: they are re-adopted and come back valid. Labels that the LLM rewords on a later search would flip them back to unknown.
- **L4. OFF VN seeds are unverified** (`scripts/import_food_seeds.py:32,237`: only `nin_vn` and `vn_fct_pdf` are trusted). The `is_verified` gate excludes them.
- **L5. Rows created before the integrity system may still be 'unknown'** (the column default). `R:944` hides them in either mode. Needs a DB check.
- **L6. FatSecret is queried with region US and language en**, using the translated query, so Vietnamese coverage is weak. New data sources are out of scope; noted only.
- **L7. The integrity gate is probably not activated** (code evidence only; confirm in prod).
  - `activate_policy()` (`IR:295`, sets `activation_run_id` at `IR:326`) has no callers in `src/`, `scripts/` or `migrations/`.
  - The control-row seed leaves `activation_run_id` NULL (migration `20260815000004…:69-78`).
  - So the SQL gate is in pending mode (`IR:90-130`): it keeps the verified-only rule until activation. The Python filter (`R:941-954`) already enforces the activated rule. That disagreement causes S8 and makes L3 permanent.

## 6. Fixes (proposed, not implemented)

### P0: local foods (backend)

1. **Region.** Vietnamese searches use `VN` + `global`, which was the behaviour before #496. Whether English-UI users in Vietnam also get `VN` is a product decision (see §10).
2. **Vietnamese matching.**
   - Search with both the original text (against `name_vi` and Vietnamese `name`) and the English canonical query (against `name`).
   - Add a Unicode-aware folding function: NFD, strip combining marks, explicit `đ→d`, lowercase. 'phở bò' becomes 'pho bo'.
   - Store a folded column with a GIN trigram index.
   - Do **not** change `normalize_food_name` in place. Meal-suggestion matching shares it, and the stored `name_normalized` values would need re-keying.
3. **Narrow the serving trigger.** Fire only on nutrition-relevant serving columns, never `name_vi`, using `UPDATE OF <cols>` or a `WHEN (OLD.x IS DISTINCT FROM NEW.x)` UPDATE trigger plus a separate INSERT/DELETE trigger.
   - Apply the same idea to the statement lock trigger on `food_reference_serving_sizes` (`UPDATE OF` works for statement triggers).
   - Treat a `name_vi`-only change as translation-only (or no change) in `invalidate_catalog_projection` (`catalog_publication_triggers.py:64-67`). First verify whether projections read serving `name_vi`.
   - Generate the migration with the CLI and include a real `downgrade()`.
4. **Repair data.** After the trigger fix, re-materialize rows with `integrity_reason='serving_changed'` (and old 'unknown' rows, L5). If the trigger isn't fixed first, they flip back.
5. **Gate consistency (a decision).** Either:
   - a. Backfill and then call `activate_policy()`, or
   - b. Make the Python status filter follow the SQL gate mode and have adoption re-materialize existing non-valid rows.

   Option (b) restores hidden foods immediately and removes the S8 loop. `integrity_policy.evaluate` (`R:946-954`) still rejects bad macros.
6. **OFF VN seeds.** Decide the verification policy, for example: trust them when they pass `integrity_policy.evaluate`.

### P1: backend latency

1. **Move adoption and label saving off the request path** (post-response task or the job table). This removes S4 and S3 from user latency.
2. **Translation cache** in Redis plus an in-process LRU, keyed by (source, target, folded text): queries, names and labels. Skip the LLM when `name_vi` already exists.
3. **Fix cache correctness.**
   - Without in-request writes, the generation stays stable within a request.
   - Bump the generation only on public-visibility changes, or use a short TTL instead.
   - Add `limit` and `autocomplete` to the key.
   - Cache English local-only results.
4. **FatSecret.**
   - Skip it when local hits are good enough (threshold checked against the AC6 golden set).
   - Otherwise give it a ~800ms budget and a 2–3s timeout, then return local-only results.
5. **Run in parallel.** Run the query translation alongside local search on the original text (after P0.2).
6. **Integrity context.** Use an in-process TTL of a few seconds instead of a UoW per request.
7. **Status filter in SQL**, which removes the OFFSET loop. EXPLAIN to confirm the trigram indexes are used.

### P2: mobile (`nutree_ai`)

1. **Use the read-only path for typing.**
   - Typing in the catalog sheet should call `/foods/autocomplete`, or a new read-only backend mode.
   - Only selecting an item should resolve or adopt it.
   - Check whether selection currently depends on `food_reference_id` from search results.
2. **Network settings.**
   - Add `CancelToken` to the food search methods and cancel it on provider dispose or on a new keystroke.
   - Enable keep-alive; first check why `persistentConnection: isLoopback` was added (git blame).
   - Give search a ~5s timeout and no 503 retry.
3. **AC4.** Show recents and favorites in the sheet's idle state from Drift. The sources already exist: `meal_relog_providers.dart:56-142,166-172`.
4. **AC5.** Session LRU of results keyed by normalized query, and filter previous results by prefix while the user types. Today there is only a 5-min `keepAlive` per query (`food_search_providers.dart:110,119`).
5. **AC7.** Offline state: show recents, favorites and cached results, plus a retry.

### P3: measurement

- Authorize Sentry to get the per-stage breakdown of `food_search.operation.latency_ms` (`H:572-592`).
- Add per-stage timing (translate, local, provider, adopt, labels) to logs.
- Send a client latency event (debounce fire → results rendered) via PostHog, for AC3's end-to-end target.

## 7. AC status

| AC | Status | Note |
|---|---|---|
| AC1 baseline | Partial | Server-side done (§2); no client e2e |
| AC2 debounce | Met | 350ms; no cancellation; each query runs the write path |
| AC3 p95 ≤300ms BE / ≤1s e2e | Far off | p95 9.5–12.8s |
| AC4 recents/favorites local | Missing | Drift data exists, not used in the sheet |
| AC5 session cache | Partial | 5-min keepAlive per query |
| AC6 no relevance regression | Not started | Needs top-5 en/vi golden set before P0/P1.4 |
| AC7 offline | Missing | — |

## 8. Pending DB checks (read-only, prod)

The Neon MCP account has no org access, so a human must run these.

```sql
SELECT active_policy_version, catalog_integrity_generation, activation_run_id
FROM food_reference_integrity_control WHERE id = 1;

SELECT integrity_status, integrity_reason, region, is_verified, source_namespace,
       count(*), count(name_vi)
FROM food_reference GROUP BY 1,2,3,4,5 ORDER BY 6 DESC;

SELECT date_trunc('day', created_at) AS day, reason_code, count(*)
FROM food_reference_integrity_events GROUP BY 1,2 ORDER BY 1 DESC LIMIT 60;

SELECT indexname, indexdef FROM pg_indexes WHERE tablename LIKE 'food_reference%';

SELECT count(*) FROM food_reference;
```

- `EXPLAIN (ANALYZE, BUFFERS)` the local-search SQL. Capture it with SQLAlchemy echo or `pg_stat_statements` if enabled.
- During a burst:

  ```sql
  SELECT pid, wait_event_type, wait_event, state, now() - query_start AS age, left(query, 80)
  FROM pg_stat_activity WHERE datname = current_database() AND state <> 'idle';

  SELECT locktype, relation::regclass, mode, granted, pid FROM pg_locks WHERE NOT granted;
  ```

- Search Render logs for `deadlock detected` / `40P01` around the 10-06 status-500 spike.

## 9. Scope note

The ticket marks ranking/relevance changes and new data sources out of scope. The region fix, `name_vi` matching and folding, and the FatSecret skip threshold all touch what users see. Argue them as regression and bug fixes: region was `VN` for Vietnamese before #496, and the trigger loop hides data that already exists. Get product-owner sign-off, and gate them on the AC6 golden set.

## 10. Unresolved questions

1. Is the integrity gate activated in prod (`activation_run_id`)? Is pending mode intended?
2. Did the VN seed import run in prod? How many VN, verified, 'valid' rows exist?
3. How many rows are 'unknown' / `serving_changed` now? How fast is that growing?
4. Should English-UI users in Vietnam see VN foods (region from user country instead of UI language)?
5. Is the `catalog_food_search_enabled` flag on in prod? Is the `?entry=search` route bypassing the flag intended?
6. What are the AC3 targets: ≤300ms for all queries, or for cache and local hits only?
7. Is diacritic-insensitive Vietnamese search in scope (ticket open question)?
8. Is DB pool exhaustion part of the bursts? (Needs `pg_stat_activity` during a burst.)
9. What caused the 10-06 status-500 spike (356)?
10. Do recipe projections read serving `name_vi`? This determines how far P0.3 can narrow the catalog trigger.
