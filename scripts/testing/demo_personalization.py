"""End-to-end demo: the three things that make a week personal.

Runs the real domain code, nothing mocked but the catalog and the log.

  1. a recipe that does not say how many it feeds is estimated, not
     dropped whole into a one-person week,
  2. the serving rules are a locale pack, not Viet Nam baked into the code,
  3. what the user actually ate steers the week, and what the LLM would be
     told about them.

    python scripts/testing/demo_personalization.py     # from the repo root
"""

from __future__ import annotations

import sys
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.domain.model.meal_recommendation import CatalogMeal, CatalogMealIngredient
from src.domain.model.weekly_meal_planner import WeeklyMealPlanPreferences
from src.domain.model.weekly_meal_planner.user_food_memory import (
    EMPTY_MEMORY,
    LoggedMealShape,
    UserFoodMemory,
    build_user_food_memory,
)
from src.domain.services.weekly_meal_planner import WeeklyPlanGenerationService
from src.domain.services.weekly_meal_planner.grocery_projection import (
    GroceryIngredient,
    GrocerySlot,
    aggregate_grocery,
)
from src.domain.services.weekly_meal_planner.meal_locale import (
    VIETNAM,
    MealLocale,
    locale_for_timezone,
)
from src.domain.services.weekly_meal_planner.weekly_plan_generation_service import (
    DayTargets,
    compose_day,
)

RULE = "─" * 70


def heading(number: int, text: str) -> None:
    print(f"\n{RULE}\n{number}. {text}\n{RULE}")


def _grocery_slot(lines, *, base_servings, confidence, people):
    return GrocerySlot(
        recipe_id="r1",
        publication_status="published",
        nutrition_status="ready",
        is_active=True,
        base_servings=base_servings,
        serving_confidence=confidence,
        people=people,
        ingredients=tuple(
            GroceryIngredient(
                position=position,
                food_reference_id=100 + position,
                name=name,
                quantity=Decimal(str(quantity)),
                unit=unit,
            )
            for position, (name, quantity, unit) in enumerate(lines, start=1)
        ),
    )


def demo_servings() -> None:
    heading(1, "Công thức không ghi phục vụ mấy người")
    pot = [("thịt heo", 600, "g"), ("cà chua", 400, "g"), ("hành lá", 50, "g")]

    before = aggregate_grocery(
        [_grocery_slot(pot, base_servings=None, confidence="unknown", people=1)], []
    )
    after_cook_for_two = aggregate_grocery(
        [_grocery_slot(pot, base_servings=None, confidence="unknown", people=2)], []
    )

    print("   Công thức: thịt heo 600g + cà chua 400g + hành lá 50g (nồi gia đình)")
    print("   Không khai báo base_servings → ước lượng từ 1050 g đã cân\n")
    for label, items in (
        ("nấu cho 1 người", before),
        ("nấu cho 2 người", after_cook_for_two),
    ):
        line = ", ".join(
            f"{item.name} {item.total_needed:.0f} {item.unit}" for item in items
        )
        confidence = items[0].quantity_confidence
        print(f"   {label:<16} {line}   [{confidence}]")
    print("\n   Trước khi sửa: cả hai trường hợp đều ra nguyên nồi 600/400/50.")

    counted = [("cà chua", 3, "quả"), ("hành lá", 1, "bó")]
    items = aggregate_grocery(
        [_grocery_slot(counted, base_servings=None, confidence="unknown", people=2)], []
    )
    print(
        f"\n   Công thức đo bằng quả/bó → {items[0].quantity_confidence}: "
        "không cân được thì không đoán."
    )


def demo_locale() -> None:
    heading(2, "Luật ăn uống theo vùng, không hardcode Việt Nam")
    print(f"   Asia/Ho_Chi_Minh  → {locale_for_timezone('Asia/Ho_Chi_Minh').code}")
    print(
        f"   America/New_York  → {locale_for_timezone('America/New_York').code} (mặc định)"
    )
    print(
        f"\n   Pack VN: cơm {VIETNAM.staple_step_grams} g/bước, "
        f"tối đa {VIETNAM.max_staple_steps} bước, "
        f"bữa {sorted(VIETNAM.staple_slots)} (0=sáng, 1=trưa, 2=tối)"
    )
    print(f"   'thit kho'  ăn với cơm? {VIETNAM.takes_staple('thit kho')}")
    print(f"   'pho ga'    ăn với cơm? {VIETNAM.takes_staple('pho ga')}")

    bread = MealLocale(
        code="demo-bread",
        staple_name="Bread",
        staple_step_grams=40,
        staple_step_macros=(3.0, 20.0, 1.0),
        max_staple_steps=3,
        min_staple_steps=1,
        staple_slots=frozenset({0, 1, 2}),
        slot_calorie_shares=(0.3, 0.3, 0.4),
        staple_dish_words=("stew",),
        standalone_dish_words=("soup",),
        staple_item_words=("bread",),
    )
    # Three dishes, because a day never serves the same one twice.
    pool = tuple(_catalog_meal(f"stew-{i}", f"Beef stew {i}", 11) for i in range(3))
    for label, locale in (
        ("Việt Nam (cơm 55 g)", VIETNAM),
        ("vùng bánh mì (40 g)", bread),
    ):
        composed = compose_day(
            (pool, pool, pool),
            DayTargets(kcal=1800, protein=100, carbs=200, fat=55),
            rice_dishes=frozenset(meal.id for meal in pool),
            locale=locale,
        )
        served = "  ".join(
            f"bữa {slot.slot_index}: {slot.rice_steps} bước = {slot.rice_grams} g"
            for slot in composed
        )
        print(f"\n   {label:<22} {served}")
    print("\n   Cùng một món, hai vùng: VN không ăn cơm bữa sáng, vùng kia có.")


def _catalog_meal(meal_id: str, name: str, food_id: int) -> CatalogMeal:
    return CatalogMeal(
        id=meal_id,
        catalog_key=meal_id,
        content_hash="a" * 64,
        name=name,
        cuisine="vietnamese",
        description=None,
        image_url=None,
        protein_g=Decimal("30"),
        carbs_g=Decimal("40"),
        fat_g=Decimal("10"),
        fiber_g=Decimal("5"),
        meal_types=("breakfast", "lunch", "dinner"),
        popularity_rank=1,
        ingredients=(
            CatalogMealIngredient(
                food_reference_id=food_id,
                display_name=name,
                quantity=Decimal("150"),
                unit="g",
            ),
        ),
    )


def demo_memory() -> None:
    heading(3, "Memory: tuần sau học từ tuần trước")
    log = [
        LoggedMealShape(
            dish_name=name,
            cuisine=cuisine,
            item_names=items,
        )
        for name, cuisine, items in (
            ("Thịt kho trứng", "vietnamese", ("Thịt heo", "Trứng gà", "Cơm trắng")),
            ("Cá kho tộ", "vietnamese", ("Cá lóc", "Cơm trắng")),
            ("Bò xào hành tây", "vietnamese", ("Thịt bò", "Hành tây", "Cơm trắng")),
            ("Phở gà", "vietnamese", ("Bánh phở", "Thịt gà")),
            ("Thịt kho trứng", "vietnamese", ("Thịt heo", "Trứng gà", "Cơm trắng")),
        )
    ]
    memory = build_user_food_memory(
        affinity_weights={11: 0.9, 12: 0.7, 22: 0.05},
        meals=log,
        is_staple_item=VIETNAM.is_staple_item,
    )

    print("   Từ 5 bữa đã log, LLM sẽ đọc đúng khối này:\n")
    for line in memory.prompt_text().splitlines():
        print(f"      {line}")

    print(
        f"\n   Nguyên liệu ăn thường xuyên: "
        f"{sorted(memory.liked_ingredient_ids)}  "
        f"(đủ tin cậy: {memory.is_confident})"
    )

    liked = [_catalog_meal(f"liked-{i}", f"Món quen {i}", 11) for i in range(3)]
    strange = [_catalog_meal(f"new-{i}", f"Món lạ {i}", 22) for i in range(3)]
    pool = liked + strange

    def week(mem: UserFoodMemory) -> int:
        slots = WeeklyPlanGenerationService().generate(
            pool,
            user_id="user-1",
            week_start_date="2026-11-09",
            daily_calories=1800,
            preferences=WeeklyMealPlanPreferences(),
            memory=mem,
        )
        return sum(1 for slot in slots if (slot.recipe_id or "").startswith("liked-"))

    cold = week(EMPTY_MEMORY)
    warm = week(memory)
    print(
        f"\n   Trong 21 bữa của tuần, số bữa dùng nguyên liệu user hay ăn:"
        f"\n      user mới (chưa log gì)  {cold}/21"
        f"\n      user đã log 5 bữa       {warm}/21"
    )
    print(
        "\n   Vị giác chỉ đáng 40 kcal độ khớp — không bao giờ lấn mục tiêu dinh dưỡng."
    )


def demo_skips() -> None:
    heading(4, "Lớp B: món user bấm skip thì ngừng lên lịch")
    five = [_catalog_meal(f"dish-{i}", f"Món {i}", 11) for i in range(5)]

    def served(mem: UserFoodMemory) -> list[str]:
        slots = WeeklyPlanGenerationService().generate(
            five,
            user_id="user-1",
            week_start_date="2026-11-09",
            daily_calories=1800,
            preferences=WeeklyMealPlanPreferences(),
            memory=mem,
        )
        return sorted({slot.recipe_id for slot in slots if slot.recipe_id})

    once = build_user_food_memory(skipped_recipe_ids=["dish-0"])
    twice = build_user_food_memory(
        skipped_recipe_ids=["dish-0", "dish-0", "dish-1", "dish-1"]
    )

    print(f"   chưa skip gì         → {served(EMPTY_MEMORY)}")
    print(f"   skip dish-0 một lần  → {served(once)}")
    print("                           (vẫn giữ: một lần thường là bận, không phải chê)")
    print(f"   skip dish-0 và 1 hai → {served(twice)}")
    print("                           (đã bỏ hẳn khỏi tuần)")

    only = [_catalog_meal("only", "Món duy nhất", 11)]
    fallback = WeeklyPlanGenerationService().generate(
        only,
        user_id="user-1",
        week_start_date="2026-11-09",
        daily_calories=1800,
        preferences=WeeklyMealPlanPreferences(),
        memory=build_user_food_memory(skipped_recipe_ids=["only"] * 5),
    )
    filled = sum(1 for slot in fallback if slot.recipe_id)
    print(
        f"\n   Catalog chỉ còn 1 món và user đã skip 5 lần → vẫn xếp {filled}/21 bữa."
        "\n   Tuần thủng lỗ còn tệ hơn bữa tối nhàm chán."
    )


def main() -> None:
    demo_servings()
    demo_locale()
    demo_memory()
    demo_skips()
    print(f"\n{RULE}\nTất cả chạy trên code thật.\n")


if __name__ == "__main__":
    main()
