---
phase: 6
title: "Verification Documentation And Rollout"
status: pending
priority: P1
effort: "2-4d"
dependencies: [2, 3, 4, 5]
mode: deep
---

# Phase 6: Verification Documentation And Rollout

## Overview

Verify the full weekly planner against the live architecture, document the
contract, and provide a reversible rollout/rollback gate. This phase proves
backend behavior only; mobile, authenticated staging, and content readiness
remain separate evidence boundaries.

## Context Links

- CI-aligned commands in `AGENTS.md` and `docs/testing-standards.md`
- Docs: `docs/api-endpoints.md`, `docs/database-guide.md`,
  `docs/system-architecture.md`, `docs/codebase-summary.md`
- Existing feature flags: `src/infra/services/feature_flag_service.py`
- Migration CLI: `scripts/development/migrate.sh`

## Requirements

- Validate Alembic graph, generated migration upgrade/downgrade/upgrade on a
  fresh PostgreSQL database, model imports, and 14-slot constraints.
- Run focused unit tests, migration tests, architecture/import-linter checks,
  Ruff, mypy, `git diff --check`, and the default `pytest tests/unit --cov=src
  --cov-fail-under=65` gate. Do not use bare unscoped pytest.
- Run explicit PostgreSQL integration tests for owner isolation, concurrent
  generate/PATCH/pantry/log, idempotency races, and rollback behavior when
  `TEST_DATABASE_URL` is available.
- Verify OpenAPI paths/DTOs/error responses and preserve existing catalog,
  recommendation, meal-suggestion, weekly-budget, and meal-log contracts.
- Add/update docs for storage names, API semantics, nutrition authority,
  localization, allergy disclosure, import fields, and rollout/rollback.
- Use the existing feature-flag system for a default-off write gate if the
  product rollout requires it; reads may remain disabled until content is ready.

## Architecture

The release gate is layered: offline/unit proves deterministic domain and
contracts; migration tests prove schema; PostgreSQL proves transactions and
concurrency; authenticated staging proves deployed routing, provider behavior,
and real catalog/content. No synthetic test is described as staging proof.

## Related Code Files

| Action | Files |
|---|---|
| Modify | `docs/api-endpoints.md`, `docs/database-guide.md`, `docs/system-architecture.md`, `docs/codebase-summary.md` |
| Modify if required | `docs/project-changelog.md`, `docs/development-roadmap.md`, or the repository's active archive/progress equivalents |
| Add | `tests/integration/postgres/test_weekly_meal_planner_e2e.py` |
| Add | `tests/migrations/test_weekly_meal_planner_migration.py` |
| Add | `tests/performance/` aggregate-only planner evidence if a staging gate is approved |
| Verify | all files from Phases 1-5 and generated migration |

## Implementation Steps

1. Build a requirement-to-test traceability matrix and run the focused gates.
2. Validate fresh DB migration lifecycle, single Alembic head, FK/check/index
   behavior, and absence of legacy `meal_plans` resurrection.
3. Run deterministic catalog/weekly fixtures at representative catalog sizes;
   record aggregate latency/query counts without secrets or user IDs.
4. Run PostgreSQL race tests and authenticated staging smoke tests separately.
5. Verify disabled/enabled rollout behavior, rollback by disabling writes, and
   no orphan slots/meals/pantry rows after failed operations.
6. Update docs and perform a whole-plan stale-term sweep for generic tables,
   client calories, direct AI mutation, and unsupported allergy claims.

## Tests Before

- All Phase 1-5 focused tests and OpenAPI snapshots are green.
- Migration graph is one head and generated migration has a real downgrade.

## Tests After / Release Matrix

| Boundary | Required evidence |
|---|---|
| Domain | Deterministic generation, filters, grocery/unit math, portion scaling |
| Application | CQRS registration, idempotency, error conversion, no leaked sessions |
| Database | Upgrade/downgrade, owner FKs, 14 slots, unique coordinates, races |
| HTTP | Auth, OpenAPI, response shapes, existing route regressions |
| Provider | AI structured-output failure/timeout and translation fallback |
| Staging | Deployed revision, authenticated planner smoke, real catalog, rollback |

## Function / Interface Checklist

- Every new command/query has one event-bus registration and fresh UoW copy.
- Every new public endpoint appears in OpenAPI with auth and error models.
- Every persistence mutation has owner predicate, idempotency, rollback, and
  integration-event behavior specified.

## Dependency Map

- Depends on all implementation phases.
- Staging/content gates may remain open without blocking local plan completion.
- Final rollout is blocked by unresolved Phase 1 product gates or missing
  reviewed catalog corpus.

## Success Criteria

- [ ] All offline and local CI-aligned gates pass without suppressions.
- [ ] PostgreSQL and authenticated staging evidence is clearly separated and
  recorded with environment/revision metadata.
- [ ] Docs and OpenAPI match the final implementation exactly.
- [ ] Rollback is tested by disabling the planner write gate; existing features
  continue to work.

## Risk Assessment

The main release risk is confusing local mocks or OpenAPI presence with live
provider/database readiness. Keep evidence boundaries explicit and do not
enable writes until schema, catalog corpus, allergy policy, and staging smoke
tests are complete.

## Security Considerations

Redact tokens, DSNs, raw prompts, source URLs when private, and user IDs from
reports. Use aggregate metrics only; verify unauthorized IDs return the same
safe not-found behavior.

## Next Steps

After user approval and product-gate answers, execute with `/ck:cook` using the
absolute plan path; keep mobile/device proof and production enablement separate.
