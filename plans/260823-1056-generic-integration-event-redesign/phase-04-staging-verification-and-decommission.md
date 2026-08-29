---
phase: 4
title: "Staging verification and decommission"
status: in-progress
priority: P1
effort: "1-2 days"
dependencies: [3]
---

# Phase 4: Staging verification and decommission

## Overview

Prove the queue-only hydration path in staging, then remove redundant local
configuration and documentation. Production activation is a separate follow-up
after staging evidence is accepted.

## Requirements

- Verify backend outbox, ingress, orchestrator, handler, ACK, retry, and DLQ.
- Configure the Worker environment's `NEON_DATABASE_URL` for hydration context
  lookup before live verification.
- Separate local test proof from live Cloudflare proof.
- Confirm staging and production queues cannot cross routes.
- Delete the unused remote D1 delivery databases after confirming no Worker binding remains.
- Document rollback as disabling the generic route or pausing ingress dispatch; accepted Queue messages cannot be recalled.

## Related Code Files

- Modify: `nutreeai_async/wrangler.jsonc`
- Modify: `nutreeai_async/docs/deployment.md`
- Modify: backend architecture and queue runbook documentation
- Remove stale D1 delivery-state references from changed docs and plans

## Implementation Steps

1. Apply only the queue configuration required by the MVP; no D1 binding is deployed.
2. Run backend unit tests, focused static checks, Worker typecheck, Worker tests, and Wrangler dry-run.
3. Configure `NEON_DATABASE_URL` and deploy Worker staging with the active
   Redis and database secrets.
4. Create one hydration and trace its event ID through outbox, ingress,
   orchestrator, Neon lookup, cache handler, and ACK.
5. Force a handler failure and verify event retry plus ingress-DLQ behavior.
6. Replay the same event and confirm cache invalidation remains safe.
7. Remove unused D1 runtime files/configuration, delete the empty remote D1 resources, and update docs.
8. Record production queue creation and rollout as a later step.

## Success Criteria

- [ ] Staging proves one hydration reaches every configured generic handler.
- [ ] Forced failure reaches the ingress DLQ after configured retries.
- [x] No D1 binding appears in the staging Worker dry-run.
- [x] No duplicate hydration cache publication remains.
- [x] Orphaned Worker cache-consumer wiring and stale generic-queue setup references are removed.
- [x] Empty delivery D1 databases and obsolete staging queue resources are removed.
- [x] Staging Worker is deployed with one ingress queue and its DLQ.
- [ ] Rollback and next-handler onboarding steps are documented.

## Risk Assessment

Cloudflare deployment success does not prove business delivery. The rollout is
not complete until a real staging hydration and forced failure produce the
expected queue evidence.
