---
date: 2026-10-03
scope: durable-catalog-preparation
status: done
---
# Durable catalog preparation implementation and verification

## Summary

Implemented typed preparation outcomes, durable jobs/versioned translations, bounded SQL claim/lease/retry repository, source-first pure nutrient computation, dedicated worker and restartable CLI/backfill. Integrator owns schema generation, source producers, registry/UoW and API/read cutover.

**Final validation:** all `tests/integration/postgres`: **37 passed in 18.07s**. After explicit upsert timestamps and aggregate metrics, preparation PostgreSQL cases reran **13 passed in 7.03s**; pure computation plus legacy enrichment (17) and weekly service (35) reran together **52 passed in 1.64s**. Scoped Ruff, compileall and both CLI `--help` imports pass. Dedicated local PostgreSQL only; all database URL aliases point to the same isolated database. No overlapping PostgreSQL suites.

## Implementation

- `CatalogPreparationJobORM`: task/recipe/facet/locale-sentinel/contract uniqueness, finite attempts, pending/running/retry_wait/succeeded/failed/superseded states, available-at, leased token and sanitized error code. `CatalogRecipeTranslationORM`: exact dependency/locale/contract presentation fields. No speculative nonunique index added.
- `AsyncCatalogPreparationRepository`: caller commits separate claim/heartbeat/final transactions. Global provider admission uses one short PostgreSQL advisory lock plus count of unexpired running claims; `FOR UPDATE SKIP LOCKED` claim, renewed token, finite exponential jitter retries and expired-lease recovery.
- Pure micronutrient computation opens no UoW/claims and commits no result. Exact linked USDA values win over estimates, canonical nutrient updates are staged, all fields validated, failures typed and provider diagnostics excluded from persisted jobs.
- Prepared translation computation groups authored English/Vietnamese text, batches bounded fields through the existing neutral service, and admits only complete translated outcomes. Oversized/unsupported/unconfigured cases are explicit failures.
- Worker claims only into free lanes; provider deadline 60s within lease 120s, process concurrency 2 and global capacity 4 by default. Input load checks current task facet/dirty flags and job token under publication lock. It renews immediately after snapshot commit before provider work, then heartbeats; provider execution has no checked-out DB connection. Lost leases cancel computation and final token/expiry checks reject stale commits.
- Final result transaction orders publication fence before job/source locks. It validates the source facet, stages intentional USDA changes, rebuilds their resulting version, persists overlay and completes job atomically. Projection jobs rebuild current inputs and queue exact facets without providers.
- Versioned micros reads use facet+contract authority; unverifiable legacy rows remain pending. Cosmetic content-hash changes preserve matching overlays. Publishing an existing facet updates its row without rewriting the legacy hash or colliding with legacy uniqueness.
- Worker and backfill run as separate processes: `python -m scripts.catalog_preparation_worker` and `python -m scripts.backfill_catalog_preparation`. Worker uses a reserved queue pool, no overflow, preserved asyncpg/PgBouncer settings and a separate OpenAI provider with zero SDK retries. SQL polling recovers lost optional wakeups; bounded keyset backfill is idempotent.
- Optional aggregate metrics expose claims, queue age, outcomes and processing duration with fixed task/locale/outcome labels. Metric failures cannot change job behavior; deployment throughput remains unmeasured. Upsert timestamps explicitly use `clock_timestamp`; SQL conflict updates do not invoke ORM column `onupdate`.

## PostgreSQL recovery evidence

Thirteen preparation cases pass:

1. Duplicate micro/translation jobs and non-null empty locale sentinel deduplicate.
2. Crashed expired lease reclaims with a new token; old heartbeat/publication fail.
3. Retry wait has bounded backoff; exhausted poison jobs remain failed.
4. Six concurrent replica claims admit exactly the configured two global slots.
5. Invalid incomplete/nonfinite ready output is terminal; exhausted expired running jobs sweep to failed.
6. Translation/job changes remain invisible before commit; rollback restores claim; recommit publishes both and replay cannot publish again.
7. Changed/dirty dependency supersedes old claims, hides stale overlays and retains durable projection work.
8. Cosmetic content-hash changes preserve current facet readiness and avoid uniqueness collisions.
9. Intentional USDA hydration publishes under the rebuilt resulting facet in the same transaction.
10. Provider computation begins with zero DB checkouts.
11. Snapshot blocked behind publication past lease expiry and reclaim never reaches the old provider.
12. Snapshot with one second of lease left renews immediately before provider admission.
13. Projection task rebuild/enqueue completes without calling providers.

Full PostgreSQL run also covers main's three short-generation transaction cases, catalog parity/locks/facets/direct SQL invalidation, existing import/degraded/E2E and eight weekly planner persistence/race tests.

The compact AI proposal unit case forbids full catalog reads, checks compact provider context, and checks exactly the two selected recipe IDs hydrate in a fresh second UoW to produce canonical groceries. Unselected recipe details never load.

## Corrections during verification

- Pure USDA fixture originally used `value`; canonical normalized extra-nutrients shape uses `amount` and units. Corrected fixture; production computation retained.
- Pantry assertion fixture originally chose recipes by random UUID order. Sorted stable catalog key; canonical total is reproducible.
- Review identified lease time lost while waiting on publication. Added claim validation after publication acquisition/after snapshot load plus immediate pre-provider renewal. Clock-sensitive checks/extensions use PostgreSQL `clock_timestamp`, avoiding stale transaction-start time after waits.

## Boundaries and next steps

- No external provider calls, live worker process kill, deployed throughput/queue age, end-to-end API latency/p95 or production readiness claim. Crash tests model committed leases plus failed/rolled-back final transactions on real PostgreSQL.
- Integrator completed generated schema migration cycles and reports full unit gates separately; retain exact local versus deployed evidence boundaries.
- Runtime flags and seven-locale API fallback are integrator-owned. Global capacity must be configured consistently across preparation replicas; defaults describe local code policy, not measured deployment settings.
- Docs impact: minor; catalog owner/integrator own deployment/runbook updates.

## Unresolved questions

None within assigned implementation/test scope. Live capacity, readiness and canary evidence remain rollout gates.
