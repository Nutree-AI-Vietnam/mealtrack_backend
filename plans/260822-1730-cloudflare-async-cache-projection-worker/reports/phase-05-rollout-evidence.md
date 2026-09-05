# Phase 05 Rollout Evidence

Status: pending/blocked on credentials

## Verified source path

- Business writes persist the authoritative row and the `cache_invalidation.v1`
  outbox event in one unit of work via `src/app/services/cache_invalidation_service.py`.
- The Python outbox worker claims that event and dispatches it through
  `src/cron/outbox_worker.py` and
  `src/infra/services/handlers/cache_invalidation_queue_handler.py`.
- Queue publication uses `src/infra/adapters/cloudflare_queue_publisher.py`
  and is controlled by the single `CLOUDFLARE_QUEUE_ENABLED` setting in
  `src/infra/config/settings.py`.
- The Cloudflare consumer lives in the sibling `nutreeai_async` repository and
  validates the event before deleting exact keys or bounded patterns through
  Upstash Redis REST.

## What is intentionally out of scope

- HMAC signing
- Revision tables and fencing
- Cache-value writes
- Local-vs-Cloudflare dual routing
- Percentage canaries

## Deployment evidence

- Source implementation evidence: present in repo.
- Local backend evidence: `2843 passed`, coverage `80.65%`, with focused cache,
  outbox, publisher, handler, and UoW-path checks green.
- Local Worker evidence: `npm run typecheck` passed, `npm test` passed with `9`
  tests, and `npx wrangler deploy --dry-run --env=staging` passed with the
  staging Redis-limit bindings rendered.
- Static evidence: Ruff, mypy on the new Python adapters/services, `git diff
  --check`, and strict plan validation all passed.
- Staging deployment evidence: pending.
- Live deployment evidence: pending.
- Blocker: staging/live Queue, Worker, and Upstash credentials or environment
  access are not available in this workspace, so no deploy proof is being
  claimed here.

## Next evidence to capture

1. Backend outbox publish log with the target `event_id`.
2. Cloudflare Queue acceptance log.
3. Worker `ack` log and Redis delete confirmation.
4. DLQ proof only if a failure case is intentionally exercised.
