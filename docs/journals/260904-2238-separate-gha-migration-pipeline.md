---
date: 2026-09-04
title: Separate GHA migration pipeline decision
---

# Separate GHA migration pipeline

## Context

Needed schema upgrades without tying them to Render deploys so code image rollback stays possible.

## What happened

Brainstorm locked: dedicated `workflow_dispatch` migrate Actions pipeline; remove Render `preDeployCommand`; disable container auto-migrate on Render. Not folded into release pipeline.

## Decisions

- GHA owns Alembic (`migrations/run.py` + `DATABASE_URL_DIRECT`)
- Render owns code only
- Staging + prod Environments; prod approval required
- Out of scope: seeds, downgrades-from-CI, auto-deploy after migrate

## Next

`/ck:plan` from `plans/reports/260904-2238-separate-gha-migration-pipeline-brainstorm.md`
