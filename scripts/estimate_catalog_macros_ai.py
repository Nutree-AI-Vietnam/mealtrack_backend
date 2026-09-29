"""Estimate macronutrients for catalog recipes using OpenAI and persist them.

This script estimates macro totals (calories, protein, carbs, fat, fiber, sugar)
for catalog recipes that do not have food reference mappings, and persists
the results into `meal_catalog.recipe_payload['nutrition']` and/or the
manifest JSON file (e.g. `scripts/data/imported-excel-recipes.json`).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from openai import AsyncOpenAI
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from tenacity import retry, stop_after_attempt, wait_exponential

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.domain.model.nutrition.macros import Macros
from src.infra.database.models.meal_recommendation import MealCatalogORM

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


def _format_ingredients(ingredients: list[dict[str, Any]]) -> str:
    lines = []
    for ing in ingredients:
        name = ing.get("name") or ing.get("display_name") or "Nguyên liệu"
        qty = ing.get("quantity")
        unit = ing.get("unit") or ""
        cat = ing.get("category") or ""
        qty_str = f"{qty} {unit}".strip() if qty is not None else "vừa đủ"
        cat_str = f" ({cat})" if cat else ""
        lines.append(f"- {name}: {qty_str}{cat_str}")
    return "\n".join(lines)


def _build_prompt(recipe: dict[str, Any]) -> str:
    name = recipe.get("name", "Món ăn")
    cuisine = recipe.get("cuisine", "vietnamese")
    servings = recipe.get("base_servings") or 1
    ingredients_text = _format_ingredients(recipe.get("ingredients", []))
    steps = recipe.get("steps", [])
    steps_preview = ""
    if steps:
        step_descs = [s.get("description", "") for s in steps[:3] if s.get("description")]
        if step_descs:
            steps_preview = "\nCách chế biến sơ lược:\n" + "\n".join(f"- {d}" for d in step_descs)

    return f"""You are an expert nutritionist and dietitian specializing in Vietnamese and Asian cuisine.
Given the recipe details below, estimate the macronutrients PER SERVING.

Dish: {name}
Cuisine: {cuisine}
Base Servings: {servings}
Ingredients for the entire recipe:
{ingredients_text}{steps_preview}

Instructions:
1. Estimate total nutritional content for the whole dish, then divide by base servings ({servings}) to get per-serving numbers.
2. Return strictly a JSON object with:
   - "protein": float (grams of protein per serving, >= 0)
   - "carbs": float (grams of total carbohydrates per serving, >= 0)
   - "fat": float (grams of fat per serving, >= 0)
   - "fiber": float (grams of dietary fiber per serving, >= 0)
   - "sugar": float (grams of sugar per serving, >= 0)
3. Do not include extra text, commentary, or markdown formatting outside JSON.
"""


def _sanitize_macro_result(raw: dict[str, Any]) -> dict[str, Any]:
    p = max(0.0, float(raw.get("protein", 0.0)))
    c = max(0.0, float(raw.get("carbs", 0.0)))
    f = max(0.0, float(raw.get("fat", 0.0)))
    fib = max(0.0, float(raw.get("fiber", 0.0)))
    sugar = max(0.0, float(raw.get("sugar", 0.0)))
    if fib > c:
        fib = c

    # Canonical backend-derived calories
    calories = round(Macros.raw_total_calories(p, c, f, fib))
    if calories <= 0:
        # Fallback for placeholder ingredient quantities (e.g. 1g seeds)
        p = max(p, 6.0)
        c = max(c, 24.0)
        f = max(f, 4.0)
        fib = max(fib, 2.0)
        sugar = max(sugar, 5.0)
        calories = round(Macros.raw_total_calories(p, c, f, fib))

    return {
        "calories": calories,
        "protein": round(p, 1),
        "carbs": round(c, 1),
        "fat": round(f, 1),
        "fiber": round(fib, 1),
        "sugar": round(sugar, 1),
    }


class MacroEstimator:
    def __init__(self, client: AsyncOpenAI, model: str, semaphore: asyncio.Semaphore):
        self._client = client
        self._model = model
        self._semaphore = semaphore

    @retry(wait=wait_exponential(multiplier=1, min=1, max=10), stop=stop_after_attempt(4))
    async def estimate_one(self, recipe: dict[str, Any]) -> dict[str, Any]:
        async with self._semaphore:
            prompt = _build_prompt(recipe)
            response = await self._client.chat.completions.create(
                model=self._model,
                messages=[{"role": "user", "content": prompt}],
                response_format={"type": "json_object"},
                max_completion_tokens=200,
            )
            content = response.choices[0].message.content or "{}"
            parsed = json.loads(content)
            return _sanitize_macro_result(parsed)


async def run_estimation(
    recipes: list[dict[str, Any]],
    estimator: MacroEstimator,
    force: bool = False,
) -> int:
    to_estimate: list[tuple[int, dict[str, Any]]] = []
    for idx, r in enumerate(recipes):
        if force or "nutrition" not in r or not r["nutrition"].get("calories"):
            to_estimate.append((idx, r))

    total = len(to_estimate)
    logger.info("Found %d recipes needing nutrition estimation (out of %d total)", total, len(recipes))
    if total == 0:
        return 0

    completed = 0

    async def _worker(idx: int, rec: dict[str, Any]):
        nonlocal completed
        try:
            nutr = await estimator.estimate_one(rec)
            rec["nutrition"] = nutr
            completed += 1
            if completed % 25 == 0 or completed == total:
                logger.info("Progress: [%d/%d] estimated (latest: %s -> %d kcal, P:%.1f, C:%.1f, F:%.1f)",
                            completed, total, rec.get("name"), nutr["calories"], nutr["protein"], nutr["carbs"], nutr["fat"])
        except Exception as exc:
            logger.error("Failed to estimate nutrition for %s: %s", rec.get("name"), exc)

    tasks = [_worker(idx, rec) for idx, rec in to_estimate]
    await asyncio.gather(*tasks)
    return completed


def update_database(recipes: list[dict[str, Any]], db_url: str) -> int:
    """Update recipe_payload['nutrition'] in meal_catalog table."""
    logger.info("Connecting to database: %s", db_url.split("@")[-1] if "@" in db_url else db_url)
    engine = create_engine(db_url)
    updated_count = 0
    with Session(engine) as session:
        for r in recipes:
            catalog_key = r.get("recipe_key")
            nutr = r.get("nutrition")
            if not catalog_key or not nutr:
                continue

            row = session.execute(
                select(MealCatalogORM).where(MealCatalogORM.catalog_key == catalog_key)
            ).scalar_one_or_none()

            if row is not None:
                payload = dict(row.recipe_payload or {})
                payload["nutrition"] = nutr
                row.recipe_payload = payload
                updated_count += 1

        session.commit()
    logger.info("Successfully updated database: %d rows updated in meal_catalog", updated_count)
    return updated_count


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Estimate catalog macros using OpenAI.")
    parser.add_argument(
        "--manifest",
        default=str(Path(__file__).resolve().parent / "data" / "imported-excel-recipes.json"),
        help="Path to recipe manifest JSON file.",
    )
    parser.add_argument(
        "--update-manifest",
        action="store_true",
        help="Write estimated macros back to the manifest JSON.",
    )
    parser.add_argument(
        "--update-db",
        action="store_true",
        help="Update recipe_payload['nutrition'] in meal_catalog table.",
    )
    parser.add_argument(
        "--database-url",
        default=None,
        help="Database URL (defaults to DATABASE_URL_DIRECT or DATABASE_URL from .env).",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Limit number of recipes to process (useful for testing).",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=20,
        help="Maximum concurrent OpenAI requests.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Force re-estimation even if nutrition already exists.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run estimation without saving to manifest or database.",
    )
    return parser.parse_args()


def main() -> None:
    load_dotenv()
    args = parse_args()

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        logger.error("OPENAI_API_KEY not found in environment.")
        sys.exit(1)

    model = os.getenv("OPENAI_TEXT_MODEL", "gpt-5.4-mini-2026-03-17")
    manifest_path = Path(args.manifest)
    if not manifest_path.exists():
        logger.error("Manifest file not found: %s", manifest_path)
        sys.exit(1)

    with open(manifest_path, encoding="utf-8") as f:
        data = json.load(f)

    recipes = data.get("recipes", [])
    if args.limit:
        recipes_to_process = recipes[: args.limit]
    else:
        recipes_to_process = recipes

    client = AsyncOpenAI(api_key=api_key)
    semaphore = asyncio.Semaphore(args.concurrency)
    estimator = MacroEstimator(client=client, model=model, semaphore=semaphore)

    logger.info("Starting estimation for %d recipes with concurrency %d using model %s",
                len(recipes_to_process), args.concurrency, model)

    asyncio.run(run_estimation(recipes_to_process, estimator, force=args.force))

    if args.dry_run:
        logger.info("Dry-run complete. Sample estimated nutrition:")
        for r in recipes_to_process[:3]:
            logger.info("  %s: %s", r.get("name"), r.get("nutrition"))
        return

    if args.update_manifest:
        logger.info("Writing updated recipes to %s", manifest_path)
        with open(manifest_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        logger.info("Manifest updated successfully.")

    if args.update_db:
        db_url = args.database_url or os.getenv("DATABASE_URL_DIRECT") or os.getenv("DATABASE_URL")
        if not db_url:
            logger.error("No database URL provided or found in environment.")
            sys.exit(1)
        # SQLAlchemy create_engine requires standard postgresql://
        if db_url.startswith("postgresql+asyncpg://"):
            db_url = db_url.replace("postgresql+asyncpg://", "postgresql://")
        update_database(recipes_to_process, db_url)


if __name__ == "__main__":
    main()
