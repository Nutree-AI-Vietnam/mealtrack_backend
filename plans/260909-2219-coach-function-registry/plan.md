---
title: "Coach function registry and direct invoke"
description: "Unify Coach on typed functions; chips alias into them; skip retrieval on known actions."
status: completed
priority: P2
effort: 2d
branch: "main"
tags:
  - refactor
  - backend
  - api
  - chat
blockedBy: []
blocks: []
created: "2026-09-09T15:20:41.882Z"
createdBy: "ck:plan"
source: skill
brainstorm: ../reports/chatbot-architecture-review-2026-09-09.md
---

# Coach function registry and direct invoke

## Overview

Prove the scale path from the 9 Sep 2026 review: **one function catalog**, chips as **direct invoke**, model tool-selection only for unlabeled free text. Do not add agents. Do not delete `ChatIntent` this round — map it.

Today every turn embeds + retrieves and offers four tools, even when the client already sent `remaining_budget`. `ChatIntent` and `CHAT_ORCHESTRATOR_TOOLS` are two catalogs for the same four actions. Knowledge search has no intent twin.

## Target

```text
POST content + intent alias OR tool name/args
        |
Resolve via CoachFunction registry (server policy)
        |
Direct invoke                    Free text
Load required context only       Offer tools from registry
Skip retrieve when flagged       Model may call, cap 2 rounds
tools=None, one synthesis         Same executors + contracts
        |
Stream + persist (reply_payload.intent stays alias for mobile)
```

## In / out of scope

**In:** registry, alias map, skip retrieve on known actions, direct-invoke synthesis without tool loop, additive `tool` on POST, capabilities.functions, sanitize accepts function names, fingerprint on resolved function.

**Out:** specialist agents, model swap, nutrition-claim validator, allergen aliases, evidence K1/K6 registry, turn deadline, usage ledger, knowledge embeddings, Discover wiring, deleting `ChatIntent` / changing chip `action` strings mobile already parses.

## Cross-plan

| Relationship | Plan | Notes |
|---|---|---|
| Related | `260903-1012-coach-next-meal-followups` | Phases done. Chip `action` is still a `ChatIntent`. Keep emitting aliases. |
| Related | `plans/reports/chatbot-architecture-review-2026-09-09.md` | Trust/cost items stay later plans. |

## Phases

| Phase | Name | Status |
|-------|------|--------|
| 1 | [Function registry and alias map](./phase-01-function-registry-and-alias-map.md) | Done |
| 2 | [Direct invoke and skip retrieval](./phase-02-direct-invoke-and-skip-retrieval.md) | Done |
| 3 | [API chips and capabilities dual-read](./phase-03-api-chips-and-capabilities-dual-read.md) | Done |

## Dependencies

- Domain stays I/O-free (`tests/architecture/`). Registry = names, schemas, contracts, flags. Executors stay in app.
- Mobile still `send(label, intent: action)` with `remaining_budget` / `next_meal` / `day_progress` / `limits`.
- Calories stay backend SoT; this plan does not change recipe calorie mapping.

## Success

- Chip `remaining_budget` / `day_progress` / `limits`: no embedding, no knowledge retrieve, no tool round.
- `next_meal` chip: still prefetches the card; skip knowledge retrieve.
- Free text: retrieve + tools unchanged enough that existing agent tests pass.
- Unknown `intent` still 422. Unknown `tool.name` 422.
- Both `intent` and `tool` present: **tool wins** (ignore intent). Same resolved function+args as tool-only for fingerprint.
- `POST tool=search_nutrition_knowledge` allowed (retrieve on; no tool loop). Follow-up `action` still aliases only.

## Validation Log

### Verification Results
- Claims checked: 24
- Verified: 23 | Failed: 0 | Unverified: 1
- Tier: Standard (Fact Checker + Contract Verifier)
- Unverified: mobile `CoachIntent` parser lives in Flutter, not this repo. Plan already treats chip `action` aliases as the compatibility contract.

#### Fact Checker (sample)
- `ChatIntent` + `CHAT_INTENTS` — `src/domain/model/chat/models.py:27-50`
- `_INTENT_TEMPLATES` / `intent_template` / `request_fingerprint` — `src/domain/services/chat/policy.py:195-238`
- `CHAT_ORCHESTRATOR_TOOLS` — `src/app/services/chat_turn_orchestrator.py:112`; offered every turn at `:415`
- `_ground` always `asyncio.gather` context + retrieve — `:908-924`
- `next_meal` prefetch — `:392-394`
- `generation["tools"] = None` after first tool batch — `:622`
- Limits executor claims logging — `:603`
- `POST` `intent` — `src/api/schemas/request/chat_requests.py:11`, `src/api/routes/v1/chat.py:93`
- Capabilities `intents` — `src/api/routes/v1/capabilities.py:97`
- Follow-up sanitize allowlist — `src/domain/services/chat/follow_up_schema.py:32`
- Layer boundary test — `tests/architecture/test_layer_boundaries.py`

#### Contract Verifier (callers to touch)
- `request_fingerprint` (chat): orchestrator `:283`, `tests/unit/domain/services/chat/test_policy.py:68-75`. Other `*_request_fingerprint` helpers are unrelated meals APIs — do not change.
- `CHAT_ORCHESTRATOR_TOOLS` readers: orchestrator `:415`, `test_chat_agent_orchestration.py:216`, `test_chat_turn_orchestrator.py:820`
- `CHAT_INTENTS`: models `intent()`, `__init__` export, capabilities, follow_up_schema, adapter `allowed_actions` `:219`
- `prepare_turn`: `chat.py:86`, orchestrator `:267/:339`, `test_chat_routes.py`, `test_chat_turn_orchestrator.py`
- `generate_follow_ups`: port, orchestrator `:643/:1113`, adapter `:200`

### Session 1 — 2026-09-09
**Trigger:** `/ck:plan validate`
**Questions asked:** 4

#### Questions & Answers

1. **[Scope]** Phase 2 skips knowledge retrieve on a next_meal chip (still prefetch the recipe card). Chunks are unused on that path today. Confirm?
   - Options: Skip retrieve on next_meal chips (Recommended) | Keep retrieve on next_meal chips
   - **Answer:** Skip retrieve on next_meal chips
   - **Rationale:** Card/recipe path does not use knowledge chunks.

2. **[Architecture]** Should POST tool=search_nutrition_knowledge be allowed?
   - Options: 422 reject (Recommended) | Allow it
   - **Answer:** Allow it — any registered function is directly invokable
   - **Rationale:** Catalog is functions; do not special-case knowledge search at the API.

3. **[Tradeoffs]** If a client sends both intent=remaining_budget and tool={name:suggest_next_meal}, what should happen?
   - Options: 422 if they resolve differently (Recommended) | tool wins, ignore intent
   - **Answer:** tool wins, ignore intent
   - **Rationale:** Avoid 422 when mobile later sends both; fingerprint follows the tool.

4. **[Contract]** Follow-up chips: aliases only vs also add a tool field?
   - Options: Emit action aliases only (Recommended) | Emit action alias plus tool={name,args}
   - **Answer:** Emit action aliases only
   - **Rationale:** No mobile change this round.

#### Confirmed Decisions
- next_meal chip: skip retrieve — keep phase 2 as written
- All four functions POST.tool-invokable
- Conflict: tool wins
- Chip emit: aliases only; no extra follow-up `tool` field

#### Action Items
- [x] Allow `search_nutrition_knowledge` on POST.tool
- [x] Tool wins when both fields present
- [x] Keep follow-up `action` aliases only

#### Impact on Phases
- Phase 1: knowledge search is not model-only; `preferred_alias` is None
- Phase 2: skip retrieve gated on `needs_retrieval`; knowledge direct-invoke retrieves once
- Phase 3: drop 422-on-conflict and 422-on-knowledge-search

### Whole-Plan Consistency Sweep
- Searched plan + phases for: `422 unless`, `not directly`, `model-only`, `direct_invoke False`, `Conflicting intent+tool 422`. Remaining `422` is unknown intent/unknown tool name only.
- `next_meal` skip retrieve consistent in overview success + phase 2.
- Follow-up emit: aliases only in plan success, phase 1 public invoke key, phase 3.
- No unresolved contradiction. Eligible to implement.
