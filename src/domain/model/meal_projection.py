"""Projection hint for meal reads.

Controls which related data a meal query eagerly loads. Lives in the domain layer
so application handlers can request a projection without importing infrastructure
(the SQLAlchemy load options for each projection stay in the repositories).
"""

from enum import Enum, auto


class MealProjection(Enum):
    MACROS_ONLY = auto()  # nutrition row only (no food_items / steps)
    MACROS_WITH_MICROS = auto()  # nutrition + food_items for NRF merge
    LIST_CARD = auto()  # image + nutrition + food_items + translations
    FULL = auto()  # image + nutrition + food_items (default)
    FULL_WITH_TRANSLATIONS = auto()  # everything, including translations
