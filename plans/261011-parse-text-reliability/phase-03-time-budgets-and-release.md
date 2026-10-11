---
phase: 3
title: "Time Budgets, Measurement, and Release"
status: pending
priority: P1
dependencies: [1, 2]
---

# Time Budgets, Measurement, and Release

## Requirements and files

Bound aggregate waiting while preserving the configured provider/model, one ordinary generation and one validation correction. Measure real cohorts before choosing aggressive defaults. Files: handler budget, API composition/settings helper, parse-specific adapter/manager timeout plumbing if needed, provider timing metrics, and existing serving-label persistence seam. Preserve behavior for every other AI purpose.

## TODOs

- [ ] Emit a privacy-safe request summary: total/cache/per-generation/semantic-validation/local-reference/FatSecret/adoption/serving-persistence durations; status/reason; initial/refinement, language, item bucket, pure/reference mode, cache state, application-generation count and physical attempts/retries/fallback.
- [ ] Reuse existing OpenAI prompt-cache token counters and instrumentation where available; result-cache hits and prompt-cache hits are different measurements. Never log text, food names, IDs, or raw payloads in the new summaries.
- [ ] Introduce an overall request deadline beginning before the first external wait, with explicit smaller optional cache/persistence budgets. Pass positive validated settings through API composition, not handler `os.getenv` lookups; retain the existing reference budget as a separate nested cap.
- [ ] Check remaining time before any correction/provider operation. One SDK retry and manager fallback must consume the same deadline; apply an outer cancellable wait so per-attempt timeouts cannot multiply aggregate request latency indefinitely. No new attempt allowance.
- [ ] On request deadline during AI work return existing `AI_UNAVAILABLE`/503. On repeated invalid output return existing `AI_OUTPUT_INVALID`/422. Preserve disconnect/task cancellation rather than swallowing it as a recoverable item failure.
- [ ] On optional reference failure/timeout use the valid original AI estimate, preserving every item. Cache timeouts skip cache. Provider adoption and serving-label DB work use bounded transaction contexts; cancellation rolls back and must not publish uncommitted local IDs.
- [ ] Keep required per-100g snapshots, backend calories, names, micros, and saveable provenance on the synchronous path. Optional work may be skipped under budget; do not introduce untracked background writes to make timings appear faster.
- [ ] Measure duplicate lookup names and per-item persistence cost. Deduplicate/batch identical reference reads only if traces show meaningful savings; preserve distinct quantities and ordered rows. Avoid speculative single-flight, new queues, or infrastructure scaling.
- [ ] Use measured stage distribution to choose documented request/optional-stage defaults before enablement. Do not silently lower shared global OpenAI timeout/retries or switch models. Record defaults, effective runtime values, cancellation behavior, and rollback setting.

## Validation planned for implementation

Focused controlled-time tests: sleeping cache/provider/DB, first invalid output then valid, no time left for retry, maximum two application generations, transport attempt accounting, cancellation cleanup, persistence rollback/provenance, and both routes with guest quota release. Compile modified Python, run focused lint and architecture checks, then relevant unit/API suites using project `.venv`. Required pre-push suite is `pytest tests/unit --cov=src --cov-fail-under=65`; no bare unscoped pytest. None run during planning.

## Provider/staging release gates

Use the existing evaluator in provider mode with reviewed realistic portions/preparation references and all ten micronutrients. Same effective model/settings/input mix for baseline and candidate; no new model-switch experiment. Include EN/VI, clear typos, small g portions, count/volume, dense foods, mixed lists, 20-row output, and refinements. Fixture tests do not prove recognition, macros, or latency.

Report first-pass correctness, correction rate, full failures, item coverage, mass violations, calorie/macro/micro error, micro completeness/null/zero, and cold/warm p50/p95/p99 with sample sizes. Separate validation evidence from actual latency measurements. Check 2048-token output capacity under the current cap; never reduce micros or drop foods to accommodate it. If real truncation is observed, propose a measured bounded output-cap adjustment separately.

Staging must exercise authenticated and guest HTTP routes, cold miss/hit, refinement, configured pure/reference modes, and save/read of resulting snapshots/provenance. Record release/effective config and committed reference IDs. Production monitoring establishes recurrence and cache cohorts; a local suite or staging response alone does not.

Release order: physical/completeness fix, validated cache enablement, then measured budget defaults. Disable newly enabled cache/budget settings independently if regressions appear. Keep the current food-name fix and established ten-micro/calorie contracts. Docs impact: minor; update evergreen external-services/troubleshooting notes with effective flags, typed failure behavior, and measured evidence after implementation.

## Outstanding evidence

No live measurements or implementation checks completed. Effective deployed model/settings, stage baseline, reviewed references, safe budget defaults, token-cap feasibility, and staging/production observations remain required evidence.
