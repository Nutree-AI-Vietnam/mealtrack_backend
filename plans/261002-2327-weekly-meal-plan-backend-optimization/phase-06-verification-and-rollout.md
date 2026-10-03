---
phase: 6
title: "Verification And Rollout"
status: in_progress
effort: ""
priority: P1
dependencies: [1, 2, 3, 4, 5]
---

# Phase 6: Verification And Rollout

## Overview

Integrated local regressions, PostgreSQL concurrency/degradation and migration rollback verified; deployment/provider/mixed-load/device gates remain pending.

## Context Links

- [Solution](./solution-document.md), [baseline](./phase-01-baseline-and-contract-guards.md), [read](./phase-02-narrow-reads-and-batching.md), [catalog](./phase-03-catalog-projections-and-generation.md), [AI](./phase-04-ai-request-policy.md), [worker](./phase-05-durable-catalog-preparation.md).
- [Testing standards](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/docs/testing-standards.md), [DB/API rules](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/docs/standards/db-api.md), [mobile release gates](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/plans/260930-1204-weekly-planner-release-blockers/plan.md).

## Key Insights

- Unit/SQLite timing, a green host, and a branch do not prove PostgreSQL behavior, deployed revisions, device UX, or persisted reopen/Undo.
- Canonical inventory has nine routes; load uses eight workflow groups by grouping recipe list+detail. Deprecated enrichment remains a separate compatibility regression.
- Target percentiles/provider/worker limits are proposed until baseline and owner review; flag rollback retains compatible source tables and fallback readers.

## Requirements

- Migrations use CLI generation, central model registry, one Alembic head, valid downgrade, bounded idempotent backfill, expand/dual-read/cutover flags and verified rollback.
- Test real PostgreSQL SQL/plans/pool/locks; preserve deterministic/hard allergy/diet/publication/nutrition/calorie parity and all owner/revision/replay/log/pantry contracts.
- Exercise degradation/recovery, mixed load, limited authenticated staging canaries and supported legacy GET behavior; preserve existing open mobile gates.

## Architecture

Release in reversible slices: narrow reads/pool correction → compact projections/backfill/dual-read → generation/AI cutover → worker/translation preparation/read-only GET cutover. Gate each flag on parity, capacity and error evidence. Roll back flags before schema contraction; keep durable jobs/results and source data compatible during rollback.

## Related Code Files

- Extend: [route contracts](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/tests/unit/api/test_weekly_meal_planner_contract.py), [plan/grocery/logging suites](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/tests/unit/app/services/test_weekly_meal_plan_service.py), [generator suite](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/tests/unit/domain/services/weekly_meal_planner/test_weekly_plan_generation_service.py).
- Extend: [catalog PostgreSQL coverage](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/tests/integration/postgres/test_meal_catalog_e2e.py), [pool assertions](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/tests/unit/infra/database/test_config_async.py), [model registry tests](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/tests/unit/infra/database/test_model_registry_metadata.py), [load harness](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/tests/performance/locust_meal_catalog.py).
- Use: [migration CLI](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/scripts/development/migrate.sh), [migration entry point](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/migrations/cli.py), [architecture tests](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/tests/architecture).
- Modify as needed: relevant deployment flags/settings, evergreen DB/API/testing docs and runbook; record evidence under this plan. Delete: none in initial release; defer schema contraction.

## Implementation Steps

1. Assign nonoverlapping implementation/test/integrator/review owners and merge dependencies 1→2→3, then 4/5 interface work, then combined release; preserve unrelated checkout changes.
2. Compile each changed module using project `.venv` Python 3.13, then run focused route/service/repository/domain/AI/worker regressions before full CI-aligned checks.
3. Run `.venv/bin/pytest tests/unit --cov=src --cov-fail-under=65`, `lint-imports`, architecture tests, Ruff and type checks appropriate to changed modules; do not use unscoped bare pytest.
4. Run PostgreSQL integration with `.venv/bin/pytest tests/integration/postgres -o addopts="" -m integration`; supply secure `TEST_DATABASE_URL`, verify actual catalog SQL/bytes/EXPLAIN and queue/NullPool engine settings.
5. Exercise same-key/different-key missing-week generation, replay conflicts, publisher exclusive/generation shared lock races, dirty browse totals, ordered slot locks/log versions, swap-versus-log, duplicate logs and stale AI apply (base_revision→expected_revision); preserve optional generic PATCH compatibility and inspect fresh committed plan/ledger/log state.
6. Compare canonical/projection nutrition and hard constraints across unmapped ingredients, allergens, publication/nutrition readiness, Unicode search, dependency edits and backend-derived calories.
7. Exercise Redis disabled/down/slow, provider errors/deadline/cancellation, failed admission, kill worker at claim/provider/result boundaries, typed computation failures, atomic overlay/job commit, inner-claim migration, dependency facet/self-update versioning, stale tokens, lease reclaim, duplicate delivery, max-attempt failure and authorized replay.
8. Rehearse migration upgrade/backfill retry/dual-read cutover and downgrade/flag rollback on real PostgreSQL snapshots; validate registry/head/indexes and no loss of user plans/logs/pantry state.
9. Run eight mixed-load groups: current GET, generate, plan PATCH, AI proposal, grocery GET, grocery PATCH, slot log, recipe list+detail. Start non-AI at 50 users/10 minutes, then measured peak/headroom; run generation storms separately and sweep locales/cache/count/catalog conditions.
10. First isolate providers with deterministic port doubles while preserving real PostgreSQL/domain work; then run bounded staging provider canaries/AI quality evaluation. Report API/worker combined capacity, event-loop lag, queue age and readiness.
11. Deploy by flags with exact revision/schema/config recorded; compare baseline p50/p95/p99/errors/bytes/checkout/lock/provider metrics to owner-approved gates and rehearse rollback.
12. Validate supported old-client GET defaults and explicit current-week click/persist/reload; perform authenticated fresh-read/screen reopen checks for full/zero/partial pantry, flags, log and Undo. Link mobile/device evidence to the existing release plan.
13. Update evergreen docs/runbook and report local checks, deployed canaries, worker readiness and mobile gates separately. Mark phases complete only when their required evidence exists.

## Validation Commands

```bash
.venv/bin/ruff check src/ tests/
.venv/bin/ruff format --check src/ tests/
.venv/bin/mypy src/
.venv/bin/lint-imports
.venv/bin/pytest tests/architecture -o addopts=""
.venv/bin/pytest tests/unit --cov=src --cov-fail-under=65
.venv/bin/pytest tests/integration/postgres -o addopts="" -m integration
```

## Todo List

- [ ] Pass focused/full CI, real PostgreSQL and migration/backfill/rollback gates.
- [ ] Prove races, hard constraints, pantry/log/Undo persistence and old-client compatibility.
- [ ] Pass Redis/provider/worker failure recovery and isolated mixed-load evaluation.
- [ ] Complete limited staging canaries and link remaining device/mobile evidence.

## Success Criteria

- [ ] No regression in paths/shapes/calories/eligibility/ownership/revisions/replay/logged slots; fresh reads prove committed state.
- [ ] SQL/bytes/lock/checkout targets and percentile/error/load gates meet agreed baseline-derived thresholds at documented capacity.
- [ ] Schema flags/backfill/rollback and worker lease/fencing/replay pass without data loss or duplicate authoritative results.
- [ ] Canary evidence identifies deployed revision and real provider/config; uncompleted mobile/device gates remain explicitly open.

## Risk Assessment and Security

- Mixed load can pollute real accounts or saturate providers: use dedicated staging identities, bounded rates/quotas, and sanctioned cleanup.
- Synthetic provider success is not quality/latency proof: separate isolated infrastructure load from live quality/canaries.
- Store aggregate timing/error counts; never commit credentials, tokens, raw prompts, user meals or DB URLs. Restrict operator replay and migration permissions.

## Unresolved Measurements and Next Steps

- Unknown until execution: final SLOs, safe concurrency, worker/DB/provider capacity, backfill duration, locale readiness and actual device/persistence timing.
- GET deprecation needs supported-client rollout and observed usage evidence; retain compatibility until then. No release or mobile completion is implied by this plan.

## Verified Local Progress — 2026-10-03

- Final CI-aligned unit snapshot:3452 passed,56 warnings,79.51% coverage against65% gate. Subsequent type-only fixes passed18 relevant tests. PostgreSQL final full suite:40 passed in20.71s.
- Changed Python Ruff/format, compileall and all4 import contracts pass. Three architecture failures are verified in HEAD: direct route commits, repository transaction allowlist, and111 domain services against stale46 cap.
- Full mypy remains red:1048 errors in160 files versus1051 in162 at HEAD; normalized comparison finds zero introduced error instances. Focused new modules/projection typing passes.
- Both generated migrations were upgraded/downgraded/re-upgraded on dedicated PostgreSQL14. A nonempty rollback rehearsal preserves14 slots, plan, pantry quantities/flags and grocery day lines exactly.
- Eight-group authenticated load harness is prepared and scope-tested. Dedicated staging accounts/fixtures,50-user10-minute mixed load, real provider canaries, Neon/pooler RTT, queue readiness and physical-device reopen/Undo remain pending. No production migration or deployment ran.

Evidence: [integrated report](../reports/implementation-2026-10-03-weekly-planner-optimization.md). Open composite acceptance criteria stay unchecked; local implementation does not establish release completion.
