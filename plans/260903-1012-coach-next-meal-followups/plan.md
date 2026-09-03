---
title: Coach next-meal discover and backend follow-ups
description: >-
  Reuse meal-suggestions/discover for next_meal cards; persist suggestions and
  model-authored follow-ups on message.completed.
status: pending
priority: P2
effort: 2d
branch: feature/chatbot-single-thread-mvp-6c05
tags:
  - feature
  - backend
  - frontend
  - api
  - chat
blockedBy: []
blocks: []
created: '2026-09-03'
createdBy: 'ck:plan'
source: skill
brainstorm: ../reports/260903-0948-coach-next-meal-followups-brainstorm.md
---

# Coach next-meal discover and backend follow-ups

## Overview

`next_meal` pre-fetches 3 discover candidates for the local meal slot and remaining macros. The stream stays a short why. `message.completed` (and GET `/v1/chat`) carry structured `suggestions` plus `follow_ups`. Mobile renders cards and server chips only. No web search. No client calorie math. No save/log from chat.

Approved design: `plans/reports/260903-0948-coach-next-meal-followups-brainstorm.md`.

## Phases

| Phase | Name | Status |
|-------|------|--------|
| 1 | [Chat contract slot suggestions follow-ups](./phase-01-chat-contract-slot-suggestions-follow-ups.md) | Complete |
| 2 | [Wire discover and generate follow-ups](./phase-02-wire-discover-and-generate-follow-ups.md) | Completed |
| 3 | [Mobile cards and server chips](./phase-03-mobile-cards-and-server-chips.md) | Completed |

## Architecture

```text
ChatContextBuilder
  timezone + now → local_hour, suggested_meal_slot
  remaining macros (already present)

next_meal (or slot override from text)
  → DiscoverMeals count=3, targets=remaining
       meal_portion_type = snack if slot==snack else main
  → grounding.candidates[]  (macros are facts)
  → stream markdown why
  → persist reply_payload.suggestions
  → structured follow-up call
  → persist reply_payload.follow_ups
  → SSE message.completed { citations, suggestions, follow_ups }
```

### Slot windows (user timezone)

| Local time | Slot |
|---|---|
| 05:00–10:29 | breakfast |
| 10:30–14:29 | lunch |
| 14:30–16:59 | snack |
| 17:00–21:59 | dinner |
| else | snack |

### `reply_payload` (nullable JSON on `chat_message`)

```json
{
  "suggestions": [
    {
      "id": "discover-id-or-null",
      "name": "Egg rice bowl",
      "meal_type": "breakfast",
      "calories": 420,
      "protein_g": 28,
      "carbs_g": 45,
      "fat_g": 12,
      "emoji": "🍳"
    }
  ],
  "follow_ups": [
    { "label": "What's left in my day?", "action": "remaining_budget" },
    { "label": "More breakfast ideas", "action": "next_meal" }
  ]
}
```

`action` ∈ `remaining_budget` | `next_meal` | `day_progress` | `limits`. Unknown → mobile sends `label` as free text.

Missing/old rows: empty lists. Do not DROP/rebuild chat tables.

## Non-negotiable

- Calories/macros on cards come from discover / food_reference path. Client never recomputes.
- Remaining-budget beakers stay `CoachDayContext` / `todaysPlan`.
- `nutrition_numbers_are_traceable` must treat candidate macros as allowed sources.
- Chat still cannot claim it logged/saved a meal.
- Discover timeout/failure/5-per-min → text-only reply, no invented cards.
- Follow-up call failure → omit chips (no mobile hardcoded fallback).
- Follow-ups use a new `ChatFollowUpPort` on the chat OpenAI adapter (`store=false`). Do not call `OpenAIProvider`.
- Discover `meal_portion_type`: `snack` slot → `snack`; breakfast/lunch/dinner → `main`.
- “More ideas” reuses discover `session_id`. Do not retry discover inside the lease.

## Out of scope

Web search, in-thread full recipes, save/log from Coach, OpenAI `web_search` tool, RAG recipe corpus.

## Dependencies

- Chat MVP on `feature/chatbot-single-thread-mvp-6c05` (already present).
- `POST /v1/meal-suggestions/discover` + `SuggestionOrchestrationService`.
- Mobile coach branch `feature/NM-439-in-app-chatbot` for phase 3.

## Success

- 08:00 `next_meal` → 3 breakfast cards whose macros match remaining.
- Cards match payload, not parsed markdown.
- Follow-ups survive hydrate.
- Remaining-budget path unchanged.
- No meal written.

## Next

Implement phase 1 first.

## Validation Log

### Session 1 — 2026-09-03
**Trigger:** `/ck:plan validate`
**Questions asked:** 3

#### Questions & Answers

1. **[Assumptions]** Fact: `generate_discovery()` requires `meal_portion_type` (main|snack|omad). The plan omitted it. How should Coach set it?
   - Options: snack slot → snack; breakfast/lunch/dinner → main (Recommended) | Always main | Always snack
   - **Answer:** snack slot → snack; breakfast/lunch/dinner → main
   - **Rationale:** Matches existing discover portion semantics.

2. **[Architecture]** Chat streams via `OpenAIChatCompletionAdapter` (text-only). Structured output lives on `OpenAIProvider`. Which wiring for follow-ups?
   - Options: New `ChatFollowUpPort` on chat adapter (Recommended) | Call `OpenAIProvider` | Deterministic chips
   - **Answer:** New `ChatFollowUpPort` + thin method on the chat OpenAI adapter (`store=false`)
   - **Rationale:** Keeps chat store=false and avoids coupling to the translation/suggestion provider.

3. **[Risks]** Discover is 5/min. User taps “More breakfast ideas” twice quickly.
   - Options: Reuse session_id; 5/min → text-only (Recommended) | Disable chip 60s | Retry in lease
   - **Answer:** Reuse session_id; 5/min → text-only + no new cards
   - **Rationale:** Discover already paginates via session_id. Retries would blow the chat lease.

#### Confirmed Decisions
- Portion map: snack slot → snack; else main.
- Follow-ups: `ChatFollowUpPort` on chat adapter, not `OpenAIProvider`.
- Burst: reuse `session_id`; degrade on 5/min; no in-lease retry.

#### Action Items
- [x] Write portion map + follow-up port + session reuse into plan + phase 2.

#### Impact on Phases
- Phase 1: list `ChatUserContext` / `ChatMessage` construction sites.
- Phase 2: portion type, `ChatFollowUpPort`, session_id + 5/min degrade.

### Verification Results
- **Tier:** Standard (Fact Checker + Contract Verifier)
- **Claims checked:** 18
- **Verified:** 14 | **Failed:** 2 | **Unverified:** 2

| Claim | Result |
|---|---|
| `ChatContextBuilder` has timezone + remaining macros | VERIFIED (`chat_context_builder.py`) |
| `ChatUserContext.to_prompt_dict` has `today` remaining fields | VERIFIED (`models.py`) |
| `message.completed` currently citations/usage only | VERIFIED (`chat_turn_orchestrator.py`) |
| `chat_message` has no JSON extras column | VERIFIED (`infra/database/models/chat.py`) |
| `generate_discovery(..., count=)` exists default 6 | VERIFIED (`suggestion_orchestration_service.py:345`) |
| Discover route 5/min | VERIFIED (`meal_suggestions.py`) |
| `nutrition_numbers_are_traceable` exists | VERIFIED (`policy.py`) |
| Mobile `activeFollowUps` hardcoded 3 intents | VERIFIED (`coach_thread_controller.dart`) |
| `CoachStreamCompleted` citations-only | VERIFIED (`coach_stream_event.dart`) |
| SSE parser maps citations only | VERIFIED (`http_coach_repository.dart`) |
| `showsDayBeakers` false for nextMeal | VERIFIED (`coach_reply.dart`) |
| OpenAI chat adapter is text stream `store=false` | VERIFIED (`openai_chat_completion_adapter.py`) |
| Structured output on `OpenAIProvider` / LangChain | VERIFIED (`openai_provider.py`, `langchain_openai_adapter.py`) |
| `DiscoverMealsCommand.count` default 6 | VERIFIED |
| Plan said reuse “existing structured-output stack” for follow-ups | FAILED — chat adapter ≠ `OpenAIProvider`. Corrected: `ChatFollowUpPort`. |
| Plan omitted required `meal_portion_type` | FAILED — corrected via validation. |
| Discover session_id pagination works for “more ideas” | UNVERIFIED (exists; Coach cache key not implemented) |
| 8s discover timeout is safe vs real latency | UNVERIFIED |

**ChatUserContext constructors (phase 1 must update):**
`chat_context_builder.py`, `tests/unit/domain/services/chat/test_golden_set.py`, `test_policy.py`, `tests/unit/app/services/test_chat_turn_orchestrator.py`

**ChatMessage constructors:**
`chat_repository_async.py`, `test_chat_routes.py`, `test_chat_turn_orchestrator.py` (multiple)

**CoachStreamCompleted sites:**
`coach_stream_event.dart`, `http_coach_repository.dart`, `stub_coach_repository.dart`, `coach_thread_controller.dart`, `http_coach_repository_test.dart`, `stub_coach_repository_test.dart`, `coach_thread_controller_test.dart`

### Whole-Plan Consistency Sweep
- Slot windows, no-web-search, recommend-only, `reply_payload` shape: consistent across plan + 3 phases.
- Follow-up stack now says `ChatFollowUpPort` (plan + phase 2). No leftover “call OpenAIProvider” as the adopted path.
- Portion type + session reuse documented in plan non-negotiables and phase 2.
- Unresolved: none that block cook. Discover latency (8s) remains an implementation guess.
