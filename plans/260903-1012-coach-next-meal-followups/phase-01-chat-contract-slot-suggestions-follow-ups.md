---
phase: 1
title: Chat contract slot suggestions follow-ups
status: in-progress
priority: P2
effort: 4h
dependencies: []
---

# Phase 1: Chat contract slot suggestions follow-ups

## Overview

Add local meal-slot facts to chat context. Persist `reply_payload` on assistant messages. Surface `suggestions` and `follow_ups` on GET `/v1/chat` and `message.completed`. No discover call yet.

## Requirements

- Functional: context includes `local_hour` + `suggested_meal_slot`; empty payload hydrates as `[]`.
- Non-functional: dual-read old `chat_message` rows; no table rebuild.

## Architecture

`ChatContextBuilder` already has timezone + `as_of`. Compute local now via `get_zone_info`. Pure function `suggested_meal_slot(hour, minute) -> breakfast|lunch|dinner|snack`.

`ChatUserContext.to_prompt_dict()` adds under `today`:

```json
"local_hour": 8,
"local_minute": 12,
"suggested_meal_slot": "breakfast"
```

Alembic: nullable JSON `reply_payload` on `chat_message`. Domain `ChatMessage` gains `reply_payload: dict | None`. Repository maps it. GET mapper + SSE completed include `suggestions` / `follow_ups` (default `[]`).

Replay path (`_replay`) must emit the same fields.

## Related Code Files

- Modify: `src/app/services/chat_context_builder.py`
- Modify: `src/domain/model/chat/models.py`
- Modify: `src/infra/database/models/chat.py`
- Modify: `src/infra/repositories/chat_repository_async.py`
- Modify: `src/api/schemas/response/chat_responses.py`
- Modify: `src/app/services/chat_turn_orchestrator.py` (SSE completed + replay)
- Create: `src/domain/services/chat/meal_slot.py` (pure slot helper)
- Create: alembic revision for `reply_payload`
- Test: `tests/unit/domain/services/chat/` slot + context dict
- Test: chat GET / SSE mapper for missing payload
- Update `ChatUserContext(` sites: `chat_context_builder.py`, `test_golden_set.py`, `test_policy.py`, `test_chat_turn_orchestrator.py`
- Update `ChatMessage(` sites: `chat_repository_async.py`, `test_chat_routes.py`, `test_chat_turn_orchestrator.py`

## Implementation Steps

1. Add `suggested_meal_slot` with the approved windows. Unit-test boundaries (04:59 snack, 05:00 breakfast, 10:29 breakfast, 10:30 lunch, 16:59 snack, 17:00 dinner, 21:59 dinner, 22:00 snack).
2. Extend `ChatUserContext` + builder. Do not await extra I/O.
3. Migration + ORM + repository dual-read (`None` → `{}`).
4. Extend `ChatMessageResponse` and `message.completed` data with `suggestions` / `follow_ups`.
5. Keep orchestrator writing `{}` until phase 2.

## Success Criteria

- [x] Slot helper tests cover every window edge.
- [x] Context prompt JSON includes slot fields.
- [x] GET `/v1/chat` on old messages returns empty arrays, not 500.
- [x] Replay SSE includes the new keys.
- [x] `pytest tests/unit` scoped to chat + new slot tests.

## Risk Assessment

JSON column on a hot table is fine if nullable. Do not add a required check constraint on payload shape in SQL — validate in domain.
