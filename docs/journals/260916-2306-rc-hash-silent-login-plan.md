---
title: RC hash silent login plan
type: journal
date: 2026-09-16
---

# RC redeem hash silent login — plan

## Context

Approved design A (RC email → hash → custom token). User asked `/ck:plan --tdd`. Scope HOLD.

## Decision

Three TDD phases in `nutree_web_funnel/plans/260916-2306-rc-hash-silent-login`. Flag `WEB_FUNNEL_SILENT_LOGIN_ENABLED` default off. New identity helper; do not reuse `resolve()`. Session route extracted so `web_funnel.py` does not grow. Mobile tries session when signed out; 404 falls back to email overlay. No production flag flip.

## Next

Red-team recommended (auth + payments). Cook only after that: `--tdd`.
