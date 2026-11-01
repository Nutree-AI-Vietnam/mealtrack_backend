"""Daily nutrient goals for people who are not pregnant or breastfeeding.

Source: Bộ Y tế – Viện Dinh dưỡng, "Nhu cầu dinh dưỡng khuyến nghị cho người
Việt Nam", NXB Y học 2016 (QĐ 2615/QĐ-BYT). Each table names the book table it
copies; the summary appendices (Phụ lục 5–7) agree unless noted.

Rows are ``(oldest age in the band, male, female)``; the last row has no upper
age. The app accepts ages 12 to 120, so tables start at the 12–14 band.

Choices the book leaves open:
- Iron uses the 15% absorption column (more than 90 g of meat or fish a day)
  and the 10% column for vegetarian or vegan diets.
- Girls 12–14 use the menstruating iron row; women 50 and over use the main
  (no periods) row.
- Zinc uses the moderate absorption column, which the book assigns to the
  Vietnamese diet.
"""

from __future__ import annotations

from typing import Final

AgeRow = tuple[int | None, float, float]
AgeTable = tuple[AgeRow, ...]

# Table 27, RDA (µg RAE). Bands split at 15–17 and 18–19.
VITAMIN_A_UG: Final[AgeTable] = (
    (14, 800, 700),
    (17, 900, 650),
    (29, 850, 650),
    (49, 900, 700),
    (69, 850, 700),
    (None, 800, 650),
)
# Table 28, RDA (µg).
VITAMIN_D_UG: Final[AgeTable] = ((49, 15, 15), (None, 20, 20))
# Table 29, AI (mg alpha-tocopherol).
VITAMIN_E_MG: Final[AgeTable] = ((17, 7.5, 6.0), (None, 6.5, 6.0))
# Table 30, AI (µg).
VITAMIN_K_UG: Final[AgeTable] = ((14, 150, 150), (17, 160, 160), (None, 150, 150))
# Table 41, RDA (mg).
VITAMIN_C_MG: Final[AgeTable] = ((14, 95, 95), (None, 100, 100))
# Table 31, RDA (mg).
THIAMIN_MG: Final[AgeTable] = (
    (14, 1.4, 1.3),
    (19, 1.4, 1.2),
    (29, 1.3, 1.1),
    (69, 1.2, 1.0),
    (None, 1.1, 1.0),
)
# Table 33, RDA (mg).
RIBOFLAVIN_MG: Final[AgeTable] = (
    (14, 1.6, 1.4),
    (19, 1.7, 1.4),
    (29, 1.5, 1.2),
    (69, 1.4, 1.2),
    (None, 1.3, 1.1),
)
# Table 35, RDA (mg NE).
NIACIN_MG: Final[AgeTable] = ((14, 12, 12), (None, 16, 14))
# Table 37, RDA (mg).
VITAMIN_B6_MG: Final[AgeTable] = (
    (14, 1.2, 1.1),
    (19, 1.3, 1.2),
    (49, 1.3, 1.3),
    (None, 1.7, 1.5),
)
# Table 38, RDA (µg DFE).
FOLATE_UG: Final[AgeTable] = ((19, 300, 400), (None, 400, 400))
# Table 39, RDA (µg).
VITAMIN_B12_UG: Final[AgeTable] = ((None, 2.4, 2.4),)
# Table 16, RDA (mg).
CALCIUM_MG: Final[AgeTable] = (
    (19, 1000, 1000),
    (49, 800, 800),
    (69, 800, 900),
    (None, 1000, 1000),
)
# Table 17, RDA (mg).
PHOSPHORUS_MG: Final[AgeTable] = ((19, 1250, 1250), (None, 700, 700))
# Table 18, RDA (mg).
MAGNESIUM_MG: Final[AgeTable] = (
    (14, 290, 280),
    (19, 350, 300),
    (29, 340, 270),
    (49, 370, 290),
    (69, 350, 290),
    (None, 320, 260),
)
# Phụ lục 7, adequate intake (mg); not the 3,510 mg dietary goal.
POTASSIUM_MG: Final[AgeTable] = (
    (14, 2400, 2200),
    (19, 2800, 2100),
    (None, 2500, 2000),
)
# Table 19, 15% absorption column (mg).
IRON_MG: Final[AgeTable] = (
    (14, 10.2, 21.8),
    (19, 11.6, 19.8),
    (49, 7.9, 17.4),
    (69, 7.9, 6.7),
    (None, 7.3, 6.3),
)
# Table 19, 10% absorption column (mg), for vegetarian or vegan diets.
IRON_PLANT_BASED_MG: Final[AgeTable] = (
    (14, 15.3, 32.6),
    (19, 17.5, 29.7),
    (49, 11.9, 26.1),
    (69, 11.9, 10.0),
    (None, 11.0, 9.4),
)
# Table 20, moderate absorption (mg). Phụ lục 5 prints 8.4 for women 20–49;
# Table 20 prints 8.0, used here.
ZINC_MG: Final[AgeTable] = ((14, 9.0, 8.0), (69, 10.0, 8.0), (None, 9.0, 7.0))
# Table 22, RDA (µg).
SELENIUM_UG: Final[AgeTable] = ((19, 32, 26), (69, 34, 26), (None, 33, 25))
# Table 15 (g). The book prints 29–31 g for boys 12–14; 31 g is used.
FIBER_G: Final[AgeTable] = (
    (14, 31, 26),
    (19, 38, 26),
    (49, 38, 25),
    (None, 30, 21),
)

# Limits rather than goals.
# Table 46 and Phụ lục 7: sodium dietary goal under 2,000 mg (5 g salt).
SODIUM_LIMIT_MG: Final = 2000.0
# NIN adult food pyramid: under 5 servings of 5 g sugar a day.
ADDED_SUGAR_LIMIT_G: Final = 25.0
# Chapter text: saturated fat at most 10% of energy.
SATURATED_FAT_ENERGY_FRACTION: Final = 0.10

GOAL_TABLES: Final[dict[str, AgeTable]] = {
    "vitamin_a": VITAMIN_A_UG,
    "vitamin_c": VITAMIN_C_MG,
    "vitamin_d": VITAMIN_D_UG,
    "vitamin_e": VITAMIN_E_MG,
    "vitamin_k": VITAMIN_K_UG,
    "thiamin": THIAMIN_MG,
    "riboflavin": RIBOFLAVIN_MG,
    "niacin": NIACIN_MG,
    "vitamin_b6": VITAMIN_B6_MG,
    "vitamin_b12": VITAMIN_B12_UG,
    "folate": FOLATE_UG,
    "calcium": CALCIUM_MG,
    "iron": IRON_MG,
    "magnesium": MAGNESIUM_MG,
    "phosphorus": PHOSPHORUS_MG,
    "potassium": POTASSIUM_MG,
    "zinc": ZINC_MG,
    "selenium": SELENIUM_UG,
}


def lookup(table: AgeTable, age: int, male: bool) -> float:
    """Value of the band that holds ``age``."""
    for oldest, male_value, female_value in table:
        if oldest is None or age <= oldest:
            return float(male_value if male else female_value)
    raise ValueError("age table has no open-ended last row")
