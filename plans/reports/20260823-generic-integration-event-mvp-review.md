# Generic Integration Event MVP Review

**Status:** DONE_WITH_CONCERNS
**Scope:** Backend pending MVP diff plus relevant `HEAD` outbox/queue changes and
`/Users/alexnguyen/Desktop/Nut/nutreeai_async` pending Worker diff.

## Summary

The durable path is wired: hydration and its `hydration.created.v1` outbox row
are created inside the same UoW; the backend relay validates and publishes to an
ingress Queue; the Worker routes to the registered hydration cache handler and
ACKs only after handler success. Active Worker code/config contains no D1,
child-delivery, delivery-ledger, or dynamic-subscription artifacts. Retry is
event-level as intended.

The MVP is not ready to land unchanged because cache invalidation behavior and
the ingress queue contract are not fully safe.

## Actionable Findings

### High — hydration cache invalidation is not behaviorally equivalent

- Evidence: backend canonical operations in
  `src/domain/cache/cache_invalidation_operations.py:91-150`; Worker operations
  in `nutreeai_async/src/domain/cache/cache-invalidation-builders.ts:72-126`;
  adapter in `nutreeai_async/src/application/event-handlers/hydration-created-cache-invalidation-handler.ts:21-35`.
- The Worker omits the activities-date pattern, weekly hydration key, and user
  streak key. It also changes the date-scoped hydration pattern to
  `user:<id>:hydration:*`, causing broader cache eviction. A successfully ACKed
  hydration can therefore leave stale activity, weekly hydration, or streak
  projections.
- Fix: use one canonical operation catalog or make the Worker builder exactly
  match the backend set; add an assertion for the complete operation list and
  date/week boundary cases.

### High — generic ingress silently falls back to the legacy Queue

- Evidence: `src/infra/services/handlers/__init__.py:82-90` uses
  `CLOUDFLARE_INGRESS_QUEUE_NAME or CLOUDFLARE_QUEUE_NAME`.
- This violates the MVP’s explicit-ingress decision. A missing ingress setting
  can publish hydration events to the legacy destination, making routing depend
  on accidental shared consumers or sending events to a queue that is no longer
  appropriate.
- Fix: require `CLOUDFLARE_INGRESS_QUEUE_NAME` for
  `hydration.created.v1`; remove the fallback and add a configuration test.

### Medium — validators accept payloads that the active handler rejects

- Evidence: backend envelope/data constraints in
  `src/app/events/integration_event.py:26-38,55-70`; Worker legacy cache parser
  UUID requirements in
  `nutreeai_async/src/domain/events/cache-invalidation-event.ts:103-120`.
- The backend/Worker integration validators accept arbitrary non-empty
  `event_id` and `user_id` strings, while the hydration adapter reuses a cache
  contract requiring UUIDs. A schema-valid event such as the test fixture’s
  `event-1` can reach the Worker and retry to DLQ during cache-event parsing.
  Source/default and date/timestamp strictness also differ between validators.
- Fix: share a golden wire fixture/schema and either enforce the UUID contract
  in both integration validators or stop adapting through the UUID-only legacy
  parser.

### Medium — current operational docs and plan reports are stale

- `docs/troubleshooting.md:95-110` still diagnoses hydration through a
  `cache_invalidation.v1` outbox row.
- `docs/external-services.md:150-164` presents the legacy cache path as the
  primary backend publication path without clearly limiting it to non-hydration
  mutations.
- `plans/260823-1056-generic-integration-event-redesign/reports/pm-260823-1218-generic-integration-event-progress.md`
  still says D1/child fan-out is implemented and asks for the first concrete
  handler, contradicting the current queue-only Worker.
- Fix: update current troubleshooting/runbook/plan-progress text to distinguish
  generic hydration ingress from legacy cache events and record live staging as
  pending.

## Scout Findings / Checklist

- Transaction boundary: verified hydration rows and outbox enqueue are inside
  `async with self.uow` at `log_hydration_command_handler.py:45-143`.
- ACK/retry: verified sequential handler invocation, ACK after all handlers, and
  retry on any parse/environment/handler failure at
  `integration-event-router.ts:52-87`; Queue config sets five retries and an
  ingress DLQ.
- D1/child fan-out: no active source/config matches found; dry-run showed only
  environment-variable bindings.
- Auth/input/data exposure: no new HTTP endpoint; payloads are bounded and
  unknown fields are rejected. Worker logs omit hydration user IDs and raw
  payloads.
- Concurrency: Worker batch processing is concurrent per message, while each
  event’s handlers are ordered; repeated cache deletion is idempotent by design.
- Live staging trace, forced failure/DLQ proof, and production deployment were
  not performed.

## Verification

- Backend focused tests: **37 passed** with `.venv` Python 3.13.2.
- Worker: `npm run typecheck` **passed**.
- Worker: `npm test` **23 files / 74 tests passed**.
- Worker: staging Wrangler dry-run **passed**, with no D1 binding.
- Backend broad unit suite: intentionally stopped after **233 passed**; no
  completion or coverage result is claimed.
- Lint/mypy: not run in this review.

## Plan Follow-up

Phases 1–3 are implemented in code but need the fixes above and synchronized
contract tests. Phase 4 remains incomplete: no live staging hydration trace,
forced retry-to-DLQ proof, or rollback proof exists. Production readiness should
remain blocked until those checks pass.

**Status:** DONE_WITH_CONCERNS
**Summary:** Queue-only orchestration and D1 removal are wired and locally verified, but cache projection parity, explicit ingress configuration, schema compatibility, and current-doc cleanup remain.
**Concerns/Blockers:** High-priority cache/ingress issues; live staging/DLQ proof absent; broad backend suite and lint/mypy were not completed.
