---
phase: 3
title: "Cache-first before UoW on hot reads"
status: pending
effort: "M"
---

# Phase 3: Cache-first before UoW on hot reads

## Overview

Weekly budget (and some other reads) still open a DB connection to resolve timezone / auto_adjust / revision **before** returning a Redis hit. At scale, cache hits must not checkout from the pool.

## Implementation Steps

1. For weekly budget: after TDEE resolution (revision known), attempt Redis get **before** UoW when week_start + auto_adjust can be resolved without DB (or cache auto_adjust separately).
2. Align with daily-macros pattern: cache check as early as possible; exit without holding a session.
3. Audit hot app-open paths: timezone, hydration, daily macros, streak, weekly budget, profile.
4. Document expected cache key + revision fields so invalidation stays correct.

## Success Criteria

- [ ] Weekly budget Redis hit path opens zero SQLAlchemy sessions (or documents unavoidable exception)
- [ ] Unit tests assert no UoW enter on pure cache hit where designed
