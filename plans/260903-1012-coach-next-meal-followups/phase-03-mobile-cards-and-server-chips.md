---
phase: 3
title: Mobile cards and server chips
status: completed
priority: P2
effort: 5h
dependencies:
  - 2
---

# Phase 3: Mobile cards and server chips

## Overview

Parse `suggestions` and `follow_ups` from SSE completed + GET `/v1/chat`. Render compact next-meal cards. Replace hardcoded chips with the last completed reply’s follow-ups.

## Requirements

- Functional: cards from payload only; chips from payload only.
- Non-functional: `NumberFormattingUtils` for display; no `toStringAsFixed`; no calorie recompute.

## Architecture

`CoachStreamCompleted` gains `suggestions` + `followUps`. HTTP repo maps GET messages the same way.

`CoachReply` already has `day` / `intent` / `showsDayBeakers` (beakers stay off for `nextMeal`). Add:

- `suggestions: List<CoachMealSuggestion>`
- `followUps: List<CoachFollowUp>` (`label`, `action` → `CoachIntent?`)

Controller: on complete, copy payload onto the reply. `activeFollowUps` becomes the last completed reply’s follow-ups (not the static three). Empty list → no chips.

UI: compact card row/column under `_ReplyTurn` when `suggestions` is non-empty — name, meal_type, kcal + P/C/F. Recommend only; no save button. Reuse theme tokens; painted cups optional, not required (cards are not the day beakers).

Hydrate: old messages without payload → no cards, no chips.

## Related Code Files

- Modify: `lib/features/coach/domain/entities/coach_reply.dart`
- Modify: `lib/features/coach/domain/entities/coach_stream_event.dart`
- Modify: `lib/features/coach/application/providers/coach_thread_controller.dart`
- Modify: `lib/features/coach/data/repositories/http_coach_repository.dart`
- Modify: `lib/features/coach/presentation/widgets/coach_turn_list.dart`
- Modify: `lib/features/coach/presentation/widgets/coach_empty_state.dart` (empty-state chips may stay as entry intents)
- Create: `lib/features/coach/presentation/widgets/coach_meal_suggestion_card.dart`
- Test: controller complete/hydrate mapping; reply.showsDayBeakers still false for nextMeal; chips empty when payload empty

Repo: `nutree_ai` on `feature/NM-439-in-app-chatbot`.

## Implementation Steps

1. Domain types + `appendDelta`/`complete` preserve suggestions/follow-ups.
2. SSE/GET parsers: missing keys → `[]`.
3. Controller `activeFollowUps` reads last reply; tap still `send(label, intent: action)`.
4. Card widget: formatCalories / formatDecimal; Semantics label = name + calories.
5. Do not show beakers on next_meal (existing `showsDayBeakers` rule).
6. Empty-state chips before the first turn may remain client-owned (no assistant message yet). After the first completed reply, only server chips.

## Success Criteria

- [x] next_meal completed event with 3 suggestions renders 3 cards matching payload numbers.
- [x] Typed/chip remaining_budget still shows beakers, not meal cards.
- [x] Last reply with `follow_ups=[]` shows no chips.
- [x] Hydrate restores cards + chips on that assistant message.
- [x] Unknown `action` sends free text.
- [x] `flutter analyze` + focused `test/features/coach/**`.

## Risk Assessment

Do not parse meal names/macros from markdown if payload is empty. Empty-state chips are the only remaining client list — document that so we do not “fix” them back onto completed turns.
