from dataclasses import dataclass
from enum import Enum


class Sex(Enum):
    MALE = "male"
    FEMALE = "female"


class JobType(Enum):
    """Job type based on daily movement requirements."""

    DESK = "desk"
    ON_FEET = "on_feet"
    PHYSICAL = "physical"


class Goal(Enum):
    CUT = "cut"
    BULK = "bulk"
    RECOMP = "recomp"


class TrainingLevel(Enum):
    """Training experience level based on consistent resistance training history.

    Stored for profile/UX context. Protein g/kg is currently goal-scoped only
    (same rate at every training level):
    - BEGINNER: <1 year consistent training
    - INTERMEDIATE: 1-3 years consistent training
    - ADVANCED: 3+ years consistent training
    """

    BEGINNER = "beginner"
    INTERMEDIATE = "intermediate"
    ADVANCED = "advanced"


class UnitSystem(Enum):
    METRIC = "metric"
    IMPERIAL = "imperial"


class MacroPreset(Enum):
    STANDARD = "standard"
    KETO = "keto"


@dataclass
class TdeeRequest:
    """Domain model for TDEE calculation request."""

    age: int
    sex: Sex
    height: float
    weight: float
    body_fat_pct: float | None
    job_type: JobType
    training_days_per_week: int  # 0-7
    training_minutes_per_session: int  # 15-180
    goal: Goal
    unit_system: UnitSystem = UnitSystem.METRIC
    training_level: TrainingLevel | None = None
    macro_preset: MacroPreset = MacroPreset.STANDARD

    def __post_init__(self):
        """Validate invariants."""
        if not (12 <= self.age <= 120):
            raise ValueError(f"Age must be between 12 and 120: {self.age}")

        if self.unit_system == UnitSystem.METRIC:
            if not (100 <= self.height <= 272):
                raise ValueError(f"Height must be between 100-272 cm: {self.height}")
        else:
            if not (39 <= self.height <= 107):
                raise ValueError(f"Height must be between 39-107 inches: {self.height}")

        if self.unit_system == UnitSystem.METRIC:
            if not (30 <= self.weight <= 250):
                raise ValueError(f"Weight must be between 30-250 kg: {self.weight}")
        else:
            if not (66 <= self.weight <= 551):
                raise ValueError(f"Weight must be between 66-551 lbs: {self.weight}")

        if self.body_fat_pct is not None:
            if not (5 <= self.body_fat_pct <= 55):
                raise ValueError(
                    f"Body fat percentage must be between 5-55%: {self.body_fat_pct}"
                )

        # Validate training parameters
        if not (0 <= self.training_days_per_week <= 7):
            raise ValueError(
                f"Training days per week must be between 0-7: {self.training_days_per_week}"
            )
        # Persisted legacy (0, 15) profiles are normalized while read.
        if self.training_days_per_week == 0:
            if self.training_minutes_per_session in (0, 15):
                self.training_minutes_per_session = 0
            else:
                raise ValueError("Training minutes must be 0 when training days are 0")
        elif not (15 <= self.training_minutes_per_session <= 180):
            raise ValueError(
                f"Training minutes per session must be between 15-180: {self.training_minutes_per_session}"
            )

    @property
    def height_cm(self) -> float:
        """Convert height to centimeters."""
        if self.unit_system == UnitSystem.METRIC:
            return self.height
        else:
            return self.height * 2.54

    @property
    def weight_kg(self) -> float:
        """Convert weight to kilograms."""
        if self.unit_system == UnitSystem.METRIC:
            return self.weight
        else:
            return self.weight * 0.453592


@dataclass
class MacroTargets:
    """Represents macro targets matching Flutter MacroTargets class."""

    calories: float
    protein: float
    fat: float
    carbs: float


@dataclass
class TdeeResponse:
    """Domain model for TDEE calculation response matching Flutter TdeeResult."""

    bmr: float
    tdee: float
    goal: Goal
    macros: MacroTargets
    formula_used: str | None = (
        None  # BMR formula used (e.g., "Mifflin-St Jeor", "Katch-McArdle")
    )
    macro_preset: MacroPreset = MacroPreset.STANDARD

    def to_dict(self) -> dict:
        """Convert to dictionary format for API response."""
        result = {
            "bmr": self.bmr,
            "tdee": self.tdee,
            "goal": self.goal.value,
            "macro_preset": self.macro_preset.value,
            "macros": {
                "calories": self.macros.calories,
                "protein": self.macros.protein,
                "fat": self.macros.fat,
                "carbs": self.macros.carbs,
            },
        }
        if self.formula_used:
            result["formula_used"] = self.formula_used
        return result
