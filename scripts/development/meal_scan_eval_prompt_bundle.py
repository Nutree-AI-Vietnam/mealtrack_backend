"""Prompt snapshots and scan corpus cases for the meal-image evaluator."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path

from src.domain.services.meal_analysis.prompt_eval_loop import (
    PromptEvalCase,
)
from src.domain.strategies.meal_analysis_strategy import BasicAnalysisStrategy

MAX_PROVIDER_CASES = 25
MAX_PROVIDER_GENERATIONS = 50
PROVIDER_TIMEOUT_SECONDS = 300.0
CASE_ID_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)+$")
MICRO_STATUSES = {"known", "unknown", "masked"}


@dataclass(frozen=True)
class ReviewedMealCase:
    evaluation: PromptEvalCase
    language: str
    image_path: Path
    split: str


class _ExplicitPromptStrategy:
    def __init__(self, prompt: str, language: str, label: str):
        self._prompt = prompt
        self._language = language
        self._label = label

    def get_analysis_prompt(self) -> str:
        return self._prompt

    def get_user_message(self) -> str:
        return BasicAnalysisStrategy(language=self._language).get_user_message()

    def get_strategy_name(self) -> str:
        return f"PromptEval:{self._label}"


def _contract_cases() -> list[PromptEvalCase]:
    """Modern schema payloads used only to exercise parser contracts offline."""
    return [
        PromptEvalCase(
            case_id="contract-single-food",
            response_payload={
                "structured_data": {
                    "dish_name": "Chicken Breast",
                    "foods": [
                        {
                            "name": "Chicken Breast",
                            "quantity_g": 150,
                            "macros": {"protein_g": 45, "carbs_g": 0, "fat_g": 5},
                            "micros": None,
                        }
                    ],
                    "confidence": 0.9,
                }
            },
        ),
        PromptEvalCase(
            case_id="contract-mixed-meal",
            response_payload={
                "structured_data": {
                    "dish_name": "Rice Bowl",
                    "foods": [
                        {
                            "name": "Rice",
                            "quantity_g": 200,
                            "macros": {"protein_g": 4, "carbs_g": 56, "fat_g": 1},
                            "micros": None,
                        },
                        {
                            "name": "Egg",
                            "quantity_g": 50,
                            "macros": {"protein_g": 6, "carbs_g": 1, "fat_g": 5},
                            "micros": None,
                        },
                    ],
                    "confidence": 0.84,
                }
            },
        ),
    ]


def resolve_gate_candidate(ranked: list, runtime_candidate_name: str):
    """Retained for callers of the previous contract evaluator API."""
    for candidate in ranked:
        if candidate.name == runtime_candidate_name:
            return candidate
    return ranked[0]


def load_prompt_bundle(
    path: str | Path, family: str | None = None
) -> tuple[dict[str, str], str]:
    source = Path(path).expanduser().resolve().read_text(encoding="utf-8")
    digest = hashlib.sha256(source.encode("utf-8")).hexdigest()
    try:
        raw = json.loads(source)
    except json.JSONDecodeError:
        raw = source
    if isinstance(raw, str):
        prompts = {"*": raw}
    elif isinstance(raw, dict) and isinstance(raw.get("prompts"), dict):
        prompt_entries = raw["prompts"]
        prompts = {}
        for key, entry in prompt_entries.items():
            if (
                not isinstance(key, str)
                or ":" not in key
                or not isinstance(entry, dict)
            ):
                continue
            entry_family, language = key.split(":", 1)
            if family is not None and entry_family != family:
                continue
            text = entry.get("text")
            expected_hash = entry.get("sha256")
            if (
                not isinstance(text, str)
                or not text.strip()
                or not isinstance(expected_hash, str)
            ):
                raise ValueError(
                    f"prompt snapshot entry {key!r} requires text and sha256"
                )
            actual_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
            if actual_hash != expected_hash:
                raise ValueError(f"prompt snapshot hash mismatch for {key!r}")
            prompts[language] = text
        if not prompts:
            raise ValueError(f"prompt snapshot has no entries for family {family!r}")
    elif (
        isinstance(raw, dict)
        and raw
        and all(
            isinstance(key, str) and isinstance(value, str) and value.strip()
            for key, value in raw.items()
        )
    ):
        prompts = dict(raw)
    else:
        raise ValueError(
            "prompt bundle must be text or a non-empty language-to-prompt JSON object"
        )
    if any(not prompt.strip() for prompt in prompts.values()):
        raise ValueError("prompt bundle contains an empty prompt")
    return prompts, digest


def rendered_prompt_hashes(
    bundle: dict[str, str], languages: set[str]
) -> dict[str, str]:
    return {
        language: hashlib.sha256(
            prompt_for_language(bundle, language).encode("utf-8")
        ).hexdigest()
        for language in sorted(languages)
    }


def prompt_for_language(bundle: dict[str, str], language: str) -> str:
    prompt = bundle.get(language) or bundle.get("*")
    if not prompt:
        raise ValueError(
            f"prompt bundle has no rendered prompt for language {language!r}"
        )
    return prompt
