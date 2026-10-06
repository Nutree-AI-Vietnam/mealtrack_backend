---
title: "Weekly Planner Grocery Note and Empty Dinner Repair"
description: "Diagnose and repair grocery day-note saves, an absent Tuesday dinner, and assignment into an empty dinner slot across backend and Flutter."
status: in_progress
priority: P1
branch: "delivery"
tags: [backend, flutter, weekly-planner, grocery]
blockedBy: []
blocks: []
created: "2026-10-06T11:42:19.621Z"
createdBy: "ck:plan"
source: skill
---

# Weekly Planner Grocery Note and Empty Dinner Repair

## Overview

Production data identifies a shared legacy-plan cause for the three reported symptoms. Repair the affected plan shape and stop the legacy writer. Backend persistence, recipe eligibility, calories, allergies, revision/idempotency, logged slots, pantry state, and Undo remain authoritative. Preserve unrelated changes in both repositories.

## Phases

| Phase | Name | Status |
|-------|------|--------|
| 1 | [Reproduce and isolate](./phase-01-reproduce-and-isolate.md) | Complete |
| 2 | [Repair write and slot flows](./phase-02-repair-write-and-slot-flows.md) | In progress |
| 3 | [Verify persistence and release](./phase-03-verify-persistence-and-release.md) | Pending |

## Dependencies

Coordinate with `../260930-1204-weekly-planner-release-blockers/` Phase 2, which already owns staging mobile persistence checks. This focused repair adds three regression scenarios; it does not block unrelated work in that plan. Backend: `/Users/alexnguyen/Desktop/Nut/mealtrack_backend`. Flutter: `/Users/alexnguyen/Desktop/Nut/nutree/nutree_ai`.

## Confirmed production cause (2026-10-06)

Read-only Neon checks found three draft `v1` plans for the week of 2026-10-05 with exactly 14 rows, seven at slot 0 and seven at slot 1. They were created on October 5 and 6 after the October 3 migration ran. Their slot-0 recipes are lunch-eligible and slot-1 recipes dinner-eligible. Current code expects 21 slots (breakfast 0, lunch 1, dinner 2). The latest malformed plan was created at 14:31 local time, matching the screenshot. Production has 751 planner-eligible dinner recipes; the selected Samyang recipe is eligible. The grocery day-note table exists and Alembic is at head. An old `v1` two-slot writer remained active after migration; its deployment identity remains to be confirmed.

Repair requires stopping the old writer, migrating only the verified `v1` two-slot shape, preserving slot IDs and logged links, then verifying fresh reads and both write paths. A migration at head is insufficient while an old writer can create new malformed plans.

## Scope and release boundary

A new CLI-generated data migration is required because the earlier migration cannot repair plans written afterward. No public API change is currently needed. Local tests and source inspection do not prove production deployment or device persistence.
