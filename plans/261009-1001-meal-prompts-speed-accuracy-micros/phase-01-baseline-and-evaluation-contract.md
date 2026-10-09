---
phase: 1
title: "Baseline and Evaluation Contract"
status: in_progress
effort: ""
---

# Phase 1: Baseline and Evaluation Contract

## Overview

Priority P1; owner: evaluation engineer. Establish real baseline evidence before changing prompts. Context: [overview](./plan.md), [validation gates](./phase-03-accuracy-and-performance-validation.md), repository README and testing standards.

## Requirements and Verified Starting Point

- `src/domain/services/prompts/system_prompts.py`: scan requires at least three components and a cooked-food fat floor; its examples contradict that floor. Text requires two to five components without explicit mass conservation.
- `src/domain/parsers/vision_response_parser.py`: received macros are portion totals and summed without a per-100g conversion. Prompt wording must match this.
- `src/domain/model/ai/nutrition_contracts.py`: scan supports eight foods, text twenty, ten nullable micronutrients, and existing preparation enum values only. OpenAI schema normalization requires all object keys, with null for nullable unknowns.
- `src/app/handlers/command_handlers/parse_meal_text_handler.py`: one normal generation, at most two application generations for output validation; text output cap is 2,048 tokens. No routine composition or localization AI repair call exists to eliminate.
- Existing reference processing may change final grams/macros/micros. AI portion micros become per-100g snapshot extras when reference extras are absent; supplied reference extras take precedence as a whole. Capture these stages separately.
- Refinement input currently carries amounts/macros, not prior micronutrients. A prompt can re-estimate micros but cannot exactly preserve values it never receives. Preserve supplied values only; do not promise exact unseen micro retention.
- The scan candidate evaluator uses canned obsolete payloads and identical candidate prompts. Parse-text offline evaluation uses canned AI; its live mode calls AI/FatSecret but retains fixture-backed local lookup, first-item scoring, and an eight-item budget reservation despite the twenty-item text schema. Neither is a sufficient whole-meal prompt benchmark as-is.

## Architecture and Files

Extend existing evaluation scripts rather than adding a separate production pipeline: `scripts/development/evaluate_meal_analyze_prompt_candidates.py` and `scripts/development/evaluate_parse_text_nutrition.py`. Extend existing evaluator helpers/tests only where whole-meal scoring requires it. Keep all provider I/O in scripts/adapters; domain scoring remains pure.

Store baseline/candidate prompt snapshots and a reviewed manifest as test/evaluation artifacts. Use existing fixture directories; real meal images must be owned/licensed and free of personal data. Do not overwrite existing reference-resolution fixtures with different meanings.

## Implementation Steps

1. Record git SHA, exact rendered prompt/hash per language, schema hash, actual configured model/provider, output limit, fallback/retry settings, and image preprocessing. Read only allowlisted nonsecret configuration values; do not dump environments.
2. Capture baseline prompt text through the actual builders, including localization and scan context variants. Candidate selection in the evaluation process must accept these snapshots explicitly; the currently ineffective optimized-prompt boolean is not an A/B switch.
3. Build 60 reviewed cases: 30 scan inputs and 30 text inputs, balanced EN/VI. Per surface, freeze 20 development and 10 held-out cases before candidate tuning. Include known-weight and unconstrained portions separately; add schema/localization smoke checks for every other supported language.
4. Include simple foods, Vietnamese mixed dishes, dry/cooked pairs, eggs/fruit/counts, liquids/density, exclusions, sauces/oils, partial/double servings, packed drinks, pastries/cropped foods, and scan negatives. Include text add/remove/quantity refinements and stress cases up to existing item limits.
5. Each scored case records edible gram truth, preparation/state, food/component labels, a reference interval or value for each available nutrient, expected-known versus genuinely-unknown fields, and source provenance. Prefer weighed recipes and matching preparation profiles; a label verifies only printed nutrients. ChatGPT responses are optional comparators, never nutritional ground truth.
6. A real-provider mode must execute the same adapter/schema/settings with baseline or candidate prompt; fail clearly if it falls back to canned data. Exercise all returned items, whole-meal totals, localization, schema errors and no-food behavior. Offline fixtures remain explicitly contract tests.
7. Record provider generation time, end-to-end duration separately, input/output/cached/reasoning tokens when available, generation/physical attempt counts when available, model route, truncation/validation outcomes, and food count. Mark unsupported telemetry unknown rather than zero. Keep aggregate reports free of raw user content and credentials.
8. Establish both raw-model results and final handler/API results under the actual configured reference mode. Do not change `PARSE_TEXT_PURE_AI_ENABLED` to make a candidate look better. Do not claim fixture-backed handler timing as staging HTTP latency.
9. Tokenize a valid maximum-size JSON response including every micronutrient key and localized names using the current model's tokenizer. If exact tokenizer support is unavailable, use actual provider output usage and mark static estimates approximate. Exercise maximum-size live outputs in provider-only mode, without reference calls or persistence; replay captured outputs through deterministic handler/mapping tests. Do not admit twenty-item live reference cases using the current eight-item budget reservation. If output limits cannot fit the supported contract, record a pre-existing capacity blocker; never trim micros/items or change the cap within this scope.

## Deliverables and Risks

- Versioned baseline snapshots, reviewed corpus manifest, scorer definitions, aggregate baseline report, and capacity report. Missing weighed/reference data leaves accuracy pending rather than replaced with plausible-looking generated truth.
- No new runtime round trips or instrumentation framework. Use existing metadata and evaluation wrappers. SDK attempts/cache metrics are reported only if observable.
- Existing production data and real users are excluded. Staging HTTP checks use a designated synthetic account and must identify any persisted test records.

## Success Criteria

- [x] Baseline/candidate snapshots differ and their hashes are recorded in `research/baseline-prompt-snapshot.json` and `research/candidate-prompt-snapshot.json`.
- [ ] Corpus split and expected nutrient coverage are frozen before tuning.
- [ ] Baseline includes whole-meal micros, backend calories, valid schema, tokens and latency.
- [ ] Offline, real-provider, and staging evidence are labeled distinctly.
- [ ] Full-micronutrient output capacity is measured, with blockers explicitly listed.

## Implementation Status

The active builders produced matching baseline and candidate prompt snapshots for the worktree base revision. No reviewed 30-case-per-surface corpus, effective provider configuration, provider baseline, token/capacity measurement, or staging authorization was available. The provider and capacity criteria therefore remain pending; offline checks are explicitly contract-only.

The evaluation status now distinguishes a balanced case/language split from complete nutrition references. A provider report can be labeled `reviewed_corpus_complete` only when each food case includes all five macro values and explicit known/unknown/masked states for all ten micronutrients, with at least one known reference per micronutrient in both splits.
