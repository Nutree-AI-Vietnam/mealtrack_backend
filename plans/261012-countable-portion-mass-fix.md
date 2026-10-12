# Countable parse-text portions must include edible grams

## Goal

When an AI parse returns a countable/non-mass portion (for example, `1 plate` or `2 slices`) without `quantity_g`, reject that candidate during the existing validation loop. The existing single correction attempt must run before custom estimation. Never infer a weight from macro totals or relax physical validation.

## Current path

- `MealTextFoodEstimate.quantity_g` is optional in `src/domain/model/ai/nutrition_contracts.py`.
- `_generate_parse_text_payload` in `src/app/handlers/command_handlers/parse_meal_text_handler.py` validates the schema and macro physics inside the two-attempt loop. `AIOutputValidationError` there already creates a correction prompt and retries once.
- After generation returns, pure-AI and reference-miss paths call `_require_custom_estimate`; `apply_custom_estimate` uses 100 g per count when no explicit grams are present. A portion whose macros exceed that baseline then fails outside the retry loop.
- `validate_parse_text_nutrition` currently skips macro-mass comparison if `quantity_g` is absent. It is also used for resolved/custom rows, whose callers provide grams.

## Smallest safe change

Extend `validate_parse_text_nutrition` for raw parse candidates. If `quantity_g` is missing and a volume unit is used, derive grams through the same density-aware `convert_quantity_to_grams` path that the custom fallback uses, then apply the existing macro physical check. For other non-mass units, the custom fallback assumes `100 g × quantity`; if macro totals exceed that baseline, raise a sanitized `quantity_g` validation error so the candidate enters the existing correction retry. This includes `cup`, tablespoon, and teaspoon because the custom fallback currently treats them as countable 100 g units.

Do not infer grams from macro totals, change `CUSTOM_COUNT_GRAMS`, drop items, or weaken physical validation. Preserve explicit mass-unit handling and usable countable/volume estimates. Reuse the current two-attempt limit and candidate-bearing correction prompt. If the second candidate still omits grams, let the existing terminal `AIOutputValidationError` escape with `attempt_count=2`.

## Regression coverage

Extend `tests/unit/handlers/command_handlers/test_parse_meal_text_handler.py`:

1. **Correction succeeds:** first response contains `1 plate` with no `quantity_g` and portion macros totaling more than the custom 100 g baseline; second response retains the stated count/unit and supplies plausible `quantity_g` with macros at or below that mass. Assert exactly two AI calls, retry details mention `items.0.quantity_g`, corrected grams reach the custom estimate, and parsing succeeds.
2. **Correction still omits grams:** both candidates contain the countable unit without `quantity_g`. Assert exactly two calls and a typed `AIOutputValidationError` with `purpose="parse_text"`, `attempt_count=2`, and a `quantity_g` validation detail.
3. **Fallback unit parity:** verify `cup` aliases and teaspoons use the same 100 g baseline as custom fallback, while low-macro ml input passes and high-macro ml olive oil is rejected using density-derived mass. When local and English units differ, preserve the local volume unit because custom estimation does too.
4. Keep existing mass-unit, volume-unit, and countable custom-estimate tests passing.

## Verification

Run the focused handler tests and validation/custom-estimate tests, then changed-file Ruff/format checks. Confirm no additional model generation occurs and no new glossary or catalog write is involved.
