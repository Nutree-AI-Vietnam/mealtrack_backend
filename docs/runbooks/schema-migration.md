# Schema Migration Runbook

**Owner:** GitHub Actions (`.github/workflows/migrate.yml`)  
**Not owner:** Render pre-deploy / container boot on Render

## Why separate

Schema upgrades are intentionally **not** tied to Render image deploys. That lets you roll back a bad code image (previous GHCR SHA) without the schema already having advanced in the same deploy.

## When to migrate vs deploy

| Need | Action |
|------|--------|
| New/changed Alembic revision | Actions → **Migrate Database** → env → (dry run) → apply |
| New application code only | Build/push image → Render deploy SHA |
| Bad code after migrate | Render → previous image SHA (schema stays) |

Prefer **expand → migrate → deploy code → contract later**. Destructive/contract steps are not rollback-friendly from this pipeline.

## Prerequisites

GitHub → Settings → Environments:

1. Create `staging` and `production`
2. On each: secret `DATABASE_URL_DIRECT` = Neon **direct** connection string (not the `-pooler` host)
3. On `production`: enable **Required reviewers**

`migrations/utils.py` reads `DATABASE_URL_DIRECT` first.

## Run migrate

1. Actions → **Migrate Database** → Run workflow
2. Choose `environment`: `staging` or `production`
3. First: `dry_run=true` → runs `python migrations/cli.py status`
4. Then: `dry_run=false` → runs `python migrations/run.py`

Production runs wait for Environment approval.

## Render behavior

- `render.yaml` has **no** migration `preDeployCommand`
- `docker-entrypoint.sh` skips Alembic when `RENDER=true` (and when `ENV`/`ENVIRONMENT=production`)
- Local/dev Docker still auto-migrates unless `AUTO_MIGRATE=false`

## Cutover checklist

- [ ] GitHub Environments `staging` / `production` exist
- [ ] `DATABASE_URL_DIRECT` set on both (direct Neon URL)
- [ ] Production required reviewers enabled
- [ ] This repo change merged (migrate workflow + Render decoupling)
- [ ] Render dashboard **Pre-Deploy Command** cleared if set outside Blueprint
- [ ] Dry-run migrate on staging
- [ ] Apply migrate on staging if heads pending
- [ ] Deploy an image-only change; confirm logs show Render migrate skip
- [ ] Repeat dry-run/apply for production when ready

## Rollback

- **Code:** point Render at the previous GHCR digest/tag
- **Schema:** not handled by this workflow (no automated downgrade). Use expand/contract discipline; emergency schema repair is a reviewed manual ops action

## Break-glass

If Actions cannot run and you must apply schema from a trusted machine:

```bash
export DATABASE_URL_DIRECT='postgresql://...@ep-....neon.tech/...'  # direct host
python migrations/cli.py status
python migrations/run.py
```

Do not re-enable Render pre-deploy migrate permanently — that reintroduces coupled rollback risk.
