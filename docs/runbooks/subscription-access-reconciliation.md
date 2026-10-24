# Subscription Access Reconciliation

RevenueCat is the payment-fact provider. PostgreSQL owns the access decision
used by `GET /v1/me/entitlements`. MoMo is reserved as a future provider value;
this runbook does not cover MoMo checkout or callbacks.

## Before running

1. Confirm the additive subscription migration is applied through the GitHub
   Actions **Migrate Database** workflow. Do not apply it from Render startup.
2. Confirm the server has `REVENUECAT_SECRET_API_KEY` and that the deployment
   has the compatible snapshot-sync code. The access endpoint stays disabled
   until reconciliation is complete.
3. Use a private local checkpoint path. It contains internal user IDs and must
   not be committed or attached to a ticket.

## Dry run and apply

Run from the repository root with the project virtual environment:

```bash
.venv/bin/python scripts/reconcile_subscription_access.py \
  --all-active --checkpoint /tmp/subscription-access-checkpoint.json
```

The default is read-only. It makes one RevenueCat request at a time, waits at
least one second between new accounts, honors numeric `Retry-After`, and retries
bounded provider failures. The summary reports grants, denials, unresolved
accounts, and checkpointed accounts; it never prints emails or provider
payloads.

After reviewing discrepancies, apply verified snapshots:

```bash
.venv/bin/python scripts/reconcile_subscription_access.py \
  --all-active --apply --checkpoint /tmp/subscription-access-checkpoint.json
```

Resume a failed pass with `--resume` and the same checkpoint path. To inspect a
single database user, use `--user-id <database-user-id>` instead of
`--all-active`. Do not use email to merge or assign accounts. Resolve ambiguous
RevenueCat identities manually before cutover.

## Cutover and rollback

Enable `SUBSCRIPTION_ACCESS_ENABLED=true` only after all potentially entitled
active accounts are reconciled and no ownership conflicts or unverified legacy
grants remain. Check the authenticated endpoint against provider state, then
confirm reads continue while RevenueCat is unreachable. A 503
`ENTITLEMENTS_UNAVAILABLE` must not be treated as a denial by clients.

Rollback by setting `SUBSCRIPTION_ACCESS_ENABLED=false` and restoring a
compatible image if needed. Keep the additive columns and subscription history
for diagnosis; downgrade only after no running code reads or writes them.
