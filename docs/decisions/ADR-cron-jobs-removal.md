# ADR: Remove External Cron Job Entry Points from `src/cron/`

**Date:** 2026-08-24  
**Status:** Accepted  
**Authors:** Engineering team

---

## Context

Two standalone Python scripts in `src/cron/` were invoked by external Render Cron jobs on a schedule, completely separate from the main Uvicorn web process:

| Script | Render schedule | Purpose |
|---|---|---|
| `src/cron/outbox_worker.py` | `*/1 * * * *` (every minute) | Claim and dispatch rows from the `outbox_events` DB table via the 3-phase `OutboxDispatchEngine` |
| `src/cron/affiliate_outbox.py` | `*/5 * * * *` (every 5 minutes) | Claim rows from `affiliate_event_outbox` and POST them to the `nutree-affiliate` service |

These process-external cron jobs introduced operational overhead (separate Render Cron services, cold-start DB connections, separate observability surfaces) and were identified as candidates for elimination as part of an architecture simplification effort.

---

## Decision

**Remove the two cron entry point scripts and their unit tests.**  
All underlying infrastructure (dispatch engine, handlers, repositories, DB tables, migrations) is **retained** so the outbox mechanism can be re-wired to a different trigger without data loss.

---

## What Was Removed

### `src/cron/outbox_worker.py` _(deleted)_

**CLI entrypoint** for the transactional outbox worker. Supported two modes:
- `--once`: drain the queue until empty, then exit (used by Render Cron)
- `--continuous`: run a polling loop indefinitely (daemon mode)

Internally it created an `OutboxDispatchEngine` backed by `create_default_handler_registry()`. The registry handled 7 event type families:

| Event types | Handler | Destination |
|---|---|---|
| `push_notification`, `notification.push`, `scheduled_push`, `push_notification.v1` | `PushNotificationQueueHandler` | Cloudflare Queue |
| `firebase_account_cleanup`, `user.account_cleanup.v1` | `FirebaseAccountCleanupHandler` | Firebase Auth REST |
| `cache_invalidation.v1` | `CacheInvalidationQueueHandler` | Cloudflare Queue |
| `telemetry_event`, `analytics.event`, `posthog.capture` | `TelemetryHandler` | PostHog |
| `affiliate_event`, `affiliate_webhook`, `affiliate.referral_created`, `affiliate.conversion` | `AffiliateWebhookHandler` | nutree-affiliate service |
| `notification_reschedule` | `NotificationRescheduleHandler` | internal DB |
| `hydration.created.v1` | `IntegrationEventQueueHandler` | Cloudflare Queue |

Configurable via env vars: `OUTBOX_POLL_INTERVAL_SECONDS`, `OUTBOX_BATCH_SIZE`, `OUTBOX_CONCURRENCY_LIMIT`, `OUTBOX_LEASE_DURATION_SECONDS`, `OUTBOX_WORKER_MODE`.

**Tests deleted:** `tests/unit/cron/test_outbox_worker.py`, `tests/unit/cron/test_outbox_worker_stress.py`

---

### `src/cron/affiliate_outbox.py` _(deleted)_

**CLI entrypoint** for the affiliate outbox dispatcher. Called `dispatch_affiliate_outbox()` from `src/infra/services/affiliate_outbox_dispatch_service.py` — claims `affiliate_event_outbox` rows marked `pending`, POSTs them to `nutree-affiliate`, and marks them `sent` or increments retry count.

Only active when `AFFILIATE_INTEGRATION_ENABLED=true`.

Rows are written to `affiliate_event_outbox` by:
- `src/api/routes/v1/webhook_subscription_lifecycle.py` (5 call sites — RevenueCat lifecycle webhooks)
- `src/app/handlers/command_handlers/referral/apply_referral_code_handler.py`

**No separate test file** for this entrypoint (it was thin; the dispatch service has its own test at
`tests/unit/infra/services/test_affiliate_outbox_dispatch_service.py`).

---

## What Was Kept (Do NOT Delete)

| Path | Why kept |
|---|---|
| `src/infra/services/outbox_dispatch_engine.py` | Core 3-phase engine, reusable by any future trigger |
| `src/infra/services/affiliate_outbox_dispatch_service.py` | Core affiliate dispatcher, reusable |
| `src/infra/services/handlers/` (all 7 handler files + registry) | All handler logic stays |
| `src/infra/repositories/outbox_repository.py` | Outbox repository |
| `src/infra/repositories/affiliate_event_outbox_repository.py` | Affiliate outbox repository |
| `src/infra/database/models/outbox_event.py` | ORM model |
| `src/infra/database/models/affiliate_event_outbox.py` | ORM model |
| `migrations/versions/20260822000001_create_outbox_events_table.py` | DB schema migration |
| `migrations/versions/20260610000001_add_affiliate_event_outbox.py` | DB schema migration |
| All `outbox.enqueue()` call sites in command handlers & webhook routes | Write side is untouched |
| `tests/unit/infra/services/test_affiliate_outbox_dispatch_service.py` | Tests the service, not the deleted entrypoint |

---

## Consequences & Required Follow-Up

> **WARNING:** The `outbox_events` and `affiliate_event_outbox` tables are still actively written to
> by the main API. Without a dispatch trigger, rows will accumulate unprocessed.

Before this removal is considered operationally complete, one of the following must be implemented:

1. **APScheduler inside Uvicorn** — schedule `OutboxDispatchEngine.run_once()` as a background task
   within the existing `lifespan()` context, eliminating any external process dependency.
2. **Render Background Worker** — a long-running `--continuous` mode process that stays alive.
3. **Cloudflare Worker** — poll the outbox via a Cloudflare Worker triggered by a queue or CRON trigger.

Until a replacement is deployed, the Render Cron jobs should remain **disabled but not deleted** from
the Render dashboard so they can be re-enabled if needed.

---

## Render Dashboard Action Required

Before deploying this code change, **disable** the following Render Cron services:
- Outbox Worker (`*/1 * * * *` → `python -m src.cron.outbox_worker --once`)
- Affiliate Outbox (`*/5 * * * *` → `python -m src.cron.affiliate_outbox`)
