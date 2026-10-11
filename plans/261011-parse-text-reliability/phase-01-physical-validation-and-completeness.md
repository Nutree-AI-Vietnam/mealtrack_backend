---
phase: 1
title: "Physical Validation and Complete Item Handling"
status: pending
priority: P1
dependencies: []
---

# Physical Validation and Complete Item Handling

## Requirements and architecture

Keep the existing schema and two-attempt handler loop. Extend parse-specific domain validation after structural validation and before flattening/resolution. Use payload-free, indexed field errors so the same existing `AIOutputValidationError` correction path handles impossible estimates. Avoid changing shared vision/recipe macro behavior.

Files: existing `ai_output_validation_service.py`, `nutrition_resolver.py`, `parse_text_custom_estimate.py`, and `parse_meal_text_handler.py`; conditional narrow changes to `nutrition_contracts.py`, OpenAI adapter/provider, and AI manager for typed parse-error propagation. Tests: existing nutrition contract/validation/resolver/custom-estimate/handler/error-propagation suites. No migration or new public fields.

## TODOs

- [ ] Establish a reusable parse-specific semantic check: finite numbers, nonnegative values, fiber/sugar <= carbs, saturated fat <= fat, added sugar <= sugar, and P+C+F <= authoritative edible mass plus a documented rounding tolerance.
- [ ] Require finite present micronutrient values; retain individual nulls and supported zeros. Custom AI fallback uses the same food-independent physical/subset validator as generated payloads. Retain `validate_ai_fallback()` default and deliberate potato/apple mismatch regressions for the legacy-provider path; add no food-specific whitelist or glossary entries.
- [ ] Use a tiny precision-derived tolerance, not the existing 10% allowance. Proposed starting tolerance for final two-decimal portion totals: 0.02 g plus machine epsilon. Calibrate with rounded provider/reference cases before release; 11 g/10 g must always reject.
- [ ] Honor explicit g/kg quantity as mass; validate contradictory `quantity_g`. Retain density conversion for volume. When mass is unestimable, validate non-mass relationships and route through established estimation behavior rather than inventing arbitrary mass to pass.
- [ ] Apply semantic checks within `_generate_parse_text_payload()` after structural validation. Correct the full response once; after the second invalid result raise existing `AI_OUTPUT_INVALID`, with no cache write. Retain all ten micros and known/null/zero distinctions.
- [ ] Keep semantic checks after structural validation inside the handler so schema-adapter validation cannot turn physical failures into provider fallback. Classify existing parse-specific adapter Pydantic/JSON failures into `AIOutputValidationError` with sanitized details; propagate through the manager instead of generic transient fallback. Preserve real transport failures/configured routing and other purposes.
- [ ] Remove the pure-AI `or item` escape hatch. Retain an untouched validated original estimate for reference-mode recovery rather than an item partially mutated by a failed resolution. Truly impossible custom rows must become explicit accounted failures, not a raw-row bypass. Use the same two-generation budget if correction is attempted; never add a third generation after reference work.
- [ ] Pair results with original row indices. For reference exception/miss, use a valid custom estimate. If that is impossible, fail the full request. Assert result count/order before DTO construction; prohibit empty/partial success. No extra generation for ordinary reference misses.
- [ ] Check parse-specific reference candidate physics before applying a reference; reject invalid candidates and use the valid AI estimate. Keep global `nutrition_integrity_v1` and its existing 110g policy unchanged outside this path.
- [ ] Validate final portion/snapshot consistency before computing totals. Avoid independent calorie or macro clamping as correction for an impossible AI estimate; calories remain derived from final macros, with fiber handled by backend formula.

## Validation planned for implementation

Cover reported 10g/11g, individually valid but aggregate-invalid macros, fiber/sugar/micro relationships, floating rounding, g/kg mass conflicts, density-aware volume, unknown mass, two failed corrections, valid-reference fallback, None/exception on one of several rows, all-row failure, order, custom provenance/units, all ten micros, refinement scaling, and both route error/quota contracts. Use independent expected numbers; fake services test control flow only, never provider accuracy.

Retain existing resolver potato/apple excessive-fat expectations. Update the custom-estimate noun-dependent density test to a genuinely impossible macro-mass case. Add valid high-fat unknown/dry cereal custom cases, including plausible 480kcal/100g granola, to ensure no blanket low-fat-noun policy limits user estimates. No glossary additions or widened name whitelist. Identity correctness cannot be inferred from mass alone: a 100g row with 98.9g fat is physically possible both when its name is potato and when its name identifies a fatty food. Reference correction and real-model evaluation establish semantic quality separately.

## Risks and success criteria

Tighter constraints may raise correction/error rates. Measure them; do not conceal the effect through renormalization or missing rows. Strict reference checks can reject historically tolerated label rounding, so include rounding fixtures and limit the change to parse-text. The phase is complete when the reported impossible portion cannot reach either response mode, every parsed row is accounted for, and existing calorie/micro/API behavior remains compatible.

Failure policy: fail the whole parse with existing `AI_OUTPUT_INVALID` if a final row has no physically accepted custom or trusted reference estimate. If later requirements deliberately select partial parsing, the existing `unmatched_terms` must explicitly account for every rejected row and all-unmatched must fail. Current implementation scope guarantees complete rows or a typed error.
