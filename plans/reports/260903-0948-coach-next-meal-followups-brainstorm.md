---
title: Coach next-meal discover + backend follow-ups
type: brainstorm
status: approved
created: 2026-09-03
source: ck:brainstorm
repos:
  - mealtrack_backend
  - nutree_ai
---

# Coach next-meal discover + backend follow-ups

## Summary

`next_meal` reuses `POST /v1/meal-suggestions/discover`. Server picks
breakfast/lunch/dinner/snack from the user timezone. Stream a short why.
Attach 1–3 suggestion cards + follow-up chips on `message.completed`.
Chips are model-authored after the stream (structured output), persisted on
the assistant message. No web search. No client calorie math. No save/log
from chat.

## Requirements

- Expected output: next_meal cards in Coach + backend `follow_ups` on every
  completed reply (hydrate-safe). Mobile drops hardcoded chips.
- Acceptance:
  - Local 08:00 → breakfast discover; 12:00 → lunch; 15:00 → snack; 19:00 → dinner.
  - Cards show discover name + kcal/P/C/F from the suggestion pipeline, not prose.
  - Remaining-budget numbers stay `todaysPlan` / `CoachDayContext`. Never parsed.
  - `message.completed` includes `suggestions[]` (next_meal) and `follow_ups[]`.
  - Reopen thread: last reply still shows its chips.
  - Allergies from profile still constrain discover.
- Out of scope: live web search, in-thread full recipes, logging/saving,
  OpenAI built-in web_search tool, client-owned chips, RAG recipe corpus.
- Constraints: calories = backend source of truth; chat cannot mutate meals;
  SSE stays markdown body + structured sidecar (not HTML, not JSON body).
- Touchpoints: chat orchestrator/context/SSE/message persist; discover
  orchestration; mobile `CoachReply`, controller follow-ups, turn list.

## Existing context

- Backend: FastAPI chat MVP. Context has remaining macros, timezone, recent
  meals. No `local_hour` / `suggested_meal_slot`. Completion port is text
  stream only — no tools.
- `message.completed` today: ids, versions, usage, `citations[]`.
- Discover: `meal_type` required; macros via food_reference / FatSecret / last-resort
  AI; calories `P×4+(C−fiber)×4+fiber×2+F×9`. Rate limit 5/min. Full recipes
  are a second `POST /recipes`.
- Brave search exists for images/barcodes only.
- Mobile: `CoachIntent.nextMeal` chip is client-hardcoded. Beakers hidden on
  next_meal. Meal-suggestion flow already has meal-type chips + discover.

OpenAI Chat Completions / our adapter do **not** emit follow-up chips.
ChatGPT suggested replies are a second structured pass. That is the API.

## Approaches evaluated

| Approach | Pros | Cons | Decision |
|---|---|---|---|
| Pre-fetch discover into grounding + structured cards on completed | One source of macros; stream stays why-text; KISS | Discover latency on next_meal; 5/min quota | **Adopt** |
| Model tool-calls discover mid-turn | Flexible | Slow; easy to leak invented numbers into prose | Reject |
| Client calls discover beside chat | Fast to hack | Two clocks, two sources | Reject |
| Web search then calculate macros | Feels “smart” | Unreliable nutrition, copyright, new calorie path, duplicates discover | Reject |
| Full recipe in thread | Rich | Duplicates meal-detail; blows lease | Reject |
| Client-hardcoded chips | Already shipped | Stale; not locale/intent-aware | Replace |

Follow-up generation:

| Approach | Decision |
|---|---|
| Post-stream structured output `{follow_ups:[{label,action}]}` | **Adopt** |
| Deterministic chips only | Rejected as sole source |
| Keep mobile hardcoded list | Reject |

## Approved design

```text
next_meal / typed “what should I eat”
  → ChatContextBuilder adds local_hour + suggested_meal_slot
  → if next_meal (or slot-seeking typed): DiscoverMeals count=3
       meal_type=slot
       calorie/protein/carbs/fat targets = remaining
  → ground model with candidates (untrusted names; macros are facts)
  → stream markdown why (no invented kcal)
  → persist suggestions JSON on assistant message
  → second cheap structured call → follow_ups
  → SSE message.completed { citations, suggestions, follow_ups }
```

### Meal slot (user timezone)

| Local hour | Slot |
|---|---|
| 05:00–10:29 | breakfast |
| 10:30–14:29 | lunch |
| 14:30–16:59 | snack |
| 17:00–21:59 | dinner |
| else | snack |

Typed “I want dinner” / a dinner-labeled chip overrides the clock.

### Suggestion card (backend payload)

Each card: `name`, `calories`, `protein_g`, `carbs_g`, `fat_g`, `meal_type`,
optional `emoji` / discover id. Mobile renders; does not recompute calories.

### Follow-ups

- 2–3 chips. `label` localized. `action` ∈ existing intents:
  `remaining_budget` | `next_meal` | `day_progress` | `limits`.
- Unknown action → send `label` as free text, no intent.
- Persist on assistant message. Hydrate GET `/v1/chat` returns them.
- Mobile `activeFollowUps` reads last completed reply only.

### Latency / quota

- Discover `count=3` (not 10). “More ideas” reuses `session_id`.
- If discover fails/times out: text-only next_meal from remaining macros;
  no fake cards.
- Follow-up call failure: omit chips (no client fallback list).

## Risks

- Discover + chat in one lease (90s). Guard with a tight timeout.
- 5/min discover limit vs 40/day chat budget — cache session per slot/day.
- Model may still write kcal in prose; keep `nutrition_numbers_are_traceable`
  including discover candidate numbers.
- Structured follow-up extra tokens/cost — keep max 3 chips, small model if
  already used elsewhere; else same chat model with tiny max tokens.

## Success

- 08:00 next_meal → 3 breakfast cards whose macros fit remaining.
- Cards match discover payload, not parsed markdown.
- Follow-ups appear only from `message.completed` / hydrate.
- next_meal does not log a meal.
- Remaining-budget path unchanged (beakers + day snapshot).

## Next

`/ck:plan` (default) — new feature, not a refactor of calorie logic.
`--tdd` only if we fold follow-up persist into the existing chat contract tests
as the first lock.
