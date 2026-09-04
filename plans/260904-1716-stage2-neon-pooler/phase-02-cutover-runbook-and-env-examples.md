---
phase: 2
title: "Cutover runbook and env examples"
status: pending
effort: "S"
---

# Phase 2: Cutover runbook and env examples

## Overview

Operators need a copy-paste cutover checklist. Do not auto-set production secrets
in this PR — document Render/Neon steps only.

## Implementation Steps

1. Add `docs/runbooks/neon-pooler-cutover.md` (prechecks, env matrix, canary, rollback).
2. Update `.env.example` with pooler URL example + Stage 2 comments.
3. Annotate `render.yaml` with sync:false env var keys for the cutover (no hardcoded secrets).
4. Cross-link from `docs/database-guide.md` and `docs/troubleshooting.md`.

## Success Criteria

- [ ] Runbook exists with rollback steps
- [ ] `.env.example` documents pooler production path
- [ ] No production env values committed
