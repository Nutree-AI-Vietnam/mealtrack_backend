# Durable Write Contract (Manual Meal Slice)

**Status:** Accepted
**Date:** 2026-08-11
**Scope:** Server-side operation identity for mutation replay; Phase 5 vertical slice.

## Context

Mobile must not blind-retry meal mutations after ambiguous transport failures.
Meal-recommendations already use `Idempotency-Key` + fingerprint. Manual meal
create and weight sync still lack operation identity.

## Decision

1. **Header:** `Idempotency-Key` (trimmed, 1–160 chars) on supported mutations.
2. **Identity:** unique `(user_id, action, idempotency_key)`.
3. **Fingerprint:** SHA-256 of canonical JSON for the request body fields that
   define the logical write (sorted keys).
4. **Claim-before-create:** insert a pending durable row before the mutation.
   Complete it with the exact response after success; abandon it on failure so
   a retry can reclaim. Concurrent same-key claims → `409`
   `IDEMPOTENCY_KEY_IN_PROGRESS` (no second mutation).
5. **Replay:** same key + same fingerprint → exact prior HTTP status + body.
6. **Conflict:** same key + different fingerprint → `409` with
   `IDEMPOTENCY_KEY_CONFLICT` (raised at claim time, before mutation).
7. **Retention:** 14 days (`expires_at`); expired rows may be pruned.
   Pending same-fingerprint claims are never auto-reclaimed (avoids
   duplicate creates after a post-mutation completion failure).
8. **Discovery:** `GET /v1/capabilities/durable-writes`.
9. **Backward compatibility:** omitting the header keeps legacy single-shot
   behavior (no store write).

The same capability document now carries the meal-item identity rollout:
`client_item_id` on v2 create, preserved `id` on v2 add, and
`MEAL_ITEM_IDENTITY_ENABLED` as the safe rollback switch. Disabling the flag
only changes discovery; it does not change meal ownership, item-membership
checks, or the legacy mutation path.

### Actions in this wave

| Action | Supported | Notes |
|---|---|---|
| `manual_meal_create` | yes | `POST /v1/meals/manual` |
| `weight_sync` | no | Deferred: no client entry ID → server ID mapping yet |

Automatic client replay stays **off** until mobile fixtures pass and a later
rollout enables it per capability.

## Consequences

- Mobile can send a stable key for manual meal create and treat 409 as a hard
  client bug (key reuse with different payload).
- Weight durable path remains unsupported until mapping ships.
- Edit / suggestion / scan actions reuse this store later behind the same
  capability document.
- Meal-item instance UUIDs can roll out independently of the older durable
  write replay behavior, with a fast disable path that leaves legacy clients
  unchanged.
