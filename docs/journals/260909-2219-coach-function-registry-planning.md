---
title: Coach function registry planning
type: journal
date: 2026-09-09
---

# Coach function registry — plan

## Context

Architecture review said keep one orchestrator. User wanted to drop intents and scale with tools.

## Decision

Tools are the catalog. Intents stay as chip aliases this round. Pure agent (model routes every chip) rejected — that raises cost as skills grow.

First slice: registry + skip retrieve on known actions + additive POST `tool`. Do not delete `ChatIntent`. Chip `action` strings stay aliases for mobile.

## Next

Plan: `plans/260909-2219-coach-function-registry/plan.md`
