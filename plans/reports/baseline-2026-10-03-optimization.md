# Weekly planner baseline guards — 3 October 2026

Status: local instrumentation and contract/pool guards verified. Live baseline pending.
Source base: `cbc31522`, plus uncommitted optimization changes in the shared checkout.
Runtime: project `.venv`, Python 3.13.2, local macOS. No database connection or provider call made by this work package.

## Implemented and verified

- Engine arguments now follow actual pool class. Direct and Neon queue pools receive configured size, overflow, timeout, recycle and pre-ping; Neon NullPool receives no queue arguments. Existing policy defaults and worker count remain unchanged.
- Startup logs report actual constructed class, mode and bounded queue settings without connection URLs. Driver-boundary tests verify SSL translation, removal of asyncpg-incompatible URL parameters, and `prepared_statement_cache_size=0` for both Neon modes.
- Provider-neutral `src/planner_observability.py` exposes `planner_phase(...)` for `with` across awaits/CPU work; infrastructure retains compatibility re-exports. Fixed phase and operation allowlists reject dynamic labels; metric attributes contain only operation, phase and outcome. Debug events contain generated correlation ID plus duration. No prompt, ingredient, recipe/user ID, token or connection URL enters new telemetry.
- Request middleware uses monotonic timing and records `response_start` separately from `request_complete`. Completion includes body delivery and awaited background work; cancellation/error outcomes propagate. Context resets per request and isolates concurrent requests. Metrics export failures cannot change business outcomes.
- Contract guards preserve missing-plan GET auto-generation and grocery counts by default; explicit count false prevents count dispatch. Explicit read-only mode retains missing-plan 404 and existing-plan reads. Unauthenticated current-plan requests dispatch no planner query or generation command.

## Local evidence

| Evidence | Result | Limits |
|---|---|---|
| Scoped config/policy/phase/HTTP contract/middleware/error-owner tests | 108 passed in 3.63 s, 4 warnings | Unit/API fixture tests; no PostgreSQL, HTTP/provider latency proof |
| Ruff for owned changed source/tests | Passed | Focused files |
| Project Python compileall for owned changed source/tests | Passed | Syntax/import compilation only |
| Empty-phase overhead, 100,000 iterations, no-op connector | 112.514 ms phase loop vs 1.989 ms empty loop; added 1.105 us/phase | Local single microbenchmark; excludes export, database, HTTP and real request workload |
| Configured vs actual QueuePool test matrix | Size 7, overflow 2, timeout 17 s, recycle 91 s, pre-ping true; 3-worker capacity 27 | Constructed engines, no connections opened; values are explicit test settings, not deployed values |
| Neon NullPool test matrix | No queue kwargs, reported capacity 0; cache disabling delivered to driver | Zero means no SQLAlchemy pool bound, not zero PostgreSQL capacity |

Validation command:

```bash
.venv/bin/pytest tests/unit/infra/database/test_config_async.py tests/unit/infra/database/test_connection_policy.py tests/unit/infra/observability/test_planner_metrics.py tests/unit/api/test_weekly_meal_planner_contract.py tests/unit/api/middleware/test_request_logger.py tests/unit/api/test_single_owner_exception_logging.py --no-cov -q
```

## Integration and pending live gates

- Lead owns route/service/UoW phase placement. Supported labels: auth, budget, checkout, plan_sql, catalog_sql, mapping, selection_cpu, lock, commit, redis, localization, admission, ai_attempt, background, response_start, request_complete, service, response_projection. Middleware supplies operation/correlation automatically for known planner/recipe routes.
- Pin staging/production backend/client revisions, actual worker/pool/provider settings, DB geography and catalog/data distributions before reporting endpoint baselines.
- Measure saved/missing/confirmed plans, cold/warm en/vi, count true/false, query/row/byte counts, cache/provider waits, CPU/event-loop delay, lock/checkout waits, memory and agreed concurrency. Capture p50/p95/p99 and distinguish 429, timeout and 5xx.
- Compare telemetry enabled/disabled with the deployed connector. This implementation records each instrumented phase; no live sampling/export-overhead evidence exists yet.
- Existing provisional latency budgets remain proposals. No owner ratification or deployed success inferred from this report.
- Pool correction may raise actual Neon queue capacity to configured values previously omitted; account for API processes and other workers against deployed Neon limits before rollout. No worker resize or deployment performed.
- Full CI, architecture checks, PostgreSQL correctness/load tests and reviewer signoff remain lead/release gates.

Docs impact: minor. This report documents measured local guards and outstanding live evidence; it does not mark the complete baseline phase finished.

Unresolved questions: live environment/data matrix, provider export overhead and agreed SLOs.
