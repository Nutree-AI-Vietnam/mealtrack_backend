---
title: "Weekly Planner Release Blockers"
description: "Guard nullable catalog ingredient IDs and close the remaining mobile weekly-planner validation gates."
status: in_progress
priority: P1
branch: "feature/weekly-meal-plan-loading"
tags: [backend, mobile, release, weekly-planner]
blockedBy: []
blocks: []
created: "2026-09-30T05:04:27.982Z"
createdBy: "ck:plan"
source: skill
---

# Weekly Planner Release Blockers

## Overview

Close the confirmed backend ingredient-ID crash and defensive repository-input gap, then verify the remaining weekly-planner mobile behaviors against persisted staging state. Keep backend and Flutter changes in their own repositories. Backend nutrition and persisted plan/grocery state remain authoritative.

## Phases

| Phase | Name | Status |
|-------|------|--------|
| 1 | [Backend Ingredient Identity Guards](./phase-01-backend-ingredient-identity-guards.md) | Complete |
| 2 | [Mobile Weekly Planner Validation](./phase-02-mobile-weekly-planner-validation.md) | In Progress |

## Dependencies

- Compatibility references: `plans/260921-0000-weekly-meal-planner-backend/` and `plans/260720-2133-meal-recommendation-ranking-v2/`.
- Coordinate the catalog statistics fix with ranking V2 Phase 4; this plan covers the null-ID release blocker and cross-surface validation, not a second ranking redesign.
- No schema or API contract change is expected. If implementation shows otherwise, update the plan and contract tests before proceeding.

## Integration Boundaries

- Backend work belongs in `/Users/alexnguyen/Desktop/Nut/mealtrack_backend`; Flutter work belongs in `/Users/alexnguyen/Desktop/Nut/nutree/nutree_ai`.
- Keep the mobile grocery projection optimistic and local-only until reconciled with the backend. Do not move nutrition arithmetic or calorie authority to the client.
- Unit/widget checks, staging API observations, and authenticated device observations are separate evidence. Local checks do not prove staging persistence or latency.

## Success Criteria

- [ ] Nullable or invalid ingredient IDs cannot crash catalog IDF construction or trigger malformed repository queries.
- [ ] Ranking remains deterministic when a catalog meal contains an unmapped ingredient.
- [ ] Cache/reopen, grocery projection and latency, first-render macros, rounded checkbox, log-portion bar, and return navigation checks pass.
- [ ] Mutations survive a fresh backend read and screen reopen in staging; timings are recorded against the applicable release SLO.
- [ ] No API/schema or client nutrition-authority regression is introduced.

## Validation Evidence

- Backend unit suite passed: 3,329 tests; focused nullable-ID and micronutrient regressions passed: 50 tests.
- Backend Ruff check and formatting passed for all changed implementation and test files.
- Flutter full suite passed: 3,785 tests; full `flutter analyze` passed with no issues.
- Authenticated staging persistence, physical-device behavior, and live latency percentiles have not been measured in this local release pass. Keep Phase 2 open until those checks are performed against the deployed revision.

## Open Questions

- None for the scoped checks. Use the existing release latency SLO; if none is documented, report measured timings without inventing a pass threshold.
