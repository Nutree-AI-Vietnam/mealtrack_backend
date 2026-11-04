"""
Centralized prompt constants with compressed templates.
Reduces prompt tokens by ~40-60% through deduplication.
"""

from typing import Dict

# =============================================================================
# INGREDIENT RULES (Compressed)
# =============================================================================
INGREDIENT_RULES = (
    "INGREDIENTS: Use ONLY listed items with exact portions (g/ml). "
    "Format: '300g rice', '450g chicken', '100g greens'. "
    "All items MUST have amounts. NO bare items like 'tomatoes'."
)

INGREDIENT_RULES_DETAILED = (
    "INGREDIENT REQUIREMENTS:\n"
    "- ALL ingredients MUST have exact portions (g/ml/pieces)\n"
    "- Use metric units only: grams (g), milliliters (ml)\n"
    "- Proteins: 400-600g, Grains: 200-400g, Vegetables: 100-300g\n"
    "- Example: ['450g chicken', '300g rice', '150g broccoli', '15ml oil']"
)


# =============================================================================
# SEASONING RULES (Compressed)
# =============================================================================
SEASONING_RULES = (
    "SEASONINGS: Exact amounts (g/ml). "
    "Example: ['2g salt', '1g pepper', '3g oregano']"
)

SEASONING_RULES_DETAILED = (
    "SEASONINGS REQUIREMENTS:\n"
    "- All seasonings MUST have exact amounts (g/ml)\n"
    "- Salt: 1-3g, Pepper: 0.5-1g, Herbs: 2-5g, Oils: 5-15ml\n"
    "- Example: ['2g salt', '1g pepper', '3g oregano', '5ml sesame oil']"
)


# =============================================================================
# NUTRITION RULES (Compressed)
# =============================================================================
NUTRITION_RULES = (
    "Match target calories (±50). Balance protein/carbs/fat per meal type."
)

# =============================================================================
# MACRO ACCURACY RULES — appended to recipe generation prompts
# =============================================================================
MACRO_ACCURACY_RULES = (
    "UNIT CONVERSION (MANDATORY):\n"
    "- Convert ALL quantities to grams before calculating macros\n"
    "- Liquid densities: honey=1.42g/ml, oil/butter=0.92g/ml, milk=1.03g/ml, "
    "soy sauce=1.2g/ml, yogurt=1.05g/ml, cream=1.01g/ml\n"
    "- Unknown liquids: assume 1.0g/ml (water density)\n"
    "- Return all ingredient amounts in GRAMS (not ml, cups, tbsp)\n"
    "  Example: '75ml honey' → '106g honey' (density 1.42)\n"
    "\n"
    "MACRO CALCULATION (MANDATORY):\n"
    "- Calculate each ingredient's macros from per-100g nutrition data\n"
    "- Sum all ingredient macros to get total macros\n"
    "- Verify: calories = protein*4 + carbs*4 + fat*9 (must match within 5%)\n"
    "- If mismatch: recalculate calories from macros\n"
)

# =============================================================================
# HEALTHY + EASY RULES — the only two things every generated dish must be
# =============================================================================
# One line for the naming phase, where the whole prompt is ~200 tokens.
HEALTHY_EASY_RULE_LINE = (
    "Healthy home cooking: whole foods, no deep-frying, "
    "no processed meat, everyday ingredients."
)

# The full version for the recipe phase, where we can afford the detail.
HEALTHY_EASY_RULES = (
    "HEALTHY AND EASY (MANDATORY):\n"
    "- Build the dish from whole foods: vegetables, whole grains, legumes, "
    "fruit, plus lean meat, fish, eggs, tofu or beans\n"
    "- Cook by grilling, steaming, boiling, stir-frying or baking "
    "— never deep-fry, and keep added oil under 15g\n"
    "- No processed meat (sausage, ham, bacon, luncheon meat), "
    "no sugary drinks or desserts\n"
    "- Everyday ingredients a home cook already has, one pan or pot, "
    "no special equipment\n"
)

# How long a dish may take when the caller did not say. Breakfast is the one
# people skip when it is hard, so it gets the tightest budget.
DEFAULT_COOKING_MINUTES: dict[str, int] = {
    "breakfast": 15,
    "snack": 10,
    "lunch": 30,
    "dinner": 30,
}
FALLBACK_COOKING_MINUTES = 30


def default_cooking_minutes(meal_type: str) -> int:
    """Minutes a dish of this meal type may take when nobody set a limit."""
    return DEFAULT_COOKING_MINUTES.get(
        (meal_type or "").strip().lower(), FALLBACK_COOKING_MINUTES
    )


# =============================================================================
# DECOMPOSITION RULES — enforce ingredient-level breakdown
# =============================================================================
DECOMPOSITION_RULES = (
    "INGREDIENT DECOMPOSITION (MANDATORY):\n"
    "- ALWAYS break down compound dishes into individual ingredients\n"
    "- Never return a single entry for a multi-ingredient dish\n"
    "- Minimum 3 ingredients per dish (most real dishes have 4+)\n"
    "- Simple foods (banana, egg, plain rice) may stay as 1 item\n"
    "- Examples:\n"
    "  ✅ 'pho bo' → rice noodle (200g), beef (100g), broth (400g), bean sprouts (50g), herbs (20g), oil (5g)\n"
    "  ✅ 'pasta carbonara' → spaghetti (180g), bacon (60g), egg (50g), parmesan (30g), cream (30g)\n"
    "  ❌ 'pho bo' → 1 entry with 450 kcal\n"
)

# Vision-specific decomposition rules (used by all 5 analysis strategies)
VISION_DECOMPOSITION_RULES = """
CRITICAL — INGREDIENT DECOMPOSITION:
- ALWAYS decompose compound dishes into individual ingredients
- If you see a bowl of soup: list broth, noodles, meat, vegetables separately
- If you see a sandwich: list bread, meat, cheese, sauce separately
- Never return compound dish names as single items (e.g. "pho" → list noodle, beef, broth, etc.)
- Simple single-ingredient items (banana, egg, plain rice) stay as 1 item
- Each ingredient: name, quantity (grams), unit, calories, macros

MACRO ACCURACY:
- All quantities in GRAMS (convert volumes using density: honey=1.42g/ml, oil=0.92g/ml)
- Verify: calories ≈ protein*4 + carbs*4 + fat*9

EMOJI SELECTION (for the "emoji" field):
- Return exactly ONE emoji that represents the OVERALL DISH, not individual ingredients
- Pick emoji based on the SERVING STYLE, not just the main ingredient:
  🍜 = noodle soup served in broth (phở, bún bò Huế, bún riêu, ramen, udon soup)
  🍝 = dry pasta/noodles without broth (spaghetti, mì xào, pad thai)
  🍚 = rice-based dishes (cơm, fried rice, bibimbap)
  🍛 = curry or saucy dish over rice
  🍲 = stew, hotpot, or thick soup (lẩu, canh, chowder)
  🥗 = salad or fresh/cold dishes (gỏi)
  🍖 = grilled/roasted meat dishes (bún chả, thịt nướng, BBQ)
  🥘 = braised/simmered dishes (kho, bò kho)
  🥟 = dumplings, spring rolls, wrapped items (nem, bánh cuốn, gyoza)
  🥪 = sandwiches, bánh mì
  🍳 = egg-based dishes (omelette, trứng chiên)
  🥣 = porridge, oatmeal, cháo
  🍱 = bento/meal box/combo platter
  🍗 = fried chicken, fried items
  🥩 = steak or large meat cuts
  🍕🍔🌮🌯 = pizza, burger, taco, burrito (Western fast food)
- If unsure, use 🍽️ as fallback
- NEVER return text or multiple emoji — exactly one emoji character
"""


# =============================================================================
# EMOJI RULES — guide AI emoji selection for meal suggestions
# =============================================================================
EMOJI_RULES = (
    "EMOJI FIELD (MANDATORY):\n"
    "- Return exactly ONE emoji based on the dish's SERVING STYLE:\n"
    "  🍜 noodle soup (phở, bún bò Huế, ramen) | 🍝 dry noodles/pasta\n"
    "  🍚 rice dishes | 🍛 curry over rice | 🍲 stew/hotpot/thick soup\n"
    "  🥗 salad/fresh | 🍖 grilled meat | 🥘 braised/simmered\n"
    "  🥟 dumplings/rolls | 🥪 sandwich/bánh mì | 🍳 egg dishes\n"
    "  🥣 porridge/cháo | 🍗 fried chicken | 🥩 steak\n"
    "- NEVER return text or multiple emoji\n"
)

# =============================================================================
# JSON SCHEMAS (Compressed - No inline comments)
# =============================================================================
JSON_SCHEMAS: Dict[str, str] = {
    "weekly_meal": """{
  "week": [{
    "day": "Monday",
    "meals": [{
      "meal_type": "breakfast",
      "name": "Meal Name",
      "emoji": "🍳",
      "description": "Brief description",
      "calories": 400,
      "protein": 25.0,
      "carbs": 45.0,
      "fat": 15.0,
      "prep_time": 10,
      "cook_time": 15,
      "ingredients": ["300g rice", "450g chicken"],
      "seasonings": ["2g salt", "1g pepper"],
      "instructions": ["step1", "step2"],
      "is_vegetarian": false,
      "is_vegan": false,
      "is_gluten_free": false,
      "cuisine_type": "International"
    }]
  }]
}""",
    "daily_meal": """{
  "meals": [{
    "meal_type": "breakfast",
    "name": "Name",
    "emoji": "🍳",
    "description": "Description",
    "calories": 400,
    "protein": 25.0,
    "carbs": 45.0,
    "fat": 15.0,
    "prep_time": 10,
    "cook_time": 15,
    "ingredients": ["300g rice", "450g chicken"],
    "seasonings": ["2g salt", "1g pepper"],
    "instructions": ["step1", "step2"],
    "is_vegetarian": false,
    "is_vegan": false,
    "is_gluten_free": false,
    "cuisine_type": "International"
  }]
}""",
    "single_meal": """{
  "name": "Name",
  "emoji": "🍳",
  "description": "Description",
  "calories": 400,
  "protein": 25.0,
  "carbs": 45.0,
  "fat": 15.0,
  "prep_time": 10,
  "cook_time": 15,
  "ingredients": ["300g rice", "450g chicken"],
  "seasonings": ["2g salt", "1g pepper"],
  "instructions": ["step1", "step2"],
  "is_vegetarian": false,
  "is_vegan": false,
  "is_gluten_free": false,
  "cuisine_type": "International"
}""",
    "suggestion_recipe": """{
  "emoji": "🍚",
  "cuisine_type": "Vietnamese",
  "origin_country": "Vietnam",
  "ingredients": [
    {"name": "ingredient1", "amount": 200, "unit": "g"},
    {"name": "ingredient2", "amount": 100, "unit": "g"},
    {"name": "ingredient3", "amount": 50, "unit": "g"}
  ],
  "recipe_steps": [
    {"step": 1, "instruction": "Action", "duration_minutes": 5},
    {"step": 2, "instruction": "Action", "duration_minutes": 10}
  ],
  "prep_time_minutes": 20
}""",
}


# =============================================================================
# GOAL GUIDANCE (Compressed)
# =============================================================================
GOAL_GUIDANCE = {
    "lose_weight": "High-volume, low-calorie, fiber-rich, high-protein",
    "gain_weight": "Calorie-dense, healthy fats, complex carbs",
    "build_muscle": "High protein, complete amino acids",
    "maintain_weight": "Balanced portions",
    "cut": "High-volume, low-calorie, fiber-rich, high-protein",
    "bulk": "Calorie-dense, healthy fats, complex carbs",
    "recomp": "Balanced macros, moderate deficit",
}


# =============================================================================
# SYSTEM MESSAGES (Compressed)
# =============================================================================
SYSTEM_MESSAGES = {
    "meal_planning": "You are a meal planning assistant. Return only valid JSON.",
    "nutritionist": "You are a professional nutritionist. Return only valid JSON.",
    "chef": "You are a professional chef. Generate complete recipe details as valid JSON.",
    "creative_chef": "You are a creative chef. Generate diverse meal names with different cuisines.",
}

# =============================================================================
# LANGUAGE NAMES (Compressed)
# =============================================================================
LANGUAGE_NAMES = {
    "en": "English",
    "vi": "Vietnamese",
    "es": "Spanish",
    "fr": "French",
    "de": "German",
    "ja": "Japanese",
    "zh": "Chinese",
}


# =============================================================================
# FALLBACK MEAL NAMES (English-only, will be translated in Phase 3)
# =============================================================================
def get_fallback_meal_name(language: str, meal_type: str, index: int) -> str:
    """Get English fallback meal name.

    Note: Always returns English names. Translation happens in Phase 3 if needed.

    Args:
        language: ISO 639-1 language code (ignored, always uses English)
        meal_type: Meal type ('breakfast', 'lunch', 'dinner', 'snack')
        index: Index number for the fallback (1-based)

    Returns:
        English fallback name like "Healthy Breakfast #1"
    """
    base_name = f"Healthy {meal_type.title()}"
    return f"{base_name} #{index}"
