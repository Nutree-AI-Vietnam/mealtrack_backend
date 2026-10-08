---
title: "NM-611 Food Search Speed and Local Foods"
description: "Make manual food search fast (local-first, budgeted provider, caches) and surface Vietnamese/local catalog foods."
status: in_progress
priority: P1
branch: "fix/nm-611-food-search-speed-local-foods (backend) / fix/nm-611-food-search-speed (mobile)"
tags: [backend, flutter, food-search, performance, i18n]
blockedBy: []
blocks: []
created: "2026-10-07T08:58:00.000Z"
createdBy: "claude"
source: jira NM-611
---

# NM-611 Food Search Speed and Local Foods

## Overview

Baseline (Render logs, 2026-10-06/07): search p50 ≈5–6s, p95 ≈9.5–12.8s, ≈1% cache hits.
Root causes and evidence: `../reports/debugger-261007-1515-nm-611-food-search-latency-and-local-foods-report.md`.

Targets (ticket AC): debounce, p95 ≤300ms backend local path / ≤1s end-to-end,
recents + favorites locally, session cache, no top-5 en/vi relevance regression, offline state.
Out of scope: ranking redesign, new data sources, UI redesign.

## Phases

| Phase | Name | Status |
|-------|------|--------|
| 1 | [Backend search path](./phase-01-backend-search-path.md) | Done |
| 2 | [Mobile search UX](./phase-02-mobile-search-ux.md) | Done |
| 3 | Verify (unit, Postgres integration, analyze, review) | Done locally; CI `postgres-integration` on the PR is the merge gate |

## Key decisions

- Accent-folded STORED generated column on `food_reference` + trigram index; Python fold mirrors SQL fold.
- Vietnamese searches read VN + US + global regions and match the original text, not only the English translation.
- Eligibility status gated in SQL only (the Python `valid` filter that disagreed with it is gone); the unbounded OFFSET loop becomes at most 4 bounded passes, needed only when rows fail the read-time integrity check.
- Provider (FatSecret) only when local results are thin, under a time budget; late results warm the cache.
- Translation results cached per text; integrity context cached for a few seconds.
- Catalog generation (part of every search cache key) bumps only when a cached page could show the changed row: not for new rows, quarantined rows, or policy-version-only changes; the serving trigger bumps at most once per shown reference per transaction.

## Repositories

- Backend worktree: `/Users/alexnguyen/Desktop/Nut/worktrees/mealtrack_backend-nm611-food-search` (PR → `delivery`)
- Mobile worktree: `/Users/alexnguyen/Desktop/Nut/nutree/worktrees/nutree_ai-nm611-food-search` (PR → `main`)
