---
phase: 2
title: Wire discover and generate follow-ups
status: completed
priority: P2
effort: 8h
dependencies:
  - 1
---

# Phase 2: Wire discover and generate follow-ups

## Overview

On `next_meal`, pre-fetch 3 discover candidates and ground the model. After the stream, a small structured-output call authors 2–3 follow-up chips. Persist both on `reply_payload`. Discover/follow-up failures degrade; they do not invent numbers.

## Requirements

- Functional: next_meal cards + chips; other intents get chips only.
- Non-functional: discover timeout inside the 90s lease; respect discover 5/min.

## Architecture

### Discover (next_meal only)

Call existing `SuggestionOrchestrationService.generate_discovery` (or the command handler) with:

- `meal_type` = context `suggested_meal_slot` unless user text clearly names breakfast/lunch/dinner/snack
- `meal_portion_type` = `snack` if slot is snack, else `main` <!-- Updated: Validation Session 1 - portion map -->
- `count` = 3
- calorie/protein/carbs/fat targets = remaining (ints; skip None)
- `language` = resolved chat locale
- reuse discover `session_id` for “More ideas”; on 5/min or timeout return no new cards

Map discover dicts → `reply_payload.suggestions`. Inject the same list into `build_grounding_message` as `MEAL CANDIDATES` (macros are facts; names untrusted).

Timeout: ~8s. On timeout/error/5-per-min: empty suggestions, continue stream. Do not retry inside the lease.

Extend `nutrition_numbers_are_traceable` source text with candidate numbers so the model may restate them.

### Follow-ups (every completed turn)

After final text exists, one structured call via `ChatFollowUpPort` on the chat OpenAI adapter (`store=false`, tiny max tokens). Do not call `OpenAIProvider`. <!-- Updated: Validation Session 1 - follow-up port -->

```json
{ "follow_ups": [ { "label": "...", "action": "remaining_budget" } ] }
```

Input: locale, intent, slot, last user line, assistant text, whether suggestions exist. Max 3 chips. `action` must be an existing `ChatIntent` or omit the chip.

Failure/timeout (~2s): `follow_ups=[]`. Do not fall back to a server-hardcoded list (matches approved design).

Do **not** put follow-ups in the streamed markdown.

### Completion port

Keep `ChatCompletionPort.stream` text-only. Add `ChatFollowUpPort` implemented on `OpenAIChatCompletionAdapter`. Do not add web_search tools.

## Related Code Files

- Modify: `src/app/services/chat_turn_orchestrator.py`
- Modify: `src/domain/services/chat/policy.py` (`build_grounding_message`, traceable numbers)
- Modify: `src/api/dependencies/chat.py` (wire discover + follow-up ports)
- Create: `src/domain/services/chat/follow_up_schema.py` (Pydantic)
- Create: `src/app/services/chat_next_meal_candidates.py` (discover adapter; keep orchestrator thin)
- Modify: OpenAI chat adapter or a sibling adapter for the structured follow-up
- Test: orchestrator with fakes — next_meal attaches 3 cards; discover fail → no cards; follow-up fail → empty chips; remaining_budget never calls discover

## Implementation Steps

1. Fake discover + follow-up ports in unit tests first (lock degrade paths).
2. `chat_next_meal_candidates` maps remaining + slot → 3 cards. No calorie formula here.
3. Orchestrator: fetch candidates before stream when intent is `next_meal`.
4. Grounding includes candidates; traceable-numbers includes them.
5. After complete: structured follow-up; persist `reply_payload`; SSE.
6. Cache session_id only if discover already returns one — do not invent a parallel session store.

## Success Criteria

- [x] next_meal + fake discover → 3 suggestions on completed + persist.
- [x] Discover timeout or 5/min → completed with `suggestions=[]` and a text reply.
- [x] Second next_meal in the same slot reuses discover `session_id` when one exists.
- [x] remaining_budget / day_progress / limits never call discover.
- [x] Follow-up success → 2–3 valid actions; invalid actions dropped.
- [x] Follow-up timeout → `follow_ups=[]`.
- [x] Model restating a candidate kcal passes traceable-numbers.
- [x] Chat still cannot claim it saved a meal (existing safety regex).

## Risk Assessment

Discover is the slow/expensive piece. Keep it off the remaining-budget path. If 5/min trips, degrade to text. Do not retry discover inside the lease.
