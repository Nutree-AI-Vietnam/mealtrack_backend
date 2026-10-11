# Parse-text: engineering handoff

Date: 2026-10-12. Scope: implementation result and remaining release gates. Local implementation and checks are complete; nothing in this document establishes deployment or authenticated staging verification.

## 1. Outcome and current evidence

For `10g granula`, infer granola, preserve the explicit 10 g, estimate nutrition, validate the complete response, and display named cards once. No new glossary entries for typos.

Backend and Flutter working trees contain the implementation. Preserve unrelated work and inspect both diffs before merging.

Measured with actual `gpt-6-luna` through the local HTTP route and configured provider composition, synthetic inputs, authentication overridden, pure-AI mode, and an isolated local Redis:

- Initial four-case batch: all HTTP requests succeeded; `10g granula` required one correction. The correction path took 5.735 s; single-generation examples took 2.525–4.087 s.
- Duplicate `10g granula` check: cold correction path 6.065 s; identical warm cache hit 2 ms with zero AI generations or HTTP attempts.
- Failure example: 10 g granola with P=1.1 g, C=6.4 g, F=3.5 g. Their sum is 11 g: reject, not silently rescale.
- Backend focused/compatibility tests: 270 passed, 5 warnings. Flutter parser/provider/screen tests: 34 passed. Focused Flutter analyzer: no issues across 8 targets. iOS simulator debug build succeeded.

The cold Luna 6 measurements used synthetic input, overridden auth, forced model configuration, and local Redis; they do not prove production latency, nutritional accuracy across foods, authenticated staging behavior, or reference-mode quality. No deployment has been performed.

## 2. Request flow and invariants

```text
POST /v1/meals/parse-text
  -> auth / existing quota / sanitize / refinement validation
  -> initial-request response cache, bounded read
  -> one AI structured response: identity + amounts + macros + all micros
  -> schema and nutrition validation
       invalid -> one correction using previous response + errors
       still invalid -> existing typed error, preserve mobile draft
  -> optional existing reference resolution, bounded across the whole request
       valid matching reference -> use reference portion
       unavailable / wrong match / invalid reference -> valid AI estimate
  -> validate final portions / derive backend calories / attach snapshots
  -> cache complete initial response, bounded write
  -> Flutter applies the complete meal once
```

- Normal path has one AI generation. Do name inference and nutrition estimation in that generation.
- Keep the public request/response shape and existing pure-AI/reference configuration.
- Every accepted AI row produces one final row in order, or the request fails. Never silently drop a row.
- Initial parse and refinement are distinct cohorts. Refinement receives the current meal and returns the complete updated list.
- Preserve explicit quantities, cooked/raw state, names, provenance, serving units and nutrition basis.
- Calories remain backend-owned: `P*4 + (C-fiber)*4 + fiber*2 + F*9`. Respect existing label/override exceptions in the calorie service.
- Keep ten micros: A, C, E, calcium, iron, magnesium, potassium, sodium, saturated fat, added sugar. Unknown is null; zero means an estimated/known zero.

## 3. Backend implementation boundaries

Paths below are relative to `/Users/alexnguyen/Desktop/Nut/mealtrack_backend`.

| Responsibility | Existing file / seam | Required work |
|---|---|---|
| Identity and estimation instructions | `src/domain/services/prompts/system_prompts.py` | Infer obvious spelling/phonetic errors in context; keep a specific uncertain term rather than generic Ingredient. Estimate one coherent per-100-g profile and scale once to the portion. Preserve raw/cooked distinctions. No routine second AI call for names. |
| Schema | `src/domain/model/ai/nutrition_contracts.py` | Reuse `MealTextNutritionResponse`; preserve 20-item limit and all micros. Keep output token budget sufficient for the maximum valid response; avoid truncating small-token limits as a speed optimization. |
| Physical validation | `src/domain/services/ai_output_validation_service.py` | Validate before custom/reference mapping and again after optional resolution. Keep these checks parse-specific. |
| Orchestration/correction | `src/app/handlers/command_handlers/parse_meal_text_handler.py` | Complete bounded correction; remove raw invalid-estimate bypasses and silent row skipping; share the request deadline across optional work. |
| Custom estimate | `src/app/services/parse_text_custom_estimate.py` | Derive normalized density/snapshot from a valid portion. Use physical validation rather than a list of known dense-food names. Keep legacy resolver-specific guards where separately needed. |
| Cache | `src/app/services/parse_text_response_cache.py` | Validate full DTO, calories, density, snapshot micros and provenance on read. A malformed hit becomes a miss. |
| Wiring | `src/api/dependencies/event_bus.py` | Inject real cache service, model namespace and deadlines into the registered handler. Verify through the actual route, not only direct handler construction. |
| Provider errors | `src/infra/services/ai/providers/openai_provider.py`, `src/infra/services/ai/ai_model_manager.py` | Route parse schema/JSON failures to the one correction. Distinguish malformed output from transport failure. Account for physical SDK retry/fallback requests. Preserve other AI purposes. |
| Settings | `src/infra/config/settings.py` | Verify effective `OPENAI_PARSE_TEXT_MODEL`; the source default currently says `gpt-5.6-luna`, while real checks explicitly selected `gpt-6-luna`. Do not assume the deployed model matches the test model. |

### Physical checks

Apply after schema validation; values must be finite and nonnegative:

```text
quantity > 0
explicit g/kg/oz/lb -> canonical grams from stated quantity
count/volume -> edible grams estimated by the model, not always 100 g
protein + carbs + fat <= edible grams + 0.15 g rounding allowance
fiber <= carbs + 0.15 g
sugar <= carbs + 0.15 g
saturated_fat <= fat + 0.15 g
added_sugar <= sugar + 0.15 g
```

Fiber and sugar are subsets of carbs, not extra mass. Saturated fat and added sugar are also subsets. Preserve micros' existing units. These checks catch impossible output; they do not prove the food profile is correct.

### Correction: known unfinished issue

`_generate_parse_text_payload()` currently rebuilds a retry system prompt from field errors but passes the same user prompt; it does not include the previous generated response. The real model repeated the same invalid small-portion estimate.

Implementation sketch:

```python
base_user_prompt = prompt
previous_payload = None
for attempt in range(2):
    candidate = await generate_with_remaining_deadline(...)
    try:
        validated = validate_schema(candidate)
        validate_nutrition(validated)
        return validated
    except AIOutputValidationError as error:
        if attempt == 1:
            raise existing_typed_error(error)
        # Keep trusted rules in system instructions; candidate JSON is data.
        prompt = correction_user_prompt(
            original_request=base_user_prompt,
            previous_response=bounded_candidate_json(candidate),
            field_errors=error.validation_details,
        )
```

Correction instructions: preserve explicit amounts and unaffected rows; recompute a coherent food profile for the erroneous row; return the complete JSON response. Do not instruct the model to merely multiply all macros until validation passes. Bound prior-response size using the existing item/input limits; do not send/log arbitrary exception payloads. If provider-side parsing fails before a candidate is available, use schema/JSON error context only.

## 4. Speed budgets and caching

Release targets, not measured guarantees: cold initial parses p50 <= 3 s and p95 <= 6 s on the agreed corpus; measure refinements and correction requests separately, plus an overall mix. Accuracy must not regress to meet the latency gate.

Start the following as a staging candidate, then validate before changing release configuration:

| Stage | Candidate budget | Rule |
|---|---:|---|
| Entire server request | 10 s | One monotonic deadline beginning at entry; includes application correction and underlying provider attempts. |
| Cache read / write | 50 ms each | Fail open to generation/response; a cache outage cannot fail a valid parse. |
| Optional reference work | <= 1 s after AI, within total budget | Local batch lookup first; bounded concurrent remote work using existing limits. Preserve a valid AI estimate if unavailable. |
| AI correction | Remaining request budget | No new full timeout for the second generation; skip correction if insufficient budget and return typed error. |

The source default was 25 s and optional reference work could add approximately 3 s afterward. The implementation candidate now sets a 10 s handler request deadline and a 1 s optional reference budget, both configurable. A hard deadline bounds waiting by returning an error; it does not make the provider faster. Reject this timeout change if real-model/staging tests show unacceptable timeout or correction-failure rates.

Do not stack independent SDK retry, model fallback, application correction and client POST retry without an aggregate budget. Preserve fast recovery when affordable; log physical attempts. Client network retries must not duplicate parse requests automatically.

Cache key: version + effective model/route + prompt version/hash + language + nutrition policy + reference mode + normalized input + any result-affecting request context. Share only context-independent results; include user scope or disable shared caching if personalization is introduced. Refinements bypass response caching.

Cache the whole DTO: names, amount/unit, allowed units, identity, origin/provider IDs, canonical name, per-100-g values, source snapshot, all represented micros, emoji and totals. Check density-to-portion consistency and provenance, not just finite numbers. Respect public calorie exceptions through the existing service. Cold/warm responses must be equivalent. Version-bump on incompatible normalization changes.

Existing guest quota/auth checks must still run on cache hits. Do not move them behind the cache.

## 5. Flutter implementation boundaries

Root: `/Users/alexnguyen/Desktop/Nut/nutree/nutree_ai`.

- `lib/features/meal_creation/application/providers/ai_meal_parser_provider.dart`: capture immutable submitted items; assign request generation; ignore stale responses after clear/new submission/dispose; guard repeated submit.
- `lib/features/meal_creation/application/mappers/parse_response_to_cart_mapper.dart`: reconcile against the submitted snapshot. Reserve unchanged rows first; match changed rows by stable source identity or unique canonical identity. Preserve unchanged IDs, units, snapshots and manual overrides.
- `lib/features/meal_creation/presentation/screens/create_meal_screen.dart`: apply successful refinement with `replaceAllItems`, not append; keep the old draft on errors. Reject results if draft changed through another path while parsing rather than guessing a destructive merge.
- `lib/features/meal_creation/presentation/widgets/ai_prompt_section.dart` and `create_meal_composer_pane.dart`: lock duplicate submit and incompatible edit/delete/save while parsing, including keyboard submit.

Same-identity rows with manual `nutritionOverride`: preserve the absolute user-entered nutrition document; an AI quantity edit must not silently clear or scale it. If several duplicate override rows cannot be matched uniquely, reject applying the refinement and preserve the draft. This ambiguity remains a review/implementation gate in the draft mapper.

Display a loading indicator until the valid response arrives, then render named cards immediately. Do not add an arbitrary delay, expose generic placeholder food rows, or make a separate name-resolution AI request. Preserve unchanged rows' existing micronutrient snapshots where appropriate; the current refinement request does not transmit previous micros, so the model cannot preserve them exactly by itself.

## 6. Accuracy and reliability acceptance

### Unit/contract cases

- Invalid 10 g / 11 g macros -> exactly one correction; corrected valid row survives; repeated invalid -> typed 422, no partial meal.
- Unparseable JSON/provider schema failure -> bounded correction; transport deadline -> existing retryable error.
- Dense unknown food, oil, tiny portions and count units -> valid estimates are not rejected by a food-name allowlist.
- Lookup exception, timeout, wrong raw/cooked match or impossible reference -> valid AI fallback keeps the row.
- Missing/null/zero micros retain their distinct meaning; subset checks work on cached snapshots too.
- Cache round trip preserves all fields; stale version/malformed totals/calories/density/provenance -> miss; Redis stall is bounded.
- Refinement: add/remove/quantity edit, unchanged identity, manual overrides, duplicate ambiguity, clear/dispose/stale response, repeated submit and disabled save.
- Confirm the serialized meal-save payload retains the same override/provenance contract as the displayed cart.

### Real-model evaluation

Build a human-reviewed corpus of at least 30 descriptions: EN/VI, spelling/phonetic errors, mixed meals, raw/cooked states, small/dense portions, serving counts, unknown specific foods, and refinements. Include the exact original yogurt + granula flow. Run at least three independent cold samples per description; identify cache hits rather than treating them as model repetitions.

For each case, record expected identity/state and portion; use label or trustworthy food-reference data for foods with known profiles. For unbranded estimates, define an acceptable reviewed interval before testing instead of asserting one exact invented number. Brand-specific precision requires label/brand information; ask for it when it changes the result materially.

Score BOTH raw model output and final API output. Report identity, portion and item completeness; macro/calorie errors; each micronutrient's error and unknown handling; correction/failure/timeout rates; latency and physical provider attempts. A bounded or physically valid output is not enough to pass accuracy.

Hard gates: all regression cases retain correct identities and explicit amounts; no silent missing items; no impossible portions; correct calorie derivation; no override loss; no systematic inflation. Compare nutrition quality against the reviewed baseline and approve acceptable error ranges per food class. Keep failed cases in the report; do not tune success rates by removing them.

### Staging/device gate

Verify the deployed revision, effective model and parse mode. Exercise actual authenticated API routes, initial/refinement/cache cohorts, quota behavior and the mobile save flow. Measure HTTP latency separately from submit-to-named-cards latency on a simulator/device. Existing local provider checks used an auth override and cannot satisfy this gate.

Use privacy-safe instrumentation: request type/language/item-count bucket, resolved model/route, cache hit, AI generations/physical attempts, validation outcome, AI/reference/cache/total duration, and status. Do not log meal text, food names, user IDs, tokens or provider payloads.

## 7. Ownership and execution order

1. Backend owner: inspect existing diff, finish correction and complete-row behavior, validate final estimates and cache fidelity.
2. Flutter owner: finish ambiguity/stale-result behavior and confirm saved metadata; can work independently of the unchanged API contract.
3. QA owner: run focused backend/Flutter regressions, then reviewed real-model corpus and staging/device checks. Real provider nutrition scoring must not be replaced by mocks.
4. Reviewer: inspect provider retry/deadline aggregation, cache auth/quota placement, unit/micro scaling and save payload consistency.
5. Release owner: compare accuracy + latency gates; deploy only passing candidate, observe the same metrics and retain an explicit rollback to previous configuration/revision.

Local implementation gates are complete. Before release, run the reviewed food/portion corpus and authenticated staging API/mobile save-read flow, then verify deployed model/configuration and production latency/cache behavior. Do not infer those gates from the small local Luna 6 sample.
