---
phase: 4
title: "AI Request Policy"
status: in_progress
effort: ""
priority: P1
dependencies: [3]
---

# Phase 4: AI Request Policy

## Overview

Dedicated OpenAI planner policy, compact inputs, deadlines, retries and safety evaluation implemented; local checks passed. Live usefulness/locale/token/capacity evaluation remains pending.

## Context Links

- [Solution](./solution-document.md), [compact catalog contract](./phase-03-catalog-projections-and-generation.md), [release verification](./phase-06-verification-and-rollout.md).
- [Performance review](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/plans/reports/meal-plan-performance-review-2026-10-02.md); [testing standards](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/docs/testing-standards.md).

## Key Insights

- Current adapter truncates a full catalog to its first 200 recipes, requests up to 3,000 output tokens, and routes through `general`; deployed routing is unverified.
- The application already releases its DB UoW before provider I/O. Keep that behavior; proposal remains separate from applying the revision-aware PATCH.
- Cached clients, strict schemas, circuit breakers, and prompt-cache policy exist. Extend those abstractions rather than duplicate them.

## Requirements

- Add `meal_plan_adjustment` across enum, string-purpose mapping, and manager chain; explicit OpenAI routing must not inherit configured Cloudflare `general` prepending.
- Filter/rank eligible candidates before prompt construction; retain explicitly requested named recipe when eligible. Backend validates every changed slot, constraint, owner, and outcome.
- Start with ≤40 ranked eligible candidates; named-recipe retrieval precedes the cap and widening occurs at most once within the remaining deadline when truncation excludes feasible matches.
- Initial proposed budget: 30 seconds from endpoint entry; provider portion ≤25 seconds and remaining total. Admission, one engine retry, validation/presentation and cancellation all fit the total; SDK auto retries are zero.
- Requested locale, concise explanation/diff and actual slot changes come from one compact structured response; no automatic writes after timeout.
- AI callers map base_revision to existing PATCH expected_revision (current Flutter already does). Generic legacy PATCH may omit it and cannot identify AI origin; preserve that compatibility. Every apply revalidates current target-slot logged/version state and source eligibility, because a log can change slot.version without plan.revision.

## Architecture

Owner-scoped plan/profile → compact eligible shortlist → bounded cross-replica admission → explicit OpenAI strict-schema request → domain validation → proposal response with `base_revision`. Carry one monotonic deadline from endpoint entry through admission, attempts and response work. Disable planner SDK auto retries; allow at most one engine transient retry within remaining provider time. Preserve explicit user apply.

## Related Code Files

- Modify: [purpose enum](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/src/domain/model/ai/model_purpose.py:6), [PURPOSE_MAP](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/src/infra/adapters/meal_generation_service.py:13), [AI manager routing/attempts](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/src/infra/services/ai/ai_model_manager.py:240).
- Modify: [structured proposal adapter](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/src/infra/adapters/weekly_meal_plan_adjustment_provider.py:45), [provider port](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/src/domain/ports/weekly_meal_plan_adjustment_provider_port.py), [proposal orchestration/validation](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/src/app/services/weekly_meal_plan_service.py:269).
- Modify through integrator: [AI route response](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/src/api/routes/v1/meal_plans.py:411), [dependency wiring](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/src/api/base_dependencies.py).
- Read/extend: [OpenAI provider](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/src/infra/services/ai/providers/openai_provider.py), [prompt-cache policy](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/src/infra/services/ai/openai_prompt_cache_policy.py), [adapter tests](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/tests/unit/infra/adapters/test_weekly_meal_plan_adjustment_provider.py).
- Create: focused deadline/admission helper where existing abstractions cannot express total budgeting, aggregate AI quality fixture/eval and provider timing evidence. Delete: none; no schema migration unless durable admission needs it.

## Implementation Steps

1. Pin current deployed chain/model/config and client timeout margin; add purpose routing tests proving planner stays OpenAI under Cloudflare `general` configuration.
2. Build request-specific shortlist from phase 3 projections, including eligible explicit recipe mentions and target-slot suitability; preserve saved/profile and explicit-request validation distinctions.
3. Tighten structured output to bounded changes/explanation/diff in requested locale. Derive token cap from actual output; maintain base_revision→expected_revision mapping and strict current target-slot/source validation on apply. Do not make generic PATCH expected_revision universally required without caller migration.
4. Place stable rules/schema first and request-specific data last; retain cached clients and inspect model-specific prompt-cache eligibility/hit token usage rather than assume savings.
5. Carry total deadline through all work; cache dedicated planner clients with SDK retries zero without mutating shared parsing/vision settings. Allow one transient engine retry maximum, respect Retry-After only if it fits, and propagate cancellation/release admission.
6. Initially allocate fixed replica/worker-count shares of the total interactive capacity and separate preparation quota; verify the deployment total. Before autoscaling use shared leased admission slots with bounded wait and crash recovery; optional Redis is not required authority.
7. Remove serial post-provider translation for explanation/diff when response supplies requested locale; selected catalog text uses phase 5 persisted fallback strategy.
8. Record attempt count/model/purpose, tokens/cache tokens, admission/provider/total duration, validation failure and deadline errors without raw prompts. Compare quality across named recipes, diets/allergies, target slots, locales and no-op/clear requests.
9. Compile and pass routing, quality, deadline, cancellation, proposal/stale-apply, and provider-degradation gates. Optional Batch is restricted to offline evaluation/preparation, with its separate completion window.

## Todo List

- [x] Implement explicit planner purpose and eligible named-recipe shortlist.
- [x] Bound output and unify requested-locale proposal generation.
- [ ] Enforce aggregate deadline, retry/cancellation and cross-replica admission.
- [ ] Record quality/tokens/attempt metrics and compare isolated provider gates.

## Success Criteria

- [x] Planner routes to OpenAI explicitly; changed recipes always satisfy current backend hard constraints.
- [ ] Timeout/cancellation creates no plan write, leaked admission, or late auto-apply; AI callers supply expected_revision and stale plan/target-slot proposals fail revision or logged/slot-version validation. Optional generic legacy PATCH semantics remain characterized.
- [ ] Backend safety violations are zero; usefulness/locale quality meets agreed comparison to current behavior, and token/attempt/deadline results identify exact model/config.
- [ ] Total deadline includes admission/retry and response work; fixed replica allocations prove bounded total capacity, and shared leased slots precede autoscaling.

## Risk Assessment and Security

- Shortlists can exclude a requested valid recipe: include named matches before ranking/capping and test rejection reasons.
- Provider may return unsafe/invalid changes: strict schemas supplement domain checks, never replace them. Treat prompt text as untrusted data.
- Retry/circuit/admission rules can prolong failure: count all attempts inside deadline and avoid unbounded waits when coordination is down.

## Unresolved Measurements and Next Steps

- Unknown: live provider chain, quality threshold, shortlist/output cap, prompt-cache hit rate, transient rate, and cross-replica quota capacity.
- Phase 6 runs provider-isolated load, then limited staging canaries; interactive standard API is the release path. All timings remain proposed until measured.

## Verified Local Progress — 2026-10-03

- Explicit purpose bypasses configured Cloudflare general routing. Eligible <=40 candidates retain named recipes and lunch/dinner coverage; at most one alternate <=40 window can reach lower-ranked candidates.
- One request deadline <=30 seconds covers dependencies/response; provider/admission/widening share <=25 seconds. Dedicated SDK retries are zero; a shared budget permits at most one transient engine retry across both windows.
- Current recipe metadata stays available outside the candidate budget; compact inputs hydrate only <=14 selected recipes for proposal groceries. Backend retains hard-constraint/coordinate/logged/revision validation and never auto-applies.
- All supported fallback copy locales are static; provider explanation/diff request the locale. Provider safety/deadline/cancellation fixtures pass, but live semantic quality and prompt-cache/token savings are not measured.
- Initial interactive allocation remains per process (default2, admission1s). Deployment must prove fixed workers×replicas allocation; shared leased interactive admission is required before autoscaling.

Evidence: [integrated report](../reports/implementation-2026-10-03-weekly-planner-optimization.md). Open composite acceptance criteria stay unchecked; local implementation does not establish release completion.
