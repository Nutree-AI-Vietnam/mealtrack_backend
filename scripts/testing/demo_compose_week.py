"""End-to-end demo: compose a week for one user and shop for it.

Runs the real backend domain code on a small Vietnamese catalog written the
way catalogs are written, duplicate names and all. Nothing is mocked except
the catalog itself.

    python scripts/testing/demo_compose_week.py     # from the repo root
"""

from __future__ import annotations

import sys
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.domain.model.meal_recommendation import CatalogMeal, CatalogMealIngredient
from src.domain.services.weekly_meal_planner.grocery_projection import (
    CountedUnit,
    GroceryIngredient,
    GrocerySlot,
    aggregate_grocery,
    is_pantry_staple,
)
from src.domain.services.weekly_meal_planner.weekly_plan_generation_service import (
    DayTargets,
    SlotBrief,
    compose_day,
    day_totals,
    dish_fresh_ingredient_ids,
    rank_reference_dishes,
)

# A catalog as crawled: the same food under different names, seasonings mixed
# in with the shopping, preparation words on the line.
CATALOG = [
    # id, name, meal types, ingredients (name, grams), rice dish?
    (
        "sua-chua-yen-mach",
        "Sữa chua yến mạch",
        ("breakfast",),
        [("sữa chua không đường", 200), ("yến mạch", 45), ("chuối", 80)],
        False,
    ),
    (
        "banh-mi-bo",
        "Bánh mì bơ trái bơ",
        ("breakfast",),
        [("bánh mì", 80), ("bơ trái", 70)],
        False,
    ),
    (
        "yen-mach-chuoi",
        "Yến mạch chuối",
        ("breakfast",),
        [("yến mạch", 60), ("sữa chua không đường", 150), ("chuối", 100)],
        False,
    ),
    (
        "pho-ga",
        "Phở gà",
        ("breakfast",),
        [
            ("thịt gà", 120),
            ("bánh phở", 180),
            ("hành lá", 15),
            ("gừng", 5),
            ("nước mắm", 8),
        ],
        False,
    ),
    (
        "banh-mi-trung",
        "Bánh mì trứng",
        ("breakfast",),
        [("trứng gà", 110), ("bánh mì", 90), ("hành ống", 10), ("dầu ăn", 6)],
        False,
    ),
    (
        "chao-thit",
        "Cháo thịt bằm",
        ("breakfast",),
        [("thịt heo xay", 80), ("gạo tẻ", 60), ("hành lá", 10), ("tiêu", 1)],
        False,
    ),
    (
        "thit-kho-trung",
        "Thịt kho trứng",
        ("lunch", "dinner"),
        [("thịt ba chỉ", 150), ("trứng gà", 100), ("nước mắm", 15), ("đường", 10)],
        True,
    ),
    (
        "ga-kho-gung",
        "Gà kho gừng",
        ("lunch", "dinner"),
        [("thịt gà", 180), ("gừng", 15), ("nước mắm", 12), ("hành tím", 10)],
        True,
    ),
    (
        "suon-ram-man",
        "Sườn ram mặn",
        ("lunch", "dinner"),
        [("sườn heo", 200), ("nước mắm", 12), ("tỏi", 8), ("đường", 12)],
        True,
    ),
    (
        "ca-kho-to",
        "Cá kho tộ",
        ("lunch", "dinner"),
        [("cá lóc", 180), ("nước mắm", 14), ("tiêu", 2), ("hành tím", 10)],
        True,
    ),
    (
        "thit-lon-xao-gia",
        "Thịt lợn xào giá",
        ("lunch", "dinner"),
        [("thịt lợn", 140), ("giá đỗ", 120), ("hành lá", 12), ("dầu ăn", 10)],
        True,
    ),
    (
        "dau-hu-sot-ca",
        "Đậu hũ sốt cà",
        ("lunch", "dinner"),
        [("đậu hũ", 200), ("cà chua", 150), ("hành lá", 10), ("dầu ăn", 10)],
        True,
    ),
    (
        "canh-chua-ca",
        "Canh chua cá",
        ("lunch", "dinner"),
        [("cá lóc", 150), ("cà chua", 100), ("giá đỗ", 80), ("ngò", 10)],
        True,
    ),
    (
        "rau-muong-xao-toi",
        "Rau muống xào tỏi",
        ("lunch", "dinner"),
        [("rau muống", 250), ("tỏi", 10), ("dầu ăn", 10)],
        True,
    ),
    (
        "bo-xao-hanh-tay",
        "Bò xào hành tây",
        ("lunch", "dinner"),
        [("thịt bò", 160), ("hành tây", 120), ("dầu ăn", 10), ("tỏi", 6)],
        True,
    ),
    (
        "ca-rot-xao-trung",
        "Cà rốt xào trứng",
        ("lunch", "dinner"),
        [("cà rốt", 180), ("trứng gà", 100), ("dầu ăn", 8), ("hành lá", 10)],
        True,
    ),
    (
        "tom-rang-thit",
        "Tôm rang thịt",
        ("lunch", "dinner"),
        [("tôm", 150), ("thịt heo xay", 80), ("nước mắm", 10), ("hành tím", 8)],
        True,
    ),
]

# Per 100 g, roughly: protein, carbs, fat, fiber.
MACROS_100G = {
    "sữa chua không đường": (10, 4, 5, 0),
    "yến mạch": (13, 60, 7, 10),
    "chuối": (1, 23, 0, 3),
    "bơ trái": (2, 9, 15, 7),
    "thịt gà": (23, 0, 8, 0),
    "thịt heo xay": (20, 0, 18, 0),
    "thịt ba chỉ": (15, 0, 30, 0),
    "thịt lợn": (20, 0, 14, 0),
    "sườn heo": (18, 0, 20, 0),
    "thịt bò": (22, 0, 12, 0),
    "cá lóc": (19, 0, 3, 0),
    "tôm": (20, 1, 1, 0),
    "trứng gà": (13, 1, 10, 0),
    "đậu hũ": (8, 2, 5, 1),
    "bánh phở": (3, 25, 0, 1),
    "bánh mì": (9, 50, 3, 3),
    "gạo tẻ": (7, 78, 1, 1),
    "giá đỗ": (3, 4, 0, 2),
    "cà chua": (1, 4, 0, 1),
    "cà rốt": (1, 10, 0, 3),
    "rau muống": (3, 3, 0, 2),
    "hành tây": (1, 9, 0, 2),
    "hành lá": (2, 7, 0, 3),
    "hành ống": (2, 7, 0, 3),
    "ngò": (2, 4, 0, 3),
    "gừng": (2, 18, 1, 2),
    "tỏi": (6, 33, 1, 2),
    "hành tím": (2, 17, 0, 3),
    "nước mắm": (5, 4, 0, 0),
    "đường": (0, 100, 0, 0),
    "dầu ăn": (0, 0, 100, 0),
    "tiêu": (10, 64, 3, 25),
}
FOOD_IDS = {name: index + 1 for index, name in enumerate(sorted(MACROS_100G))}


def _meal(entry) -> CatalogMeal:
    meal_id, name, meal_types, lines, _rice = entry
    protein = carbs = fat = fiber = 0.0
    for ingredient, grams in lines:
        p, c, f, fi = MACROS_100G[ingredient]
        protein += p * grams / 100
        carbs += c * grams / 100
        fat += f * grams / 100
        fiber += fi * grams / 100
    return CatalogMeal(
        id=meal_id,
        catalog_key=meal_id,
        content_hash="a" * 64,
        name=name,
        cuisine="vietnamese",
        description=None,
        image_url=None,
        protein_g=Decimal(str(round(protein, 1))),
        carbs_g=Decimal(str(round(carbs, 1))),
        fat_g=Decimal(str(round(fat, 1))),
        fiber_g=Decimal(str(round(fiber, 1))),
        meal_types=meal_types,
        popularity_rank=1,
        ingredients=tuple(
            CatalogMealIngredient(
                food_reference_id=FOOD_IDS[ingredient],
                display_name=ingredient,
                quantity=Decimal(str(grams)),
                unit="g",
            )
            for ingredient, grams in lines
        ),
    )


MEALS = [_meal(entry) for entry in CATALOG]
BY_ID = {meal.id: meal for meal in MEALS}
LINES = {entry[0]: entry[3] for entry in CATALOG}
RICE_DISHES = frozenset(entry[0] for entry in CATALOG if entry[4])
# What a shopper actually buys by the piece. In production these come from
# food_reference_serving_sizes; here they are written out so the demo can
# show the same thing.
COUNTED_UNITS = {
    FOOD_IDS["trứng gà"]: CountedUnit(grams=55.0, unit_name="quả"),
    FOOD_IDS["chuối"]: CountedUnit(grams=100.0, unit_name="quả"),
    FOOD_IDS["bơ trái"]: CountedUnit(grams=170.0, unit_name="quả"),
}

SLOT_NAMES = ("Sáng", "Trưa", "Tối")
DAY_NAMES = ("T2", "T3", "T4", "T5", "T6", "T7", "CN")


def for_slot(index: int):
    wanted = ("breakfast", "lunch", "dinner")[index]
    return tuple(meal for meal in MEALS if wanted in meal.meal_types)


def main() -> None:
    # A 55 kg woman maintaining: the backend's own kind of target.
    targets = DayTargets(kcal=1700.0, protein=94.0, carbs=215.0, fat=47.0)
    print(
        f"Mục tiêu mỗi ngày: {targets.kcal:.0f} kcal · {targets.protein:.0f} g đạm "
        f"· {targets.carbs:.0f} g tinh bột · {targets.fat:.0f} g béo\n"
    )

    basket: set[int] = set()
    served: dict[str, int] = {}
    week, slots_for_grocery = [], []
    for day in range(7):
        # Shortlist the dishes worth considering, then compose the meal.
        shortlists = []
        for slot in range(3):
            brief = SlotBrief(
                slot_index=slot,
                kcal_target=targets.kcal * (0.25, 0.375, 0.375)[slot],
                basket=frozenset(basket),
                takes_rice=RICE_DISHES,
            )
            ranked = rank_reference_dishes(for_slot(slot), brief, limit=6)
            shortlists.append(tuple(dish.meal for dish in ranked))
        composed = compose_day(
            tuple(shortlists),
            targets,
            day_index=day,
            rice_dishes=RICE_DISHES,
            used_counts=served,
        )
        week.append(composed)
        for slot in composed:
            if slot.recipe_id:
                served[slot.recipe_id] = served.get(slot.recipe_id, 0) + 1
                basket |= dish_fresh_ingredient_ids(BY_ID[slot.recipe_id])
                slots_for_grocery.append(
                    GrocerySlot(
                        recipe_id=slot.recipe_id,
                        publication_status="published",
                        nutrition_status="ready",
                        is_active=True,
                        base_servings=1,
                        serving_confidence="verified",
                        people=1,
                        day_index=day,
                        ingredients=tuple(
                            GroceryIngredient(
                                position=position,
                                food_reference_id=FOOD_IDS[ingredient],
                                name=ingredient,
                                quantity=Decimal(str(round(grams * slot.portion, 1))),
                                unit="g",
                            )
                            for position, (ingredient, grams) in enumerate(
                                LINES[slot.recipe_id]
                            )
                        ),
                    )
                )

    print("THỰC ĐƠN TUẦN")
    errors = []
    for day, composed in enumerate(week):
        kcal, protein, carbs, fat = day_totals(composed, BY_ID)
        errors.append(abs(kcal - targets.kcal) / targets.kcal)
        print(
            f"\n{DAY_NAMES[day]}  {kcal:.0f} kcal · {protein:.0f}g đạm "
            f"· {carbs:.0f}g tinh bột · {fat:.0f}g béo"
        )
        for slot in composed:
            if not slot.recipe_id:
                continue
            rice = f" + {slot.rice_grams}g cơm" if slot.rice_steps else ""
            portion = "" if slot.portion == 1.0 else f" ×{slot.portion}"
            print(
                f"   {SLOT_NAMES[slot.slot_index]:<5} {BY_ID[slot.recipe_id].name}{portion}{rice}"
            )

    print(
        f"\nLệch calo trung bình: {sum(errors) / len(errors) * 100:.1f} % "
        f"· {len(served)} món khác nhau trong tuần"
    )

    items = aggregate_grocery(slots_for_grocery, [], counted_units=COUNTED_UNITS)
    fresh = [item for item in items if not is_pantry_staple(item.name)]
    staples = [item for item in items if is_pantry_staple(item.name)]
    written = sum(len(LINES[s.recipe_id]) for s in slots_for_grocery)
    print(
        f"\nĐI CHỢ — {len(fresh)} món cần mua "
        f"(từ {written} dòng nguyên liệu trong công thức)"
    )
    for item in sorted(fresh, key=lambda i: -i.total_needed):
        print(f"   {item.name:<22} {item.shopping_label:>10}")
    print(
        f"\nGia vị có sẵn nhà ({len(staples)}): "
        + ", ".join(sorted(item.name for item in staples))
    )


if __name__ == "__main__":
    main()
