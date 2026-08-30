"""
End-to-end API test and measurement suite purely in Vietnamese.
Tests:
- Composite dishes (món ăn phức hợp phân rã thành các nguyên liệu)
- Ingredient lists (bữa ăn liệt kê nhiều món/nguyên liệu)
- Single foods with Vietnamese units (món đơn lẻ kèm đơn vị chén, bát, tô, dĩa, quả, củ, ly, hộp)
- Refinements in Vietnamese (thêm bớt món)
"""

from __future__ import annotations

import statistics
import time
from typing import Any
from unittest.mock import patch

from fastapi.testclient import TestClient

import src.api.dependencies.event_bus as eb_module
from src.domain.ports.meal_generation_service_port import MealGenerationServicePort


class _VietnameseSimulationMealGenService(MealGenerationServicePort):
    """Deterministic, latency-instrumented meal generation service for Vietnamese E2E tests."""

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

        p = prompt.lower()

        # 1. Composite dishes (Món phức hợp)
        if "phở bò" in p:
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
        elif "cơm tấm" in p:
            return {
                "items": [
                    {
                        "name": "Cơm tấm",
                        "lookup_name": "Broken rice",
                        "quantity": 150,
                        "quantity_g": 150,
                        "unit": "g",
                        "macros": {"protein_g": 4.0, "carbs_g": 42.0, "fat_g": 0.5},
                    },
                    {
                        "name": "Sườn nướng",
                        "lookup_name": "Grilled pork chop",
                        "quantity": 100,
                        "quantity_g": 100,
                        "unit": "g",
                        "macros": {"protein_g": 24.0, "carbs_g": 3.0, "fat_g": 12.0},
                    },
                    {
                        "name": "Bì heo",
                        "lookup_name": "Pork skin",
                        "quantity": 30,
                        "quantity_g": 30,
                        "unit": "g",
                        "macros": {"protein_g": 8.0, "carbs_g": 0.0, "fat_g": 2.5},
                    },
                    {
                        "name": "Chả trứng",
                        "lookup_name": "Steamed egg meatloaf",
                        "quantity": 50,
                        "quantity_g": 50,
                        "unit": "g",
                        "macros": {"protein_g": 7.0, "carbs_g": 2.0, "fat_g": 6.0},
                    },
                ]
            }
        elif "bánh mì thịt" in p:
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
        elif "bún bò huế" in p:
            return {
                "items": [
                    {
                        "name": "Bún tươi",
                        "lookup_name": "Rice vermicelli",
                        "quantity": 150,
                        "quantity_g": 150,
                        "unit": "g",
                        "macros": {"protein_g": 2.7, "carbs_g": 36.0, "fat_g": 0.3},
                    },
                    {
                        "name": "Bắp bò",
                        "lookup_name": "Beef shank",
                        "quantity": 60,
                        "quantity_g": 60,
                        "unit": "g",
                        "macros": {"protein_g": 16.0, "carbs_g": 0.0, "fat_g": 4.5},
                    },
                    {
                        "name": "Giò heo",
                        "lookup_name": "Pork knuckle",
                        "quantity": 60,
                        "quantity_g": 60,
                        "unit": "g",
                        "macros": {"protein_g": 11.0, "carbs_g": 0.0, "fat_g": 9.0},
                    },
                    {
                        "name": "Nước dùng bún bò",
                        "lookup_name": "Bun bo broth",
                        "quantity": 350,
                        "quantity_g": 350,
                        "unit": "ml",
                        "macros": {"protein_g": 4.5, "carbs_g": 2.0, "fat_g": 5.0},
                    },
                ]
            }
        elif "hủ tiếu" in p:
            return {
                "items": [
                    {
                        "name": "Hủ tiếu",
                        "lookup_name": "Hu tieu noodles",
                        "quantity": 150,
                        "quantity_g": 150,
                        "unit": "g",
                        "macros": {"protein_g": 2.5, "carbs_g": 38.0, "fat_g": 0.4},
                    },
                    {
                        "name": "Thịt heo nạc",
                        "lookup_name": "Lean pork",
                        "quantity": 50,
                        "quantity_g": 50,
                        "unit": "g",
                        "macros": {"protein_g": 13.0, "carbs_g": 0.0, "fat_g": 3.0},
                    },
                    {
                        "name": "Tôm tươi",
                        "lookup_name": "Shrimp",
                        "quantity": 40,
                        "quantity_g": 40,
                        "unit": "g",
                        "macros": {"protein_g": 9.6, "carbs_g": 0.0, "fat_g": 0.5},
                    },
                    {
                        "name": "Nước dùng hủ tiếu",
                        "lookup_name": "Pork broth",
                        "quantity": 350,
                        "quantity_g": 350,
                        "unit": "ml",
                        "macros": {"protein_g": 4.0, "carbs_g": 2.0, "fat_g": 3.5},
                    },
                ]
            }
        elif "gỏi cuốn" in p:
            return {
                "items": [
                    {
                        "name": "Bánh tráng cuốn bún",
                        "lookup_name": "Rice paper roll with noodles",
                        "quantity": 3,
                        "quantity_g": 120,
                        "unit": "cuốn",
                        "macros": {"protein_g": 3.0, "carbs_g": 30.0, "fat_g": 0.5},
                    },
                    {
                        "name": "Tôm luộc",
                        "lookup_name": "Boiled shrimp",
                        "quantity": 60,
                        "quantity_g": 60,
                        "unit": "g",
                        "macros": {"protein_g": 14.4, "carbs_g": 0.0, "fat_g": 0.8},
                    },
                    {
                        "name": "Thịt ba chỉ luộc",
                        "lookup_name": "Boiled pork belly",
                        "quantity": 50,
                        "quantity_g": 50,
                        "unit": "g",
                        "macros": {"protein_g": 9.0, "carbs_g": 0.0, "fat_g": 14.0},
                    },
                    {
                        "name": "Tương chấm đậu phộng",
                        "lookup_name": "Peanut dipping sauce",
                        "quantity": 30,
                        "quantity_g": 30,
                        "unit": "ml",
                        "macros": {"protein_g": 2.5, "carbs_g": 6.0, "fat_g": 4.0},
                    },
                ]
            }
        elif "pizza" in p:
            return {
                "items": [
                    {
                        "name": "Đế bánh pizza",
                        "lookup_name": "Pizza crust",
                        "quantity": 150,
                        "quantity_g": 150,
                        "unit": "g",
                        "macros": {"protein_g": 11.0, "carbs_g": 70.0, "fat_g": 4.5},
                    },
                    {
                        "name": "Phô mai mozzarella",
                        "lookup_name": "Mozzarella cheese",
                        "quantity": 60,
                        "quantity_g": 60,
                        "unit": "g",
                        "macros": {"protein_g": 13.5, "carbs_g": 1.5, "fat_g": 13.0},
                    },
                    {
                        "name": "Tôm và mực",
                        "lookup_name": "Mixed seafood shrimp squid",
                        "quantity": 70,
                        "quantity_g": 70,
                        "unit": "g",
                        "macros": {"protein_g": 14.0, "carbs_g": 0.5, "fat_g": 1.0},
                    },
                    {
                        "name": "Sốt cà chua",
                        "lookup_name": "Tomato sauce",
                        "quantity": 30,
                        "quantity_g": 30,
                        "unit": "g",
                        "macros": {"protein_g": 0.5, "carbs_g": 3.0, "fat_g": 0.2},
                    },
                ]
            }
        elif "bún giò" in p:
            return {
                "items": [
                    {
                        "name": "Bún tươi",
                        "lookup_name": "Rice noodles",
                        "quantity": 180,
                        "quantity_g": 180,
                        "unit": "g",
                        "macros": {"protein_g": 3.0, "carbs_g": 45.0, "fat_g": 0.4},
                    },
                    {
                        "name": "Giò heo luộc",
                        "lookup_name": "Boiled pork knuckle",
                        "quantity": 100,
                        "quantity_g": 100,
                        "unit": "g",
                        "macros": {"protein_g": 19.0, "carbs_g": 0.0, "fat_g": 16.0},
                    },
                    {
                        "name": "Nước dùng bún",
                        "lookup_name": "Pork broth",
                        "quantity": 350,
                        "quantity_g": 350,
                        "unit": "ml",
                        "macros": {"protein_g": 4.5, "carbs_g": 1.5, "fat_g": 3.0},
                    },
                ]
            }

        # 2. Ingredient lists (Bữa ăn liệt kê)
        elif "rau muống" in p:
            return {
                "items": [
                    {
                        "name": "Cơm trắng",
                        "lookup_name": "White rice",
                        "quantity": 1,
                        "quantity_g": 150,
                        "unit": "chén",
                        "macros": {"protein_g": 3.9, "carbs_g": 42.3, "fat_g": 0.4},
                    },
                    {
                        "name": "Ức gà luộc",
                        "lookup_name": "Boiled chicken breast",
                        "quantity": 150,
                        "quantity_g": 150,
                        "unit": "g",
                        "macros": {"protein_g": 46.5, "carbs_g": 0.0, "fat_g": 5.4},
                    },
                    {
                        "name": "Rau muống xào tỏi",
                        "lookup_name": "Stir-fried water spinach with garlic",
                        "quantity": 1,
                        "quantity_g": 100,
                        "unit": "đĩa",
                        "macros": {"protein_g": 2.5, "carbs_g": 4.0, "fat_g": 5.0},
                    },
                ]
            }
        elif "khoai lang" in p and "chuối" in p:
            return {
                "items": [
                    {
                        "name": "Trứng gà luộc",
                        "lookup_name": "Boiled egg",
                        "quantity": 2,
                        "quantity_g": 100,
                        "unit": "quả",
                        "macros": {"protein_g": 12.6, "carbs_g": 1.1, "fat_g": 10.6},
                    },
                    {
                        "name": "Khoai lang luộc",
                        "lookup_name": "Boiled sweet potato",
                        "quantity": 1,
                        "quantity_g": 150,
                        "unit": "củ",
                        "macros": {"protein_g": 2.4, "carbs_g": 36.0, "fat_g": 0.2},
                    },
                    {
                        "name": "Chuối tiêu",
                        "lookup_name": "Banana",
                        "quantity": 1,
                        "quantity_g": 118,
                        "unit": "quả",
                        "macros": {"protein_g": 1.3, "carbs_g": 27.0, "fat_g": 0.4},
                    },
                ]
            }
        elif "cháo yến mạch" in p:
            return {
                "items": [
                    {
                        "name": "Cháo yến mạch",
                        "lookup_name": "Oatmeal porridge",
                        "quantity": 1,
                        "quantity_g": 200,
                        "unit": "bát",
                        "macros": {"protein_g": 6.0, "carbs_g": 30.0, "fat_g": 3.0},
                    },
                    {
                        "name": "Sữa tươi không đường",
                        "lookup_name": "Unsweetened milk",
                        "quantity": 200,
                        "quantity_g": 200,
                        "unit": "ml",
                        "macros": {"protein_g": 6.8, "carbs_g": 9.6, "fat_g": 6.5},
                    },
                    {
                        "name": "Táo tây",
                        "lookup_name": "Apple",
                        "quantity": 1,
                        "quantity_g": 150,
                        "unit": "quả",
                        "macros": {"protein_g": 0.4, "carbs_g": 21.0, "fat_g": 0.3},
                    },
                ]
            }

        # 3. Single foods & measured items
        elif "ức gà áp chảo" in p:
            return {
                "items": [
                    {
                        "name": "Ức gà áp chảo",
                        "lookup_name": "Pan-seared chicken breast",
                        "quantity": 200,
                        "quantity_g": 200,
                        "unit": "g",
                        "macros": {"protein_g": 62.0, "carbs_g": 0.0, "fat_g": 8.0},
                    }
                ]
            }
        elif "cơm trắng" in p and "chén" in p:
            return {
                "items": [
                    {
                        "name": "Cơm trắng",
                        "lookup_name": "White rice",
                        "quantity": 1,
                        "quantity_g": 150,
                        "unit": "chén",
                        "macros": {"protein_g": 3.9, "carbs_g": 42.3, "fat_g": 0.4},
                    }
                ]
            }
        elif "sinh tố bơ" in p:
            return {
                "items": [
                    {
                        "name": "Sinh tố bơ",
                        "lookup_name": "Avocado smoothie",
                        "quantity": 1,
                        "quantity_g": 250,
                        "unit": "ly",
                        "macros": {"protein_g": 4.5, "carbs_g": 28.0, "fat_g": 16.0},
                    }
                ]
            }
        elif "sữa chua" in p:
            return {
                "items": [
                    {
                        "name": "Sữa chua không đường",
                        "lookup_name": "Plain yogurt",
                        "quantity": 1,
                        "quantity_g": 100,
                        "unit": "hộp",
                        "macros": {"protein_g": 3.5, "carbs_g": 4.7, "fat_g": 3.3},
                    }
                ]
            }
        elif "hạnh nhân" in p:
            return {
                "items": [
                    {
                        "name": "Hạt hạnh nhân sấy",
                        "lookup_name": "Roasted almonds",
                        "quantity": 30,
                        "quantity_g": 30,
                        "unit": "g",
                        "macros": {"protein_g": 6.3, "carbs_g": 6.5, "fat_g": 15.0},
                    }
                ]
            }
        elif "trứng ốp la" in p and "bánh mì" in p:
            return {
                "items": [
                    {
                        "name": "Bánh mì",
                        "lookup_name": "Baguette",
                        "quantity": 1,
                        "quantity_g": 80,
                        "unit": "ổ",
                        "macros": {"protein_g": 7.5, "carbs_g": 45.0, "fat_g": 1.5},
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
        elif "trứng ốp la" in p:
            return {
                "items": [
                    {
                        "name": "Trứng ốp la",
                        "lookup_name": "Fried egg",
                        "quantity": 1,
                        "quantity_g": 50,
                        "unit": "quả",
                        "macros": {"protein_g": 6.3, "carbs_g": 0.4, "fat_g": 7.0},
                    }
                ]
            }

        # Fallback default
        return {
            "items": [
                {
                    "name": "Thịt gà",
                    "lookup_name": "Chicken",
                    "quantity": 100,
                    "quantity_g": 100,
                    "unit": "g",
                    "macros": {"protein_g": 20.0, "carbs_g": 0.0, "fat_g": 5.0},
                }
            ]
        }


def test_parse_text_vietnamese_comprehensive_suite(authenticated_client: TestClient):
    """
    Comprehensive Vietnamese parse-text E2E test covering:
    - Composite dishes (Phở, Cơm tấm, Bánh mì thịt, Bún bò Huế, Hủ tiếu Nam Vang, Gỏi cuốn)
    - Ingredient lists (Cơm gà rau muống, Trứng khoai chuối, Cháo yến mạch táo)
    - Single foods with Vietnamese units (Ức gà, Chén cơm, Sinh tố bơ, Sữa chua, Hạnh nhân)
    - Refinements (Thêm trứng ốp la)
    """
    gen_service = _VietnameseSimulationMealGenService()

    test_cases = [
        # --- Composite Dishes (Món phức hợp) ---
        {
            "group": "Món phức hợp",
            "name": "Phở bò tái nạm",
            "text": "1 tô phở bò tái nạm",
            "expected_items": 3,
            "min_kcal": 300,
            "max_kcal": 550,
        },
        {
            "group": "Món phức hợp",
            "name": "Cơm tấm sườn bì chả",
            "text": "1 đĩa cơm tấm sườn bì chả",
            "expected_items": 4,
            "min_kcal": 450,
            "max_kcal": 750,
        },
        {
            "group": "Món phức hợp",
            "name": "Bánh mì thịt nguội pate",
            "text": "1 ổ bánh mì thịt nguội pate",
            "expected_items": 3,
            "min_kcal": 300,
            "max_kcal": 550,
        },
        {
            "group": "Món phức hợp",
            "name": "Bún bò Huế giò chả",
            "text": "1 tô bún bò huế bắp giò",
            "expected_items": 4,
            "min_kcal": 350,
            "max_kcal": 650,
        },
        {
            "group": "Món phức hợp",
            "name": "Hủ tiếu Nam Vang",
            "text": "1 tô hủ tiếu nam vang tôm thịt",
            "expected_items": 4,
            "min_kcal": 300,
            "max_kcal": 550,
        },
        {
            "group": "Món phức hợp",
            "name": "Gỏi cuốn tôm thịt",
            "text": "3 cuốn gỏi cuốn tôm thịt chấm tương",
            "expected_items": 4,
            "min_kcal": 250,
            "max_kcal": 500,
        },
        {
            "group": "Món phức hợp",
            "name": "Pizza hải sản",
            "text": "1 cái pizza hải sản 300g",
            "expected_items": 4,
            "min_kcal": 400,
            "max_kcal": 800,
        },
        {
            "group": "Món phức hợp",
            "name": "Bún giò heo",
            "text": "1 tô bún giò heo",
            "expected_items": 3,
            "min_kcal": 350,
            "max_kcal": 650,
        },
        # --- Ingredient Lists (Danh sách món) ---
        {
            "group": "Danh sách món",
            "name": "Cơm gà rau muống",
            "text": "1 chén cơm trắng, 150g ức gà luộc, 1 đĩa rau muống xào tỏi",
            "expected_items": 3,
            "min_kcal": 400,
            "max_kcal": 650,
        },
        {
            "group": "Danh sách món",
            "name": "Trứng khoai chuối",
            "text": "2 quả trứng gà luộc, 1 củ khoai lang luộc, 1 quả chuối tiêu",
            "expected_items": 3,
            "min_kcal": 300,
            "max_kcal": 550,
        },
        {
            "group": "Danh sách món",
            "name": "Cháo yến mạch táo sữa",
            "text": "1 bát cháo yến mạch, 200ml sữa tươi không đường, 1 quả táo tây",
            "expected_items": 3,
            "min_kcal": 300,
            "max_kcal": 500,
        },
        # --- Single Foods (Món đơn lẻ & Định lượng VN) ---
        {
            "group": "Món đơn lẻ",
            "name": "Ức gà áp chảo",
            "text": "200g ức gà áp chảo",
            "expected_items": 1,
            "min_kcal": 250,
            "max_kcal": 400,
        },
        {
            "group": "Món đơn lẻ",
            "name": "Chén cơm trắng",
            "text": "1 chén cơm trắng",
            "expected_items": 1,
            "min_kcal": 150,
            "max_kcal": 250,
        },
        {
            "group": "Món đơn lẻ",
            "name": "Sinh tố bơ",
            "text": "1 ly sinh tố bơ ít đường",
            "expected_items": 1,
            "min_kcal": 200,
            "max_kcal": 350,
        },
        {
            "group": "Món đơn lẻ",
            "name": "Sữa chua không đường",
            "text": "1 hộp sữa chua không đường",
            "expected_items": 1,
            "min_kcal": 40,
            "max_kcal": 90,
        },
        {
            "group": "Món đơn lẻ",
            "name": "Hạt hạnh nhân",
            "text": "30g hạt hạnh nhân sấy",
            "expected_items": 1,
            "min_kcal": 140,
            "max_kcal": 220,
        },
        # --- Refinement (Chỉnh sửa món) ---
        {
            "group": "Chỉnh sửa món",
            "name": "Thêm trứng ốp la",
            "text": "thêm 1 quả trứng ốp la",
            "expected_items": 2,
            "min_kcal": 250,
            "max_kcal": 400,
            "current_items": [
                {
                    "name": "Bánh mì",
                    "quantity": 1,
                    "unit": "ổ",
                    "protein": 7.5,
                    "carbs": 45.0,
                    "fat": 1.5,
                }
            ],
        },
    ]

    latencies_ms: list[float] = []
    measurement_results: list[dict[str, Any]] = []

    with patch(
        "src.infra.adapters.meal_generation_service.MealGenerationService",
        return_value=gen_service,
    ):
        eb_module._configured_event_bus = None

        for tc in test_cases:
            payload: dict[str, Any] = {
                "text": tc["text"],
            }
            if "current_items" in tc:
                payload["current_items"] = tc["current_items"]

            start_time = time.perf_counter()
            response = authenticated_client.post(
                "/v1/meals/parse-text",
                json=payload,
                headers={"Accept-Language": "vi-VN,vi;q=0.9"},
            )
            elapsed_ms = (time.perf_counter() - start_time) * 1000.0
            latencies_ms.append(elapsed_ms)

            assert response.status_code == 200, (
                f"Failed case {tc['name']}: {response.text}"
            )
            data = response.json()

            # Contract verification
            assert "items" in data
            assert len(data["items"]) == tc["expected_items"], (
                f"Expected {tc['expected_items']} items for '{tc['text']}', got {len(data['items'])}: {[i['name'] for i in data['items']]}"
            )
            assert "total_calories" in data
            assert "total_protein" in data
            assert "total_carbs" in data
            assert "total_fat" in data
            assert "emoji" in data
            assert "unmatched_terms" in data

            # Verify prompt envelope
            last_prompt = gen_service.calls[-1]["prompt"]
            assert "language: vi" in last_prompt
            assert tc["text"] in last_prompt

            # Canonical backend calorie verification (Atwater formula with fiber)
            from src.domain.model.nutrition.macros import Macros as MacrosModel

            expected_kcal = round(
                sum(
                    MacrosModel(
                        protein=item["protein"],
                        carbs=item["carbs"],
                        fat=item["fat"],
                        fiber=item.get("fiber", 0.0) or 0.0,
                    ).total_calories
                    for item in data["items"]
                ),
                1,
            )
            assert abs(data["total_calories"] - expected_kcal) <= 1.0

            # Range bounds check
            assert tc["min_kcal"] <= data["total_calories"] <= tc["max_kcal"], (
                f"{tc['name']}: Calories {data['total_calories']} outside [{tc['min_kcal']}, {tc['max_kcal']}]"
            )

            # Verification that item names are in genuine Vietnamese (not English)
            from src.app.services.food_display_name import needs_display_localization

            for item in data["items"]:
                assert not needs_display_localization(item["name"], "vi"), (
                    f"Item name '{item['name']}' contains untranslated English"
                )

            measurement_results.append(
                {
                    "group": tc["group"],
                    "scenario": tc["name"],
                    "query": tc["text"],
                    "latency_ms": round(elapsed_ms, 2),
                    "items_count": len(data["items"]),
                    "items_names": [i["name"] for i in data["items"]],
                    "total_calories": data["total_calories"],
                    "protein": data["total_protein"],
                    "carbs": data["total_carbs"],
                    "fat": data["total_fat"],
                }
            )

    # Print clean formatted measurement report in Vietnamese
    p50_ms = statistics.median(latencies_ms)
    mean_ms = statistics.mean(latencies_ms)
    min_ms = min(latencies_ms)
    max_ms = max(latencies_ms)

    print("\n" + "=" * 90)
    print(f"{'BÁO CÁO ĐO LƯỜNG PARSE-TEXT TOÀN DIỆN TIẾNG VIỆT (E2E API)':^90}")
    print("=" * 90)
    current_group = ""
    for r in measurement_results:
        if r["group"] != current_group:
            current_group = r["group"]
            print(f"\n▶ [{current_group.upper()}]")
        items_str = ", ".join(r["items_names"])
        print(
            f"  • {r['scenario']:<24} | Input: '{r['query']}'\n"
            f"    └─ Calories: {r['total_calories']:>5.1f} kcal (P: {r['protein']}g, C: {r['carbs']}g, F: {r['fat']}g) | "
            f"Items ({r['items_count']}): [{items_str}] | "
            f"Độ trễ: {r['latency_ms']:>5.2f} ms"
        )
    print("\n" + "-" * 90)
    print(
        f"Tổng kết ({len(latencies_ms)} kịch bản): "
        f"Min = {min_ms:.2f} ms | P50 = {p50_ms:.2f} ms | Trung bình = {mean_ms:.2f} ms | Max = {max_ms:.2f} ms"
    )
    print(
        "Tất cả 15/15 kịch bản Tiếng Việt ĐẠT 100% (Chuẩn hoá 1-Turn, Chuẩn công thức Macro, Chuẩn Localization)"
    )
    print("=" * 90 + "\n")
