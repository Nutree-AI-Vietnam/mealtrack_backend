---
phase: 4
title: "Release and Rollback"
status: pending
effort: ""
---

# Phase 4: Release and Rollback

## Overview

Priority P1; owner: release engineer; depends on all Phase 3 gates. Release only the measured prompt/context change and its tests. This phase describes later execution; creating this plan performs no deploy or live experiment. Context: [overview](./plan.md), [gates](./phase-03-accuracy-and-performance-validation.md).

## Requirements and Compatibility

- Public routes, request/response types, preparation enums, schemas, nutrient units, backend calorie rules, and source/reference precedence remain unchanged. No data migration or backfill.
- Food-label OCR, barcode, recipe generation, recommendations and mobile UI are outside this change. Sharing a prompt module is not authorization to rewrite neighboring prompts.
- Existing request limits, provider fallback and output budgets stay unchanged. Changed prompt hashes naturally produce different provider prompt-cache keys; do not clear unrelated application caches.

## Implementation Steps

1. Save baseline revision/prompt hashes, candidate revision/hashes, evaluation report and effective nonsecret settings. Stage only owned prompt, context, evaluator, test and documentation files; preserve unrelated work in the checkout.
2. Complete code review and CI, deploy the candidate to staging using the normal workflow, then verify that actual requests use the candidate hash/revision. Re-run representative EN/VI scan/text cases, every localized builder smoke test, refinements, and full-micronutrient cases.
3. Ship through the existing release process only after the candidate passes the documented gates. Do not invent a new traffic router or use the no-op optimized-prompt setting as a rollout switch. If the existing platform supports a canary, use it; otherwise release the tested commit with the baseline revert prepared.
4. Observe first-pass failures, retries/fallbacks, output-token usage, nutrient completeness in sampled test cases, and p50/p95 by surface/language/item count. Separate initial cold-cache behavior from steady-state observations. Use existing metrics only; do not add raw-food/user logging.
5. Roll back the prompt/context commit if full micros disappear, item exclusions or nutrient scaling fail, validation/truncation regressions appear, or p95 exceeds the matched baseline by more than 5% in two comparable windows of at least 100 requests per affected surface. A confirmed severe semantic error triggers rollback without waiting for window counts. If traffic is insufficient, state that production latency remains unverified.
6. Revert only the owned prompt/context changes to the saved baseline; no schema/data rollback is needed. Confirm baseline hashes and a representative full-micronutrient case after rollback. Leave evaluator improvements available if compatible.
7. Docs impact: minor. Update current testing standards with real-provider versus offline evaluation usage and limitations; add a concise project journal/release note with actual measurements. Update this plan's phase state via `ck plan check` only when phase deliverables are complete. Do not edit archived documentation as current authority or create missing roadmap files solely for this change.

## Delivery Evidence and Risks

Final implementation handoff includes commit/PR links, exact model and prompt hashes, corpus/version, local test results, full-micronutrient coverage/error, calorie/portion/detection quality, repeatability, tokens/cost/retries, provider and endpoint latency, staging revision, release state and rollback revision.

Keep “source changed,” “tests passed,” “real-provider benchmark passed,” “staging verified,” and “production observed” separate. A ChatGPT-style answer is a useful comparison, not proof of nutritional truth or identical performance.

## Execution Boundary

Execute the release sequence only after validation; any prerequisite that is unavailable remains explicitly pending. The current user request ends with the saved plan.

## Success Criteria

- [ ] Candidate revision is verified in staging with full nutrient and localization checks.
- [ ] Evaluation and CI gates pass without weakening micronutrient coverage or changing scope.
- [ ] Baseline-only revert is prepared and verified procedurally.
- [ ] Production deployment/observation status is reported accurately, with no inferred completion.
- [ ] Documentation and phase statuses match actual evidence.
