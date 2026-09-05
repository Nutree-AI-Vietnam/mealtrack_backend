---
phase: 3
title: Verify unit tests and PR
status: completed
effort: S
---

# Phase 3: Verify unit tests and PR

## Overview

Prove connection policy + focused tests still pass; open draft PR against `delivery`.

## Implementation Steps

1. Run `pytest tests/unit/infra/database/`
2. Commit/push on `feature/stage2-neon-pooler-ab72`
3. Open draft PR (separate from Stage 1)

## Success Criteria

- [ ] Database unit tests green
- [ ] Branch pushed; PR opened or create-blocked noted
