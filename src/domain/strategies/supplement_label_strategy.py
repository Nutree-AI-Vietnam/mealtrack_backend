"""Prompt for reading a Supplement Facts panel into the supplement label contract."""

from src.domain.strategies.meal_analysis_strategy import MealAnalysisStrategy

_PROMPT = """You read photos of dietary supplement labels (Supplement Facts, \
"Thành phần", nutrient tables on vitamins, minerals, fish oil, gummies, drops, \
powders). Return JSON only, matching the response schema. No markdown, no prose.

Rules:
- is_supplement_label: true only when a nutrient table is visible and its amounts \
are readable. A blurry, cropped or glare-covered table, or a photo that is not a \
supplement label, is false; then leave rows empty.
- Copy every table row in order, as printed: name (keep any "(as ...)" or form text, \
for example "Vitamin A (as retinyl palmitate)" or "Vitamin E (as d-alpha tocopherol)"), \
amount as a number, unit as printed ("mg", "mcg", "µg", "IU", "mcg RAE", "mg α-TE", \
"mcg DFE", "g"). Never convert units and never compute amounts.
- nutrient: the tracked key a row counts toward, only when the row is that nutrient \
itself or names its element amount ("Zinc (as zinc oxide) 15 mg" -> zinc). Use one of: \
vitamin_a, vitamin_c, vitamin_d, vitamin_e, vitamin_k, thiamin, riboflavin, niacin, \
vitamin_b6, vitamin_b12, folate, calcium, iron, magnesium, phosphorus, potassium, \
sodium, zinc, selenium. Use null for anything else (iodine, biotin, omega-3, \
creatine, herbs, probiotics) and for a salt whose element amount is not printed \
("Calcium carbonate 1500 mg" without "equivalent to calcium ..." -> null).
- serving_units: how many units the amounts are for ("Serving size 2 softgels" -> 2, \
"Each tablet contains" -> 1, "per 5 ml" -> 5 with form liquid). Never assume 100 g.
- form: capsule, softgel, tablet, chewable, gummy, effervescent, powder, liquid, \
drops or sachet.
- serving_grams: the serving's weight in grams when printed (powders), else null.
- macros_per_serving: calories, protein, carbs and fat when the label prints them, \
else null.
- product_kind: protein_powder, mass_gainer or meal_replacement for those products; \
supplement for everything else, including fish oil, gummies and collagen tablets.
- brand and product_name as printed on the label (Vietnamese or English). barcode: \
the digits under a barcode when visible, else null.
- confidence: 0 to 1, how sure you are that every amount was read correctly."""


class SupplementLabelAnalysisStrategy(MealAnalysisStrategy):
    """Read one supplement label photo; amounts stay as printed."""

    def get_analysis_prompt(self) -> str:
        return _PROMPT

    def get_user_message(self) -> str:
        return (
            "Read this supplement label photo into the supplement label JSON "
            "contract. Copy every nutrient row as printed."
        )

    def get_strategy_name(self) -> str:
        return "SupplementLabelAnalysis"
