---
phase: 5
title: "Verification And Rollout"
status: in_progress
priority: P1
effort: "1d"
dependencies: [2, 3, 4]
---

# Phase 5: Verification And Rollout

## Overview

Prove the migration in the right order: local proof first, then CI proof, then
staging deployment proof, then optional live FCM proof. Also update the docs so
the branch no longer points operators at deleted outbox paths.

## Key Insights

- Local worker tests prove the generic meal-event insight handler
  and FCM adapter in isolation
  (`../nutreeai_async/test/meal-value-insight-event-handler.test.ts:7-85`,
  `../nutreeai_async/test/fcm-adapter.test.ts:10-82`), but they do not prove
  backend cache compatibility.
- Backend tests prove the cache-only reader accepts the Worker-owned
  `meal_insight:{meal_id}` namespace
  (`tests/unit/domain/services/test_meal_value_insight_service.py`). Worker
  tests prove the generated JSON is written with a seven-day TTL before push.
  Phase 5 must keep those assertions while separating local proof from staging
  proof.
- Existing docs are stale in different ways:
  the system architecture doc already describes direct integration events
  (`docs/system-architecture.md:61-87`), while the older cache-worker plan and
  proposal still speak in outbox terms.

## Requirements

- Functional: record local backend and Worker proof separately from staging,
  deployment, Queue/Redis, retry/DLQ, and live FCM proof.
- Non-functional: keep local proof, CI proof, staging proof, deployment proof,
  and optional FCM proof as separate checklist items.
- Non-functional: update operator docs and the stale plan/proposal notes.

## Architecture

Validation ladder:

1. Backend unit tests
2. Worker unit/type tests
3. Backend lint/type/unit CI set
4. Worker dry-run deploy
5. Staging backend publish + Worker ACK + Redis cache-hit proof
6. Optional staging FCM smoke with test tokens

## Related Code Files

- Modify: `docs/system-architecture.md`
- Modify: `docs/external-services.md`
- Modify: `docs/troubleshooting.md`
- Modify: `../nutreeai_async/docs/deployment.md`
- Modify: active plan notes/reports if they still reference deleted outbox steps
- Read-only validation:
  `tests/unit/infra/adapters/test_cloudflare_queue_publisher.py`,
  `tests/unit/domain/services/test_meal_value_insight_service.py`,
  `../nutreeai_async/test/queue-consumer-router.test.ts`

## Implementation Steps

1. Run focused backend tests and compile/Ruff checks.
2. Run Worker unit tests and record pre-existing typecheck failures separately.
3. Record that staging deploy, Queue/Redis, retry/DLQ, and live FCM proof remain
   pending unless separately executed with real credentials and bindings.
4. Keep plan notes aligned with the direct-Queue and Worker-owned cache flow.

## Todo List

- [x] Run focused backend tests and full backend unit gate
- [x] Re-run full backend gate after transaction/context fixes
- [x] Run backend compile + focused Ruff
- [x] Run Worker unit tests
- [x] Validate Worker staging/production dry-run configuration
- [x] Record Worker typecheck pre-existing failures
- [x] Update stale plan notes
- [ ] Capture staging publish -> ack -> cache-hit proof
- [ ] Capture retry / DLQ proof
- [ ] Capture optional staging FCM smoke if credentials exist

## Success Criteria

- [x] Backend unit/quality gates pass for the changed surfaces.
- [x] Worker unit tests pass with cache-write, generic fan-out, invalidation, ordering, and empty-result
  coverage; typecheck
  remains blocked by unrelated pre-existing errors.
- [ ] Staging proves backend publish, Worker ACK, and later backend cache hit for insights.
- [ ] Staging proves retry/DLQ on AI or Redis failure.
- [x] Docs describe the Worker-first cache invalidation path and the event-driven insight path without outbox instructions.

## Risk Assessment

- High likelihood / High impact: local test success is mistaken for staging or deploy proof.
  Mitigation: require separate evidence lines for each boundary.
- Medium likelihood / High impact: the cache write works locally but the staging
  Redis namespace or queue binding is wrong.
  Mitigation: staging trace must include publish log, Worker ACK log, and later
  backend cache hit.
- Low likelihood / Medium impact: FCM smoke delays or obscures cache success.
  Mitigation: keep push proof optional and explicitly secondary to cache proof.

## Security Considerations

- Do not capture raw queue payloads, auth headers, Redis tokens, OpenAI keys, or
  FCM private keys in rollout evidence.
- Use staging test users/tokens only for optional FCM proof.

## Next Steps

- If staging passes, the implementation can move to a focused PR with both repo
  diffs reviewed together.
- If staging fails, revert only the insight publish path first; the meal cache
  invalidation path stays Worker-owned.

## Unresolved Questions

- None.
