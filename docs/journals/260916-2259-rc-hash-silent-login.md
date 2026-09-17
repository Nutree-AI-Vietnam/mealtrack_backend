---
title: RC hash silent login design
type: journal
date: 2026-09-16
---

# RC redeem hash → silent login — design

## Context

Web funnel still makes buyers retype email after RC’s redeem mail. Hash → lead.email already exists. Goal: one tap, attach existing Google/Apple, no second Nutree mail.

## What happened

Prior report A (Nutree/Resend magic link) superseded. Locked: RC email is the only activation tap. Custom token from hash, then current preflight/redeem/finalize. Allow provider `custom`. Do not flip `WEB_FUNNEL_LEGACY_CLAIM_ENABLED`. Wrong signed-in user does not auto-switch.

## Decisions

- Approach A approved. Report-only this round; no plan.
- New identity helper — `resolve()` still rejects Google/Apple.
- Exchange returns token only, never email.
- Feasible only with new endpoint + allowlist + coordinator skip of `emailEntry`. Not a config toggle.

## Next

Plan later (`/ck:plan --tdd` when ready). Do not implement until then.

Report: `nutree_web_funnel/plans/reports/260916-2259-rc-hash-silent-login.md`
