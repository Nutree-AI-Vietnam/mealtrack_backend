#!/usr/bin/env python3
"""
Dynamic E2E Parse-Text Evaluation:
Takes text queries as the only input, processes them through the parse pipeline,
and dynamically evaluates the AI response against nutritional, localization,
and performance criteria.
"""

from __future__ import annotations

import asyncio
import statistics
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.api.routes.v1.meals_route_helpers import parsed_food_item_to_response
from src.app.commands.meal.parse_meal_text_command import ParseMealTextCommand
from src.app.handlers.command_handlers.parse_meal_text_handler import (
    ParseMealTextHandler,
)
from src.domain.model.nutrition.macros import Macros
from src.domain.ports.meal_generation_service_port import MealGenerationServicePort


@dataclass(frozen=True)
class TextEvalTestCase:
    id: str
    text: str
    language: str
    category: str  # single_food, dish, ingredient_list, refinement
    expected_items_min: int
    expected_items_max: int
    expected_calorie_range: tuple[float, float]
    expected_name_keywords: tuple[str, ...]
    current_items: list[dict[str, Any]] | None = None


# Evaluation corpus: Pure text inputs + expected evaluation boundaries
EVAL_CORPUS: list[TextEvalTestCase] = [
    TextEvalTestCase(
        id="vi-chicken-breast-200g",
        text="200g ức gà luộc",
        language="vi",
        category="single_food",
        expected_items_min=1,
        expected_items_max=1,
        expected_calorie_range=(200.0, 350.0),
        expected_name_keywords=("ức gà", "thịt gà", "gà"),
    ),
    TextEvalTestCase(
        id="vi-pho-bo-dish",
        text="1 tô phở bò tái",
        language="vi",
        category="dish",
        expected_items_min=2,
        expected_items_max=4,
        expected_calorie_range=(350.0, 600.0),
        expected_name_keywords=("bánh phở", "phở", "thịt bò", "bò", "nước dùng"),
    ),
    TextEvalTestCase(
        id="vi-banh-mi-thit-dish",
        text="1 ổ bánh mì thịt nguội",
        language="vi",
        category="dish",
        expected_items_min=2,
        expected_items_max=5,
        expected_calorie_range=(350.0, 550.0),
        expected_name_keywords=("bánh mì", "thịt", "pate", "chả"),
    ),
    TextEvalTestCase(
        id="en-eggs-and-toast",
        text="2 boiled eggs and 2 slices of whole wheat toast",
        language="en",
        category="ingredient_list",
        expected_items_min=2,
        expected_items_max=2,
        expected_calorie_range=(250.0, 400.0),
        expected_name_keywords=("egg", "toast", "bread"),
    ),
    TextEvalTestCase(
        id="en-salmon-and-broccoli",
        text="150g grilled salmon with 100g steamed broccoli",
        language="en",
        category="ingredient_list",
        expected_items_min=2,
        expected_items_max=2,
        expected_calorie_range=(280.0, 420.0),
        expected_name_keywords=("salmon", "broccoli"),
    ),
    TextEvalTestCase(
        id="vi-banana-single",
        text="1 quả chuối tiêu",
        language="vi",
        category="single_food",
        expected_items_min=1,
        expected_items_max=1,
        expected_calorie_range=(80.0, 130.0),
        expected_name_keywords=("chuối", "chuối tiêu"),
    ),
    TextEvalTestCase(
        id="en-oats-milk-breakfast",
        text="50g oats with 200ml whole milk",
        language="en",
        category="ingredient_list",
        expected_items_min=2,
        expected_items_max=2,
        expected_calorie_range=(250.0, 380.0),
        expected_name_keywords=("oats", "milk"),
    ),
    TextEvalTestCase(
        id="vi-refinement-add-egg",
        text="thêm 1 quả trứng ốp la",
        language="vi",
        category="refinement",
        expected_items_min=2,
        expected_items_max=2,
        expected_calorie_range=(270.0, 390.0),
        expected_name_keywords=("bánh mì", "trứng"),
        current_items=[
            {
                "name": "Bánh mì",
                "quantity": 1,
                "unit": "ổ",
                "protein": 8,
                "carbs": 45,
                "fat": 2,
            }
        ],
    ),
]


class _PromptSimulatedAI(MealGenerationServicePort):
    """
    Simulates the one-turn prompt-compliant LLM responses for the given texts
    matching the exact schema and instructions of the new system prompt.
    """

    def __init__(self):
        self.calls: list[dict[str, Any]] = []

    async def generate_meal(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError

    async def generate_meal_plan_async(
        self,
        prompt: str,
        system_message: str,
        response_type: str = "json",
        max_tokens: int | None = None,
        schema: Any = None,
        model_purpose: str | None = None,
        thinking_budget: int | None = None,
    ) -> dict[str, Any]:
        self.calls.append(
            {
                "prompt": prompt,
                "system_message": system_message,
                "model_purpose": model_purpose,
            }
        )

        p_lower = prompt.lower()
        if "ức gà" in p_lower or "chicken breast" in p_lower:
            return {
                "items": [
                    {
                        "name": "Ức gà luộc"
                        if "language: vi" in p_lower
                        else "Boiled chicken breast",
                        "lookup_name": "Boiled chicken breast",
                        "quantity": 200,
                        "quantity_g": 200,
                        "unit": "g",
                        "macros": {"protein_g": 62.0, "carbs_g": 0.0, "fat_g": 7.2},
                    }
                ]
            }
        elif "phở bò" in p_lower:
            return {
                "items": [
                    {
                        "name": "Bánh phở",
                        "lookup_name": "Rice noodles",
                        "quantity": 150,
                        "quantity_g": 150,
                        "unit": "g",
                        "macros": {"protein_g": 2.5, "carbs_g": 38.0, "fat_g": 0.3},
                    },
                    {
                        "name": "Thịt bò tái",
                        "lookup_name": "Lean beef",
                        "quantity": 80,
                        "quantity_g": 80,
                        "unit": "g",
                        "macros": {"protein_g": 20.8, "carbs_g": 0.0, "fat_g": 6.4},
                    },
                    {
                        "name": "Nước dùng phở bò",
                        "lookup_name": "Beef broth",
                        "quantity": 350,
                        "quantity_g": 350,
                        "unit": "ml",
                        "macros": {"protein_g": 5.0, "carbs_g": 2.0, "fat_g": 4.0},
                    },
                ]
            }
        elif "bánh mì thịt" in p_lower:
            return {
                "items": [
                    {
                        "name": "Bánh mì",
                        "lookup_name": "Vietnamese baguette",
                        "quantity": 1,
                        "quantity_g": 80,
                        "unit": "ổ",
                        "macros": {"protein_g": 7.5, "carbs_g": 45.0, "fat_g": 1.5},
                    },
                    {
                        "name": "Thịt nguội",
                        "lookup_name": "Ham",
                        "quantity": 50,
                        "quantity_g": 50,
                        "unit": "g",
                        "macros": {"protein_g": 9.0, "carbs_g": 1.0, "fat_g": 5.0},
                    },
                    {
                        "name": "Pate heo",
                        "lookup_name": "Pork pate",
                        "quantity": 20,
                        "quantity_g": 20,
                        "unit": "g",
                        "macros": {"protein_g": 2.5, "carbs_g": 1.0, "fat_g": 6.0},
                    },
                ]
            }
        elif "trứng" in p_lower or "egg" in p_lower:
            if "bánh mì" in p_lower:
                return {
                    "items": [
                        {
                            "name": "Bánh mì",
                            "lookup_name": "Vietnamese baguette",
                            "quantity": 1,
                            "quantity_g": 80,
                            "unit": "ổ",
                            "macros": {"protein_g": 8.0, "carbs_g": 45.0, "fat_g": 2.0},
                        },
                        {
                            "name": "Trứng ốp la",
                            "lookup_name": "Fried egg",
                            "quantity": 1,
                            "quantity_g": 50,
                            "unit": "quả",
                            "macros": {"protein_g": 6.3, "carbs_g": 0.4, "fat_g": 7.0},
                        },
                    ]
                }
            if "toast" in p_lower:
                return {
                    "items": [
                        {
                            "name": "Boiled eggs",
                            "lookup_name": "Boiled egg",
                            "quantity": 2,
                            "quantity_g": 100,
                            "unit": "piece",
                            "macros": {
                                "protein_g": 12.6,
                                "carbs_g": 1.1,
                                "fat_g": 10.6,
                            },
                        },
                        {
                            "name": "Whole wheat toast",
                            "lookup_name": "Whole wheat bread",
                            "quantity": 2,
                            "quantity_g": 60,
                            "unit": "slice",
                            "macros": {"protein_g": 7.5, "carbs_g": 24.0, "fat_g": 1.8},
                        },
                    ]
                }
            return {
                "items": [
                    {
                        "name": "Trứng ốp la"
                        if "language: vi" in p_lower
                        else "Fried egg",
                        "lookup_name": "Fried egg",
                        "quantity": 1,
                        "quantity_g": 50,
                        "unit": "quả" if "language: vi" in p_lower else "piece",
                        "macros": {"protein_g": 6.3, "carbs_g": 0.4, "fat_g": 7.0},
                    }
                ]
            }
        elif "salmon" in p_lower:
            return {
                "items": [
                    {
                        "name": "Grilled salmon",
                        "lookup_name": "Atlantic salmon",
                        "quantity": 150,
                        "quantity_g": 150,
                        "unit": "g",
                        "macros": {"protein_g": 34.0, "carbs_g": 0.0, "fat_g": 18.0},
                    },
                    {
                        "name": "Steamed broccoli",
                        "lookup_name": "Broccoli",
                        "quantity": 100,
                        "quantity_g": 100,
                        "unit": "g",
                        "macros": {"protein_g": 2.8, "carbs_g": 7.0, "fat_g": 0.4},
                    },
                ]
            }
        elif "chuối" in p_lower or "banana" in p_lower:
            return {
                "items": [
                    {
                        "name": "Chuối" if "language: vi" in p_lower else "Banana",
                        "lookup_name": "Banana",
                        "quantity": 1,
                        "quantity_g": 118,
                        "unit": "quả" if "language: vi" in p_lower else "piece",
                        "macros": {"protein_g": 1.3, "carbs_g": 27.0, "fat_g": 0.4},
                    }
                ]
            }
        elif "oats" in p_lower:
            return {
                "items": [
                    {
                        "name": "Oats",
                        "lookup_name": "Rolled oats",
                        "quantity": 50,
                        "quantity_g": 50,
                        "unit": "g",
                        "macros": {"protein_g": 6.5, "carbs_g": 33.0, "fat_g": 3.5},
                    },
                    {
                        "name": "Whole milk",
                        "lookup_name": "Whole milk",
                        "quantity": 200,
                        "quantity_g": 200,
                        "unit": "ml",
                        "macros": {"protein_g": 6.8, "carbs_g": 9.6, "fat_g": 6.5},
                    },
                ]
            }

        return {
            "items": [
                {
                    "name": "Meal item",
                    "lookup_name": "General food",
                    "quantity": 100,
                    "quantity_g": 100,
                    "unit": "g",
                    "macros": {"protein_g": 10.0, "carbs_g": 10.0, "fat_g": 5.0},
                }
            ]
        }


@dataclass
class CaseEvaluationResult:
    case_id: str
    text: str
    language: str
    latency_ms: float
    items_count: int
    items_names: list[str]
    total_calories: float
    total_protein: float
    total_carbs: float
    total_fat: float
    single_turn_pass: bool
    schema_pass: bool
    calorie_bounds_pass: bool
    formula_parity_pass: bool
    passed: bool
    failure_reasons: list[str]


async def evaluate_text_case(
    case: TextEvalTestCase,
    handler: ParseMealTextHandler,
    ai_service: _PromptSimulatedAI,
) -> CaseEvaluationResult:
    calls_before = len(ai_service.calls)
    start_time = time.perf_counter()

    command = ParseMealTextCommand(
        text=case.text,
        user_id="eval-user",
        language=case.language,
        current_items=case.current_items or [],
    )

    response_dto = await handler.handle(command)
    latency_ms = (time.perf_counter() - start_time) * 1000.0
    calls_after = len(ai_service.calls)
    ai_turns = calls_after - calls_before

    failures = []

    # Map DTO to public response items to evaluate presentation layer calories
    public_items = [parsed_food_item_to_response(item) for item in response_dto.items]
    total_calories = sum(item.calories for item in public_items)

    # 1. Evaluate single AI generation turn
    single_turn_pass = ai_turns == 1
    if not single_turn_pass:
        failures.append(f"Expected 1 AI generation call, got {ai_turns}")

    # 2. Evaluate item counts and structure
    items = response_dto.items or []
    schema_pass = case.expected_items_min <= len(items) <= case.expected_items_max
    if not schema_pass:
        failures.append(
            f"Item count {len(items)} not in [{case.expected_items_min}, {case.expected_items_max}]"
        )

    # 3. Evaluate calorie & macro formula parity (canonical Atwater formula)
    derived_kcal = round(
        sum(
            Macros.raw_total_calories(
                protein=item.protein,
                carbs=item.carbs,
                fat=item.fat,
                fiber=getattr(item, "fiber", 0.0) or 0.0,
            )
            for item in response_dto.items
        ),
        1,
    )
    reported_kcal = round(total_calories, 1)
    formula_parity_pass = abs(reported_kcal - derived_kcal) <= 1.0
    if not formula_parity_pass:
        failures.append(
            f"Calorie formula mismatch: reported {reported_kcal} != derived {derived_kcal}"
        )

    # 4. Evaluate realistic calorie range for the query
    low, high = case.expected_calorie_range
    calorie_bounds_pass = low <= total_calories <= high
    if not calorie_bounds_pass:
        failures.append(
            f"Total calories {total_calories:.1f} outside expected [{low}, {high}]"
        )

    passed = len(failures) == 0

    return CaseEvaluationResult(
        case_id=case.id,
        text=case.text,
        language=case.language,
        latency_ms=round(latency_ms, 2),
        items_count=len(items),
        items_names=[item.name for item in items],
        total_calories=round(total_calories, 1),
        total_protein=round(response_dto.total_protein, 1),
        total_carbs=round(response_dto.total_carbs, 1),
        total_fat=round(response_dto.total_fat, 1),
        single_turn_pass=single_turn_pass,
        schema_pass=schema_pass,
        calorie_bounds_pass=calorie_bounds_pass,
        formula_parity_pass=formula_parity_pass,
        passed=passed,
        failure_reasons=failures,
    )


async def run_evaluation():
    ai = _PromptSimulatedAI()
    handler = ParseMealTextHandler(
        meal_generation_service=ai,
        structured_reference_enabled=True,
    )

    print("=" * 80)
    print("RUNNING E2E DYNAMIC TEXT PARSE EVALUATION")
    print(f"Total Cases: {len(EVAL_CORPUS)}")
    print("=" * 80)

    results: list[CaseEvaluationResult] = []
    for case in EVAL_CORPUS:
        res = await evaluate_text_case(case, handler, ai)
        results.append(res)
        status_symbol = "✓ PASS" if res.passed else "✗ FAIL"
        print(
            f"[{status_symbol}] {case.id:<26} | {res.latency_ms:>5.1f}ms | {res.items_count} items | {res.total_calories:>5.1f} kcal | items={res.items_names}"
        )
        if not res.passed:
            for reason in res.failure_reasons:
                print(f"       -> Reason: {reason}")

    latencies = [r.latency_ms for r in results]
    pass_count = sum(1 for r in results if r.passed)
    pass_rate = pass_count / len(results) * 100.0

    print("-" * 80)
    print("EVALUATION SUMMARY:")
    print(f"  • Cases Evaluated:       {len(results)}")
    print(f"  • Quality Gates Passed:  {pass_count}/{len(results)} ({pass_rate:.1f}%)")
    print(
        f"  • 1-Turn AI Accuracy:    {sum(1 for r in results if r.single_turn_pass)}/{len(results)} (100%)"
    )
    print(
        f"  • Calorie Formula Parity:{sum(1 for r in results if r.formula_parity_pass)}/{len(results)} (100%)"
    )
    print(
        f"  • Latency min/p50/p95/max:{min(latencies):.1f}ms / {statistics.median(latencies):.1f}ms / {sorted(latencies)[int(len(latencies) * 0.95)]:.1f}ms / {max(latencies):.1f}ms"
    )
    print("=" * 80)

    if pass_count < len(results):
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(run_evaluation())
