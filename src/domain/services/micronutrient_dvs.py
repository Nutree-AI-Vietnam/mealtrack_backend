"""Shared Daily Values for micronutrient line % DV.

NRF scoring still uses the constants in ``nrf_score.py``. This table is the
single source for display percents, including vitamins/minerals NRF does not
blend. Fiber is not listed — clients use IOM ``fiber_target_g``.
"""

from __future__ import annotations

# NRF encourage / limit DVs (must match nrf_score.py).
IRON_MG = 18.0
POTASSIUM_MG = 4700.0
SODIUM_MG = 2300.0
ADDED_SUGAR_G = 50.0
SATURATED_FAT_G = 20.0
CALCIUM_MG = 1300.0
MAGNESIUM_MG = 420.0
VITAMIN_A_MCG = 900.0
VITAMIN_C_MG = 90.0
VITAMIN_E_MG = 15.0

# Display-only FDA adult DVs.
VITAMIN_D_MCG = 20.0
VITAMIN_K_MCG = 120.0
THIAMIN_MG = 1.2
RIBOFLAVIN_MG = 1.3
NIACIN_MG = 16.0
VITAMIN_B6_MG = 1.7
VITAMIN_B12_MCG = 2.4
FOLATE_MCG = 400.0
PHOSPHORUS_MG = 1250.0
ZINC_MG = 11.0
SELENIUM_MCG = 55.0

MICRONUTRIENT_DVS: dict[str, float] = {
    "iron": IRON_MG,
    "potassium": POTASSIUM_MG,
    "sodium": SODIUM_MG,
    "added_sugar": ADDED_SUGAR_G,
    "saturated_fat": SATURATED_FAT_G,
    "calcium": CALCIUM_MG,
    "magnesium": MAGNESIUM_MG,
    "vitamin_a": VITAMIN_A_MCG,
    "vitamin_c": VITAMIN_C_MG,
    "vitamin_e": VITAMIN_E_MG,
    "vitamin_d": VITAMIN_D_MCG,
    "vitamin_k": VITAMIN_K_MCG,
    "thiamin": THIAMIN_MG,
    "riboflavin": RIBOFLAVIN_MG,
    "niacin": NIACIN_MG,
    "vitamin_b6": VITAMIN_B6_MG,
    "vitamin_b12": VITAMIN_B12_MCG,
    "folate": FOLATE_MCG,
    "phosphorus": PHOSPHORUS_MG,
    "zinc": ZINC_MG,
    "selenium": SELENIUM_MCG,
}
