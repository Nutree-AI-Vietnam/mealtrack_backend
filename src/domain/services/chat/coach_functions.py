"""Typed Coach function catalog: aliases, contracts, and OpenAI tool specs.

Domain-only: no I/O. Executors live in the app layer.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

FUNCTION_CHECK_DAILY_PROGRESS = "check_daily_progress"
FUNCTION_SUGGEST_NEXT_MEAL = "suggest_next_meal"
FUNCTION_SEARCH_KNOWLEDGE = "search_nutrition_knowledge"
FUNCTION_EXPLAIN_LIMITS = "explain_limits_and_guidelines"

_OUTPUT_CONTRACTS: dict[str, str] = {
    "remaining_budget": (
        "COACH INTENT remaining_budget. The user text is only the localized label.\n"
        "The app already shows remaining kcal and P/C/F as beakers from Nutree. "
        "Write 1-2 short sentences about what is left. Do not repeat the leftover "
        "numbers. Do not list meals. Do not claim you logged anything."
    ),
    "day_progress": (
        "COACH INTENT day_progress. The user text is only the localized label.\n"
        "The app already shows remaining beakers from Nutree. Write 1-2 short "
        "sentences about how the day is going (nothing logged yet, on track, or "
        "over) using USER CONTEXT only. Do not repeat every macro number."
    ),
    "next_meal": (
        "COACH INTENT next_meal.\n"
        "The app displays the recommended meal card below with photo and macros. "
        "Write 1-2 short sentences: introduce the recommended meal warmly in the user's language and explain why it fits their remaining budget. "
        "Do not repeat exact kcal or gram numbers. "
        "Tell the user they can tap the card to see the full ingredients, recipe steps, and log it. "
        "Never claim you logged or saved a meal."
    ),
    "limits": (
        "COACH INTENT limits. The user text is only the localized label.\n"
        "The app already shows a can/can't card. Write at most two sentences: "
        "you explain the log and suggest meals; you cannot log meals, change "
        "targets, or give medical advice. Do not include nutrition numbers."
    ),
}


@dataclass(frozen=True, slots=True)
class CoachFunctionSpec:
    name: str
    description: str
    parameters: dict[str, Any]
    needs_retrieval: bool
    offered_on_free_text: bool = True
    default_args: dict[str, Any] = field(default_factory=dict)
    # Chip alias used in reply_payload.intent / follow-up action when known.
    chip_alias: str | None = None
    # Key into _OUTPUT_CONTRACTS; progress uses focus-specific contracts.
    output_contract_key: str | None = None


@dataclass(frozen=True, slots=True)
class ResolvedCoachFunction:
    name: str
    args: dict[str, Any]
    spec: CoachFunctionSpec

    @property
    def output_contract(self) -> str:
        if self.name == FUNCTION_CHECK_DAILY_PROGRESS:
            focus = self.args.get("focus", "remaining_budget")
            return _OUTPUT_CONTRACTS.get(str(focus), "")
        key = self.spec.output_contract_key
        if key is None:
            return ""
        return _OUTPUT_CONTRACTS.get(key, "")

    @property
    def preferred_alias(self) -> str | None:
        return preferred_alias(self.name, self.args)


_SPECS: dict[str, CoachFunctionSpec] = {
    FUNCTION_SUGGEST_NEXT_MEAL: CoachFunctionSpec(
        name=FUNCTION_SUGGEST_NEXT_MEAL,
        description=(
            "Generate a personalised meal recommendation that fits the user's remaining calorie and macro budget. "
            "Call this when the user asks for a meal idea, recipe, dinner/lunch/breakfast suggestion, "
            "or says they are hungry — in any language."
        ),
        parameters={
            "type": "object",
            "properties": {
                "slot": {
                    "type": "string",
                    "enum": ["breakfast", "lunch", "dinner", "snack"],
                    "description": "Which meal slot this suggestion targets.",
                },
                "query": {
                    "type": "string",
                    "description": "Optional user preference or dietary constraint for this suggestion.",
                },
            },
            "required": [],
        },
        needs_retrieval=False,
        chip_alias="next_meal",
        output_contract_key="next_meal",
    ),
    FUNCTION_CHECK_DAILY_PROGRESS: CoachFunctionSpec(
        name=FUNCTION_CHECK_DAILY_PROGRESS,
        description=(
            "Check the user's daily progress and remaining calorie and macro budget for today. "
            "Call this when the user asks about remaining calories, daily progress, how much they've eaten, or how much is left."
        ),
        parameters={
            "type": "object",
            "properties": {
                "focus": {
                    "type": "string",
                    "enum": ["remaining_budget", "day_progress"],
                    "description": "Whether the user specifically asked for remaining budget vs overall daily progress.",
                },
            },
            "required": [],
        },
        needs_retrieval=False,
        default_args={"focus": "remaining_budget"},
        chip_alias="remaining_budget",
    ),
    FUNCTION_SEARCH_KNOWLEDGE: CoachFunctionSpec(
        name=FUNCTION_SEARCH_KNOWLEDGE,
        description=(
            "Search Nutree's reviewed and verified nutrition and food knowledge base. "
            "Call this when the user asks specific questions about nutrition science, food safety, ingredient benefits, "
            "vitamins, or dietary guidelines."
        ),
        parameters={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The search query for nutrition knowledge.",
                },
            },
            "required": ["query"],
        },
        needs_retrieval=True,
        chip_alias=None,
        output_contract_key=None,
    ),
    FUNCTION_EXPLAIN_LIMITS: CoachFunctionSpec(
        name=FUNCTION_EXPLAIN_LIMITS,
        description=(
            "Explain Nutree Coach capabilities, guidelines, boundaries, and medical disclaimers. "
            "Call this when the user asks what Nutree Coach can or cannot do, asks for medical advice, or inquires about app capabilities."
        ),
        parameters={
            "type": "object",
            "properties": {},
            "required": [],
        },
        needs_retrieval=False,
        chip_alias="limits",
        output_contract_key="limits",
    ),
}

# Chip intent strings → (function name, default args).
_ALIASES: dict[str, tuple[str, dict[str, Any]]] = {
    "remaining_budget": (
        FUNCTION_CHECK_DAILY_PROGRESS,
        {"focus": "remaining_budget"},
    ),
    "day_progress": (FUNCTION_CHECK_DAILY_PROGRESS, {"focus": "day_progress"}),
    "next_meal": (FUNCTION_SUGGEST_NEXT_MEAL, {}),
    "limits": (FUNCTION_EXPLAIN_LIMITS, {}),
}

# Stable tool order for free-text offerings (matches legacy CHAT_ORCHESTRATOR_TOOLS).
_FREE_TEXT_TOOL_ORDER = (
    FUNCTION_SUGGEST_NEXT_MEAL,
    FUNCTION_CHECK_DAILY_PROGRESS,
    FUNCTION_SEARCH_KNOWLEDGE,
    FUNCTION_EXPLAIN_LIMITS,
)


def resolve_coach_function(
    raw: str | None,
    *,
    args: dict[str, Any] | None = None,
) -> ResolvedCoachFunction | None:
    """Resolve a chip alias or function name to a typed spec + args."""
    if raw is None:
        return None
    key = raw.strip()
    if not key:
        return None

    if key in _ALIASES:
        name, default_args = _ALIASES[key]
        merged = {**default_args, **(args or {})}
        return ResolvedCoachFunction(name=name, args=merged, spec=_SPECS[name])

    spec = _SPECS.get(key)
    if spec is None:
        return None
    merged = {**spec.default_args, **(args or {})}
    return ResolvedCoachFunction(name=spec.name, args=merged, spec=spec)


def preferred_alias(name: str, args: dict[str, Any] | None = None) -> str | None:
    """Map function+args back to the mobile chip alias when one exists."""
    args = args or {}
    if name == FUNCTION_CHECK_DAILY_PROGRESS:
        focus = args.get("focus", "remaining_budget")
        if focus in ("remaining_budget", "day_progress"):
            return str(focus)
        return "remaining_budget"
    spec = _SPECS.get(name)
    if spec is None:
        return None
    return spec.chip_alias


def needs_retrieval(name: str) -> bool:
    spec = _SPECS.get(name)
    if spec is None:
        return True
    return spec.needs_retrieval


def output_contract_for(raw: str | None) -> str:
    """Alias or function name → grounded output contract text."""
    resolved = resolve_coach_function(raw)
    if resolved is None:
        return ""
    return resolved.output_contract


def openai_tools() -> list[dict[str, Any]]:
    """OpenAI tool JSON for free-text turns (offered_on_free_text specs)."""
    tools: list[dict[str, Any]] = []
    for name in _FREE_TEXT_TOOL_ORDER:
        spec = _SPECS[name]
        if not spec.offered_on_free_text:
            continue
        tools.append(
            {
                "type": "function",
                "function": {
                    "name": spec.name,
                    "description": spec.description,
                    "parameters": spec.parameters,
                },
            }
        )
    return tools
