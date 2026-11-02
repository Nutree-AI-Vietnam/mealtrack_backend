# AI reliability, accuracy, and speed implementation plan

Status: Implementation and local verification complete; provider-backed evaluation and rollout pending.
Date: 2026-10-28
Scope: MealTrack backend, with a small coordinated Flutter contract/UI change where nutrition provenance must be visible.

## Goal and order

Prevent incomplete nutrition and missed allergen matches from becoming confident recommendations, then reduce duplicate external calls without weakening accuracy.

Deliver in this order: baseline → nutrition correctness → allergen matching → food identity and cache correctness → performance → regression evaluation and rollout. Keep each implementation change independently reviewable. The allergen work does not depend on the nutrition refactor and can be released separately.

## Evidence and scope corrections

| Finding | Evidence | Planning consequence |
| --- | --- | --- |
| Failed ingredient estimates become zeros; immediate AI errors can also populate the success cache | `nutrition_lookup_service.py`, `_ai_estimate`, `_resolve_uncached` | Model unresolved nutrition explicitly and stop caching failures as nutritional facts. |
| A positive partial lookup replaces the entire recipe's original macros | `chat_next_meal_candidates.py`, `_reconcile_one_meal` | Gate replacement on completeness, not positive calories. Apply the same rule before recipe scaling. |
| Coach allergy filtering uses literal substrings | `chat/policy.py`, `meal_conflicts_with_allergies` | Add category/ingredient mappings. Isolated reproduction missed dairy/yogurt, milk/whey, and shellfish/shrimp. |
| Planner already has allergen preference aliases | `weekly_meal_planner/allergen_constraint.py` | Reuse compatible normalization in a shared domain module; preserve the planner's catalog-code contract. |
| Per-100g cache values are reconstructed from rounded portions | `nutrition_lookup_service.py`, `_cache_result` | Cache source per-100g data before scaling or rounding. |
| Current name normalization removes raw, cooked, dried, and other meaningful descriptors | `meal_suggestion/ingredient_name_normalizer.py` | Establish an identity-safe key before deduplicating; assess persisted reference compatibility. |
| Recipe and ingredient lookups both use concurrent gathers | `chat_next_meal_candidates.py`, `_reconcile_lookup_macros`; `nutrition_lookup_service.py`, `calculate_meal_macros` | Deduplicate identical ingredients across a recipe batch and bound provider concurrency. |
| Ingredient-aware prompt truncates after six ingredients | `meal_analysis_strategy.py`, `IngredientAwareAnalysisStrategy` | Lower priority: an adapter and port exist, but no application caller was found during review. Verify reachability before treating it as a production incident. |

The initial review was static, with an isolated allergy-function reproduction. On 2026-10-29, the repository's Python 3.13.2 virtual environment became available and the backend gates ran successfully. The offline repeated-ingredient benchmark is recorded in `offline-benchmark-report.md`; production latency, model accuracy against reference meals, and rollout behavior still need provider-backed evaluation. Flutter/Dart are unavailable in this workspace, so mobile validation is limited to checking the seven ARB files as JSON and verifying the additive payload/model changes by inspection. Existing repository-wide Ruff and mypy failures remain outside these focused changes.

## Phase 0 — Baseline and reproducible regressions

1. Set up the repository's supported Python 3.13.2 test environment and documented dependencies.
2. Trace all callers of `calculate_meal_macros`, `scale_to_target`, and allergy filters, including chat, discovery recipes, and fallback cards. Verify mobile consumption of suggestion metadata.
3. Add regression cases for partial lookup failure, cached failures, allergy aliases, small-portion cache round trips, and raw/cooked identity collisions.
4. Establish repeatable cold-cache and warm-cache baselines for a three-recipe request with repeated and distinct ingredients. Include normal responses, provider timeout, malformed output, and rate limits.
5. Record completion rate, recipe-ready p50/p95, lookup-stage p50/p95, provider calls per request, token use, unresolved ingredient count, and cache hit rate. Separate text-ready, card-ready, and image-ready times.

Acceptance: regressions reproduce the existing behavior; benchmark inputs, cache conditions, model/configuration, and sample counts are recorded. Live-provider runs are a separate step and require available credentials; deterministic fake-provider tests must work without them.

## Phase 1 — Correct nutrition failures and completeness

Primary files:
- `src/domain/services/meal_suggestion/nutrition_lookup_service.py`
- `src/domain/services/meal_suggestion/recipe_attempt_builder.py`
- `src/domain/services/meal_suggestion/macro_validation_service.py`
- `src/app/services/chat_next_meal_candidates.py`

Implementation:
1. Introduce a typed resolution result distinguishing database-backed, AI-estimated, and unresolved ingredients. Keep source, failure reason, and original ingredient identity. Use explicit missing values for unresolved nutrition; legitimate zero-calorie ingredients remain valid resolved results.
2. Validate finite, nonnegative nutrient values and per-100g consistency before accepting provider/cache data. Missing required macros must not become zero by default.
3. Aggregate completeness and provenance with totals. A meal is complete only when every included ingredient resolves; estimated ingredients are identified separately from reference-backed ingredients.
4. Do not replace whole-meal macros with a partial sum. For chat, retain a validated full-meal estimate with an explicit estimated status when available; otherwise use an existing validated fallback or omit the incomplete card. Do not mix a partial lookup total with a full-meal estimate.
5. Reject incomplete nutrition before discovery recipe scaling, since scaling a partial sum can inflate ingredient portions. Audit saved-card/replay paths so the same provenance survives logging and reload.
6. Never write errors/timeouts/invalid output to the successful nutrition cache. Successful AI estimates retain provenance. Use a new cache payload version/namespace to avoid reading previously cached failure zeros; allow old keys to expire.
7. Add backward-compatible response metadata for nutrition status/source, and update Flutter coach models/cards to distinguish estimates. Older clients must still receive coherent complete numerical totals, never partial sums or zero placeholders. Calories remain backend-owned.

Acceptance tests:
- One known ingredient plus one failed lookup cannot produce an apparently complete replacement total or be scaled to a target.
- All lookups fail: no zero-calorie success response and no nutrition-success cache entry.
- A genuine zero-calorie reference still resolves normally.
- Provider errors, invalid JSON, NaN/infinite values, timeouts, and cancellation have explicit outcomes.
- A valid AI estimate remains usable and visibly estimated; provenance survives cache, serialization, replay, and save paths.
- Backend and Flutter contract tests cover both absent metadata on old responses and new metadata.

## Phase 2 — Shared allergen matching for coach recommendations

Primary files:
- `src/domain/services/chat/policy.py`
- `src/domain/services/weekly_meal_planner/allergen_constraint.py`
- `src/app/services/chat_next_meal_candidates.py`
- A shared domain allergen policy and reviewed mapping fixtures

Implementation:
1. Extract compatible preference normalization and alias handling from the planner; keep trusted catalog allergen codes authoritative for catalog recipes.
2. Add deterministic ingredient mappings for common categories and derivatives, including dairy/milk/whey/casein/yogurt and shellfish/shrimp/crab. Cover persisted preference codes and supported-language aliases with reviewed fixtures.
3. Use phrase/token-aware matching and explicit food identities to avoid substring errors, such as treating every plant-based milk as dairy. Keep milk allergy and lactose intolerance distinguishable.
4. Return match, no-known-conflict, or unknown with a reason. Unrecognized allergy preferences or insufficient compound-ingredient information must not silently pass as checked; withhold the affected suggestion and use a validated alternative when available.
5. Apply the same policy to direct AI recipes, discovery candidates, and fallback cards. Audit retrieved allergen tags and recommendation-bearing coach text for inconsistent category matching, without blocking ordinary educational mentions of allergens.

Acceptance tests:
- The three reproduced missed matches are blocked.
- Tree nuts, peanuts, soy, wheat/gluten, and supported locale aliases are covered.
- Negative cases cover word boundaries, plant-based alternatives, and informational text.
- Missing ingredients and unknown preferences take the explicit unknown path.
- Existing planner catalog filtering and dietary-preference behavior remain compatible.

## Phase 3 — Preserve food identity and cache precision

Primary files:
- `src/domain/services/meal_suggestion/nutrition_lookup_service.py`
- `src/domain/services/meal_suggestion/ingredient_name_normalizer.py`
- `src/domain/services/meal_suggestion/ingredient_nutrition_resolver.py`
- `src/infra/repositories/food_reference_repository_async.py`

Implementation:
1. Store validated source per-100g nutrients and provenance directly. Scale from those values for each request; round only at the established output boundary.
2. Define a versioned identity key preserving nutritionally relevant preparation/state and supported Unicode names. Prefer stable food reference IDs where available. Raw rice and cooked rice must not share a cache or in-flight lookup key.
3. Audit existing normalized database keys, importers, and cached reference rows before changing matching. Reject state-incompatible or ambiguous legacy matches; do not silently reuse them or perform an unreviewed global renormalization.
4. If persisted-key changes are needed, prepare a collision report and a separate reversible migration using the repository migration CLI. Keep ambiguous rows out of automatic merging.
5. Use the validated cache payload introduced in phase 1, with an identity-key version. No broad Redis scans or deletes on request paths.

Acceptance tests:
- A tiny first portion cannot alter the per-100g values used for a later large portion.
- Cold and warm cache results agree within established display rounding tolerance.
- Raw/cooked and dry/rehydrated fixtures remain distinct; equivalent cosmetic variants still match.
- Legacy ambiguous rows, source IDs, and unsupported names have explicit fallback behavior.

## Phase 4 — Reduce duplicate work and bound latency

Dependencies: phase 1 resolution results and phase 3 identity keys.

Implementation:
1. Resolve each unique food identity once per multi-recipe request, then scale its per-100g data independently for each ingredient row. Preserve original recipe ordering and quantities.
2. Share in-flight lookups across the three cards in a request. Avoid cross-request task sharing initially; that adds lifecycle and cancellation complexity. Retain the existing batch database lookup and consider batched Redis reads where the cache interface supports them.
3. Add configurable provider-specific concurrency bounds in the application/infrastructure orchestration layer, preserving the domain's no-outer-I/O rule. Count queued time against the request deadline.
4. Use one end-to-end lookup budget and pass the remaining budget to external stages. Align the current outer three-second AI timeout with the inner ten-second timeout; avoid nested retry budgets that outlive the parent request.
5. Test cancellation and cleanup explicitly. An expired request must not leave orphaned external calls, held permits, or stale in-flight entries.
6. After deduplication is measured, consider one structured AI request for unresolved ingredients, keyed by stable request item IDs. Enable batching only if evaluation confirms equal-or-better completeness and accuracy; a batch failure must remain an unresolved result, not zero nutrition.

Acceptance tests and performance gate:
- Repeated identical ingredients across three recipes cause one external resolution sequence per unique identity within that request.
- Raw/cooked variants never share a resolution.
- Slow fakes prove concurrency bounds, deadline accounting, cleanup, and independent portion scaling.
- Proposed staging target: at least 20% lower p95 lookup-stage latency on the recorded repeated-ingredient cold-cache workload, with no accuracy regression. This is a target to validate, not an estimated result.
- Also report end-to-end recipe-ready latency, unique-ingredient workloads, error rates, and token cost. Do not ship an optimization solely because it makes the synthetic repeated-input case faster.

## Phase 5 — Photo context, evaluation, and rollout

1. Verify whether ingredient-aware photo analysis is reachable from a supported user flow. Remove silent `ingredients[:6]` truncation and preserve all accepted ingredients; if an input bound is necessary, enforce it explicitly at the entry point. Keep this a small separate change and do not invent a new UI/API to activate a dormant path.
2. Extend the existing meal-analysis and text-nutrition evaluation harnesses rather than creating a competing framework. Add relevant cases for known portions, mixed meals, oils/sauces, cooking state, multilingual names, and difficult labels. Use reference totals to measure calorie/macro error; internal arithmetic consistency alone is not accuracy.
3. Run scoped regressions first, then the repository-required backend gates:
   - `pytest tests/unit --cov=src --cov-fail-under=65`
   - `pytest tests/architecture -q`
   - `ruff check src/` and `mypy src/`, with formatting checks on changed files
4. For Flutter changes, follow its AGENTS/conventions, regenerate affected models/localizations where required, and run `flutter analyze && flutter test`.
5. Release additive backend metadata before the Flutter consumer. Stage cache changes and performance settings separately; start external concurrency conservatively and tune with measurements.
6. Compare unresolved rates, recommendation availability, allergen rejections/unknowns, nutrition error, p95 latency, provider errors, and cost against phase 0. Use bounded metric labels; exclude user text, food names, and user IDs from metric dimensions.
7. Disable new batching/concurrency behavior if accuracy, availability, or latency regresses. Rollbacks must keep failure-aware nutrition handling and must not resume reading legacy poisoned cache entries; use a known-safe fallback if necessary.

## Deliverables and boundaries

- Baseline/regression evidence and reproducible benchmark inputs.
- Separate reviewable changes for nutrition correctness, allergen policy, identity/cache precision, performance, and photo-context cleanup.
- Coordinated additive API/mobile provenance contract where required.
- Before/after evaluation report with measured results and unresolved limitations.

Model replacement, provider migration, a new recommendation engine, and broad UI redesign are outside this plan. Image-generation changes and additional label-reading improvements can be scoped separately after baseline data identifies their impact. The implementation changes are in place; the remaining verification and rollout work is listed below.

## Implementation status

The code changes cover incomplete nutrition handling and scaling guards, complete-estimate fallback for chat cards, shared allergen aliases for recipe and knowledge filtering, state-preserving Unicode food identities, versioned per-100g caching, bounded batch lookups, additive Flutter nutrition provenance, and removal of the six-ingredient photo prompt truncation. No schema migration was needed because the new normalized identity is stored in the existing reference key and old keys are not globally rewritten.

Verification completed against the current `delivery` base: `pytest tests/unit --cov=src --cov-fail-under=65` passed 6,178 tests at 83.59% coverage; the focused post-change regression set passed 130 tests; `pytest tests/architecture -q` passed 23 tests; formatting, scoped Ruff core checks, and focused mypy passed. Full-source Ruff/mypy still report existing repository-wide findings, and Flutter/Dart validation could not run because those CLIs are unavailable. The deterministic fake-provider benchmark shows a 65.8% p95 lookup reduction for its repeated cold-cache fixture and similar timings for distinct inputs. It is not a production latency or accuracy result. Deployment and staged rollout have not been performed.
