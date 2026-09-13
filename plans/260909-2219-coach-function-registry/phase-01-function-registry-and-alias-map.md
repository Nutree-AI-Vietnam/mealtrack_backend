---
phase: 1
title: "Function registry and alias map"
status: completed
priority: P1
effort: 4h
dependencies: []
---

# Phase 1: Function registry and alias map

## Overview

TDD. Domain owns one `CoachFunction` catalog. `ChatIntent` values become aliases. Output contracts move off the parallel `_INTENT_TEMPLATES` map. Orchestrator still uses the old path until phase 2.

## Requirements

- Functional: resolve alias or function name → spec + default args. Unknown → free text (`None`).
- Functional: `remaining_budget` and `day_progress` are one function (`check_daily_progress`) with different `focus`.
- Non-functional: no I/O in domain. OpenAI tool JSON is data on the spec, not a live client.

## Architecture

```python
# src/domain/services/chat/coach_functions.py  (sketch)

FUNCTION_CHECK_DAILY_PROGRESS = "check_daily_progress"
FUNCTION_SUGGEST_NEXT_MEAL = "suggest_next_meal"
FUNCTION_SEARCH_KNOWLEDGE = "search_nutrition_knowledge"
FUNCTION_EXPLAIN_LIMITS = "explain_limits_and_guidelines"

ALIASES = {
    "remaining_budget": (FUNCTION_CHECK_DAILY_PROGRESS, {"focus": "remaining_budget"}),
    "day_progress": (FUNCTION_CHECK_DAILY_PROGRESS, {"focus": "day_progress"}),
    "next_meal": (FUNCTION_SUGGEST_NEXT_MEAL, {}),
    "limits": (FUNCTION_EXPLAIN_LIMITS, {}),
}

# needs_retrieval:
#   check_daily_progress False
#   explain_limits_and_guidelines False
#   suggest_next_meal False   # card/recipe path; knowledge chunks unused
#   search_nutrition_knowledge True  # no chip alias; POST.tool allowed (validation)
```

`intent_template()` in `policy.py` becomes a thin wrapper: alias or function name → `spec.output_contract`, missing → existing free-text contract. Do not keep two copies of the four COACH INTENT strings.

`CHAT_ORCHESTRATOR_TOOLS` in the orchestrator can keep working in phase 1 if tests import it; phase 2 must generate that list from the registry (`offered_on_free_text=True` for all four current tools).

Public invoke key for chips stays the **alias** (`remaining_budget`, not `check_daily_progress`) so mobile `CoachIntent` still parses. Registry must round-trip: function+args → preferred alias for `reply_payload.intent` and follow-up `action`.

## Related Code Files

- Create: `src/domain/services/chat/coach_functions.py`
- Create: `tests/unit/domain/services/chat/test_coach_functions.py`
- Modify: `src/domain/services/chat/policy.py` (`intent_template`, keep `build_grounding_message` signature)
- Modify: `tests/unit/domain/services/chat/test_policy.py` (still pass with alias strings)
- Do not delete: `src/domain/model/chat/models.py` `ChatIntent`

## Implementation Steps

1. Red: tests for `resolve_coach_function(raw)` covering aliases, function names, default args, unknown, empty, `search_nutrition_knowledge` (no alias), `preferred_alias(name, args)`, `needs_retrieval`, `openai_tools()` length/names.
2. Green: implement registry. Move the four `_INTENT_TEMPLATES` bodies onto specs. Fix `explain_limits` **result copy** later in phase 2; this phase only moves the **output contract** (already read-only: cannot log).
3. `intent_template("remaining_budget")` still contains `COACH INTENT remaining_budget` so existing policy tests stay green.
4. `ruff format` / `ruff check` on new files. No orchestrator behavior change yet.

## Success Criteria

- [ ] `resolve_coach_function("remaining_budget")` → `check_daily_progress` + `focus=remaining_budget`
- [ ] `resolve_coach_function("check_daily_progress")` works; missing focus defaults to `remaining_budget`
- [ ] `preferred_alias("search_nutrition_knowledge", {})` → `None`
- [ ] `needs_retrieval` false for progress, limits, next_meal; true for knowledge search
- [ ] Existing `test_policy.py` grounding/intent tests pass without rewriting contracts
- [ ] Domain module imports nothing from `src.app` / `src.infra`

## Risk Assessment

- Drift if orchestrator tools stay hardcoded after this phase — phase 2 must switch `CHAT_ORCHESTRATOR_TOOLS` to `openai_tools()`.
- Do not invent a fifth function. YAGNI.

<!-- Updated: Validation Session 1 - search_nutrition_knowledge is POST.tool-allowed, not model-only -->
