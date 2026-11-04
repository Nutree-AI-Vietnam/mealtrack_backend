"""End-to-end demo: the safety layers between a user and a meal plan.

Runs the real backend domain code. Nothing is mocked. Shows, in order:

  1. an allergy typed in Vietnamese reaching the dish data,
  2. an allergy the data cannot check being refused instead of guessed,
  3. who must never be given a calorie deficit,
  4. protein capped for chronic kidney disease,
  5. a crawled recipe draft screened before any user sees it.

    python scripts/testing/demo_safety_checks.py     # from the repo root
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.domain.model.user.tdee import (
    Goal,
    JobType,
    Sex,
    TdeeRequest,
)
from src.domain.services.tdee_service import (
    CKD_PROTEIN_PER_KG,
    TdeeCalculationService,
    deficit_not_allowed,
)
from src.domain.services.weekly_meal_planner.allergen_constraint import (
    matched_allergen_codes,
    resolve_allergen_terms,
)
from src.domain.services.weekly_meal_planner.recipe_publication import (
    INGREDIENT_AI_ESTIMATED,
    INGREDIENT_FROM_DATABASE,
    INGREDIENT_UNRESOLVED,
    DraftIngredient,
    RecipeDraft,
    review_recipe_draft,
)

# The nine allergen codes seeded in the database.
SEEDED = (
    "milk",
    "egg",
    "fish",
    "shellfish",
    "peanut",
    "tree_nut",
    "soy",
    "wheat",
    "sesame",
)

RULE = "─" * 68


def heading(number: int, text: str) -> None:
    print(f"\n{RULE}\n{number}. {text}\n{RULE}")


def demo_allergy_terms() -> None:
    heading(1, "Dị ứng gõ bằng tiếng Việt vẫn khớp được với dữ liệu món ăn")
    for typed in ("tôm", "tom", "đậu phộng", "lạc", "sữa", "hải sản", "mè"):
        codes, unresolved = resolve_allergen_terms([typed], SEEDED)
        print(f"   người dùng gõ {typed!r:<20} → {', '.join(codes) or '(không khớp)'}")
        assert not unresolved, typed

    # The dish discloses its allergens as free text, not as codes.
    disclosure = "Tôm, trứng gà, nước mắm"
    on_the_dish = matched_allergen_codes([disclosure], SEEDED)
    print(f"\n   món khai báo {disclosure!r}")
    print(f"   hệ thống đọc ra       → {', '.join(on_the_dish)}")
    user_codes, _ = resolve_allergen_terms(["tôm"], SEEDED)
    clash = set(on_the_dish) & set(user_codes)
    print(f"   người dị ứng 'tôm'    → trùng {', '.join(sorted(clash))} ⇒ loại món này")
    assert "shellfish" in on_the_dish
    assert clash == {"shellfish"}


def demo_unknown_allergy() -> None:
    heading(2, "Dị ứng hệ thống không kiểm được thì từ chối, không đoán")
    codes, unresolved = resolve_allergen_terms(["tôm", "thịt dê"], SEEDED)
    print(f"   gõ 'tôm, thịt dê'      → khớp: {', '.join(codes)}")
    print(f"                             không khớp: {', '.join(unresolved)}")
    print("   ⇒ API trả lỗi ALLERGY_UNRESOLVED thay vì lên thực đơn thiếu an toàn.")
    assert unresolved == ("thịt dê",)


def demo_deficit_screen() -> None:
    heading(3, "Ai không được phép nhận mức calo thâm hụt")
    cases = (
        ("bạn nữ 16 tuổi, 50 kg, 1m60", {"age": 16, "weight_kg": 50, "height_cm": 160}),
        (
            "nữ 28 tuổi, 44 kg, 1m62 (BMI 16.8)",
            {"age": 28, "weight_kg": 44, "height_cm": 162},
        ),
        (
            "nữ 30 tuổi đang mang thai",
            {
                "age": 30,
                "weight_kg": 58,
                "height_cm": 160,
                "health_conditions": ["pregnancy"],
            },
        ),
        (
            "nữ 30 tuổi, tiền sử rối loạn ăn uống",
            {
                "age": 30,
                "weight_kg": 58,
                "height_cm": 160,
                "health_conditions": ["eating_disorder_history"],
            },
        ),
        ("nam 30 tuổi, 80 kg, 1m75", {"age": 30, "weight_kg": 80, "height_cm": 175}),
    )
    for label, kwargs in cases:
        blocked = deficit_not_allowed(**kwargs)
        verdict = "CHẶN → ăn duy trì" if blocked else "cho phép giảm cân"
        print(f"   {label:<40} {verdict}")
    assert deficit_not_allowed(age=16, weight_kg=50, height_cm=160)
    assert not deficit_not_allowed(age=30, weight_kg=80, height_cm=175)


def _plan(**overrides) -> object:
    """Ask the real TDEE service for a plan, the way the API does."""
    request = TdeeRequest(
        age=overrides.pop("age", 30),
        sex=overrides.pop("sex", Sex.FEMALE),
        height=overrides.pop("height", 160.0),
        weight=overrides.pop("weight", 60.0),
        body_fat_pct=None,
        job_type=JobType.DESK,
        training_days_per_week=3,
        training_minutes_per_session=45,
        goal=overrides.pop("goal", Goal.CUT),
        **overrides,
    )
    return TdeeCalculationService().calculate_tdee(request)


def demo_kidney_protein() -> None:
    heading(4, "Suy thận mạn: đạm bị hạ trần, không bị sàn 60 g kéo lên")
    normal = _plan()
    limited = _plan(health_conditions=("kidney_disease",))
    print(f"   nữ 60 kg bình thường      {normal.macros.protein:.0f} g đạm/ngày")
    print(
        f"   nữ 60 kg suy thận mạn     {limited.macros.protein:.0f} g đạm/ngày "
        f"(KDIGO {CKD_PROTEIN_PER_KG} g/kg = {60 * CKD_PROTEIN_PER_KG:.0f} g)"
    )
    assert limited.macros.protein <= 60 * CKD_PROTEIN_PER_KG + 0.5
    assert limited.macros.protein < normal.macros.protein

    cutting = _plan(goal=Goal.CUT)
    pregnant = _plan(goal=Goal.CUT, health_conditions=("pregnancy",))
    print(
        f"\n   xin giảm cân, bình thường {cutting.macros.calories:.0f} kcal/ngày "
        f"({cutting.goal.value})"
    )
    print(
        f"   xin giảm cân, mang thai   {pregnant.macros.calories:.0f} kcal/ngày "
        f"({pregnant.goal.value}) ⇒ đổi sang ăn duy trì"
    )
    print(
        f"   app nhận được lý do:      deficit_blocked_reason="
        f"{pregnant.deficit_blocked_reason!r} (bình thường: "
        f"{cutting.deficit_blocked_reason!r})"
    )
    assert pregnant.macros.calories > cutting.macros.calories
    assert pregnant.deficit_blocked_reason == "health_condition"
    assert cutting.deficit_blocked_reason is None


def _line(name, grams, source=INGREDIENT_FROM_DATABASE, **kw):
    return DraftIngredient(
        name=name, grams=grams, source=source, food_reference_id=1, **kw
    )


def demo_recipe_review() -> None:
    heading(5, "Công thức crawl về bị soi trước khi tới tay người dùng")
    drafts = (
        (
            "Tiết canh vịt",
            RecipeDraft(
                name="Tiết canh vịt",
                ingredients=(_line("tiết vịt", 200.0), _line("lạc rang", 30.0)),
                steps=("Đánh tiết, để đông, ăn ngay.",),
            ),
        ),
        (
            "Thịt gà không có bước nào nói là đã nấu",
            RecipeDraft(
                name="Gỏi gà bắp cải",
                ingredients=(_line("thịt gà", 250.0), _line("bắp cải", 150.0)),
                steps=("Xé thịt gà.", "Trộn với bắp cải và nước mắm, dọn ra."),
            ),
        ),
        (
            "Món xào nhưng không khai dầu mỡ",
            RecipeDraft(
                name="Rau muống xào tỏi",
                ingredients=(_line("rau muống", 300.0), _line("tỏi", 10.0)),
                steps=("Xào rau muống với tỏi 5 phút cho chín.",),
            ),
        ),
        (
            "Có dòng nguyên liệu không nhận dạng được",
            RecipeDraft(
                name="Canh rau tập tàng",
                ingredients=(
                    _line("thịt heo xay", 100.0),
                    DraftIngredient(
                        name="rau tập tàng", grams=150.0, source=INGREDIENT_UNRESOLVED
                    ),
                ),
                steps=("Nấu canh, đun sôi 5 phút.",),
            ),
        ),
        (
            "Dinh dưỡng do AI ước lượng",
            RecipeDraft(
                name="Cá kho tộ",
                ingredients=(
                    _line("cá lóc", 250.0),
                    _line("nước dừa", 100.0, source=INGREDIENT_AI_ESTIMATED),
                ),
                steps=("Kho cá 30 phút đến khi chín mềm.",),
            ),
        ),
        (
            "Công thức sạch",
            RecipeDraft(
                name="Thịt kho trứng",
                ingredients=(
                    _line("thịt heo", 300.0),
                    _line("trứng gà", 100.0),
                    _line("dầu ăn", 10.0),
                ),
                steps=("Kho 45 phút đến khi thịt chín mềm, trứng chín kỹ.",),
            ),
        ),
    )
    for label, draft in drafts:
        review = review_recipe_draft(draft)
        print(f"\n   {label}")
        if review.rejections:
            print(f"      TỪ CHỐI: {'; '.join(review.rejections)}")
        if review.flags:
            print(f"      CẦN DUYỆT TAY: {'; '.join(review.flags)}")
        if not review.constraints_checkable:
            print(
                "      không kiểm được dị ứng ⇒ không đưa cho người có chế độ ăn riêng"
            )
        if review.usable and not review.flags and review.constraints_checkable:
            print("      ĐẠT: hiện cho mọi người dùng")


def main() -> None:
    demo_allergy_terms()
    demo_unknown_allergy()
    demo_deficit_screen()
    demo_kidney_protein()
    demo_recipe_review()
    print(f"\n{RULE}\nTất cả các lớp chặn đều chạy đúng trên code thật.\n")


if __name__ == "__main__":
    main()
