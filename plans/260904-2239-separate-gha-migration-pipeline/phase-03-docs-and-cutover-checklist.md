---
phase: 3
title: "Docs and cutover checklist"
status: completed
priority: P2
effort: 1h
dependencies: [1, 2]
---

# Phase 3: Docs and cutover checklist

## Overview

Make ownership unambiguous in docs and give operators a cutover checklist so staging/prod stop relying on Render pre-deploy without a surprise schema gap.

## Requirements

- Docs state: GHA migrate owns schema; Render owns code images
- Runbook covers order, rollback, and break-glass
- Cutover steps for GitHub Environments + Render dashboard

## Related Code Files

- Modify: `README.md` (Production migrations paragraph ~L80–83)
- Modify: `docs/archive/guides/render-cd.md` (pre-deploy sections — mark superseded / point to new owner)
- Create: `docs/runbooks/schema-migration.md` (evergreen operator runbook)
- Optional touch: `migrations/README.md` Production Deployment section if it still says pre-deploy

## Implementation Steps

1. **Runbook** `docs/runbooks/schema-migration.md`:
   - When to migrate vs when to deploy
   - Commands: Actions → Migrate → env → dry_run first → apply
   - Rollback code = previous GHCR SHA on Render (schema unchanged)
   - No downgrade from this pipeline
   - Expand → deploy code → contract later
   - Required secret: `DATABASE_URL_DIRECT` on GitHub Environments `staging` / `production`
2. **README**: replace “pre-deploy command” wording with GHA migrate + link runbook; note Render sets `RENDER=true` so containers skip migrate; local still uses `AUTO_MIGRATE`.
3. **Archive guide** `docs/archive/guides/render-cd.md`: add banner that pre-deploy migrate is retired; point to runbook; keep historical steps but strikethrough or “historical” label on preDeploy commands.
4. **Cutover checklist** (in runbook):
   - [ ] Create GH Environments + `DATABASE_URL_DIRECT` secrets
   - [ ] Enable prod required reviewers
   - [ ] Merge Phase 1+2
   - [ ] Clear Render dashboard Pre-Deploy Command if set outside Blueprint
   - [ ] Confirm staging/prod service env has no expectation of boot migrate
   - [ ] Dry-run migrate on staging
   - [ ] Apply migrate on staging (if pending heads)
   - [ ] Deploy a no-op/image-only change; confirm logs show skip on Render
5. Do not invent new CI jobs in this phase.

## Success Criteria

- [ ] Runbook exists and is linked from README
- [ ] README no longer claims Render pre-deploy runs migrations
- [ ] Archive render-cd marked historical / redirected
- [ ] Cutover checklist includes GH secrets + Render dashboard clear
- [ ] Docs mention dry_run = `migrations/cli.py status`

## Risk Assessment

| Risk | Mitigation |
|------|------------|
| Stale archive docs mislead during incident | Explicit superseded banner at top of render-cd |
| Operators forget Environment secrets | Checklist gate before first staging apply |
| Someone re-adds preDeploy in dashboard | Checklist + runbook “verify empty preDeploy” |
