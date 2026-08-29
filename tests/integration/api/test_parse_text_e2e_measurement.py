"""
End-to-end API test and measurement for meal text parsing (/v1/meals/parse-text).
Measures latency, schema contract validity, and calorie calculation accuracy.
"""

from __future__ import annotations

import statistics
import time
from typing import Any
from unittest.mock import patch

from fastapi.testclient import TestClient

import src.api.dependencies.event_bus as eb_module
from src.domain.ports.meal_generation_service_port import MealGenerationServicePort


class _MeasurementMealGenerationService(MealGenerationServicePort):
    """Deterministic, latency-instrumented meal generation service for E2E measurement."""

    def __init__(self, scenarios: dict[str, dict[str, Any]] | None = None):
        self.scenarios = scenarios or {}
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
        # Find matching scenario by keyword in prompt
        for key, payload in self.scenarios.items():
            if key.lower() in prompt.lower():
                return payload

        # Default fallback payload
        return {
            "items": [
                {
                    "name": "Thịt gà",
                    "lookup_name": "Chicken breast",
                    "quantity": 150,
                    "unit": "g",
                    "protein": 31.0,
                    "carbs": 0.0,
                    "fat": 3.6,
                }
            ]
        }


def test_parse_text_e2e_api_measurement_suite(authenticated_client: TestClient):
    """
    Run an end-to-end API measurement suite across standard scenarios:
    1. Single food (Vietnamese)
    2. Dish composition (Vietnamese)
    3. Ingredient list (English)
    4. Multi-item meal (English)
    5. Refinement with existing items
    """
    scenarios = {
        "ức gà": {
            "items": [
                {
                    "name": "Ức gà",
                    "lookup_name": "Chicken breast",
                    "quantity": 200,
                    "unit": "g",
                    "protein": 62.0,
                    "carbs": 0.0,
                    "fat": 7.2,
                }
            ]
        },
        "phở bò": {
            "items": [
                {
                    "name": "Bánh phở",
                    "lookup_name": "Rice noodles",
                    "quantity": 150,
                    "unit": "g",
                    "protein": 2.5,
                    "carbs": 38.0,
                    "fat": 0.3,
                },
                {
                    "name": "Thịt bò tái",
                    "lookup_name": "Lean beef",
                    "quantity": 80,
                    "unit": "g",
                    "protein": 20.8,
                    "carbs": 0.0,
                    "fat": 6.4,
                },
                {
                    "name": "Nước dùng phở bò",
                    "lookup_name": "Beef broth",
                    "quantity": 350,
                    "unit": "ml",
                    "protein": 5.0,
                    "carbs": 2.0,
                    "fat": 4.0,
                },
            ]
        },
        "eggs and toast": {
            "items": [
                {
                    "name": "Eggs",
                    "lookup_name": "Whole egg",
                    "quantity": 2,
                    "unit": "piece",
                    "protein": 12.6,
                    "carbs": 0.8,
                    "fat": 9.5,
                },
                {
                    "name": "Whole wheat toast",
                    "lookup_name": "Whole wheat bread",
                    "quantity": 2,
                    "unit": "slice",
                    "protein": 8.0,
                    "carbs": 24.0,
                    "fat": 2.0,
                },
            ]
        },
        "salmon": {
            "items": [
                {
                    "name": "Grilled salmon",
                    "lookup_name": "Atlantic salmon",
                    "quantity": 150,
                    "unit": "g",
                    "protein": 34.0,
                    "carbs": 0.0,
                    "fat": 18.0,
                },
                {
                    "name": "Steamed broccoli",
                    "lookup_name": "Broccoli",
                    "quantity": 100,
                    "unit": "g",
                    "protein": 2.8,
                    "carbs": 7.0,
                    "fat": 0.4,
                },
            ]
        },
    }

    gen_service = _MeasurementMealGenerationService(scenarios=scenarios)

    test_cases = [
        {
            "name": "Single Food (VN)",
            "payload": {"text": "200g ức gà luộc", "language": "vi"},
            "expected_items": 1,
        },
        {
            "name": "Dish Composition (VN)",
            "payload": {"text": "1 tô phở bò tái", "language": "vi"},
            "expected_items": 3,
        },
        {
            "name": "Ingredient List (EN)",
            "payload": {"text": "2 eggs and toast", "language": "en"},
            "expected_items": 2,
        },
        {
            "name": "Multi-item meal (EN)",
            "payload": {"text": "150g salmon with 100g broccoli", "language": "en"},
            "expected_items": 2,
        },
        {
            "name": "Refinement request",
            "payload": {
                "text": "add 200g ức gà",
                "language": "vi",
                "current_items": [
                    {
                        "name": "Cơm trắng",
                        "quantity": 150,
                        "unit": "g",
                        "protein": 4.0,
                        "carbs": 42.0,
                        "fat": 0.5,
                    }
                ],
            },
            "expected_items": 1,
        },
    ]

    latencies_ms: list[float] = []
    measurement_results: list[dict[str, Any]] = []

    with patch(
        "src.infra.adapters.meal_generation_service.MealGenerationService",
        return_value=gen_service,
    ):
        # Reset cached event bus
        eb_module._configured_event_bus = None

        for tc in test_cases:
            start_time = time.perf_counter()
            response = authenticated_client.post(
                "/v1/meals/parse-text", json=tc["payload"]
            )
            elapsed_ms = (time.perf_counter() - start_time) * 1000.0
            latencies_ms.append(elapsed_ms)

            assert response.status_code == 200, (
                f"Failed case {tc['name']}: {response.text}"
            )
            data = response.json()

            # Contract verification
            assert "items" in data
            assert len(data["items"]) == tc["expected_items"]
            assert "total_calories" in data
            assert "total_protein" in data
            assert "total_carbs" in data
            assert "total_fat" in data
            assert "emoji" in data
            assert "unmatched_terms" in data

            # Derived backend calorie verification (4*P + 4*C + 9*F)
            expected_kcal = round(
                data["total_protein"] * 4.0
                + data["total_carbs"] * 4.0
                + data["total_fat"] * 9.0,
                1,
            )
            assert abs(data["total_calories"] - expected_kcal) <= 1.0

            measurement_results.append(
                {
                    "scenario": tc["name"],
                    "latency_ms": round(elapsed_ms, 2),
                    "status": response.status_code,
                    "items_count": len(data["items"]),
                    "total_calories": data["total_calories"],
                    "total_protein": data["total_protein"],
                    "total_carbs": data["total_carbs"],
                    "total_fat": data["total_fat"],
                }
            )

    # Compute summary statistics
    p50_ms = statistics.median(latencies_ms)
    mean_ms = statistics.mean(latencies_ms)
    min_ms = min(latencies_ms)
    max_ms = max(latencies_ms)

    print("\n" + "=" * 75)
    print(f"{'E2E API PARSE-TEXT MEASUREMENT REPORT':^75}")
    print("=" * 75)
    for r in measurement_results:
        print(
            f"  - {r['scenario']:<25} | HTTP {r['status']} | "
            f"Items: {r['items_count']} | Calories: {r['total_calories']:>5.1f} kcal | "
            f"Latency: {r['latency_ms']:>6.2f} ms"
        )
    print("-" * 75)
    print(
        f"Summary ({len(latencies_ms)} requests): "
        f"min={min_ms:.2f}ms | p50={p50_ms:.2f}ms | mean={mean_ms:.2f}ms | max={max_ms:.2f}ms"
    )
    print("=" * 75 + "\n")

    assert p50_ms < 100.0, f"p50 latency {p50_ms}ms exceeded budget"
