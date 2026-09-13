---
phase: 3
title: "API chips and capabilities dual-read"
status: completed
priority: P2
effort: 4h
dependencies:
  - 2
---

# Phase 3: API chips and capabilities dual-read

## Overview

TDD. Additive API: clients may send `tool` instead of `intent`. Follow-up sanitizer accepts aliases **and** function names, **emits aliases**. Capabilities keep `intents` and add `functions`. Idempotency fingerprint hashes the resolved function + args so alias and tool name collide.

## Requirements

- Functional: `POST /v1/chat/messages` still accepts `intent` enum only from `ChatIntent`.
- Functional: optional `tool: { name: str, args?: object }`. Name must be a registry function. Args validated/defaulted by registry.
- Functional: `intent` and `tool` both absent → free text.
- Functional: both present → **tool wins**; ignore `intent`. Log at info. Fingerprint uses the tool resolve only.
- Functional: chips: `sanitize_follow_ups` accepts `next_meal` and `suggest_next_meal`; output `action` is `next_meal`.
- Non-functional: no mobile change required. Unknown extra JSON fields on follow-ups already ignored by Flutter if it only reads `label`/`action`.

## Architecture

Request (additive):

```python
class ChatToolCallRequest(BaseModel):
    name: str
    args: dict[str, Any] = Field(default_factory=dict)

class ChatMessageCreateRequest(BaseModel):
    content: str
    locale: str | None = None
    intent: ChatIntent | None = None
    tool: ChatToolCallRequest | None = None
```

Route resolves via `resolve_coach_function` / `resolve_coach_tool` before `prepare_turn`. Pass a single `intent` alias into orchestrator **or** extend `PreparedChatTurn` with `function_name` + `function_args`. Prefer extending `PreparedChatTurn` so fingerprint can use resolved identity:

```python
request_fingerprint(content, locale, function_name, args)
# intent-only client: remaining_budget → check_daily_progress + {focus: remaining_budget}
```

**Do not** change emitted follow-up `action` strings. Update `_FOLLOW_UP_INSTRUCTIONS` `allowed_actions` to still list aliases. Sanitizer: if action is a function name, map through `preferred_alias` before uniqueness.

Capabilities `/v1/capabilities/chat`:

```python
"intents": list(CHAT_INTENTS),  # unchanged order
"functions": [
  {"name": "...", "aliases": ["..."], "direct_invoke": True, "needs_retrieval": False},
  ...
],
```

`search_nutrition_knowledge`: `direct_invoke` True. No chip alias (`preferred_alias` → `None`; omit `reply_payload.intent`). Capabilities `aliases: []`.

`out_of_scope_follow_ups` stays alias actions.

Fingerprint change: old in-flight keys used `{content,intent,locale}`. New turns use resolved function. Same chip tap still matches if we include equivalent identity. Document: replay of *new* keys after deploy is fine; do not migrate old fingerprints.

## Related Code Files

- Modify: `src/api/schemas/request/chat_requests.py`
- Modify: `src/api/routes/v1/chat.py`
- Modify: `src/api/routes/v1/capabilities.py`
- Modify: `src/app/services/chat_turn_orchestrator.py` (`PreparedChatTurn`, `prepare_turn`, fingerprint)
- Modify: `src/domain/services/chat/policy.py` (`request_fingerprint`)
- Modify: `src/domain/services/chat/follow_up_schema.py`
- Modify: `src/infra/adapters/openai_chat_completion_adapter.py` (`_FOLLOW_UP_INSTRUCTIONS` still alias list)
- Modify: `src/domain/ports/chat_follow_up_port.py` (keep `intent` param as alias; optional later)
- Modify: `tests/unit/api/routes/test_chat_routes.py`
- Modify: `tests/unit/domain/services/chat/test_follow_up_schema.py`
- Modify: `tests/unit/domain/services/chat/test_policy.py` (fingerprint if signature changes — add overload/default to keep old tests compiling)

## Implementation Steps

1. Red: `test_post_forwards_structured_intent` still green. New: `tool.name=check_daily_progress` + `args.focus=remaining_budget` reaches prepare. Unknown tool 422. `tool.name=search_nutrition_knowledge` 200 (retrieve on). Conflicting intent+tool: prepare uses tool, not intent.
2. Red: sanitize `action=suggest_next_meal` → emitted `next_meal`. `action=save_meal` still dropped.
3. Red: capabilities body includes `functions` and unchanged `intents`.
4. Green: implement resolve in route or a small `src/app/services/chat_invoke.py` used by the route (keep route thin).
5. Fingerprint tests: alias POST and tool POST with same content/locale/focus hash equal.
6. `pytest tests/unit/api/routes/test_chat_routes.py tests/unit/domain/services/chat/ tests/unit/app/services/test_chat_turn_orchestrator.py tests/unit/app/services/test_chat_agent_orchestration.py`

## Success Criteria

- [ ] Existing mobile `intent` POST unchanged (200 + prepare intent alias)
- [ ] New `tool` POST works for all four functions including `search_nutrition_knowledge`
- [ ] Follow-up `action` values remain the four aliases
- [ ] Capabilities advertise `functions` without removing `intents`
- [ ] No Flutter files in this repo — contract note only

## Risk Assessment

- Both fields: tool wins. A buggy client can ignore its own chip intent; cheaper than 422 on a field mobile might start sending together.
- Do not emit function names as `action`. No extra `tool` field on follow-ups this round. Later mobile cutover can switch POST to `tool` using capabilities.functions.

<!-- Updated: Validation Session 1 - tool wins on conflict; knowledge search POST allowed; aliases-only emit -->
- OpenAPI will show `tool` automatically from the Pydantic model; no separate docs campaign required beyond capabilities.
