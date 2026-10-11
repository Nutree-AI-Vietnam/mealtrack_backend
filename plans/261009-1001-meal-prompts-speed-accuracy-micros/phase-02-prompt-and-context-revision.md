---
phase: 2
title: "Prompt and Context Revision"
status: complete
effort: ""
---

# Phase 2: Prompt and Context Revision

## Overview

Priority P1; owner: prompt engineer; depends on Phase 1. Rewrite instructions for accurate food/portion/nutrient estimates and compact output while retaining every supported nutrient category. Context: [overview](./plan.md), [baseline](./phase-01-baseline-and-evaluation-contract.md).

## Requirements and Interfaces

- No public API, schema, enum, retry, model, image-detail, calorie-formula, reference-priority, or persistence change.
- Keep existing single-ingredient/list semantics, scan food guard, drinks-as-food behavior, canonical/localized identity fields, and `beverage_metadata: null`.
- Retain valid micronutrient estimates for identifiable foods using typical preparation-matched profiles. Unknown values may be null, but lack of certainty is not a reason to blanket-null ordinary foods.
- Apply the same nutrition semantics to scan and text; retain their separate output contracts. Edit only active prompt definitions and context strings, not unused legacy constants or recipe/food-label prompts.

## Related Code Files and Ownership

- Modify `src/domain/services/prompts/system_prompts.py`: active scan/text prompts and locale builder.
- Modify prompt strings only in `src/domain/strategies/meal_analysis_strategy.py`: portion, weight, ingredient and user-context messages.
- Modify refinement prompt assembly only in `src/app/handlers/command_handlers/parse_meal_text_handler.py`; keep validation/sanitization and request processing unchanged.
- Evaluation owner updates existing prompt/strategy/handler tests in Phase 3; coordinate shared files before parallel work. No other agent should edit prompt-owned files.

## Shared Instruction Block

Integrate this content into both prompts, with their existing field definitions and JSON contracts. Remove old rules that contradict it; do not simply append it beneath them.

```text
Estimate the food shown or described and the amount consumed. Use the most
likely realistic estimate, without systematic upward or downward bias.

Honor explicit amounts, consumed fractions, preparation, product variants
and ingredient exclusions. When information is absent, use an ordinary
serving and the typical preparation for the identified food.

Break mixed dishes into meaningful principal components, without a minimum
component count. Do not invent ingredients to fill a quota or count both
the whole dish and its ingredients. Preserve explicitly listed atomic foods
one-for-one. Group minor garnishes only when their nutrition is retained.

quantity_g is the total edible mass of that row after counts and fractions.
Exclude inedible parts and packaging. Convert liquid volume using appropriate
density. Distribute a supplied total edible weight across components; their
weights must sum to that total. Scale each component exactly once.

Use a nutrient profile matching raw, dry, cooked, drained or ready-to-eat
state. Preserve distinctions such as sweetened, unsweetened, skin-on,
skinless, and lean versus fatty cuts. Do not equate cooked and dry weights.

All macros and micronutrients are totals for the row's quantity_g, not
per-100g values. Scale the matching typical profile by quantity_g / 100.
Carbs include fiber and sugar; do not add them again. Account for absorbed
cooking fat, dressing and sauces once, within the prepared ingredient or
as a separate component. Do not assume added fat for every cooked food.

Estimate all supported micronutrients when reasonably supported by the
identified food and preparation: vitamin_a in mcg RAE; vitamin_c, vitamin_e,
calcium, iron, magnesium, potassium and sodium in mg; saturated_fat and
added_sugar in g. Use null for genuinely unknown individual values, not
zero. Zero means a supported absence. Do not omit micronutrient estimation
to shorten the response. Include required nullable keys exactly as specified
by the schema. If no supported micronutrient can reasonably be estimated,
micros may be null; this is exceptional, not the normal food default.

Use preparation-appropriate vitamin/mineral estimates; do not assume every
micronutrient survives cooking unchanged. Distinguish added sugar from total
sugar. Sodium depends on likely seasoning/sauce; do not invent an exact
brand recipe or fortification profile without supporting information.

Check nonnegative nutrients, fiber and sugar within total carbs, saturated
fat within total fat, and added sugar within total sugar. Protein + carbs +
fat must fit the edible mass, allowing rounding; do not add fiber again.
Return concise names and numeric values without spurious decimal precision,
while retaining meaningful small micronutrient values. Return only required
JSON, with no calories, reasoning narrative, confidence notes or extra fields.
```

## Implementation Steps

1. Remove forced minimum component counts, the cooked-food fat floor, and the ambiguous per-100g output instruction. Keep existing maximum item limits and every meaningful explicitly listed food.
2. Replace lengthy full-response examples with the schema description and at most two compact behavioral examples: a known-weight dish whose components partition the weight, and a half-portion whose grams/macros/micros all scale together. Add an example only for a demonstrated development-set failure; keep examples out of held-out cases.
3. Scan instructions: use visible count, thickness, container fill and reliable size cues before generic serving assumptions. User-supplied food facts/amounts take precedence over visual guesses, but never override the output schema or non-food guard. Do not infer unseen consumption; absent context, estimate the visible edible portion.
4. Keep likely-food acceptance for pastries/cropped/display-case foods. Return the existing non-food shape for clear negatives. Confidence reflects both identity and portion/preparation uncertainty; do not add fields or manufacture calories for a zero-calorie drink to pass downstream validation.
5. Weight/portion context must explicitly scale quantities and all nutrients together. Scope named-food corrections to that food; scope whole-meal amounts to the full meal. Total weight means edible consumed weight unless the user explicitly supplies a different basis.
6. Text instructions: favor numeric total edible grams when estimable; preserve localized quantity/unit and English identity/unit contracts. Treat ready-to-eat dish weights as served weights unless specified otherwise. Standalone uncooked ingredients use the ordinary ingredient state; record supported explicit preparation only. For unsupported preparation words retain them in identity/name and use `unknown`, not an invented enum or inaccurate alias.
7. Refinement: return the complete updated meal; preserve supplied unchanged food amounts/macros; apply requested additions/removals once. Scale supplied nutrients on amount-only edits. Estimate missing micronutrients consistently for the final food/state/portion; prior micros are not provided by the current refinement contract, so exact preservation is not promised. Keep all sanitizer bounds and metadata protections intact.
8. Preserve or replace the locale builder's exact string anchors together with the prompt. EN names remain canonical; requested localized fields remain complete; language changes must not change nutrient estimates. Use short focused builder tests for all supported languages.
9. Keep static instructions stable and put variable language/user context in existing locations. Existing prompt-cache hashing already versions changed prompts. Do not add a cache, split into multiple AI calls, or reduce nutrient fields/output cap for speed.

## Risks

Prompt-only checks guide estimates; they are not deterministic validators or database lookups. Do not claim that mentioning standard nutrient profiles queries a database. Speed comes from removing duplicated instructions, unnecessary ingredient rows and malformed outputs, with full micronutrients retained.

## Success Criteria

- [x] Both active prompts implement the locked rules with no contradictory legacy clauses.
- [x] Micronutrients retain all ten fields, units, portion scaling and unknown-versus-zero meaning.
- [x] Scan context variants and text refinements apply the same amount semantics.
- [x] Canonical/localized fields and existing schema/fallback JSON remain valid.
- [x] No unrelated prompts, APIs, model settings, limits or production paths change.

## Implementation Evidence

The prompt owner updated the active scan/text instructions and scan context strings. The parse-text refinement context now explicitly says prior micronutrients are absent and cannot be preserved exactly. Regression tests cover the ten nullable micronutrient fields and units, portion scaling language, localization builders, and refinement semantics. A separate prompt review found and resolved a provider fallback-shape issue.

Rendered prompt character counts from the actual builders:

| Prompt | Baseline | Candidate | Change |
|---|---:|---:|---:|
| Meal scan EN | 5,136 | 4,399 | -14.3% |
| Meal scan VI | 5,835 | 5,097 | -12.6% |
| Parse text EN | 2,987 | 3,120 | +4.5% |
| Parse text VI | 2,990 | 3,123 | +4.4% |

Hashes are in the baseline and candidate snapshot JSON files. These size changes describe prompt input only; they do not establish provider latency or nutrition accuracy. Parse-text is longer because its fallback example now demonstrates non-blanket micronutrient estimates; only matched provider observations can show whether this affects speed.
