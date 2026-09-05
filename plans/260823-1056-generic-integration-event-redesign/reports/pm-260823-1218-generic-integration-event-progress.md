## Project Status: 2026-08-23

### Active plan

| Plan | Progress | Status | Next action |
|---|---:|---|---|
| Generic Integration Event Redesign | Phases 1–3 complete; phase 4 in progress | In progress | Run live staging hydration, retry, and ingress-DLQ trace |

### Completed this session

- Added a versioned `IntegrationEvent` parent envelope and `hydration.created.v1` snapshot in backend and Worker.
- Published hydration events through the existing transactional PostgreSQL outbox to a configurable ingress Queue.
- Added a Worker ingress orchestrator, environment checks, sequential handler execution, and event-level retry/ACK ordering.
- Preserved the specialized `cache_invalidation.v1` compatibility route and existing purpose queues.
- Removed HMAC from scope; staging/production isolation remains queue/binding/credential based.

### Verification

- Backend: `pytest tests/unit --cov=src --cov-fail-under=65` — 2,768 passed, 80.66% coverage.
- Backend: focused Ruff and mypy checks — passed.
- Worker: `npm run typecheck` — passed.
- Worker: `npm test` — 22 files, 75 tests passed.
- Worker: `npx wrangler deploy --dry-run --env=staging` — passed; queue bindings visible, no live delivery claimed.
- Review fixes: paused disabled publication, explicit canonical ingress, redacted compatibility logs, required environment, strict event fields, and removal of redundant D1/child-delivery runtime paths.

### Blockers and risks

- Notification and email handlers are intentionally not active until their payload and effect-idempotency contracts are explicit.
- Backend queue names are explicit but environment naming is configuration-dependent; wrong queue credentials/names must be checked before rollout.
- No staging/live trace or external side-effect proof exists.
- Documentation was updated in both repositories; generated `repomix-output.xml` is a local artifact and is not part of the implementation.

### Next steps

1. Deploy the staging Worker and configure the explicit ingress queue.
2. Create one hydration and trace its event ID through outbox, ingress, handler, and ACK.
3. Force a handler failure and verify whole-event retry plus ingress-DLQ behavior.
4. Add notification/email handlers only after their idempotency contracts are defined.

### Unresolved questions

- When should notification/email handlers be enabled, and what idempotency keys will they use?
