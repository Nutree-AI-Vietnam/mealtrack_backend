---
date: 2026-10-11
scope: parse-text backend regression verification
status: done
---

# Parse Text Reliability Verification

## Summary

Final combined focused/compatibility run on latest source: **243 passed, zero failures, 5 warnings, 4.60s**. Python compile, Ruff lint/format checks, and test/fixture diff whitespace check pass.

No backend source edited by test owner. No real provider requests, production/staging checks, commits, or pushes. Preserved original food-name edits and all other shared work. Baseline before regression edits: 73 passed, 5 failed. Corrected invalid success fixtures and obsolete custom/drop expectations in owned tests. Lead fixed the cache validation/scaling and evaluator whole-request error contract exposed by regressions.

## Commands and results

```bash
.venv/bin/python -m pytest tests/unit/domain/services/test_ai_output_validation_service.py tests/unit/app/services/test_parse_text_custom_estimate.py tests/unit/app/services/test_parse_text_response_cache.py tests/unit/handlers/command_handlers/test_parse_meal_text_handler.py tests/unit/infra/services/ai/test_parse_text_routing.py tests/unit/infra/services/ai/providers/test_openai_provider.py tests/unit/api/test_event_bus_dependency_singletons.py tests/unit/domain/services/test_nutrition_resolver.py tests/unit/domain/model/ai/test_nutrition_contracts.py tests/unit/api/test_guest_parse_trial.py tests/unit/api/test_routes_with_mocked_event_bus.py tests/unit/api/test_base_dependencies.py tests/unit/handlers/command_handlers/test_ai_output_validation_error_propagation.py tests/unit/scripts/test_evaluate_parse_text_nutrition.py tests/unit/app/services/test_food_display_name.py -q --no-cov
# 243 passed, 5 warnings, 4.60s

.venv/bin/python -m py_compile tests/unit/domain/services/test_ai_output_validation_service.py tests/unit/app/services/test_parse_text_custom_estimate.py tests/unit/handlers/command_handlers/test_parse_meal_text_handler.py tests/unit/app/services/test_parse_text_response_cache.py tests/unit/infra/services/ai/test_parse_text_routing.py tests/unit/infra/services/ai/providers/test_openai_provider.py tests/unit/api/test_event_bus_dependency_singletons.py
# Passed

.venv/bin/ruff check tests/unit/domain/services/test_ai_output_validation_service.py tests/unit/app/services/test_parse_text_custom_estimate.py tests/unit/handlers/command_handlers/test_parse_meal_text_handler.py tests/unit/app/services/test_parse_text_response_cache.py tests/unit/infra/services/ai/test_parse_text_routing.py tests/unit/infra/services/ai/providers/test_openai_provider.py tests/unit/api/test_event_bus_dependency_singletons.py
# Passed

.venv/bin/ruff format --check tests/unit/domain/services/test_ai_output_validation_service.py tests/unit/app/services/test_parse_text_custom_estimate.py tests/unit/handlers/command_handlers/test_parse_meal_text_handler.py tests/unit/app/services/test_parse_text_response_cache.py tests/unit/infra/services/ai/test_parse_text_routing.py tests/unit/infra/services/ai/providers/test_openai_provider.py tests/unit/api/test_event_bus_dependency_singletons.py
# Passed: 7 files already formatted

git diff --check -- tests/unit/domain/services/test_ai_output_validation_service.py tests/unit/app/services/test_parse_text_custom_estimate.py tests/unit/handlers/command_handlers/test_parse_meal_text_handler.py tests/unit/infra/services/ai/test_parse_text_routing.py tests/unit/infra/services/ai/providers/test_openai_provider.py tests/unit/api/test_event_bus_dependency_singletons.py tests/fixtures/parse_text_nutrition_golden_cases.json
# Passed
```

## Findings

`test_corrupt_saveable_cache_payload_is_a_miss` now rejects all these previously accepted corrupt payloads:

| Parameter | Corruption rejected |
|---|---|
| portion-density-disagrees | Per-100g protein inconsistent with portion protein |
| derived-energy-disagrees | Per-100g calories inconsistent with backend formula |
| missing-save-snapshot | Required snapshot missing |
| snapshot-disagrees | Snapshot protein inconsistent with item density |
| negative-micro | Snapshot iron is negative |
| satfat-subset | Snapshot saturated fat exceeds total fat |
| added-sugar-subset | Snapshot added sugar exceeds total sugar |
| invalid-serving-weight | Allowed-unit gram weight negative |
| provider-without-identity | Provider origin lacks durable provider identity |
| partial-success | Nonempty unmatched terms represent incomplete success |

Confirmed passes: 11g macros for 10g corrected exactly once in both modes; repeated invalid output raises typed error and is never cached; valid 20-row output uses one generation; failed/mutated reference fallback preserves rows/order/original nutrition; AI/caller/cache cancellation cleans up; cold/cache DTO equality; all ten represented cache micro fields including null/zero; valid per-100g micros scale for portion subset validation; raw negative micros reject before conversion; refinement bypass; mode/cohort/model/policy key separation; typed provider schema errors stop fallback; API composition supplies cache/deadline/model context; legacy provider potato/apple heuristics; authenticated/guest route contracts.

Retry regressions assert the prior candidate JSON and indexed validation details appear in the correction user prompt, while the trusted system prompt is unchanged. The offline golden impossible case now uses 101g fat in a stated 100g portion, and the evaluator verifies typed whole-request rejection. `test_offline_cli_writes_private_non_raw_report_without_overwrite` passes with this contract.

## Recommendations

- Retain rejection/fidelity/correction assertions and the existing provider potato/apple regressions.
- Default unit coverage gate, architecture checks, real provider quality/latency and staging save/read checks were not run by this tester. Local fixture passes do not establish provider accuracy or deployed speed.

## Unresolved questions

None. No remaining failure in assigned focused verification; no test process is running.
