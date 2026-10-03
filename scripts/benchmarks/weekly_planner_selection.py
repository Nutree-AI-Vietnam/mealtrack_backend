"""Compare deterministic selection against a Git baseline using synthetic meals."""

import argparse
import hashlib
import json
import statistics
import subprocess
import sys
import time
import types
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.domain.model.meal_recommendation import (  # noqa: E402
    CatalogMeal,
    CatalogMealIngredient,
)
from src.domain.model.meal_recommendation.catalog_selection_features import (
    CatalogSelectionFeatures,  # noqa: E402
)
from src.domain.model.weekly_meal_planner import WeeklyMealPlanPreferences  # noqa: E402
from src.domain.services.weekly_meal_planner import (
    weekly_plan_generation_service as optimized,  # noqa: E402
)


def baseline_generator(ref):
    source = subprocess.check_output(
        [
            "git",
            "show",
            f"{ref}:src/domain/services/weekly_meal_planner/weekly_plan_generation_service.py",
        ],
        text=True,
    )
    module = types.ModuleType("planner_benchmark_baseline")
    sys.modules[module.__name__] = module
    exec(compile(source, "git_baseline_generator", "exec"), module.__dict__)
    return module.WeeklyPlanGenerationService()


def meals(count):
    return [
        CatalogMeal(
            id=f"recipe-{i:05}",
            catalog_key=f"synthetic-{i}",
            content_hash="a" * 64,
            name=f"Vegetable rice bowl {i}",
            cuisine="vietnamese",
            description="Tofu and vegetables",
            image_url=None,
            protein_g=Decimal(25 + i % 10),
            carbs_g=Decimal(65 + i % 20),
            fat_g=Decimal(10 + i % 5),
            fiber_g=Decimal(5),
            meal_types=("lunch", "dinner"),
            popularity_rank=i % 100,
            allergen_codes=("soy",),
            ingredients=tuple(
                CatalogMealIngredient(
                    food_reference_id=j,
                    display_name=name,
                    quantity=Decimal(100),
                    unit="g",
                )
                for j, name in enumerate(("Rice", "Tofu", "Vegetables"))
            ),
        )
        for i in range(count)
    ]


def compact(meal):
    haystack = optimized._haystack(meal)
    return replace(
        meal,
        ingredients=(),
        selection_features=CatalogSelectionFeatures(
            haystack,
            optimized._contains_any(haystack, optimized._MEAT_WORDS),
            optimized._contains_any(haystack, optimized._PORK_WORDS),
            optimized.is_non_meal_title(meal.name, meal.tag),
        ),
    )


def run(ref, repeats):
    baseline = baseline_generator(ref)
    current = optimized.WeeklyPlanGenerationService()
    args = {
        "user_id": "synthetic-benchmark",
        "week_start_date": "2026-09-28",
        "daily_calories": 2000,
        "preferences": WeeklyMealPlanPreferences(diet="vegetarian"),
    }
    results = []
    for count in (200, 1000, 5000, 10000):
        canonical = meals(count)
        candidates = [compact(meal) for meal in canonical]
        reference = None
        row = {"recipes": count}
        for label, generator, inputs in [
            ("baseline", baseline, canonical),
            ("optimized", current, canonical),
            ("compact", current, candidates),
        ]:
            generator.generate(inputs, **args)
            durations = []
            for _ in range(repeats):
                start = time.process_time()
                generated = generator.generate(inputs, **args)
                durations.append((time.process_time() - start) * 1000)
                coordinates = [
                    (slot.day_index, slot.slot_index, slot.recipe_id)
                    for slot in generated
                ]
                if reference is None:
                    reference = coordinates
                assert coordinates == reference, "Selection parity changed"
            row[f"{label}_median_cpu_ms"] = round(statistics.median(durations), 3)
        row["selection_digest"] = hashlib.sha256(
            json.dumps(reference).encode()
        ).hexdigest()
        results.append(row)
    print(
        json.dumps(
            {
                "baseline": ref,
                "repeats": repeats,
                "construction_timed": False,
                "rows": results,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", default="HEAD")
    parser.add_argument("--repeats", type=int, default=5)
    options = parser.parse_args()
    if options.repeats < 1:
        parser.error("repeats must be positive")
    run(options.baseline, options.repeats)
