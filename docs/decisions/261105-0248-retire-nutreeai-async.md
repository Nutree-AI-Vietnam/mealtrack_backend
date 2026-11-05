# Decision: Retire nutreeai_async and handle integration events in the API process

**Status:** Accepted and implemented. The API process handles every integration event; the Cloudflare Queue publisher is removed.

**Date:** 2026-11-05

**Supersedes:** the Cloudflare Queue ingress in
[260822-1631-cloudflare-async-cache-worker-proposal.md](./260822-1631-cloudflare-async-cache-worker-proposal.md)

## Context

Every committed write that has follow-up work publishes an integration event:

```text
nutree_backend --HTTPS publish--> Cloudflare Queue <--HTTPS pull/ack, ~1/s-- nutreeai_async (Render worker)
```

Render metrics, 7 days to 2026-11-05:

| Service | Outbound traffic | CPU peak | Memory |
|---|---|---|---|
| `nutree_backend` (1 CPU, 2 GB, 1 instance) | ~1.8 GB/day in total | ~0.11 CPU | 0.96 → 1.94 GB between deploys |
| `nutreeai_async` (0.5 CPU, 512 MB) | ~20–25 GB/day | ~0.2–0.25 CPU | ~0.26 GB |

The worker handled a few events a minute. What it did with them:

| Job | Who uses the result | Outcome |
|---|---|---|
| Meal, hydration and movement cache purges | Nobody new: the write path purges by exact key before responding | Not carried over |
| Profile, cheat-day and saved-suggestion cache purges | Readers of those caches; the backend's builders existed but nothing called them | Moved to the API process |
| Meal value insights (`meal_insight:{meal_id}`) | The app, through `GET /v1/meals/{meal_id}/value-insights` | Moved to the API process |
| Meal recommendation plans | Nothing reads `meal_recommendations` rows or `user:{id}:meal_recommendation_plan` | Not carried over |
| Firebase account deletion | Account deletion | Moved to the API process |
| `subscription.lifecycle.v1` receipts | A log line | Not carried over |

The worker's costs came from its own mechanics: every pattern delete walked the
whole Redis keyspace (`SCAN COUNT 100`), each recommendation downloaded the
whole catalog from Neon, a failing handler retried the event with every scan
before it, and the Queue added HTTPS calls at both ends. It also kept its own
copy of the Neon credentials; since 2026-11-04 07:19 UTC that copy has been
stale and every hydration and recommendation event has failed and retried.

## Decision

The best-effort publisher hands each event to `InProcessIntegrationEventRouter`
(`src/app/events/in_process_event_router.py`) instead of the Queue. It
already ran every publish on a background task and drains them at shutdown,
so handlers never delay or fail a write. No new service is needed. Handlers
are registered at startup by `src/api/services/in_process_integration_events.py`,
whether or not Redis came up:

| Event types | Handler |
|---|---|
| `meal.created.v1`, `meal.updated.v1`, `meal.deleted.v1` | `MealInsightEventHandler` (`src/api/services/local_meal_insight_cache.py`) |
| `user.deleted.v1`, `user.account_cleanup.v1` | `delete_firebase_account` (`src/bootstrap/integration_services.py`) |
| `user.profile_updated.v1`, `user.onboarding_completed.v1`, `user.custom_macros_updated.v1` | `apply_profile_cache_invalidation` |
| `cheat_day.marked.v1`, `cheat_day.unmarked.v1` | `apply_cheat_day_cache_invalidation` |
| `saved_suggestion.created.v1`, `saved_suggestion.deleted.v1` | `apply_saved_suggestion_cache_invalidation` |
| everything else | none: the write path finished the work, or nothing reads it |

The purges live in `src/api/services/write_cache_invalidation.py` and reuse
the existing builders. Their pattern deletes run here, off the write path, and
`RedisClient.delete_pattern` now scans 1,000 keys per round trip instead of
Redis' default 10. A profile purge measured 48 SCAN calls over 5,011 keys for
its 8 patterns, where the default would need about 4,000.

### Meal insights

- Generated with the backend's insight prompt (output language and
  micronutrient rules included) and validated by `parse_ai_result`. This
  replaces the development-only local writer, which is removed.
- Fenced by revision: the event's `occurred_at` in milliseconds, stored at
  `meal_insight:{meal_id}:__revision`. Each event first clears the insight and
  records its revision (`CacheService.invalidate_with_revision`), then writes
  only if no newer revision exists (`CacheService.set_json_with_revision`).
  Edits clear the old meal's insight, deletes remove it, and a late or older
  generation can never overwrite a newer one.
- `meal_insight_recovery` keeps working: it re-publishes the meal's event with
  the same revision, which regenerates a missing insight.

### Firebase account deletion

`delete_firebase_account` calls Firebase Admin on the existing Firebase thread
pool, three attempts with backoff; an account that is already gone counts as
deleted. After the last failure the UID is logged at ERROR so
`scripts/delete_missed_firebase_accounts.py` can finish it.

## Rollout

1. Merge and deploy `nutree_backend`. From that deploy on, the backend stops
   publishing to the Cloudflare Queue and handles its events itself.
2. Check the startup log `Integration events handled in-process`, then: a
   logged meal's value insights appear within about a minute; a test account
   deletion logs `Firebase account cleanup completed`; a profile change
   refreshes targets.
3. To roll back, redeploy the previous build on Render. Keep `nutreeai_async`
   running until this deploy has proved itself, so the previous build still
   has its consumer.
4. Then suspend `nutreeai_async` on Render, delete the Cloudflare Queue and its
   DLQ, remove the `CLOUDFLARE_QUEUE_*` variables from the Render services, and
   archive the `nutreeai_async` repository.

## Consequences

Gains:

- No Cloudflare calls, no worker traffic, one fewer Render service and one
  copy of the database credentials.
- Insights use the backend's fuller prompt and are fenced against reordering.
- Pattern purges make about 100 times fewer SCAN round trips.

Costs and risks:

- Follow-up work shares the API process. CPU has ample room; memory is the
  constraint and already grows about 160 MB a day between deploys, a separate
  issue worth fixing.
- Work still running at shutdown is dropped after the 5 s drain. A dropped
  insight is re-requested when the app reads it two minutes later; a dropped
  Firebase deletion is covered by the reconcile script.
- There is no queue-style retry or dead-letter for purges. They are a second
  pass over caches that also expire.
