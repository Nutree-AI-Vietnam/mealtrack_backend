---
phase: 2
title: "Direct invoke and skip retrieval"
status: completed
priority: P1
effort: 6h
dependencies:
  - 1
---

# Phase 2: Direct invoke and skip retrieval

## Overview

TDD. When the turn already resolved to a known function, skip embedding/retrieve **unless** `needs_retrieval`, do not offer tools, run the function server-side, then one synthesis with that contract. Free text keeps today’s retrieve + 2-round tool loop.

## Requirements

- Functional: client alias or resolved function from phase 3’s `PreparedChatTurn` is enough; this phase can still take `intent: str | None` and resolve it.
- Functional: `next_meal` chip keeps existing candidate prefetch; skip knowledge retrieve.
- Functional: tool executor uses registry names; after a model tool call, set `reply_payload.intent` via `preferred_alias`, not by duplicating `ChatIntent` if-else.
- Non-functional: do not share SQLAlchemy session across concurrent tasks (existing gather of context+retrieve stays only when retrieve is on).
- Non-functional: existing agent tests still pass for free text.

## Architecture

```text
stream_prepared
  resolved = resolve_coach_function(intent)   # phase 3 adds tool
  if resolved and not spec.needs_retrieval:
      context = await context_builder.build(...)
      chunks = []
  else:
      context, chunks, embedding = await _ground(...)  # today

  if resolved:  # direct invoke
      run executor once (same code path as tool loop)
      put result + output contract in grounding
      generation["tools"] = None
      one _iter_validated_sentences
  else:
      tools = spec.openai_tools()  # all offered_on_free_text
      existing while iteration < 2
```

Executor extraction: move the `if name == "suggest_next_meal"` / `check_daily_progress` / `search_nutrition_knowledge` / `explain_limits_and_guidelines` block behind a function keyed by registry name. Direct invoke and model tool-calls share it.

**Limits copy:** executor result for `explain_limits_and_guidelines` must match read-only product. Current string says “Can help with: logging meals…”. Replace with: explain/suggest/track; cannot log, change targets, or give medical advice. That is in scope because the string moves with the executor.

Schema: if model args are not a dict after JSON parse, treat as `{}` then validate against spec properties. Drop unknown keys. Do not add retries here.

Fingerprint: still `request_fingerprint(content, locale, intent)` until phase 3. Direct invoke behavior must not change idempotency for existing `intent` clients.

## Related Code Files

- Modify: `src/app/services/chat_turn_orchestrator.py` (`_ground` gating, tool loop, `_iter_validated_sentences` tools, executor)
- Modify: `tests/unit/app/services/test_chat_turn_orchestrator.py`
- Modify: `tests/unit/app/services/test_chat_agent_orchestration.py`
- Create if needed: `src/app/services/chat_function_executor.py` (keep orchestrator from growing; domain stays I/O-free)
- Modify: `src/domain/services/chat/policy.py` only if grounding needs a “function result” block (prefer passing meal_candidates-style structured text)

## Implementation Steps

1. Red: orchestrator tests with fake embedding/retrieval:
   - `intent=remaining_budget` → retrieval adapter not called, embedding not called, completion `tools` is None/empty.
   - `intent=day_progress` same.
   - `intent=limits` same.
   - `intent=next_meal` → next-meal fetch called; retrieval not called.
   - `intent=None` → retrieval still called; tools offered.
2. Green: gate `_ground`; direct-invoke branch; extract executor.
3. Keep next_meal prefetch on alias `next_meal` **and** when executor runs `suggest_next_meal` (avoid double-generate if prefetch already filled `suggestions`).
4. `explain_limits` executor copy aligned with `capabilities.read_only`.
5. Run `pytest tests/unit/app/services/test_chat_turn_orchestrator.py tests/unit/app/services/test_chat_agent_orchestration.py tests/unit/domain/services/chat/` — do not run bare `pytest`.

## Success Criteria

- [ ] Remaining-budget and day-progress chips do not call embed or retrieve
- [ ] Limits chip does not retrieve
- [ ] Next-meal chip still produces a recipe card path; no knowledge retrieve
- [ ] Free-text “What's left?” still can call `check_daily_progress` via tools (existing test)
- [ ] `reply_payload.intent` after tool call is still an alias (`day_progress`, not `check_daily_progress`)
- [ ] Limits executor no longer claims logging help

## Risk Assessment

- Skipping retrieve on free text is **out of scope**. Direct invoke of `search_nutrition_knowledge` **does** retrieve (`needs_retrieval=True`). Run `_ground` (or retrieve with `args.query` if it differs from user content). Do not retrieve a second time inside the executor when chunks are already present for that query.
- Direct invoke of progress/limits/next_meal never needs `_merge_retrieved_chunks`.

<!-- Updated: Validation Session 1 - next_meal skip retrieve confirmed; knowledge search direct-invoke retrieves once -->
- Empty chunks + nutrition validator: progress answers must not invent numbers; facts are in USER CONTEXT. Existing validator still number-presence only (known review gap — do not “fix” attribution here).
- If completion adapter treats `tools=None` differently from omit: match how the second loop already sets `generation["tools"] = None`.
