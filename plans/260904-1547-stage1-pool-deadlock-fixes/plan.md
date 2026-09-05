---
title: Stage 1 pool deadlock and UoW serialization fixes
description: >-
  Stop Sep 4 QueuePool crashes by removing nested connection checkouts and
  shared AsyncUnitOfWork locks; foundation for 10k concurrency stages.
status: completed
priority: P1
branch: feature/stage1-pool-deadlock-fixes-ab72
tags:
  - concurrency
  - deadlock
  - uow
  - pool
blockedBy: []
blocks: []
created: '2026-09-04T08:47:35.748Z'
createdBy: 'ck:plan'
source: skill
---

# Stage 1 pool deadlock and UoW serialization fixes

## Overview

Sep 4 crashes were **not traffic overload**. Two software bugs exhaust the SQLAlchemy pool under modest concurrency:

1. **Nested checkout deadlock** — `GetWeeklyBudgetQueryHandler` holds Connection 1, then calls `GetUserTdeeQueryHandler` which needs Connection 2. N concurrent requests can fill the pool with holders waiting for a second connection → `QueuePool limit reached` after 10s.
2. **Shared singleton `AsyncUnitOfWork`** — ~28 handlers registered with `uow=AsyncUnitOfWork()` share one instance and its `asyncio.Lock()`, serializing unrelated requests.

Daily macros already uses the safe pattern (TDEE **before** UoW). This plan applies that pattern and removes shared UoW instances.

**Do not start Stage 2 (Neon pooler / more workers) until Stage 1 is done** — more capacity makes nested deadlocks worse.

## Roadmap context

| Stage | Users | This plan? | Solution |
|-------|-------|------------|----------|
| **1 (Now)** | 10–500 | **Yes** | Un-nest handlers, UoW factory, cache-first |
| 2 | 500–2k | Later | Completed |
| 3 | 2k–10k+ | Later | Completed |

## Phases

| Phase | Name | Status |
|-------|------|--------|
| 1 | [Unnest weekly budget TDEE](./phase-01-unnest-weekly-budget-tdee.md) | Completed |
| 2 | [Convert singleton UoW to factory](./phase-02-convert-singleton-uow-to-factory.md) | Completed |
| 3 | [Cache-first before UoW on hot reads](./phase-03-cache-first-before-uow-on-hot-reads.md) | Completed |
| 4 | [Verify and document Stage 2 prerequisites](./phase-04-verify-and-document-stage-2-prerequisites.md) | Completed |

## Dependencies

- Pattern reference: `get_daily_macros_query_handler.py` (TDEE before UoW)
- Pool docs: `docs/troubleshooting.md` (QueuePool / neon_pooler)
- Related research: `plans/260610-1404-async-boundary-hygiene-and-manual-save-timing/research/asyncio-usage-research.md`
