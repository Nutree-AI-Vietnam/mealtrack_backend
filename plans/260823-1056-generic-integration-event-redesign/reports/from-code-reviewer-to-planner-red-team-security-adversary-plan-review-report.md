# Security Adversary review

## Findings

- **Critical — producer authenticity missing:** the current publisher uses a bearer credential at `src/infra/adapters/cloudflare_queue_publisher.py:78-85`, while Worker routing begins from `message.body` at `nutreeai_async/src/interfaces/cloudflare/queue-consumer-router.ts:200-217`. The finding was accepted as a queue access-control/environment-isolation requirement; cryptographic signing was explicitly deferred as unnecessary for the current threat model.
- **Critical — delivery ID alone is not idempotency:** push and email perform external calls at `nutreeai_async/src/application/event-handlers/push-notification-event-handler.ts:11-24` and `lifecycle-email-event-handler.ts:24-33`. Phase 3 now requires D1 delivery state plus effect-level idempotency and excludes unsafe initial subscriptions.
- **Critical — target capabilities must not cross the generic boundary:** current handlers trust target data, including push tokens and Firebase identifiers, at `nutreeai_async/src/application/event-handlers/push-notification-event-handler.ts:11-22` and `account-cleanup-event-handler.ts:7-11`. Phase 1 now forbids secrets/capabilities in generic events and requires field-specific validation.
- **High — existing logs and outbox can expose sensitive data:** Worker logs at `nutreeai_async/src/interfaces/cloudflare/queue-consumer-router.ts:266-275` and `:325-333`; backend payload/error persistence at `src/infra/database/models/outbox_event.py:33-40`. Phase 1 now requires redaction and retention rules.
- **High — staging/production isolation is implicit:** backend has one queue setting at `src/infra/config/settings.py:102-106`, while Worker names differ in `nutreeai_async/wrangler.jsonc:64-93` and `:109-138`. Phase 4 now requires fail-closed environment binding validation.

**Status:** DONE
