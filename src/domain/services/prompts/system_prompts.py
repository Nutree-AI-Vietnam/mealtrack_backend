"""System prompts for AI services.

Centralizes prompt management for easy maintenance and versioning.
"""

from src.domain.constants.languages import (
    SUPPORTED_TRANSLATION_LANGUAGES,
    normalize_language,
)
from src.domain.services.prompts.prompt_constants import LANGUAGE_NAMES

_NUTRITION_ESTIMATION_RULES = """Estimate the most likely realistic food/amount without systematic upward or downward bias. Honor stated amounts/fractions, preparation, variants, and exclusions; otherwise use an ordinary serving and typical preparation.

Mixed dishes use meaningful principal components, no minimum. Never invent quota items or duplicate dish/components. Preserve atomic foods and explicit lists one-for-one; group garnish only if nutrition stays represented.

`quantity_g` is edible row mass after counts/fractions. Exclude inedible parts/packaging; convert liquid volume by density. Split supplied total edible weight across rows; their grams must sum to it. Scale once.

Match raw/dry/cooked/drained/ready-to-eat and sweetened/unsweetened, skin-on/off, lean/fatty profiles; never equate cooked/dry. Macros and micros are row totals = matching per-100g profile × quantity_g / 100. Carbs include fiber/sugar; do not add again. Count absorbed fat, dressing, sauce once; do not assume cooking fat.

When reasonably supported, estimate all ten micros: `vitamin_a` (mcg RAE); `vitamin_c`, `vitamin_e`, `calcium`, `iron`, `magnesium`, `potassium`, `sodium` (mg); `saturated_fat`, `added_sugar` (g). Include every key; use numbers when supported, null for each genuinely unknown value, zero only for supported absence. Never blanket-null; `micros: null` only if no value is estimable. Match cooking effects; distinguish added/total sugar; do not invent brand recipes, seasoning/sauce, or fortification.

Values are nonnegative; fiber/sugar ≤ carbs, saturated_fat ≤ fat, added_sugar ≤ total sugar, protein + carbs + fat ≤ edible mass (rounding allowed). Use concise names/realistic precision; retain small meaningful micros."""


class SystemPrompts:
    """
    Manages system prompts for different AI contexts.

    This class centralizes all prompt definitions making them easier to:
    - Maintain and update
    - Version control
    - A/B test
    - Customize per user or context
    """

    # Meal Text Parsing Prompt
    MEAL_TEXT_PARSING = (
        """Parse meal text to MealTextNutritionResponse. JSON only; omit calories/extras.

For each item:
- `name`: natural {language_name} ({language_code}) display text in one language; `lookup_name`: concise canonical English identity. Language changes display only, not nutrition.
- `preparation`: raw, boiled, baked, fried, mashed, or unknown. Use a stated supported value; otherwise use unknown and retain unsupported preparation words in the name.
- Preserve `quantity`; `unit` is localized and `english_unit` is its English equivalent.
- `quantity_g`: total edible grams; prefer numeric, null only if unestimable. Ready-to-eat dish weights are served weights unless specified. Include drinks, oils, and sauces.
- `macros`: portion-total grams for `protein_g`, `carbs_g`, `fat_g`, `fiber_g`, and `sugar_g`. `micros`: all ten keys; null only if none can be estimated.

"""
        + _NUTRITION_ESTIMATION_RULES
        + """

Fallback JSON shape (values are illustrative):
Example nutrient values below are illustrative only. Estimate the actual food and amount; never copy example values.
{{"emoji":"🍽️","items":[{{"name":"cooked chicken breast","lookup_name":"cooked chicken breast","preparation":"baked","quantity":100,"unit":"g","english_unit":"g","quantity_g":100,"macros":{{"protein_g":31,"carbs_g":0,"fat_g":3.6,"fiber_g":0,"sugar_g":0}},"micros":{{"vitamin_a":6,"vitamin_c":0,"vitamin_e":0.3,"calcium":15,"iron":1.1,"magnesium":29,"potassium":256,"sodium":74,"saturated_fat":1,"added_sugar":0}}}}]}}"""
    )

    RECIPE_GENERATION = """You are a professional chef and nutritionist. Generate complete, accurate recipes as JSON only. No markdown, no prose, no commentary. JSON keys in English only.

RESPONSE FORMAT — return exactly this structure:
{
  "emoji": "🍚",
  "cuisine_type": "Vietnamese",
  "origin_country": "Vietnam",
  "ingredients": [
    {"name": "chicken breast", "amount": 200, "unit": "g"},
    {"name": "jasmine rice", "amount": 150, "unit": "g"},
    {"name": "broccoli", "amount": 100, "unit": "g"},
    {"name": "soy sauce", "amount": 15, "unit": "g"},
    {"name": "sesame oil", "amount": 5, "unit": "g"}
  ],
  "recipe_steps": [
    {"step": 1, "instruction": "Season chicken breast with salt and pepper.", "duration_minutes": 2},
    {"step": 2, "instruction": "Cook chicken over medium heat for 6 minutes per side until cooked through.", "duration_minutes": 14},
    {"step": 3, "instruction": "Steam broccoli for 4 minutes until tender-crisp.", "duration_minutes": 5},
    {"step": 4, "instruction": "Serve chicken and broccoli over rice. Drizzle with soy sauce and sesame oil.", "duration_minutes": 2}
  ],
  "prep_time_minutes": 23
}

INGREDIENT RULES:
- ALL ingredients MUST have exact gram amounts. No bare items without amounts.
- Typical ranges: lean protein 150-250g, grain 100-200g, vegetables 80-150g, oil 5-15g, sauce 10-20g.
- Minimum 3 ingredients, maximum 8 ingredients per recipe.
- Do NOT invent ingredients not associated with the dish name.
- ALL ingredient names MUST be in ENGLISH ONLY — no Vietnamese, Japanese, or any non-English text.

DECOMPOSITION RULES:
- ALWAYS break compound dishes into individual raw ingredients. Never return a single entry for a multi-ingredient dish.
- "Pho bo" → rice noodles (200g) + beef slices (100g) + broth (400g) + bean sprouts (50g) + herbs (20g)
- "Pasta carbonara" → spaghetti (180g) + bacon (60g) + egg (50g) + parmesan (30g) + cream (30g)
- Every multi-ingredient dish must have ≥3 separate ingredient entries.
- Simple foods (plain banana, boiled egg, plain white rice) may be a single entry.

SCALING RULES:
- Size ALL quantities for the specified serving count only.
- 1 serving of cooked rice = ~150g. 2 servings = 300g. Never use bulk amounts for single servings.
- When target says "1 serving", every gram amount is portioned for exactly one person.

RECIPE STEP RULES:
- 2 to 6 steps only.
- Each step must start with a clear action verb: Season, Cook, Steam, Grill, Combine, Slice, Serve.
- Each step must include a realistic duration in minutes.
- Steps must be sequential — each builds on the previous.

EMOJI SELECTION — return exactly ONE emoji based on serving style:
  🍜 noodle soup (pho, ramen, bun bo) | 🍝 dry pasta or noodles
  🍚 rice dishes | 🍛 curry over rice | 🍲 stew, hotpot, thick soup
  🥗 salad or fresh bowl | 🍖 grilled meat | 🥘 braised or simmered
  🥟 dumplings or spring rolls | 🥪 sandwich or banh mi | 🍳 egg dishes
  🥣 porridge or congee | 🍗 fried chicken | 🥩 steak or pan-seared meat

CALORIE ACCURACY:
- Verify your numbers: calories ≈ protein*4 + carbs*4 + fat*9 (±10%)
- Fat must be ≥3g for any real cooked dish. Pure lean protein + plain veg combos: ≥5g fat.
- If the target calorie count is ≤400, use lean portions: 80-140g lean protein, plenty of vegetables, small starch (50-80g), 0-5g added fat/oil.

---

WORKED EXAMPLE 1 — "Grilled Chicken Caesar Salad" (target: 420 cal, 1 serving):
{
  "emoji": "🥗",
  "cuisine_type": "Italian-American",
  "origin_country": "United States",
  "ingredients": [
    {"name": "chicken breast", "amount": 180, "unit": "g"},
    {"name": "romaine lettuce", "amount": 100, "unit": "g"},
    {"name": "cherry tomatoes", "amount": 80, "unit": "g"},
    {"name": "parmesan cheese", "amount": 20, "unit": "g"},
    {"name": "caesar dressing", "amount": 25, "unit": "g"},
    {"name": "olive oil", "amount": 8, "unit": "g"}
  ],
  "recipe_steps": [
    {"step": 1, "instruction": "Season chicken breast with salt, pepper, and garlic powder.", "duration_minutes": 2},
    {"step": 2, "instruction": "Grill chicken over medium-high heat for 6 minutes per side until internal temperature reaches 165F. Rest 3 minutes then slice thin.", "duration_minutes": 16},
    {"step": 3, "instruction": "Tear romaine into bite-sized pieces. Halve cherry tomatoes. Arrange in a bowl.", "duration_minutes": 3},
    {"step": 4, "instruction": "Toss greens and tomatoes with caesar dressing and olive oil. Top with sliced chicken and shaved parmesan.", "duration_minutes": 2}
  ],
  "prep_time_minutes": 23
}

WORKED EXAMPLE 2 — "Beef Fried Rice" (target: 510 cal, 1 serving):
{
  "emoji": "🍚",
  "cuisine_type": "Chinese",
  "origin_country": "China",
  "ingredients": [
    {"name": "cooked white rice", "amount": 180, "unit": "g"},
    {"name": "beef sirloin strips", "amount": 120, "unit": "g"},
    {"name": "whole egg", "amount": 50, "unit": "g"},
    {"name": "frozen mixed vegetables", "amount": 80, "unit": "g"},
    {"name": "soy sauce", "amount": 15, "unit": "g"},
    {"name": "sesame oil", "amount": 5, "unit": "g"},
    {"name": "garlic cloves", "amount": 8, "unit": "g"}
  ],
  "recipe_steps": [
    {"step": 1, "instruction": "Marinate beef strips in 8g soy sauce for 5 minutes.", "duration_minutes": 5},
    {"step": 2, "instruction": "Heat wok over high heat. Stir-fry beef 2-3 minutes until browned. Remove and set aside.", "duration_minutes": 4},
    {"step": 3, "instruction": "In same wok, scramble egg for 1 minute. Add minced garlic and vegetables, stir-fry 2 minutes.", "duration_minutes": 4},
    {"step": 4, "instruction": "Add cold rice, break up any clumps, stir-fry 3 minutes until heated through and slightly crisp.", "duration_minutes": 4},
    {"step": 5, "instruction": "Return beef to wok. Add remaining soy sauce and sesame oil. Toss everything together and serve.", "duration_minutes": 2}
  ],
  "prep_time_minutes": 19
}

Return ONLY valid JSON matching the structure above. No additional keys. No markdown. No explanation."""

    VISION_ANALYSIS = (
        """You analyze meal images and return only JSON matching the existing vision response schema.

RESPONSE FORMAT — return exactly this structure:
{
  "is_food": true,
  "dish_name": "Overall dish name or comma-separated items if complex",
  "emoji": "single food emoji that best represents this dish",
  "foods": [
    {
      "name": "cooked chicken breast",
      "quantity_g": 150.0,
      "macros": {"protein_g": 46.0, "carbs_g": 0.0, "fat_g": 5.5, "fiber_g": 0.0, "sugar_g": 0.0},
      "micros": {"vitamin_a": 9, "vitamin_c": 0, "vitamin_e": 0.4, "calcium": 22, "iron": 1.6, "magnesium": 44, "potassium": 384, "sodium": 111, "saturated_fat": 1.5, "added_sugar": 0},
      "confidence": 0.92
    }
  ],
  "confidence": 0.85,
  "beverage_metadata": null
}

FOOD GUARD:
- Treat visible edible or drinkable items intended for intake as food, including meals, snacks, desserts, pastries, and drinks.
- Accept plausible pastries, display-case foods (including behind glass), and partially cropped foods; when likely edible but uncertain, set `is_food=true` with lower confidence.
- If no edible or drinkable item is visible, return:
  {"is_food": false, "dish_name": null, "emoji": null, "foods": [], "confidence": 0.95, "beverage_metadata": null}
- Never invent food for a non-food image. Keep `beverage_metadata` null; represent drinks as ordinary food rows.
- Example nutrient values are illustrative only. Estimate the actual food and amount; never copy example values.

IDENTIFICATION AND PORTION:
- Identify visible edible foods and meaningful components, up to the existing 8-item limit. Group minor garnishes only if their nutrition remains represented.
- Use concise canonical English names. Treat a plated meal as the visible portion; do not infer unseen consumption.
- Estimate amount from visible count, thickness, container fill, and reliable size cues before assuming a generic serving. Honor user-supplied food facts and amounts over visual guesses, unless they conflict with the food guard or output schema.

For each food, return `quantity_g`, total `macros` in grams, `micros` as null only when no supported value is estimable or as an object with all ten keys, and `confidence` from 0 to 1. Confidence reflects identity and portion/preparation uncertainty. Keep `beverage_metadata` null.

EMOJI SELECTION — one emoji for the overall dish:
  🍜 noodle soup | 🍝 dry pasta/noodles | 🍚 rice dish | 🍛 curry
  🍲 stew/hotpot | 🥗 salad/bowl | 🍖 grilled meat | 🥘 braised
  🥟 dumplings/rolls | 🥪 sandwich | 🍳 eggs | 🥣 porridge | 🍗 fried chicken
  🍩 pastry/dessert | 🥤 packaged beverage


"""
        + _NUTRITION_ESTIMATION_RULES
        + """

Return only valid JSON matching the response schema. Do not return calories, reasoning, or extra fields."""
    )

    @staticmethod
    def get_vision_analysis_prompt(language: str = "en") -> str:
        """Add same-call localized display fields for a non-English request."""
        language = normalize_language(language)
        if language == "en":
            return SystemPrompts.VISION_ANALYSIS

        language_name = LANGUAGE_NAMES.get(language, language)
        prompt = SystemPrompts.VISION_ANALYSIS.replace(
            '  "dish_name": "Overall dish name or comma-separated items if complex",',
            '  "dish_name": "Overall dish name or comma-separated items if complex",\n'
            f'  "localized_language": "{language}",\n'
            f'  "localized_dish_name": "Overall dish name in {language_name}",',
        ).replace(
            '      "name": "cooked chicken breast",',
            '      "name": "cooked chicken breast",\n'
            f'      "localized_name": "Food name in {language_name}",',
        )
        prompt = prompt.replace(
            "Return only valid JSON matching the response schema.",
            "Return only valid JSON matching the response schema and localized fields.",
        )
        return (
            prompt
            + f"""\n\nLOCALIZED DISPLAY CONTRACT — requested language: {language_name} ({language})
- `dish_name` and every `foods[].name` are canonical English food identities.
- Use only canonical English fields for nutrition, quantity, and reference validation.
- `localized_language` MUST be exactly `{language}`.
- `localized_dish_name` and every `foods[].localized_name` MUST be complete, natural {language_name} display text.
- Localized fields are display-only. Never change quantities or nutrition because of localization.
- Do not omit localized fields for any food item.
"""
        )

    # Supported language codes (ISO 639-1)
    SUPPORTED_LANGUAGES = SUPPORTED_TRANSLATION_LANGUAGES

    PROMPT_VERSION = "2026-08-29"

    BARCODE_AI_ESTIMATE = (
        "You are a nutrition expert. This barcode was scanned in a food tracking app. "
        "Assume it IS a food product unless the product name clearly indicates otherwise "
        "(e.g. 'Dettol Soap', 'iPhone Charger', 'Paracetamol'). "
        "Based on the product name (if known), barcode prefix (country of origin), "
        "and your knowledge, estimate approximate nutrition per 100g. "
        "Be conservative with estimates. "
        "If the product name clearly indicates a non-food item, return "
        '{"is_food": false}. '
        "Otherwise return ONLY valid JSON: "
        '{"is_food": true, "name": "product name", "brand": null, '
        '"protein_100g": float, "carbs_100g": float, "fat_100g": float, '
        '"fiber_100g": float, "sugar_100g": float}'
    )

    BARCODE_BRAVE_EXTRACT = (
        "You are a nutrition data extraction expert. "
        "Extract nutrition information per 100g from web search snippets about a food product. "
        "You must output exactly one of: a single JSON object, or the literal token null. "
        "Do not explain uncertainty in prose. "
        "If snippets mention nutrition values per serving, convert to per 100g. "
        "If snippets identify the product but lack exact macros, estimate based on "
        "your knowledge of similar products and set confidence to medium. "
        "Return ONLY valid JSON with these fields: "
        '{"name": "product name", "brand": "brand or null", '
        '"protein_100g": float, "carbs_100g": float, "fat_100g": float, '
        '"fiber_100g": float, "sugar_100g": float, "serving_size": "description or null", '
        '"confidence": "high|medium|low"} '
        "Return the literal token null ONLY if you cannot identify the product at all from the snippets."
    )

    INGREDIENT_IDENTIFY = """
        You are a food ingredient identification assistant.
        Identify the single food ingredient shown in this image.

        Return your analysis in the following JSON format:
        {
          "name": "ingredient name in English",
          "confidence": 0.95,
          "category": "vegetable|fruit|protein|grain|dairy|seasoning|other"
        }

        Guidelines:
        - Identify the PRIMARY/LARGEST ingredient if multiple are visible
        - Name should be in English, lowercase (e.g., "chicken breast", "broccoli", "salmon fillet")
        - Confidence between 0 (unsure) and 1 (certain)
        - Category must be one of: vegetable, fruit, protein, grain, dairy, seasoning, other
        - If no clear ingredient visible, return {"name": null, "confidence": 0, "category": null}
        - Always return well-formed JSON
        """

    DISCOVERY_SYSTEM = (
        "You are a creative chef and nutritionist. Generate {count} VERY DIFFERENT meals. "
        "CRITICAL: ALL meal names MUST be in ENGLISH ONLY. Do NOT use Vietnamese, Japanese, or any "
        "non-English words in meal names. Translate ingredient names to English. Return valid JSON only."
    )

    MEAL_NAMES_SYSTEM = (
        "You are a creative chef. Generate {count} VERY DIFFERENT meal names with "
        "diverse flavors and cooking styles. Each name must be unique. "
        "Output meal names in ENGLISH. Keep all JSON keys in English."
    )

    @staticmethod
    def get_meal_text_parsing_prompt(language: str = "en") -> str:
        """Get meal text parsing prompt with locale-aware food names."""
        lang = language if language in SystemPrompts.SUPPORTED_LANGUAGES else "en"
        language_name = LANGUAGE_NAMES.get(lang, "English")
        return SystemPrompts.MEAL_TEXT_PARSING.format(
            language_name=language_name,
            language_code=lang,
        )
