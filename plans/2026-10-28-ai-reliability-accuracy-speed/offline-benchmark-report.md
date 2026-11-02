# Offline nutrition lookup benchmark

Run with `PYTHONPATH=. .venv/bin/python plans/2026-10-28-ai-reliability-accuracy-speed/benchmark_lookup_dedup.py` from the backend root.

This compares the legacy three-way `calculate_meal_macros` path with `calculate_meals_macros`. Each request has three recipes with four 80–82 g ingredients each. The repeated workload uses the same four identities in every recipe; the distinct workload uses twelve identities. The fake T2 provider takes 20 ms per lookup and has a shared concurrency limit of four. Cold runs start with an empty cache; warm runs contain valid per-100g T2 cache entries. Each row is 30 requests. Timings are lookup orchestration only and exclude model generation, real database/provider latency, and UI readiness.

| Workload | Cache | Path | p50 ms | p95 ms | Provider calls/request | DB batches/request |
| --- | --- | --- | ---: | ---: | ---: | ---: |
| Repeated | Cold | Single-meal | 63.24 | 65.42 | 12 | 3 |
| Repeated | Cold | Batched | 21.25 | 22.34 | 4 | 1 |
| Repeated | Warm | Single-meal | 0.39 | 0.80 | 0 | 0 |
| Repeated | Warm | Batched | 0.25 | 0.45 | 0 | 0 |
| Distinct | Cold | Single-meal | 63.07 | 64.40 | 12 | 3 |
| Distinct | Cold | Batched | 62.84 | 63.76 | 12 | 1 |
| Distinct | Warm | Single-meal | 0.52 | 1.12 | 0 | 0 |
| Distinct | Warm | Batched | 0.40 | 0.79 | 0 | 0 |

For the repeated cold-cache fixture, batching reduced fake-provider calls from 12 to 4 and p95 lookup time by 65.8%. Distinct cold-cache inputs retained 12 provider calls and had similar latency, while reducing database batch operations from three to one. Warm-cache latency was below 1.2 ms in both paths. These synthetic measurements demonstrate deduplication behavior under the stated concurrency cap; they do not establish production latency or nutrition accuracy. Run a staged provider-backed comparison before setting rollout targets.
