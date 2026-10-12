---
title: "Parse Text Reliability and Bounded Latency"
description: "Reject impossible estimates, preserve every parsed item, activate faithful caching, and bound external waits."
status: complete
priority: P1
created: "2026-10-11"
source: skill
blockedBy: []
blocks: []
---

# Parse Text Reliability and Bounded Latency

## Objective

Make parse-text return a complete, physically valid, saveable meal or an existing typed error. Improve repeat-request speed through validated caching, and measure/bound cold-request stages. A reported real-provider result contained 11 g of protein + carbs + fat for 10 g of granola; this plan traces why that can survive current code. It does not reproduce the provider request.

## Locked scope

- Keep the food-name fix currently in the shared tree: preserve meaningful AI names and resolve clear spelling/context in the normal generation. No glossary additions.
- Preserve public request/response fields, backend calorie derivation, all ten micronutrients and individual null/zero meanings, reference precedence, guest quota, and ownership rules.
- One normal application generation; at most one validation correction. No new model, provider route, routine translation generation, RAG, or migration. Include the narrow Flutter refinement append-versus-replace correction identified during source review.
- Source review and planning only completed here. No tests, provider calls, deployment, or measured speed claim.

## Phases and TODOs

| Phase | Deliverable | Status |
|---|---|---|
| 1 | [Physical validation and complete item handling](./phase-01-physical-validation-and-completeness.md) | Complete |
| 2 | [Cache fidelity and production wiring](./phase-02-cache-fidelity-and-wiring.md) | Complete |
| 3 | [Time budgets, measurement, and release gates](./phase-03-time-budgets-and-release.md) | Complete locally; staging gates pending |
| 4 | [Flutter refinement result application](./phase-04-flutter-refinement-application.md) | Complete; simulator build succeeded |

- [x] Complete Phase 1 before enabling result caching.
- [x] Complete cache round-trip/compatibility gates before production wiring.
- [x] Add stage measurements and bound optional waits before asserting latency improvement.
- [x] Apply complete refinement responses by replacement while preserving unchanged cart identities/metadata.
- [x] Validate a typo through real Luna 6 locally; full reviewed-corpus and staging routes remain release gates.

## Dependencies and ownership

The existing `261009-1001-meal-prompts-speed-accuracy-micros` plan has implemented prompt/context changes and pending provider/staging evidence. Reuse those prompts and evaluation tools; this plan owns the follow-up runtime constraints identified by that plan's release boundary. The older `260814-1035-parse-text-fatsecret-validation-harness` plan is completed and supplies established resolver/route seams. Neither plan is rewritten here.

Backend owner: existing handler, parse-specific domain validation, custom-estimate handling, and API composition/cache seams. Provider owner: only parse-text error classification/deadline plumbing if required; preserve other purposes. Flutter owner: existing parser provider, response-to-cart mapper, and create-meal success application in `/Users/alexnguyen/Desktop/Nut/nutree/nutree_ai`. Test owner: focused existing contract, resolver, handler, provider, both route files, and Flutter parser/mapper/controller tests. Work sequentially where ownership overlaps; preserve current food-name edits and unrelated changes.

## Success criteria

- 11 g macros for a stated 10 g portion is rejected and corrected through the existing retry; never bypassed, silently clamped into invented nutrition, or cached.
- Every validated input row yields one ordered result, or the entire parse fails using an existing typed error. Partial and empty success are prohibited.
- Refining an existing cart applies the complete updated meal once; it does not duplicate old rows or discard unchanged rows' IDs, reference metadata, overrides, or snapshots.
- Cold and cached results agree on item values, totals, identity, units, provenance, snapshots, and all represented micronutrients.
- Repeated initial parses can use the cache in real API composition; refinements bypass it.
- Total waiting has a documented budget; transport retries/fallback cannot create unbounded aggregate latency. Optional cache/reference/persistence timeouts preserve valid estimates.
- Same-model, same-corpus measurements report initial/refinement, language, item count, pure/reference mode, cache state, application generations, and physical attempts. Lower warm median and no material cold p95 regression are release gates, not current claims.

## Evidence and rollback

Exact current source pointers and failure scenarios: [source review](./source-review.md). All references describe the 2026-10-11 shared tree after the naming fix; recheck line numbers before implementation.

Ship validation/completeness first, cache enablement second, budget tuning last. Disable newly wired result caching independently if needed; version cache payloads so rollback ignores incompatible entries. Retain the naming fix. Do not weaken the physical invariant or omit micros to meet latency targets.

## Implementation verification

- Backend focused and compatibility set: 270 passed, 5 warnings.
- Flutter parser/provider/screen focused set: 34 passed; analyzer clean across 8 changed source/test files; iOS simulator debug build succeeded.
- Real Luna 6 local HTTP route: `10g granula` returned `granola`; invalid first-candidate macro mass was corrected once. Cold 6.06s; identical warm cache hit 2ms. Synthetic input, auth dependency overridden, local Redis; not staging or broad nutrition-accuracy proof.
- Whole-request limit defaults to 10s with 0.3s reserved to finish the response; reference stage capped at 1s, cache operations at 50ms.

Remaining release evidence: full reviewed food/portion corpus, authenticated staging API/mobile save-read path, deployment model/configuration, and production latency/cache activation. There are no open product questions; do not claim those release gates from the local evidence above.

Confirmed implementation policy: custom AI estimates use separate food-independent physical/subset validation; legacy provider resolution retains `validate_ai_fallback` and its potato/apple regressions. This serves valid unknown dense foods without widening a food whitelist. Physical validity does not establish food-identity/nutrition accuracy; references remain preferred and real-model evaluation must assess semantic quality. Truly invalid final estimates produce an accounted typed error; no silent dropping or raw-row bypass.
