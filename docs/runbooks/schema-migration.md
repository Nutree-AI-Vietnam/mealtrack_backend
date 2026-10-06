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

## Late legacy weekly-plan rows

The three-meal planner expects 21 slots per week: breakfast 0, lunch 1,
dinner 2. A two-slot server can still create 14-slot `v1` plans after an
earlier repair migration has run. An Alembic head check alone does not prove
the stored plans are valid.

The current server also repairs that same shape when a plan is loaded or
updated, so a three-slot deploy can serve those plans before this migration
runs. That in-request repair does not change the plan revision, so a save
still matches the revision the client loaded. The migration below does bump
revision, because it repairs plans that no open client has reloaded.

For the bulk repair, first verify that every production writer is running the
three-slot planner and drain older instances. Then apply the repair migration
through **Migrate Database**. This order is intentional: running it while a
two-slot writer remains active permits new malformed plans immediately
afterward, and the two-slot server cannot read a repaired 21-slot plan. The
migration touches only exact draft `v1` plans with seven slot-0 and seven
slot-1 rows. It keeps existing lunch/dinner slot IDs and logged links, inserts
empty breakfast slots, and bumps each repaired plan's revision so stale
clients must reload.

Afterward, verify that no 14-slot current-week `v1` plans remain, then use a
fresh authenticated plan read and exercise dinner assignment and grocery-day
note writes. Check the meal and grocery projection again after reopening the
client. If any new 14-slot plan appears, stop and identify the writer before
rerunning a repair.

Read-only count before and after the repair:

```sql
SELECT count(*) AS late_legacy_plans
FROM (
    SELECT p.id
    FROM weekly_meal_plans AS p
    JOIN weekly_meal_plan_slots AS s ON s.plan_id = p.id
    WHERE p.algorithm_version = 'v1' AND p.status = 'draft'
    GROUP BY p.id
    HAVING count(*) = 14
       AND count(DISTINCT s.day_index) = 7
       AND count(*) FILTER (WHERE s.slot_index = 0) = 7
       AND count(*) FILTER (WHERE s.slot_index = 1) = 7
) AS candidates;
```

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
