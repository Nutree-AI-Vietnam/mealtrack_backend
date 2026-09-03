import logging
from abc import ABC, abstractmethod
from typing import Any

from src.domain.services.prompts.system_prompts import SystemPrompts

logger = logging.getLogger(__name__)


class MealAnalysisStrategy(ABC):
    """
    Abstract base class for meal analysis strategies.

    This implements the Strategy pattern for different types of context-aware
    meal analysis (basic, portion-aware, ingredient-aware, etc.)
    """

    @abstractmethod
    def get_analysis_prompt(self) -> str:
        """
        Get the system prompt for this analysis strategy.

        Returns:
            str: The system prompt text
        """
        pass

    @abstractmethod
    def get_user_message(self) -> str:
        """
        Get the user message for this analysis strategy.

        Returns:
            str: The user message text with context
        """
        pass

    @abstractmethod
    def get_strategy_name(self) -> str:
        """
        Get the name of this strategy for logging.

        Returns:
            str: Strategy name
        """
        pass


class BasicAnalysisStrategy(MealAnalysisStrategy):
    """
    Basic meal analysis strategy without additional context.
    """

    def __init__(
        self,
        optimized_prompt_enabled: bool | None = None,
        language: str = "en",
    ):
        if optimized_prompt_enabled is None:
            optimized_prompt_enabled = True
        self.optimized_prompt_enabled = bool(optimized_prompt_enabled)
        self.language = language

    def get_analysis_prompt(self) -> str:
        return SystemPrompts.get_vision_analysis_prompt(self.language)

    def get_user_message(self) -> str:
        return "Analyze this food image and provide nutritional information:"

    def get_strategy_name(self) -> str:
        return "BasicAnalysis"


class PortionAwareAnalysisStrategy(MealAnalysisStrategy):
    """
    Portion-aware meal analysis strategy.
    """

    def __init__(self, portion_size: float, unit: str):
        self.portion_size = portion_size
        self.unit = unit
        logger.info(f"Created PortionAwareAnalysisStrategy: {portion_size} {unit}")

    def get_analysis_prompt(self) -> str:
        return SystemPrompts.VISION_ANALYSIS

    def get_user_message(self) -> str:
        return (
            f"Analyze this food image.\n"
            f"Portion context: {self.portion_size} {self.unit}. "
            f"Scale all nutrition values to match this portion."
        )

    def get_strategy_name(self) -> str:
        return f"PortionAware({self.portion_size}{self.unit})"


class IngredientAwareAnalysisStrategy(MealAnalysisStrategy):
    """
    Ingredient-aware meal analysis strategy.
    """

    def __init__(self, ingredients: list[dict[str, Any]]):
        self.ingredients = ingredients
        logger.info(
            f"Created IngredientAwareAnalysisStrategy with {len(ingredients)} ingredients"
        )

    def get_analysis_prompt(self) -> str:
        return SystemPrompts.VISION_ANALYSIS

    def get_user_message(self) -> str:
        ing_str = ", ".join(
            f"{i.get('name', '')} ({i.get('quantity', '')} {i.get('unit', '')})"
            for i in self.ingredients[:6]
        )
        return (
            f"Analyze this food image.\n"
            f"Known ingredients: {ing_str}. "
            f"Use this context to improve accuracy."
        )

    def get_strategy_name(self) -> str:
        return f"IngredientAware({len(self.ingredients)}ingredients)"


class WeightAwareAnalysisStrategy(MealAnalysisStrategy):
    """
    Weight-aware meal analysis strategy.
    """

    def __init__(self, weight_grams: float):
        self.weight_grams = weight_grams
        logger.info(f"Created WeightAwareAnalysisStrategy: {weight_grams}g")

    def get_analysis_prompt(self) -> str:
        return SystemPrompts.VISION_ANALYSIS

    def get_user_message(self) -> str:
        return (
            f"Analyze this food image.\n"
            f"Total weight: {self.weight_grams}g. "
            f"Scale all nutrition values proportionally to this total weight."
        )

    def get_strategy_name(self) -> str:
        return f"WeightAware({self.weight_grams}g)"


class IngredientIdentificationStrategy(MealAnalysisStrategy):
    """
    Strategy for identifying a single ingredient from an image.

    Used when user takes a photo of an unknown food/ingredient and wants
    to identify it before getting meal suggestions.
    """

    def get_analysis_prompt(self) -> str:
        return SystemPrompts.INGREDIENT_IDENTIFY

    def get_user_message(self) -> str:
        return "Identify the food ingredient in this image:"

    def get_strategy_name(self) -> str:
        return "IngredientIdentification"


class FoodLabelImageAnalysisStrategy(MealAnalysisStrategy):
    """Read a packaged nutrition label image into the food-label contract."""

    def __init__(
        self,
        crop_metadata: dict[str, Any] | None = None,
    ):
        self.crop_metadata = crop_metadata or {}

    def get_analysis_prompt(self) -> str:
        return """You are a packaged-food nutrition-label reader. Analyze the attached label image and return structured nutrition facts as JSON only. No markdown, no prose, no commentary.

RESPONSE FORMAT - return exactly this structure:
{
  "is_food_label": true,
  "product_name": "Product name as printed, or Scanned Food Label",
  "brand": "Brand as printed, or null",
  "serving_size": {"display_text": "1 tsp (2g)", "grams": 2.0},
  "servings_per_package": 100.0,
  "label_calories_per_serving": 5.0,
  "macros_per_serving": {
    "protein_g": 0.0,
    "carbs_g": 1.0,
    "fat_g": 0.0,
    "fiber_g": 0.0,
    "sugar_g": 0.0
  },
  "micros_per_serving": null,
  "confidence": 0.86,
  "label_notes": ["Read from nutrition panel."]
}

LABEL GUARD:
- Use this flow only for packaged-food nutrition labels, Nutrition Facts panels, supplement facts, or international equivalent nutrition tables.
- If no nutrition table or nutrient values are visible, return is_food_label=false with product_name="Scanned Food Label", serving_size={"display_text":"100g","grams":100}, servings_per_package=1, macros_per_serving all zeros, confidence <= 0.2, and a short label note.
- Do not analyze plated meals here. Do not invent nutrition for a front-of-package photo without a readable nutrition panel.
- The image is the source of truth. Ignore any assumptions from product category, brand reputation, or front-label marketing unless the nutrition table is unreadable and you are returning is_food_label=false.

LANGUAGE AND IMAGE READING RULES:
- The label may be in Thai, Vietnamese, Japanese, Chinese, Korean, Arabic, Spanish, French, German, English, or mixed languages.
- Read nutrition facts directly from the image pixels. Do not require any pre-extracted text.
- Translate nutrient names internally, but return product_name and brand exactly as printed when visible.
- Accept comma decimals and local unit formatting, e.g. "1,5 g" means 1.5g.
- Read curved, rotated, low-contrast, or partially cropped labels carefully. Prefer the main nutrition table over address, halal, barcode, importer, or marketing text.

FIELD RULES:
- product_name: If visible, copy the product name as printed. If not visible, use "Scanned Food Label".
- brand: Copy only when visible and clearly a brand. Otherwise null.
- serving_size.display_text: Copy the serving-size text when visible, translated only if necessary for clarity.
- serving_size.grams: Convert the serving size to grams. If serving size is missing or only per-100g values are shown, use 100.
- servings_per_package: Use the label value when visible. If missing or unreadable, use 1.
- label_calories_per_serving: Use kcal/calories per serving. If only kJ is visible, convert kcal = kJ / 4.184. If no energy value is readable, null is allowed.
- macros_per_serving: Return protein_g, carbs_g, fat_g, fiber_g, and sugar_g per serving. Missing fiber or sugar should be 0.0. Protein, carbs, and fat must be present; use 0.0 only when the label explicitly says 0 or the row is clearly absent from a simplified zero-macro label.
- micros_per_serving: When printed, include sodium (mg), saturated_fat (g), added_sugar (g), potassium (mg), calcium (mg), iron (mg), vitamin_a (mcg), vitamin_c (mg), vitamin_e (mg), magnesium (mg). Omit a field rather than guessing. Do not convert %DV into milligrams unless the label also prints the absolute amount.
- confidence: 0.0 to 1.0 based on image clarity and table readability.
- label_notes: Short factual notes only: source basis, per-100g fallback, kJ conversion, unreadable rows, or omitted micronutrients.

NUTRIENT MAPPING:
- protein_g: protein, proteine, proteina, proteinas, โปรตีน, たんぱく質, 蛋白质, 단백질.
- carbs_g: total carbohydrate, carbohydrate, carbs, glucides, carbohidratos, คาร์โบไฮเดรต, 炭水化物, 碳水化合物.
- fat_g: total fat, fat, lipides, grasas, ไขมันทั้งหมด, 脂質, 脂肪.
- fiber_g: dietary fiber, fibre, fibra, ใยอาหาร, 食物繊維, 膳食纤维.
- sugar_g: total sugars, sugars, sucre, azucares, น้ำตาล, 糖類, 糖.
- Read sodium, saturated fat, added sugars, potassium, and printed vitamins/minerals into micros_per_serving when the label shows absolute amounts. Ignore percent daily value columns unless they are the only source and you add a note.

SERVING NORMALIZATION:
- If the table gives values "per serving", use those values directly.
- If the table gives both per serving and per 100g, use per serving.
- If the table gives only per 100g or per 100ml, set serving_size to 100g, servings_per_package to 1, and add a label note.
- If the serving is "1 tsp (2g)" and the table values are per serving, do not scale them to 100g.
- Do not use Daily Value percentages as gram values.

CALORIE CONSISTENCY:
- Check plausibility: kcal should be roughly protein*4 + carbs*4 + fat*9, unless label rounding or fiber energy explains a small difference.
- Do not alter printed macro values just to force calorie consistency. Keep printed values and add a note if calories seem inconsistent.

WORKED EXAMPLE 1 - Thai nutrition panel:
{
  "is_food_label": true,
  "product_name": "Scanned Food Label",
  "brand": null,
  "serving_size": {"display_text": "1 tsp (2g)", "grams": 2.0},
  "servings_per_package": 100.0,
  "label_calories_per_serving": 5.0,
  "macros_per_serving": {"protein_g": 0.0, "carbs_g": 1.0, "fat_g": 0.0, "fiber_g": 0.0, "sugar_g": 0.0},
  "confidence": 0.82,
  "label_notes": ["Thai nutrition panel read from image."]
}

WORKED EXAMPLE 2 - Per 100g European label:
{
  "is_food_label": true,
  "product_name": "Scanned Food Label",
  "brand": null,
  "serving_size": {"display_text": "100g", "grams": 100.0},
  "servings_per_package": 1.0,
  "label_calories_per_serving": 476.0,
  "macros_per_serving": {"protein_g": 10.0, "carbs_g": 78.0, "fat_g": 12.0, "fiber_g": 0.0, "sugar_g": 22.0},
  "confidence": 0.78,
  "label_notes": ["Only per-100g values were visible; serving normalized to 100g."]
}

Return ONLY valid JSON matching the response format above. No additional keys."""

    def get_user_message(self) -> str:
        return (
            "Read this packaged food nutrition-label image. Extract only the main "
            "nutrition table values into the food-label JSON contract. Prefer the "
            "label crop over surrounding package text, and use conservative defaults "
            "for missing package metadata while preserving readable macro values.\n\n"
            f"{self._metadata_block()}"
        )

    def get_strategy_name(self) -> str:
        return "FoodLabelImageAnalysis"

    def _metadata_block(self) -> str:
        blocks: list[str] = []
        crop_entries = self._safe_entries(
            self.crop_metadata,
            (
                "crop_strategy",
                "coordinate_space",
                "source_image_width",
                "source_image_height",
                "crop_x",
                "crop_y",
                "crop_width",
                "crop_height",
                "crop_image_width",
                "crop_image_height",
            ),
        )
        if crop_entries:
            blocks.append("Crop metadata:\n" + "\n".join(crop_entries))

        if not blocks:
            return ""
        return "\n\n".join(blocks) + "\n\n"

    def _safe_entries(
        self,
        metadata: dict[str, Any],
        safe_keys: tuple[str, ...],
    ) -> list[str]:
        entries: list[str] = []
        for key in safe_keys:
            value = metadata.get(key)
            if value is not None:
                entries.append(f"- {key}: {value}")
        return entries


class UserContextAwareAnalysisStrategy(MealAnalysisStrategy):
    """
    Analysis strategy that incorporates user-provided context.
    Used when user provides a description alongside their photo.

    Canonical food identities remain English; localized display fields are
    generated in the same structured response when a language is provided.
    """

    def __init__(self, user_description: str, language: str = "en"):
        self.user_description = user_description
        self.language = language
        logger.info(
            f"Created UserContextAwareAnalysisStrategy: desc_len={len(user_description)}"
        )

    def get_analysis_prompt(self) -> str:
        return SystemPrompts.get_vision_analysis_prompt(self.language)

    def get_user_message(self) -> str:
        return (
            f"Analyze this food image.\n"
            f"User context: {self.user_description}. "
            f"Provide accurate nutrition data for this meal."
        )

    def get_strategy_name(self) -> str:
        return "UserContextAware"


class AnalysisStrategyFactory:
    """
    Factory class for creating meal analysis strategies.

    Strategies keep canonical food identities in English and can request
    localized display fields in the same structured response.
    """

    @staticmethod
    def create_basic_strategy(
        optimized_prompt_enabled: bool | None = None,
        language: str = "en",
    ) -> MealAnalysisStrategy:
        """Create a basic analysis strategy."""
        return BasicAnalysisStrategy(
            optimized_prompt_enabled=optimized_prompt_enabled,
            language=language,
        )

    @staticmethod
    def create_portion_strategy(portion_size: float, unit: str) -> MealAnalysisStrategy:
        """Create a portion-aware analysis strategy."""
        return PortionAwareAnalysisStrategy(portion_size, unit)

    @staticmethod
    def create_ingredient_strategy(
        ingredients: list[dict[str, Any]],
    ) -> MealAnalysisStrategy:
        """Create an ingredient-aware analysis strategy."""
        return IngredientAwareAnalysisStrategy(ingredients)

    @staticmethod
    def create_weight_strategy(weight_grams: float) -> MealAnalysisStrategy:
        """Create a weight-aware analysis strategy."""
        return WeightAwareAnalysisStrategy(weight_grams)

    @staticmethod
    def create_ingredient_identification_strategy() -> MealAnalysisStrategy:
        """Create an ingredient identification strategy for photo recognition."""
        return IngredientIdentificationStrategy()

    @staticmethod
    def create_user_context_strategy(
        user_description: str,
        language: str = "en",
    ) -> MealAnalysisStrategy:
        """Create a user-context-aware analysis strategy.

        Args:
            user_description: Sanitized user-provided description

        Returns:
            MealAnalysisStrategy: Strategy that incorporates user context
        """
        return UserContextAwareAnalysisStrategy(user_description, language=language)

    @staticmethod
    def create_combined_strategy(
        portion_size: float | None = None,
        unit: str | None = None,
        ingredients: list[dict[str, Any]] | None = None,
    ) -> MealAnalysisStrategy:
        """
        Create a combined strategy with both portion and ingredient context.

        Args:
            portion_size: Target portion size (optional)
            unit: Unit of portion size (optional)
            ingredients: List of ingredients (optional)

        Returns:
            MealAnalysisStrategy: Appropriate strategy based on provided context
        """
        if portion_size and unit and ingredients:
            # TODO: Implement CombinedAnalysisStrategy for future use
            logger.info(
                "Combined strategy requested - using ingredient strategy for now"
            )
            return IngredientAwareAnalysisStrategy(ingredients)
        elif portion_size and unit:
            return PortionAwareAnalysisStrategy(portion_size, unit)
        elif ingredients:
            return IngredientAwareAnalysisStrategy(ingredients)
        else:
            return BasicAnalysisStrategy()
