---
phase: 2
title: "Cache Fidelity and Production Wiring"
status: pending
priority: P1
dependencies: [1]
---

# Cache Fidelity and Production Wiring

## Requirements and files

Wire the existing `CachePort` through `get_configured_event_bus()` after exact round-trip validation. Cache only complete successful initial parses. Files: handler and existing DTO/cache mapping seams, `src/api/base_dependencies.py`, `src/api/dependencies/event_bus.py`, focused handler/API composition tests. Reuse `src/infra/cache/cache_service.py`; no new cache backend or public response fields.

## TODOs

- [ ] Consolidate cold-response construction and cache deserialization mapping; explicitly cover every current DTO field and snapshot. Dataclass construction alone is not cache validation.
- [ ] Store an internal versioned envelope; require current policy/prompt context, a nonempty items list, finite positive quantities, physically valid macros, consistent totals and per-100g snapshots, safe serving options, and valid provenance shape. Treat malformed/stale entries as misses, not repaired successful responses.
- [ ] Preserve `source_snapshot.extra_nutrients` values exactly, including null and supported zero; retain all ten estimated nutrient categories, canonical/display names, local/provider/custom IDs, nutrition contract, units, and emoji.
- [ ] Version beyond the food-name fix's `v2` result key. Include injected semantic/prompt version, pure/reference mode, normalized locale, and guest/authenticated cohort. Include relevant effective model/routing revision through composition if it can change independently; never read infra settings from app/domain code.
- [ ] Preserve refinement cache bypass. Do not include raw text, user IDs, tokens, or provider payloads in cache/telemetry labels. Current text hashing may remain.
- [ ] Retain cache failure as an optional miss/write skip. Bound get/set latency under Phase 3 policy; do not cache failures, partial rows, or failed adoption metadata.
- [ ] Pass existing `cache_service` to handler registration using an independently controllable parse-result-cache setting. Keep guest quota logic around hits; distinguish response fidelity from catalog-adoption side effects.
- [ ] Initially separate guest/authenticated keys so an authenticated adopted row does not substitute for a guest parse. Cached IDs must represent committed global food references, not user-specific objects; verify this against response-save flows.
- [ ] Add low-cardinality hit/miss/invalid/bypass/error counters and cache stage duration. Report runtime enablement explicitly; source wiring alone is not production-hit evidence.

## Validation and rollout

Planned tests: equality of cold/cached DTO and HTTP response (every field and ten micros), malformed/empty cache, nonfinite values, inconsistent totals, stale versions, mode/locale/cohort separation, refinement bypass, get/set timeout, one generation on miss, zero on hit, and guest reservation/completion behavior. Enable only after these pass; disable the new cache setting for rollback and ignore incompatible payload versions. Do not run these tests as part of this planning task.

Risk: mode/policy changes or guest/authenticated side effects can make a text-only key unsafe; context versioning and cohort separation are required before activation. A seven-day cache TTL does not replace validity/version checks.
