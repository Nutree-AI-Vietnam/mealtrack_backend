---
phase: 3
title: "Accuracy and Performance Validation"
status: in_progress
effort: ""
---

# Phase 3: Accuracy and Performance Validation

## Overview

Priority P1; owner: evaluation engineer, then independent reviewer; depends on Phase 2. Validate nutrient completeness and accuracy together with speed. Context: [baseline protocol](./phase-01-baseline-and-evaluation-contract.md), [candidate instructions](./phase-02-prompt-and-context-revision.md).

## Test Protocol

- Run focused offline schema/parser/strategy/handler tests first. They prove contract compatibility only. Add regression cases for grams versus per-100g, cooked/dry state, partial servings, total mass, cooking-fat duplication, micros units/scaling/unknowns, localization, refinement, and accepted maximum item counts.
- Reuse the frozen 60-case corpus with three repetitions per prompt and case: 360 nominal provider generations across baseline/candidate. Admit bounded batches under existing staging guards; retain current per-run limits of at most 25 cases and 50 application generations. Count physical SDK/fallback attempts separately where observable. Do not remove staging confirmations, deadlines or spend limits.
- Run corpus quality comparisons and high-item capacity stress against the real provider directly with the existing schemas/settings. High-item stress has no reference I/O or persistence. For the separate live reference-handler checks, reserve the full schema maximum of twenty items per admitted text request against the existing search/detail limits; if capacity is insufficient, stop the batch. An eight-item reservation is not safe merely because the input is expected to produce fewer items. Capture extra handler/HTTP runs separately from the 360 nominal corpus generations.
- Interleave baseline/candidate order by case and repetition under identical model, output budget, input image bytes and context. Use concurrency one for paired latency comparisons. Report cold/warm cache cohorts separately; warm both candidates equally. Do not attribute queue/cache/provider changes to prompt text.
- Tune on the 40 development cases only. Freeze candidate before opening the 20 held-out cases; failure means a revised candidate requires a fresh held-out set for another release claim. Publish development and held-out results separately.
- Score provider output, handler output and staging HTTP results separately. Run authenticated/guest parse-text and upload/scan-by-URL contract smoke checks with a designated staging test account; verify the final saved scan nutrients and identities where a route persists data. Never issue corpus writes to production.

## Metrics and Proposed Gates

These are engineering acceptance targets chosen for this plan, not established accuracy claims or promises. Lock them before baseline/candidate comparison. Report every surface separately; no averaging away a scan or text regression.

| Area | Metric and acceptance rule |
|---|---|
| Food detection | Match expected food/component identities using reviewed aliases. Precision/recall cannot fall below baseline on held-out cases; zero new violations of explicit exclusions or clear non-food negatives. Ingredient counts alone are not accuracy. |
| Portions | Exact stated edible weights within max(1 g, 1%) rounding tolerance; whole-dish component totals within the same tolerance. On inferred-weight cases, median absolute gram error no worse than baseline. |
| Calories | Apply the unchanged backend fiber-aware formula to model macros and reference macros. Target at least 10% lower median whole-meal absolute kcal error versus baseline; if baseline error is zero, require zero. Median/p90 error cannot regress; no new >2x or <0.5x reference errors on food meals with reference >=50 kcal. |
| Macros | Per-nutrient whole-meal absolute error no worse than baseline; correct total-carb/fiber semantics and no new physically impossible values. |
| Micronutrient coverage | On premarked expected-known cells, >=95% populated per nutrient and no decrease versus baseline. No blanket-null shortcut or unknown-as-zero policy. Score genuinely unknown cells separately; do not require fabricated values. |
| Micronutrient accuracy | Per nutrient, mean normalized absolute error no worse than baseline on a fixed expected-known cell set. Use max(reference value, 1 mcg for A / 0.1 mg for minerals,C,E / 0.01 g for saturated fat,added sugar) as denominator; score a missing expected value as normalized error 1 for either prompt. Evaluate known-zero cells too. Coverage gates apply independently, so missing values cannot be excluded to improve the score. No new mg/mcg or per-100g/portion errors. |
| Repeatability | For repeated identical inputs, calorie and micronutrient dispersion no worse than baseline; report range/standard deviation per case and median across cases. No zero-fill reward. |
| Reliability | First-attempt schema validity >=99% and no worse than baseline; all evaluated successful food cases valid by the existing repair bound. No new output truncation. Correct no-food responses are successes, not nutrient failures. |
| Speed | Target >=10% lower median provider time per surface; provider and endpoint p95 at most 5% above paired baseline. Confidence/sample limits accompany p95; lower tokens alone do not prove lower end-to-end latency. |
| Efficiency | Report real input/output/cached tokens and cost per valid complete meal. Tokens/cost per successful meal and validation retries must not increase. Completeness gates prevent winning by returning fewer nutrients or dropping foods. |

For range-based ground truth, error is zero inside the reviewed interval and distance to the nearest bound outside; use the interval midpoint only for denominator and large-outlier checks. Do not replace intervals with arbitrary exact targets. Mask nutrient cells whose reference cannot be established before either candidate is run. Include non-scored qualitative ambiguity cases without mixing them into quantitative accuracy.

## Implementation Steps and Files

1. Update existing evaluator/script tests so actual candidate prompts are used, modern payloads parse, every item contributes, micronutrient coverage/error is scored, and mock/live modes cannot be confused. Synthetic doubles are valid for contract tests; never label them provider quality evidence.
2. Update focused tests in `tests/unit/infra/services/ai/prompts/test_system_prompts.py`, `tests/unit/domain/strategies/test_meal_analysis_strategy.py`, `tests/unit/handlers/command_handlers/test_parse_meal_text_handler.py`, existing script tests, and relevant nutrition-contract/micro conversion tests. Assert behavior/contracts, not arbitrary prompt length or exact prose except essential schema anchors.
3. Run focused tests using `.venv/bin/python -m pytest` and the existing architecture/import checks. Before a future commit/push, run CI-aligned `.venv/bin/python -m pytest tests/unit --cov=src --cov-fail-under=65`, `ruff check`, format check and mypy as applicable. Do not run bare unscoped pytest or silently exclude failures.
4. Execute the bounded real-provider comparison, summarize tokens/latency/nutrient results, then staging HTTP/persistence checks using the effective deployment configuration. Retain detailed evaluation outputs only in restricted artifacts; commit aggregate reports and nonpersonal fixtures.
5. Review raw-to-final micronutrient propagation, reference override effects, and gram changes. If existing postprocessing loses or rescales micros incorrectly, show the failing case as a runtime limitation rather than claiming the prompt fixed it.
6. Have a code reviewer verify the final prompt/assembly/test diff and the measurement report. Resolve concrete correctness findings; do not let a performance suggestion reverse the user's micronutrient requirement.

## Failure Handling

- If speed improves by losing micronutrients or foods, reject the candidate.
- If quality holds but latency/calorie-improvement targets are missed, report partial results, do not declare the full goal achieved, and continue prompt iteration on development cases only.
- If full twenty-item text responses cannot fit the existing output cap, preserve the contract and record the measured minimum required capacity. A future token-limit adjustment is a separately scoped runtime change; this plan does not authorize it or bypass the gate.
- If final API behavior is limited by reference override, refinement context omissions, zero-calorie scan rejection, or other existing code, document the exact affected cohort. Do not broaden runtime scope or conceal it in aggregate scores.
- If gains are smaller than measurement noise, report inconclusive; extend matched samples within explicit evaluation budgets before making a speed claim.

## Evidence Recording

Implementation remains pending. Execute the sequence above after the candidate prompts are ready; record check commands, revision hashes, corpus version and real-versus-offline evidence with the results.

## Success Criteria

- [ ] Required local contract and architecture checks pass; unrelated failures are documented without being suppressed.
- [ ] Full ten-field micronutrient completeness and accuracy gates pass.
- [ ] Held-out identity, portions, macros, calories, repeatability and localization gates pass.
- [ ] Matched latency, retries, truncation, token/cost and capacity results are recorded.
- [ ] Staging final-response/persistence evidence is distinct from raw model evidence.
- [ ] Reviewer has no unresolved correctness issue; unmet targets remain explicit.

## Implementation Status

The focused evaluator and prompt regression tests pass (96 tests). The CI-aligned unit suite passes with 3,541 tests and 80.32% coverage. The scan `contract` and parse-text `offline` commands both exit successfully and label their output as offline contract evidence; parse-text reports fixture timing separately from provider speed. No real-provider, staging HTTP, or persistence comparison was run. No reviewed photo/text corpus was present, so detection, calories, portions, micronutrient quality, provider latency, and output-capacity gates remain unmeasured.

The architecture suite has three existing failures on this base: two allowlist failures in unchanged route/repository files and a domain-service count of 113 against a cap of 46. The worktree returns the service count to the base count of 113; the evaluator helpers were placed under `src/domain/evaluation/` rather than increasing `src/domain/services/`.
