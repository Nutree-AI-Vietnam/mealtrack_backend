#!/usr/bin/env python3
"""Build the draft text-eval gold set for AI_SCAN_FULL_MICROS_ENABLED.

Thirty everyday Vietnamese foods with stated weights, scored against Vietnam's
National Institute of Nutrition food table (viendinhduong.vn foods API, the
same source as ``scripts/scrapers/fetch_nin_vn.py``). Values are per 100 g
scaled to the stated grams; a nutrient the table leaves blank is masked, never
guessed. The manifest is written as a draft: the eval refuses it until a
person sets ``review_status``, ``reviewed_by`` and ``privacy_reviewed``.

Usage:
    python scripts/development/build_full_micros_text_manifest.py \\
        --nin-foods nin_foods.json --output evals/full_micros/<name>.json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
import sys  # noqa: E402

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.domain.evaluation.meal_text_nutrition_eval_models import (  # noqa: E402
    FULL_MICRO_UNITS,
)

# Our micros key -> NIN nutrient key (first one present wins).
NIN_MICROS = {
    "vitamin_a": ("vit-a-rae",),
    "vitamin_c": ("vit-c",),
    "vitamin_d": ("vit-d-d2-d3",),
    "vitamin_e": ("vit-e",),
    "vitamin_k": ("vit-k-phylloquinone",),
    "thiamin": ("thiamin",),
    "riboflavin": ("riboflavin",),
    "niacin": ("niacin",),
    "vitamin_b6": ("vit-b6",),
    "vitamin_b12": ("vit-b12",),
    "folate": ("folate-dfe", "folate-total"),
    "calcium": ("ca",),
    "iron": ("fe",),
    "magnesium": ("mg",),
    "phosphorus": ("p",),
    "potassium": ("k",),
    "sodium": ("na",),
    "zinc": ("zn",),
    "selenium": ("se",),
    "saturated_fat": ("fatty-acids-total-saturated",),
}
NIN_MACROS = {
    "protein_g": "protein",
    "carbs_g": "carbohydrate-by-difference",
    "fat_g": "total-lipid-fat",
    "fiber_g": "fibre-total-dietary",
    "sugar_g": "sugars-total",
}
# Meat, fish, eggs and milk carry no fiber and next to no sugar or starch, so
# a blank there is zero; a blank for a plant food stops the build.
ANIMAL_CATEGORIES = {
    "Milk and processed products",
    "Meat and processed products",
    "Seafood and processed products",
    "Eggs and processed products",
}
SWEETENED = {"10035"}

# (NIN code, grams, Vietnamese text, English text[, English name if the
# text is not just grams and the name])
FOODS = [
    ("10001", 200, "200g sữa bò tươi", "200g fresh cow's milk"),
    ("10036", 100, "1 hộp sữa chua không đường 100g", "100g plain unsweetened yogurt"),
    (
        "9001",
        50,
        "1 quả trứng gà luộc 50g",
        "1 boiled chicken egg, 50g",
        "boiled chicken egg",
    ),
    ("7028002", 150, "150g thịt vịt luộc", "150g boiled duck meat"),
    ("7095003", 150, "150g thịt bò vai hấp", "150g steamed beef shoulder"),
    (
        "7018002",
        100,
        "100g thịt lợn nửa nạc nửa mỡ luộc",
        "100g boiled medium-fat pork",
    ),
    ("7041002", 50, "50g gan lợn luộc", "50g boiled pork liver"),
    ("8020002", 150, "150g cá nục luộc", "150g boiled scad fish (cá nục)"),
    ("8051002", 100, "100g tôm biển luộc", "100g boiled sea shrimp"),
    ("4016002", 150, "150g cải xanh luộc", "150g boiled mustard greens"),
    ("4100002", 150, "150g súp lơ xanh luộc", "150g boiled broccoli"),
    ("5017", 200, "200g đu đủ chín", "200g ripe papaya"),
    ("3015", 30, "30g hạt điều", "30g cashew nuts"),
    ("5051", 150, "1 quả táo 150g", "1 apple, 150g", "apple"),
    ("4027", 150, "150g dưa chuột", "150g cucumber"),
    ("8040003", 150, "150g mực hấp", "150g steamed squid"),
    ("7048002", 100, "100g mề gà luộc", "100g boiled chicken gizzard"),
    ("4036002", 100, "100g giá đỗ luộc", "100g boiled mung bean sprouts"),
    ("5040", 150, "1 quả ổi chín 150g", "1 ripe guava, 150g", "ripe guava"),
    ("4010002", 150, "150g bắp cải luộc", "150g boiled cabbage"),
    ("9007", 50, "5 quả trứng cút luộc, 50g", "50g boiled quail eggs"),
    ("7032002", 150, "150g chân giò lợn luộc", "150g boiled pork leg"),
    ("7040002", 50, "50g gan gà luộc", "50g boiled chicken liver"),
    ("8020014", 120, "120g cá nục nướng", "120g grilled scad fish (cá nục)"),
    ("8011", 100, "100g cá hồi sống sashimi", "100g raw salmon sashimi"),
    ("7113002", 150, "150g lườn vịt luộc", "150g boiled duck breast"),
    ("10035", 100, "1 hộp sữa chua có đường 100g", "100g sweetened yogurt"),
    ("4005", 100, "100g cà chua", "100g raw tomato"),
    ("5037", 100, "100g nho", "100g sweet grapes"),
    ("4015002", 150, "150g cải thìa luộc", "150g boiled bok choy"),
]


def _round(value: float) -> float:
    return round(value, 3)


def build_case(food: tuple, row: dict, split: str, language: str) -> dict:
    code, grams, text_vi, text_en, *name = food
    name_en = name[0] if name else text_en.split(" ", 1)[1]
    values = {
        n["key"]: float(n["value"])
        for n in row["nutrition"]
        if n.get("value") not in ("", None)
    }
    scale = grams / 100
    animal = row["categoryEn"] in ANIMAL_CATEGORIES
    macros = {}
    for name, key in NIN_MACROS.items():
        if key in values:
            macros[name] = _round(values[key] * scale)
        elif animal and name != "protein_g":
            macros[name] = 0.0
        else:
            raise ValueError(f"{code}: NIN has no {key} for a plant food")
    micros = {}
    for name, unit in FULL_MICRO_UNITS.items():
        if name == "added_sugar":
            micros[name] = (
                {"status": "masked", "unit": unit}
                if code in SWEETENED
                else {"status": "known", "unit": unit, "value": 0}
            )
            continue
        key = next((k for k in NIN_MICROS[name] if k in values), None)
        micros[name] = (
            {"status": "known", "unit": unit, "value": _round(values[key] * scale)}
            if key
            else {"status": "masked", "unit": unit}
        )
    energy = float(row["energy"]) * scale
    return {
        "case_id": f"nin-{code}-{grams}g-{language}",
        "split": split,
        "language": language,
        "text": text_vi if language == "vi" else text_en,
        "expected_items": [
            {
                "aliases": [name_en, row["name_vi"].strip()],
                "quantity_g": grams,
            }
        ],
        "expected_calories_kcal": [_round(energy * 0.85), _round(energy * 1.15)],
        "expected_macros": macros,
        "expected_micros": micros,
        "nin_code": code,
    }


def build_manifest(rows: list[dict]) -> dict:
    by_code = {row["code"]: row for row in rows}
    # Every third food is held out (10 of 30); within each split the
    # languages alternate, so both splits are half Vietnamese.
    seen = {"development": 0, "held_out": 0}
    cases = []
    for index, food in enumerate(FOODS):
        split = "held_out" if index % 3 == 2 else "development"
        language = "vi" if seen[split] % 2 == 0 else "en"
        seen[split] += 1
        cases.append(build_case(food, by_code[food[0]], split, language))
    return {
        "schema_version": 1,
        "corpus_version": "nin-foods-full-micros-v1",
        "review_status": "draft",
        "reviewed_by": "",
        "privacy_reviewed": False,
        "source_provenance": (
            "Vietnam National Institute of Nutrition food composition table "
            "(viendinhduong.vn foods API); per-100 g values scaled to the stated "
            "grams, blanks masked. No user data."
        ),
        "cases": cases,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--nin-foods", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    raw = json.loads(Path(args.nin_foods).read_text(encoding="utf-8"))
    rows = raw["data"] if isinstance(raw, dict) else raw
    manifest = build_manifest(rows)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"wrote {len(manifest['cases'])} cases to {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
