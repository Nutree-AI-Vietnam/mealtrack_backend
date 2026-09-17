---
title: RC hash silent login cook
type: journal
date: 2026-09-17
---

# RC redeem hash silent login — cook

## Context

TDD cook of `nutree_web_funnel/plans/260916-2306-rc-hash-silent-login`. Phases 1–3 implemented. Flag stays off.

## What happened

Phase 3 goldens: Dart now hashes the nested `url` string as-is (TS). Three shared SHA-256 vectors match. Docs cover flag-off overlay, flag-on silent, kill-switch. SIT checklist written, not executed.

Code review FAIL: `_usedSilentLogin` was in-memory, so cold-start custom + preflight 403 stayed signed in. Fixed by `isCustomAuthSession` (`signInProvider == custom`) plus restore/Google 403 tests. Re-review PASS.

## Decisions

- Dart follows TS raw nested URL; do not rewrite correlated web hashes.
- Kill-switch keys off ID-token `custom`, not process-local memory.
- Production `WEB_FUNNEL_SILENT_LOGIN_ENABLED` remains false.

## Next

Staging SIT from `reports/sit-checklist-rc-hash-silent-login.md` in a slot that is not Phase 8 passwordless. Do not flip prod. Commit when asked.
