"""
Centralized prompt template manager for meal generation.
Reduces prompt tokens through template compression and reuse.
"""

from typing import List, Optional

from .prompt_constants import (
    INGREDIENT_RULES,
    INGREDIENT_RULES_DETAILED,
    SEASONING_RULES,
    SEASONING_RULES_DETAILED,
    NUTRITION_RULES,
    JSON_SCHEMAS,
    GOAL_GUIDANCE,
    SYSTEM_MESSAGES,
    DECOMPOSITION_RULES,
    EMOJI_RULES,
    HEALTHY_EASY_RULE_LINE,
    HEALTHY_EASY_RULES,
    default_cooking_minutes,
)


class PromptTemplateManager:
    """
    Centralized prompt templates with compression.
    Reduces token count by 40-60% through deduplication.
    """

    @classmethod
    def get_ingredient_rules(cls, detailed: bool = False) -> str:
        """Get ingredient rules (compact or detailed)."""
        return INGREDIENT_RULES_DETAILED if detailed else INGREDIENT_RULES

    @classmethod
    def get_seasoning_rules(cls, detailed: bool = False) -> str:
        """Get seasoning rules (compact or detailed)."""
        return SEASONING_RULES_DETAILED if detailed else SEASONING_RULES

    @classmethod
    def get_nutrition_rules(cls) -> str:
        """Get nutrition rules."""
        return NUTRITION_RULES

    @classmethod
    def get_json_schema(cls, schema_type: str) -> str:
        """Get JSON schema for specified type."""
        return JSON_SCHEMAS.get(schema_type, JSON_SCHEMAS["single_meal"])

    @classmethod
    def get_goal_guidance(cls, goal: str) -> str:
        """Get goal-specific guidance."""
        return GOAL_GUIDANCE.get(goal, GOAL_GUIDANCE["maintain_weight"])

    @classmethod
    def get_system_message(cls, message_type: str) -> str:
        """Get system message for specified type."""
        return SYSTEM_MESSAGES.get(message_type, SYSTEM_MESSAGES["meal_planning"])

    @classmethod
    def build_base_requirements(
        cls,
        ingredients: List[str],
        seasonings: Optional[List[str]] = None,
        dietary_preferences: Optional[List[str]] = None,
        allergies: Optional[List[str]] = None,
    ) -> str:
        """
        Build compressed base requirements section.

        Args:
            ingredients: Available ingredients list
            seasonings: Available seasonings list
            dietary_preferences: User dietary preferences
            allergies: User allergies

        Returns:
            Compressed requirements string
        """
        parts = []

        # Ingredients (limit to 8 for token savings)
        ing_str = ", ".join(ingredients[:8]) if ingredients else "common ingredients"
        parts.append(f"Ingredients: {ing_str}")

        # Seasonings (optional)
        if seasonings:
            seas_str = ", ".join(seasonings[:5])
            parts.append(f"Seasonings: {seas_str}")

        # Constraints
        constraints = []
        if allergies:
            constraints.append(f"AVOID: {', '.join(allergies)}")
        if dietary_preferences:
            constraints.append(f"Diet: {', '.join(dietary_preferences)}")

        if constraints:
            parts.append(" | ".join(constraints))

        return "\n".join(parts)

    @classmethod
    def build_meal_targets(
        cls,
        meal_type: str,
        calories: int,
        protein: float,
        carbs: float,
        fat: float,
    ) -> str:
        """Build compressed meal targets string."""
        return (
            f"{meal_type}: {calories}cal (±50), "
            f"{int(protein)}g protein, {int(carbs)}g carbs, {int(fat)}g fat"
        )

    @classmethod
    def build_suggestion_prompt(
        cls,
        meal_type: str,
        target_calories: int,
        cooking_time_minutes: Optional[int] = None,
        ingredients: Optional[List[str]] = None,
        allergies: Optional[List[str]] = None,
        dietary_preferences: Optional[List[str]] = None,
        protein_hint: str = "",
    ) -> str:
        """
        Build compressed meal suggestion prompt.
        Target: ~500 tokens (down from ~1000+).
        """
        ingredients = ingredients or []
        # Build constraints
        constraints = []
        if allergies:
            constraints.append(f"⚠️ AVOID: {', '.join(allergies)}")
        if dietary_preferences:
            constraints.append(f"Diet: {', '.join(dietary_preferences)}")
        constraints_str = "\n".join(constraints) if constraints else ""

        # Build ingredients string
        ing_str = ", ".join(ingredients[:8]) if ingredients else "common ingredients"

        minutes = cooking_time_minutes or default_cooking_minutes(meal_type)
        time_str = f", ≤{minutes}min"

        return f"""Generate 1 {meal_type} meal (~{target_calories} cal{time_str}).
{HEALTHY_EASY_RULE_LINE}

INGREDIENTS: {ing_str}
{constraints_str}
{protein_hint}

{cls.get_ingredient_rules()}
{cls.get_seasoning_rules()}

OUTPUT JSON:
{cls.get_json_schema("suggestion_recipe")}

{EMOJI_RULES}
RULES:
1. ALL fields required: name, emoji, description, ingredients (3-8), recipe_steps (2-6), prep_time_minutes
2. Complete entire JSON - no truncation
3. Natural dish name (not "Quick/Speedy/Power Bowl")
4. Use listed ingredients with exact amounts (g/ml/tbsp/tsp)
5. Return ONLY valid JSON, no markdown
"""

    @classmethod
    def build_discovery_prompt(
        cls,
        meal_type: str,
        target_calories: int,
        count: int = 6,
        ingredients: Optional[List[str]] = None,
        cuisine_region: Optional[str] = None,
        exclude_meal_names: Optional[List[str]] = None,
        protein_target: Optional[float] = None,
        carbs_target: Optional[float] = None,
        fat_target: Optional[float] = None,
    ) -> str:
        """Build prompt for lightweight discovery: names + macros in a single call.
        ~200 token response for 6 meals."""
        ing_str = ", ".join((ingredients or [])[:4]) or "common ingredients"

        cuisine_str = f"\nCuisine: {cuisine_region}" if cuisine_region else ""

        exclude_str = ""
        if exclude_meal_names:
            exclude_str = f"\nDO NOT suggest: {', '.join(exclude_meal_names[:10])}"

        macro_str = ""
        if protein_target and carbs_target and fat_target:
            macro_str = f"\nMacro targets per meal: ~{int(protein_target)}g P, ~{int(carbs_target)}g C, ~{int(fat_target)}g F"

        minutes = default_cooking_minutes(meal_type)

        return f"""Generate {count} different {meal_type} meals, each ~{target_calories} cal, ≤{minutes}min.
MUST USE these ingredients (translate to English if not already): {ing_str}{cuisine_str}{macro_str}

Rules:
- {HEALTHY_EASY_RULE_LINE}
- Names: ENGLISH ONLY (no Vietnamese/foreign words like "Gà", "Cơm", "Sốt" — use "Chicken", "Rice", "Sauce")
- Names: natural, concise (max 5 words), VERY DIFFERENT styles
- Macros: realistic, calories ≈ protein*4 + carbs*4 + fat*9 (±10%)
- Fat must be ≥3g (real meals always have some fat){exclude_str}"""

    @classmethod
    def build_meal_names_prompt(
        cls,
        meal_type: str,
        target_calories: int,
        cooking_time_minutes: Optional[int] = None,
        ingredients: Optional[List[str]] = None,
        allergies: Optional[List[str]] = None,
        dietary_preferences: Optional[List[str]] = None,
        exclude_meal_names: Optional[List[str]] = None,
        servings: int = 1,
        cooking_equipment: Optional[List[str]] = None,
        cuisine_region: Optional[str] = None,
        names_count: int = 4,
    ) -> str:
        """
        Build prompt for Phase 1 meal name generation.
        Target: ~200 tokens.
        Always generates in English (translation happens in Phase 3).
        """
        ingredients = ingredients or []
        ing_str = ", ".join(ingredients[:4]) if ingredients else "common ingredients"

        constraints = []
        if allergies:
            constraints.append(f"Avoid: {', '.join(allergies)}")
        if dietary_preferences:
            veg_check = "vegetarian" in " ".join(dietary_preferences).lower()
            constraints.append("Vegetarian" if veg_check else "Diet: OK")

        constraints_str = " | " + " | ".join(constraints) if constraints else ""

        # Add exclusion list for regeneration
        exclude_str = ""
        if exclude_meal_names:
            exclude_str = f"\nDO NOT suggest: {', '.join(exclude_meal_names[:10])}"  # Limit to 10 to keep prompt short

        # Always state serving count explicitly so the AI sizes the
        # recipe correctly. Silence here was causing multi-serving
        # recipes to slip through for single-serving requests.
        servings_str = f" for {servings} serving{'s' if servings > 1 else ''}"

        # Add cooking equipment constraint
        equipment_str = ""
        if cooking_equipment:
            equipment_str = f"\nEquipment: {', '.join(cooking_equipment)}"

        # Add cuisine region constraint
        if cuisine_region:
            cuisine_str = f"\nCuisine: {cuisine_region} ({names_count} different dishes from this region)"
        else:
            cuisine_str = f"\nCuisines: {names_count} distinct (Asian, Mediterranean, Latin, American)"

        # A dish nobody can cook on a weekday is a dish nobody eats, so fall
        # back to a sensible ceiling instead of leaving the time open.
        minutes = cooking_time_minutes or default_cooking_minutes(meal_type)
        time_str = f", ≤{minutes}min"

        return f"""Generate {names_count} different {meal_type} names, ~{target_calories}cal{servings_str}{time_str}.
MUST USE these ingredients as main components: {ing_str}{constraints_str}{equipment_str}{cuisine_str}
{HEALTHY_EASY_RULE_LINE}
Names: Natural, concise (max 5 words), no "Quick/Healthy/Power" tags.{exclude_str}."""

    @classmethod
    def build_recipe_details_prompt(
        cls,
        meal_name: str,
        meal_type: str,
        target_calories: int,
        cooking_time_minutes: Optional[int] = None,
        ingredients: Optional[List[str]] = None,
        allergies: Optional[List[str]] = None,
        dietary_preferences: Optional[List[str]] = None,
        servings: int = 1,
        cooking_equipment: Optional[List[str]] = None,
        cuisine_region=None,
        protein_target: Optional[float] = None,
        carbs_target: Optional[float] = None,
        fat_target: Optional[float] = None,
    ) -> str:
        """
        Build prompt for Phase 2 recipe detail generation.
        Target: ~400 tokens.
        Always generates in English (translation happens in Phase 3).
        """
        ingredients = ingredients or []
        ing_str = ", ".join(ingredients[:6]) if ingredients else ""

        constraints_parts = []
        if allergies:
            constraints_parts.append(f"⚠️ AVOID: {', '.join(allergies)}")
        if dietary_preferences:
            constraints_parts.append(f"Diet: {', '.join(dietary_preferences)}")

        constraints_str = " | ".join(constraints_parts) if constraints_parts else ""

        if ing_str:
            ing_line = f"Use these ingredients as main components where compatible: {ing_str}"
        else:
            ing_line = "Use common ingredients appropriate for the dish."
        if constraints_str:
            ing_line += f" | {constraints_str}"

        # Always state serving count explicitly so the AI sizes the
        # recipe correctly.
        servings_str = f" for {servings} serving{'s' if servings > 1 else ''}"

        # Add cooking equipment constraint
        equipment_str = ""
        if cooking_equipment:
            equipment_str = f"\nEquipment available: {', '.join(cooking_equipment)}"

        # Add cuisine region if specified
        cuisine_str = ""
        if cuisine_region:
            cuisine_str = f"\nCuisine region: {cuisine_region}"

        # Add explicit macro targets when overrides provided
        macro_target_str = ""
        if (
            protein_target is not None
            and carbs_target is not None
            and fat_target is not None
        ):
            macro_target_str = f"\nMacro targets: ~{int(protein_target)}g protein, ~{int(carbs_target)}g carbs, ~{int(fat_target)}g fat"

        low_calorie_str = ""
        if target_calories <= 400:
            low_calorie_str = (
                "\nLow-calorie portion guidance: use 80-140g lean protein, "
                "plenty of vegetables, small starch portions, and 0-5g oil or added fat."
            )

        minutes = cooking_time_minutes or default_cooking_minutes(meal_type)
        time_str = f" | ≤{minutes} min"
        time_req_str = (
            f"\n- 2-6 clear recipe steps with duration"
            f"\n- Total prep_time ≤{minutes} min"
        )

        return f"""Generate complete recipe for: "{meal_name}"

{ing_line}
Target:{servings_str} — ~{target_calories} cal{time_str}{equipment_str}{cuisine_str}{macro_target_str}{low_calorie_str}

CRITICAL: Size all quantities for {servings} serving only — no batch scaling.

REQUIREMENTS:
- Match name "{meal_name}" exactly
- 3-8 ingredients in GRAMS, scaled for {servings} serving{'s' if servings > 1 else ''}{time_req_str}
- Include origin_country and cuisine_type in JSON

{HEALTHY_EASY_RULES}

{DECOMPOSITION_RULES}

{EMOJI_RULES}

OUTPUT JSON:
{cls.get_json_schema("suggestion_recipe")}
Return ONLY valid JSON, no markdown.
"""
