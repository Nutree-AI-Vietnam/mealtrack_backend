---
title: "Meal Scan and Parse Text Prompt Quality with Micronutrients"
description: "Improve meal recognition, portion and nutrient estimates, first-pass reliability, and latency through prompts while retaining micronutrients."
status: in_progress
priority: P1
branch: "feature/meal-scan-parse-text-prompts"
tags: [backend, api, nutrition, performance, prompts]
blockedBy: []
blocks: []
created: "2026-10-09T03:01:42.134Z"
createdBy: "ck:plan"
source: skill
---

# Meal Scan and Parse Text Prompt Quality with Micronutrients

## Overview

Improve meal-image scan and parse-text through clearer, shorter prompts and consistent context messages. Retain all supported micronutrients, editable ingredient breakdowns, and backend-derived calories. Target more accurate and repeatable food/portion estimates with lower generation latency and fewer retries.

Prompt/context implementation and offline contract validation are complete. Provider accuracy, provider latency, staging, and release evidence remain pending because there is no reviewed meal corpus or provider/staging run in this worktree.

## Locked Decisions

- User selected flexible meaningful ingredient breakdowns, without forced minimum counts, and the most likely estimate rather than systematic calorie inflation.
- Micronutrients remain part of every food's estimation task. Never gain speed by blanket-null micros, omitting supported nutrient categories, substituting unknowns with zero, or suppressing meaningful foods.
- Retain the ten current fields: vitamin A, C, E, calcium, iron, magnesium, potassium, sodium, saturated fat, and added sugar. Reasonably supported typical-food estimates are valid; genuinely unknown individual values remain null.
- Runtime scope: prompt text and prompt/context assembly only. Evaluation and regression tests support that change. No RAG, new retrieval, model swap, additional AI passes, schema migration, public API change, or client change.
- Preserve model/provider settings, output limits, existing reference precedence, calorie rules, and localization contracts. Report an out-of-scope constraint if they prevent the targets; do not hide it by dropping data.

## Phases

| Phase | Name | Status |
|-------|------|--------|
| 1 | [Baseline and Evaluation Contract](./phase-01-baseline-and-evaluation-contract.md) | In progress |
| 2 | [Prompt and Context Revision](./phase-02-prompt-and-context-revision.md) | Complete |
| 3 | [Accuracy and Performance Validation](./phase-03-accuracy-and-performance-validation.md) | In progress |
| 4 | [Release and Rollback](./phase-04-release-and-rollback.md) | Pending |

## Dependencies

- Prior scan prompt and parse-text validation plans are completed. The active canonical-nutrition-integrity plan has a pending Flutter phase; this work preserves its backend contract and has no blocking dependency on that phase.
- Implementation needs reviewed evaluation inputs with weighed edible portions, preparation-matched nutrient references, and authorized staging/provider access. These are future validation prerequisites, not completed evidence.
- Preserve unrelated web-funnel changes already present in this checkout. Recheck source revision and worktree state when implementation starts.

## Success Criteria

- No loss of supported micronutrients or their coverage; correct units and portion scaling; no invented precision or unknown-as-zero policy.
- Better food, portion, and whole-meal calorie estimates on real, repeated comparisons; no new major errors or localization regressions.
- Proposed targets: at least 10% lower median provider latency and 10% lower median calorie absolute error per surface; no material p95 regression. These are targets, not measured promises. Exact gates and failure handling are in Phase 3.
- All ordinary successful requests retain one AI generation. Existing repair/fallback behavior stays intact; first-pass success must not regress.
- Local checks, real provider quality, staging HTTP/persistence, and production observation are reported separately.

## Ownership and Handoff

Evaluation owner: Phases 1 and 3, evaluation scripts/fixtures/tests only. Prompt owner: Phase 2, active prompt definitions and context builders only. Reviewer checks correctness and micronutrient preservation after tests; release owner executes Phase 4. Keep file ownership distinct and preserve others' edits.

Implementation instruction: execute these phases in order, keep micronutrients and public contracts intact, compare baseline and candidate using the same model/input/settings, and never claim improvement from canned fixtures. If output capacity or postprocessing prevents the gates, document the exact evidence and bounded follow-up instead of expanding this prompt-only change.

## Sources and Open Items

- Local evidence and exact files: Phase 1; candidate instructions: Phase 2; measurement and gates: Phase 3; rollback and documentation: Phase 4.
- Provider guidance: [direct reasoning-model prompts](https://developers.openai.com/api/docs/guides/reasoning-best-practices), [latency optimization](https://developers.openai.com/api/docs/guides/latency-optimization).
- Open product questions: none. Outstanding evidence: corpus references, baseline measurements, output-cap feasibility, and live effective configuration.
