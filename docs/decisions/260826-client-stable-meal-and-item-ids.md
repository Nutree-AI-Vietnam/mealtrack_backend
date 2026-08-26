# Client-Stable Meal and Item IDs

**Status:** Accepted  
**Date:** 2026-08-26  
**Scope:** Manual and parse-text meal create, edit-add ingredient identity, and mobile optimistic create/edit convergence.

## Context

Manual and parse-text create used server-minted meal and food-item primary keys.
Mobile promoted `localId` → `serverId` after create and remapped ingredient ids
when the server returned different PKs. That forced add/remove outboxes, alias
merge on Home, and snap-back when users edited the plate before create ack.

Photo, scan, and hydration create paths were already server-minted and unrelated
to parse/cart identity. Durable write operation identity (`Idempotency-Key`) is
documented separately in
`backend/docs/decisions/260811-durable-write-contract.md`.

Calories-from-macros remains unchanged: macros are source of truth; backend
derives calories (`tdee_service.py`, meal nutrition paths).

## Decision

### Create (manual / parse-text)

1. **Optional client UUIDs are server PKs.** `CreateManualMealFromFoodsRequest`
   accepts optional `meal_id` and per-item `id`. When present and valid, the
   handler **INSERT**s those values as primary keys — no upsert, no remint.
2. **Omit → mint.** Missing or blank ids are replaced with server-generated
   UUID v4 (`client_resource_id.resolve_client_meal_id`, item id mint in create
   handler). Legacy clients that omit ids keep working.
3. **INSERT-only conflicts → 409.** If a requested meal id already exists:
   same user → `409` `CLIENT_MEAL_ID_CONFLICT`; another user → `409`
   `CLIENT_MEAL_ID_CONFLICT` (wrong account). Never attach an existing row.
4. **Payload shape errors → 400.** Duplicate item ids in one create payload →
   `400` `DUPLICATE_CLIENT_ITEM_ID`. Non-UUID `meal_id` or item `id` →
   `400` `INVALID_CLIENT_RESOURCE_ID`. Request schema validators live on
   `CreateManualMealFromFoodsRequest`; handler checks in
   `client_resource_id.py`.
5. **Idempotency-Key stays operation identity.** Header fingerprint replay is
   unchanged (`260811-durable-write-contract.md`). Body `meal_id` is the
   resource primary key. Create may send the same UUID in both places; the
   server evaluates them independently (replay claim vs PK reservation).
6. **Photo / scan / hydration unchanged.** Those flows do not accept client
   meal or item PKs; ids remain server-minted. Do not unify with manual/parse.

### Edit-add

7. **Honor client item id on add.** `AddFoodItemStrategy._resolve_add_item_id`
   uses `change.id` when it is a valid UUID and the id is unused on the meal;
   mint only when omitted. Invalid UUID → reject. Re-adding the same id on the
   same meal is idempotent update semantics (existing row wins).

### Mobile contract (pointer)

8. Mobile sends cart item UUIDs from parse/search and uses Save `operationId`
   as create `meal_id`. After create ack: tombstone → DELETE; else if the
   local plate is dirty → one PUT of the current plate; else apply create body
   (same ids). No food-item remap or add/remove outbox for new creates.
   Authoritative detail: `mobile/docs/decisions/reliable-write-contract.md` and
   `mobile/docs/architecture.md` (Caching intent).

## Consequences

Positive:

- Optimistic create/edit uses one id end-to-end; no promote/remap race on Home
  or edit.
- Rapid edit during in-flight create converges with a single diff PUT, not an
  outbox of per-item adds/removes.
- PK conflicts are explicit (`409`) instead of silent upsert corruption.
- Photo/scan/hydration and calories-from-macros contracts stay isolated.

Trade-offs:

- `food_item.id` is a global PK; clients must not reuse ids across meals.
- Expand/contract: older cache rows may still have `localId != serverId`; dual-read
  remains for one release.
- `MealWriteCoordinator.automaticMutationReplayEnabled` stays **false**; ambiguous
  transport is persisted, not auto-retried.

## Validation

Evidence:

- `backend/src/app/handlers/command_handlers/client_resource_id.py`
- `backend/tests/unit/handlers/command_handlers/test_create_manual_meal_command_handler.py`
- `backend/src/domain/strategies/meal_edit_strategies.py` (`_resolve_add_item_id`)
- `backend/tests/unit/domain/test_meal_edit_strategies.py` (`test_add_keeps_client_item_id`)
- `mobile/test/features/meal_edit/application/providers/meal_edit_promote_after_create_test.dart`
