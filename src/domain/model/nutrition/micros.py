from dataclasses import dataclass
from typing import Dict, Optional


@dataclass(frozen=True)
class Micros:
    """
    Value object representing micronutrients in a meal.
    Flexible structure allowing various vitamins and minerals.
    """

    # Common vitamins (in mg or μg)
    vitamin_a: Optional[float] = None
    vitamin_c: Optional[float] = None
    vitamin_d: Optional[float] = None
    vitamin_e: Optional[float] = None
    vitamin_k: Optional[float] = None
    thiamin: Optional[float] = None
    riboflavin: Optional[float] = None
    niacin: Optional[float] = None
    vitamin_b6: Optional[float] = None
    vitamin_b12: Optional[float] = None
    folate: Optional[float] = None

    # Common minerals (in mg)
    calcium: Optional[float] = None
    iron: Optional[float] = None
    magnesium: Optional[float] = None
    phosphorus: Optional[float] = None
    potassium: Optional[float] = None
    sodium: Optional[float] = None
    zinc: Optional[float] = None
    selenium: Optional[float] = None

    # NRF limit nutrients, stored with micros (grams)
    saturated_fat: Optional[float] = None
    added_sugar: Optional[float] = None

    def __post_init__(self):
        """Validate all micronutrients are non-negative."""
        for field_name, value in self.__dict__.items():
            if value is not None and value < 0:
                raise ValueError(f"{field_name} cannot be negative: {value}")

    def to_dict(self) -> Dict[str, float]:
        """Convert to dictionary, excluding None values."""
        return {k: v for k, v in self.__dict__.items() if v is not None}

    @classmethod
    def from_dict(cls, data: Dict[str, float]) -> "Micros":
        """Create a Micros instance from a dictionary."""
        # Filter out any keys that are not fields in the dataclass
        valid_fields = {f.name for f in cls.__dataclass_fields__.values()}
        filtered_data = {k: v for k, v in data.items() if k in valid_fields}
        return cls(**filtered_data)
