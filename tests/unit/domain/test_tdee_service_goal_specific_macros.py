"""
Unit tests for TDEE service weight-based macro calculations.

Tests the refactored _calculate_all_macro_targets method to verify:
1. Goal-specific calorie adjustments (surplus/deficit)
2. Weight-based protein and fat calculations (g/kg body weight)
3. Carbs calculated as remainder calories
4. Min/max bounds enforcement
"""

import pytest

from src.domain.constants import TDEEConstants
from src.domain.model.user import (
    Goal,
    JobType,
    Sex,
    TdeeRequest,
    TrainingLevel,
    UnitSystem,
)
from src.domain.services.tdee_service import TdeeCalculationService


class TestTdeeServiceGoalSpecificMacros:
    """Test goal-specific macro calculations in TdeeCalculationService."""

    @pytest.fixture
    def service(self):
        """Provide TdeeCalculationService instance."""
        return TdeeCalculationService()

    @pytest.fixture
    def base_request(self):
        """Provide base TDEE request for testing."""
        # 30-year-old male, 80kg, 180cm, desk job with moderate training
        return TdeeRequest(
            age=30,
            sex=Sex.MALE,
            height=180,
            weight=80,
            body_fat_pct=None,
            job_type=JobType.DESK,
            training_days_per_week=4,
            training_minutes_per_session=60,
            goal=Goal.RECOMP,
            unit_system=UnitSystem.METRIC,
        )

    @pytest.fixture
    def base_tdee(self):
        """Calculate base TDEE for test case."""
        # 30-year-old male, 80kg, 180cm, desk baseline
        # Mifflin-St Jeor: 10*80 + 6.25*180 - 5*30 + 5 = 800 + 1125 - 150 + 5 = 1780
        # JobType.DESK (1.2), planned training excluded and logged separately.
        # TDEE = 1780 * 1.2 = 2136
        return 2136.0

    # ===== BULKING TESTS =====

    def test_bulking_uses_300_calorie_surplus(self, service, base_request):
        """Verify bulking goal applies 300 calorie surplus to TDEE."""
        base_request.goal = Goal.BULK
        response = service.calculate_tdee(base_request)

        # Expected: TDEE + 300 = 2136 + 300 = 2436
        expected_calories = 2136.0 + 300
        assert response.macros.calories == pytest.approx(expected_calories, abs=0.1)

    def test_bulking_uses_weight_based_protein(self, service, base_request):
        """Verify bulking protein is calculated from body weight."""
        base_request.goal = Goal.BULK
        response = service.calculate_tdee(base_request)

        # Expected: 80kg * 1.6 g/kg = 128g protein
        expected_protein = 80 * 1.6
        assert response.macros.protein == pytest.approx(expected_protein, abs=1)

    def test_bulking_uses_weight_based_fat(self, service, base_request):
        """Verify bulking fat is calculated from body weight."""
        base_request.goal = Goal.BULK
        response = service.calculate_tdee(base_request)

        # Expected: 80kg * 1.0 g/kg = 80g fat (weight-based)
        # Dual-gate: max(80g weight, 2436*0.25/9=67.7g percent) = 80g
        expected_fat = 80.0
        assert response.macros.fat == pytest.approx(expected_fat, abs=1)

    def test_bulking_carbs_calculated_as_remainder(self, service, base_request):
        """Verify bulking carbs are calculated from remaining calories."""
        base_request.goal = Goal.BULK
        response = service.calculate_tdee(base_request)

        calories = response.macros.calories
        protein_cals = response.macros.protein * 4
        fat_cals = response.macros.fat * 9
        expected_carbs = (calories - protein_cals - fat_cals) / 4

        assert response.macros.carbs == pytest.approx(expected_carbs, abs=1)

    # ===== CUTTING TESTS =====

    def test_cutting_uses_300_calorie_deficit(self, service, base_request):
        """Verify cutting goal applies 300 calorie deficit to TDEE."""
        base_request.goal = Goal.CUT
        response = service.calculate_tdee(base_request)

        # Expected: TDEE - 300 = 2136 - 300 = 1836
        expected_calories = 2136.0 - 300
        assert response.macros.calories == pytest.approx(expected_calories, abs=0.1)

    def test_cutting_uses_weight_based_protein(self, service, base_request):
        """Verify cutting protein is calculated from body weight (higher than bulk)."""
        base_request.goal = Goal.CUT
        response = service.calculate_tdee(base_request)

        # Expected: 80kg * 1.8 g/kg = 144g protein
        expected_protein = 80 * 1.8
        assert response.macros.protein == pytest.approx(expected_protein, abs=1)

    def test_cutting_uses_weight_based_fat(self, service, base_request):
        """Verify cutting fat is calculated from body weight (lower than bulk)."""
        base_request.goal = Goal.CUT
        response = service.calculate_tdee(base_request)

        # Expected: 80kg * 0.8 g/kg = 64g fat (lower to preserve calories)
        expected_fat = 80 * 0.8
        assert response.macros.fat == pytest.approx(expected_fat, abs=1)

    def test_cutting_carbs_calculated_as_remainder(self, service, base_request):
        """Verify cutting carbs are calculated from remaining calories."""
        base_request.goal = Goal.CUT
        response = service.calculate_tdee(base_request)

        calories = response.macros.calories
        protein_cals = response.macros.protein * 4
        fat_cals = response.macros.fat * 9
        expected_carbs = (calories - protein_cals - fat_cals) / 4

        assert response.macros.carbs == pytest.approx(expected_carbs, abs=1)

    # ===== RECOMP TESTS =====

    def test_recomp_uses_tdee(self, service, base_request):
        """Verify recomposition goal uses TDEE without adjustment."""
        base_request.goal = Goal.RECOMP
        response = service.calculate_tdee(base_request)

        # Expected: TDEE = 2136 (no adjustment)
        expected_calories = 2136.0
        assert response.macros.calories == pytest.approx(expected_calories, abs=0.1)

    def test_recomp_uses_weight_based_protein(self, service, base_request):
        """Verify recomp protein is calculated from body weight."""
        base_request.goal = Goal.RECOMP
        response = service.calculate_tdee(base_request)

        # Expected: 80kg * 1.7 g/kg = 136g protein
        expected_protein = 80 * 1.7
        assert response.macros.protein == pytest.approx(expected_protein, abs=1)

    def test_recomp_uses_weight_based_fat(self, service, base_request):
        """Verify recomp fat is calculated from body weight."""
        base_request.goal = Goal.RECOMP
        response = service.calculate_tdee(base_request)

        # Expected: 80kg * 0.9 g/kg = 72g fat (weight-based)
        # Dual-gate: max(72g weight, 2136*0.25/9=59.3g percent) = 72g
        expected_fat = 72.0
        assert response.macros.fat == pytest.approx(expected_fat, abs=1)

    def test_recomp_carbs_calculated_as_remainder(self, service, base_request):
        """Verify recomp carbs are calculated from remaining calories."""
        base_request.goal = Goal.RECOMP
        response = service.calculate_tdee(base_request)

        calories = response.macros.calories
        protein_cals = response.macros.protein * 4
        fat_cals = response.macros.fat * 9
        expected_carbs = (calories - protein_cals - fat_cals) / 4

        assert response.macros.carbs == pytest.approx(expected_carbs, abs=1)

    # ===== EDGE CASE TESTS - CLAMPING =====

    def test_protein_clamped_to_max(self, service, base_request):
        """Verify protein is clamped to MAX_PROTEIN_G for heavy users."""
        base_request.weight = 200  # 200kg * 1.8 = 360g > 300 max
        base_request.goal = Goal.CUT
        response = service.calculate_tdee(base_request)

        assert response.macros.protein == TDEEConstants.MAX_PROTEIN_G

    def test_protein_clamped_to_min(self, service, base_request):
        """Verify protein is clamped to MIN_PROTEIN_G for light users."""
        base_request.weight = 30  # 30kg * 1.6 = 48g < 60 min
        base_request.goal = Goal.BULK
        response = service.calculate_tdee(base_request)

        assert response.macros.protein == TDEEConstants.MIN_PROTEIN_G

    def test_fat_clamped_to_max(self, service, base_request):
        """Verify fat is clamped to MAX_FAT_G for heavy users."""
        base_request.weight = 200  # 200kg * 1.0 = 200g > 150 max
        base_request.goal = Goal.BULK
        response = service.calculate_tdee(base_request)

        assert response.macros.fat == TDEEConstants.MAX_FAT_G

    def test_fat_clamped_to_min(self, service, base_request):
        """Verify fat is clamped to MIN_FAT_G for light users."""
        base_request.weight = 30  # 30kg * 0.8 = 24g < 40 min
        base_request.goal = Goal.CUT
        response = service.calculate_tdee(base_request)

        assert response.macros.fat == TDEEConstants.MIN_FAT_G

    def test_carbs_minimum_50g(self, service, base_request):
        """Verify minimum 50g carbs even when remaining calories are low."""
        # With low TDEE and high weight, carbs could go negative
        # Set up a scenario with low calorie target
        base_request.weight = 80
        base_request.goal = Goal.CUT
        response = service.calculate_tdee(base_request)

        # Carbs should always be at least MIN_CARBS_G
        assert response.macros.carbs >= TDEEConstants.MIN_CARBS_G

    # ===== ORIGINAL PROBLEM CASE TEST =====

    def test_84kg_user_gets_142_8g_protein_for_recomp(self, service, base_request):
        """Verify 84kg user gets ~142.8g protein for recomp."""
        base_request.weight = 84
        base_request.goal = Goal.RECOMP
        response = service.calculate_tdee(base_request)

        # 84kg * 1.7 g/kg = 142.8g
        expected_protein = 84 * 1.7
        assert response.macros.protein == pytest.approx(expected_protein, abs=1)

    # ===== GOAL ENUM TESTS =====

    def test_goal_enum_has_recomp(self):
        """Verify Goal enum contains RECOMP value."""
        assert hasattr(Goal, "RECOMP")
        assert Goal.RECOMP.value == "recomp"

    def test_goal_enum_has_all_required_goals(self):
        """Verify Goal enum has all required goal types."""
        required_goals = {"CUT", "BULK", "RECOMP"}
        available_goals = {g.name for g in Goal}
        assert required_goals.issubset(available_goals)

    # ===== MACRO TARGETS OBJECT TESTS =====

    def test_macro_targets_has_required_fields(self, service, base_request):
        """Verify MacroTargets object has calories, protein, fat, carbs fields."""
        response = service.calculate_tdee(base_request)
        macro_targets = response.macros

        # Verify all required fields exist and are floats
        assert hasattr(macro_targets, "calories")
        assert hasattr(macro_targets, "protein")
        assert hasattr(macro_targets, "fat")
        assert hasattr(macro_targets, "carbs")

        assert isinstance(macro_targets.calories, (int, float))
        assert isinstance(macro_targets.protein, (int, float))
        assert isinstance(macro_targets.fat, (int, float))
        assert isinstance(macro_targets.carbs, (int, float))

    def test_macro_targets_values_are_positive(self, service, base_request):
        """Verify all macro target values are positive."""
        response = service.calculate_tdee(base_request)
        macro_targets = response.macros

        assert macro_targets.calories > 0
        assert macro_targets.protein > 0
        assert macro_targets.fat > 0
        assert macro_targets.carbs > 0

    # ===== CROSS-GOAL COMPARISON TESTS =====

    def test_bulking_calories_higher_than_recomp(self, service, base_request):
        """Verify bulking has higher calories than recomposition."""
        base_request.goal = Goal.BULK
        bulking = service.calculate_tdee(base_request)

        base_request.goal = Goal.RECOMP
        recomp = service.calculate_tdee(base_request)

        assert bulking.macros.calories > recomp.macros.calories
        assert bulking.macros.calories == pytest.approx(
            recomp.macros.calories + 300, abs=0.1
        )

    def test_cutting_calories_lower_than_recomp(self, service, base_request):
        """Verify cutting has lower calories than recomposition."""
        base_request.goal = Goal.CUT
        cutting = service.calculate_tdee(base_request)

        base_request.goal = Goal.RECOMP
        recomp = service.calculate_tdee(base_request)

        assert cutting.macros.calories < recomp.macros.calories
        assert cutting.macros.calories == pytest.approx(
            recomp.macros.calories - 300, abs=0.1
        )

    def test_recomp_calories_equal_tdee(self, service, base_request):
        """Verify recomposition calories equal TDEE (no calorie adjustment)."""
        base_request.goal = Goal.RECOMP
        response = service.calculate_tdee(base_request)

        # Recomp should have no calorie adjustment (calories = TDEE)
        assert response.macros.calories == pytest.approx(response.tdee, abs=0.1)

    def test_cutting_higher_protein_per_kg_than_bulking(self, service, base_request):
        """Verify cutting uses higher protein per kg than bulking."""
        # Cutting: 1.8 g/kg, Bulking: 1.6 g/kg
        base_request.goal = Goal.CUT
        cutting = service.calculate_tdee(base_request)

        base_request.goal = Goal.BULK
        bulking = service.calculate_tdee(base_request)

        # Both use same weight, so cutting should have more absolute protein
        assert cutting.macros.protein > bulking.macros.protein

    # ===== CONSTANTS VALIDATION TESTS =====

    def test_tdee_constants_have_goal_adjustments(self):
        """Verify TDEEConstants defines goal adjustments."""
        assert hasattr(TDEEConstants, "CUTTING_DEFICIT")
        assert hasattr(TDEEConstants, "BULKING_SURPLUS")
        assert hasattr(TDEEConstants, "RECOMP_ADJUSTMENT")

        assert TDEEConstants.CUTTING_DEFICIT == 300
        assert TDEEConstants.BULKING_SURPLUS == 300
        assert TDEEConstants.RECOMP_ADJUSTMENT == 0

    def test_tdee_constants_have_weight_based_multipliers(self):
        """Verify TDEEConstants defines weight-based multipliers."""
        assert hasattr(TDEEConstants, "PROTEIN_PER_KG")
        assert hasattr(TDEEConstants, "FAT_PER_KG")
        assert hasattr(TDEEConstants, "FAT_MIN_PERCENT")
        assert hasattr(TDEEConstants, "MIN_CARBS_PER_KG")

        required_goals = {"bulk", "cut", "recomp"}
        assert required_goals.issubset(set(TDEEConstants.PROTEIN_PER_KG.keys()))
        assert required_goals.issubset(set(TDEEConstants.FAT_PER_KG.keys()))
        assert required_goals.issubset(set(TDEEConstants.FAT_MIN_PERCENT.keys()))

        # Verify correct protein values
        assert TDEEConstants.PROTEIN_PER_KG["cut"] == 1.8
        assert TDEEConstants.PROTEIN_PER_KG["recomp"] == 1.7
        assert TDEEConstants.PROTEIN_PER_KG["bulk"] == 1.6
        assert TDEEConstants.FAT_PER_KG["cut"] == 0.8
        assert TDEEConstants.FAT_PER_KG["recomp"] == 0.9
        assert TDEEConstants.FAT_PER_KG["bulk"] == 1.0

        # Verify fat min percent values
        assert TDEEConstants.FAT_MIN_PERCENT["cut"] == 0.20
        assert TDEEConstants.FAT_MIN_PERCENT["recomp"] == 0.25
        assert TDEEConstants.FAT_MIN_PERCENT["bulk"] == 0.25

        # Verify min carbs per kg
        assert TDEEConstants.MIN_CARBS_PER_KG == 2.5

    def test_tdee_constants_have_min_max_bounds(self):
        """Verify TDEEConstants defines min/max bounds for macros."""
        assert hasattr(TDEEConstants, "MIN_PROTEIN_G")
        assert hasattr(TDEEConstants, "MAX_PROTEIN_G")
        assert hasattr(TDEEConstants, "MIN_FAT_G")
        assert hasattr(TDEEConstants, "MAX_FAT_G")
        assert hasattr(TDEEConstants, "MIN_CARBS_G")

        assert TDEEConstants.MIN_PROTEIN_G == 60
        assert TDEEConstants.MAX_PROTEIN_G == 300
        assert TDEEConstants.MIN_FAT_G == 40
        assert TDEEConstants.MAX_FAT_G == 150
        assert TDEEConstants.MIN_CARBS_G == 50

    # ===== DIFFERENT JOB TYPES TEST =====

    def test_macro_targets_consistent_across_job_types(self, service, base_request):
        """Verify macro calculation logic works across different job types."""
        job_types = [JobType.DESK, JobType.ON_FEET, JobType.PHYSICAL]

        base_request.goal = Goal.BULK

        for job_type in job_types:
            base_request.job_type = job_type
            base_request.training_days_per_week = 4
            base_request.training_minutes_per_session = 60
            response = service.calculate_tdee(base_request)

            # Verify structure is valid for all job types
            assert response.macros.calories > 0
            assert response.macros.protein > 0
            assert response.macros.fat > 0
            assert response.macros.carbs > 0

            # Verify weight-based protein (should be same regardless of TDEE)
            expected_protein = 80 * 1.6  # bulk = 1.6 g/kg
            assert response.macros.protein == pytest.approx(expected_protein, abs=1)

    # ===== DIFFERENT WEIGHTS TEST =====

    def test_protein_scales_with_weight(self, service, base_request):
        """Verify protein scales proportionally with body weight."""
        base_request.goal = Goal.RECOMP
        base_request.weight = 80
        response_80kg = service.calculate_tdee(base_request)

        base_request.weight = 100
        response_100kg = service.calculate_tdee(base_request)

        # Protein should scale with weight (1.7 g/kg for recomp)
        assert response_100kg.macros.protein > response_80kg.macros.protein
        assert response_80kg.macros.protein == pytest.approx(80 * 1.7, abs=1)
        assert response_100kg.macros.protein == pytest.approx(100 * 1.7, abs=1)

    # ===== FAT PERCENTAGE FLOOR TESTS =====

    def test_fat_percentage_floor_overrides_weight_based(self, service, base_request):
        """Fat % floor activates when calories × min% / 9 > weight × g/kg."""
        # 50kg female, active, recomp → high TDEE relative to weight
        base_request.age = 25
        base_request.sex = Sex.FEMALE
        base_request.height = 160
        base_request.weight = 50
        base_request.job_type = JobType.ON_FEET
        base_request.goal = Goal.RECOMP

        response = service.calculate_tdee(base_request)

        # Fat from weight: 50 × 0.9 = 45g
        # Fat from percent: TDEE × 0.25 / 9 ≈ 79g (percent floor wins)
        fat_from_weight = 50 * 0.9
        assert response.macros.fat > fat_from_weight

    def test_fat_weight_based_wins_for_heavy_user(self, service, base_request):
        """Fat g/kg wins over % floor for heavier users."""
        # 100kg male, moderate, bulk
        base_request.age = 30
        base_request.sex = Sex.MALE
        base_request.height = 185
        base_request.weight = 100
        base_request.job_type = JobType.DESK
        base_request.goal = Goal.BULK

        response = service.calculate_tdee(base_request)

        # Fat from weight: 100 × 1.0 = 100g
        # Fat from percent: ~3100 × 0.25 / 9 ≈ 86g (weight-based wins)
        assert response.macros.fat == pytest.approx(100.0, abs=1.0)


# ===== TRAINING LEVEL TESTS =====


class TestTrainingLevelProtein:
    """Tests for training-level-aware protein multipliers."""

    @pytest.fixture
    def service(self):
        """Provide TdeeCalculationService instance."""
        return TdeeCalculationService()

    def test_beginner_recomp_gets_default_protein(self, service):
        """Beginner recomp: 1.7 g/kg (matches goal default)."""
        request = TdeeRequest(
            age=25,
            sex=Sex.MALE,
            height=180,
            weight=80,
            body_fat_pct=None,
            job_type=JobType.DESK,
            training_days_per_week=4,
            training_minutes_per_session=60,
            goal=Goal.RECOMP,
            training_level=TrainingLevel.BEGINNER,
        )
        response = service.calculate_tdee(request)
        assert response.macros.protein == pytest.approx(80 * 1.7, abs=1)

    def test_intermediate_recomp_gets_default_protein(self, service):
        """Intermediate recomp: 1.7 g/kg (matches goal default)."""
        request = TdeeRequest(
            age=25,
            sex=Sex.MALE,
            height=180,
            weight=80,
            body_fat_pct=None,
            job_type=JobType.DESK,
            training_days_per_week=4,
            training_minutes_per_session=60,
            goal=Goal.RECOMP,
            training_level=TrainingLevel.INTERMEDIATE,
        )
        response = service.calculate_tdee(request)
        assert response.macros.protein == pytest.approx(80 * 1.7, abs=1)

    def test_advanced_recomp_gets_default_protein(self, service):
        """Advanced recomp: 1.7 g/kg (matches goal default)."""
        request = TdeeRequest(
            age=25,
            sex=Sex.MALE,
            height=180,
            weight=80,
            body_fat_pct=None,
            job_type=JobType.DESK,
            training_days_per_week=4,
            training_minutes_per_session=60,
            goal=Goal.RECOMP,
            training_level=TrainingLevel.ADVANCED,
        )
        response = service.calculate_tdee(request)
        assert response.macros.protein == pytest.approx(80 * 1.7, abs=1)

    def test_cut_ignores_training_level(self, service):
        """Cut always uses 1.8 g/kg regardless of training level."""
        for level in TrainingLevel:
            request = TdeeRequest(
                age=25,
                sex=Sex.MALE,
                height=180,
                weight=80,
                body_fat_pct=None,
                job_type=JobType.DESK,
                training_days_per_week=4,
                training_minutes_per_session=60,
                goal=Goal.CUT,
                training_level=level,
            )
            response = service.calculate_tdee(request)
            assert response.macros.protein == pytest.approx(80 * 1.8, abs=1)

    def test_none_training_level_uses_default(self, service):
        """None training level falls back to PROTEIN_PER_KG defaults."""
        request = TdeeRequest(
            age=25,
            sex=Sex.MALE,
            height=180,
            weight=80,
            body_fat_pct=None,
            job_type=JobType.DESK,
            training_days_per_week=4,
            training_minutes_per_session=60,
            goal=Goal.RECOMP,
            training_level=None,
        )
        response = service.calculate_tdee(request)
        # Falls back to PROTEIN_PER_KG["recomp"] = 1.7
        assert response.macros.protein == pytest.approx(80 * 1.7, abs=1)

    def test_bulk_beginner_gets_1_6_protein(self, service):
        """Bulk beginner: 1.6 g/kg."""
        request = TdeeRequest(
            age=25,
            sex=Sex.MALE,
            height=180,
            weight=80,
            body_fat_pct=None,
            job_type=JobType.DESK,
            training_days_per_week=4,
            training_minutes_per_session=60,
            goal=Goal.BULK,
            training_level=TrainingLevel.BEGINNER,
        )
        response = service.calculate_tdee(request)
        assert response.macros.protein == pytest.approx(80 * 1.6, abs=1)

    def test_bulk_intermediate_gets_1_6_protein(self, service):
        """Bulk intermediate: 1.6 g/kg."""
        request = TdeeRequest(
            age=25,
            sex=Sex.MALE,
            height=180,
            weight=80,
            body_fat_pct=None,
            job_type=JobType.DESK,
            training_days_per_week=4,
            training_minutes_per_session=60,
            goal=Goal.BULK,
            training_level=TrainingLevel.INTERMEDIATE,
        )
        response = service.calculate_tdee(request)
        assert response.macros.protein == pytest.approx(80 * 1.6, abs=1)

    def test_bulk_advanced_gets_1_6_protein(self, service):
        """Bulk advanced: 1.6 g/kg."""
        request = TdeeRequest(
            age=25,
            sex=Sex.MALE,
            height=180,
            weight=80,
            body_fat_pct=None,
            job_type=JobType.DESK,
            training_days_per_week=4,
            training_minutes_per_session=60,
            goal=Goal.BULK,
            training_level=TrainingLevel.ADVANCED,
        )
        response = service.calculate_tdee(request)
        assert response.macros.protein == pytest.approx(80 * 1.6, abs=1)

    def test_training_level_constants_exist(self):
        """Verify PROTEIN_PER_KG_BY_TRAINING has all combos."""
        for goal in ["cut", "recomp", "bulk"]:
            for level in ["beginner", "intermediate", "advanced"]:
                assert level in TDEEConstants.PROTEIN_PER_KG_BY_TRAINING[goal]

    def test_training_level_constants_match_goal_defaults(self):
        """Verify training table matches goal-scoped PROTEIN_PER_KG rates."""
        for level in ["beginner", "intermediate", "advanced"]:
            assert TDEEConstants.PROTEIN_PER_KG_BY_TRAINING["cut"][level] == 1.8
            assert TDEEConstants.PROTEIN_PER_KG_BY_TRAINING["recomp"][level] == 1.7
            assert TDEEConstants.PROTEIN_PER_KG_BY_TRAINING["bulk"][level] == 1.6


class TestBmrFloorProtection:
    """Test BMR floor protection for small users on cutting goal."""

    @pytest.fixture
    def service(self):
        return TdeeCalculationService()

    def test_small_female_cutting_respects_1200_floor(self, service):
        """48kg/156cm/23F cutting should get 1200 kcal, not below-BMR 1148.

        Reported bug: user got 1147 kcal (below BMR of 1179).
        """
        request = TdeeRequest(
            age=23,
            sex=Sex.FEMALE,
            height=156,
            weight=48,
            body_fat_pct=None,
            job_type=JobType.DESK,
            training_days_per_week=4,
            training_minutes_per_session=60,
            goal=Goal.CUT,
        )
        response = service.calculate_tdee(request)

        # BMR for this user: 10*48 + 6.25*156 - 5*23 - 161 = 1179 kcal
        assert response.bmr == pytest.approx(1179, abs=1)

        # Calories should be at least 1200 (female clinical minimum)
        assert response.macros.calories >= TDEEConstants.MIN_CALORIES_FEMALE

    def test_small_male_cutting_respects_1500_floor(self, service):
        """Small male cutting should get at least 1500 kcal (male minimum)."""
        request = TdeeRequest(
            age=25,
            sex=Sex.MALE,
            height=165,
            weight=55,
            body_fat_pct=None,
            job_type=JobType.DESK,
            training_days_per_week=2,
            training_minutes_per_session=30,
            goal=Goal.CUT,
        )
        response = service.calculate_tdee(request)

        # Should respect male clinical minimum
        assert response.macros.calories >= TDEEConstants.MIN_CALORIES_MALE

    def test_large_user_cutting_unaffected_by_floor(self, service):
        """Large user cutting should apply full 300 deficit (floor not triggered)."""
        request = TdeeRequest(
            age=30,
            sex=Sex.MALE,
            height=185,
            weight=90,
            body_fat_pct=None,
            job_type=JobType.ON_FEET,
            training_days_per_week=5,
            training_minutes_per_session=90,
            goal=Goal.CUT,
        )
        response = service.calculate_tdee(request)

        # TDEE minus 300 should be well above 1500, so full deficit applies
        expected_cut_calories = response.tdee - 300
        assert response.macros.calories == pytest.approx(expected_cut_calories, abs=10)
        assert response.macros.calories > TDEEConstants.MIN_CALORIES_MALE

    def test_floor_uses_bmr_when_higher_than_clinical_min(self, service):
        """When BMR > clinical min, use BMR as floor."""
        # 55kg female with desk job + 3d×45min training
        # BMR ~1214, TDEE ~1600, after -300 deficit = ~1300 (above both floors)
        request = TdeeRequest(
            age=35,
            sex=Sex.FEMALE,
            height=160,
            weight=55,
            body_fat_pct=None,
            job_type=JobType.DESK,
            training_days_per_week=3,
            training_minutes_per_session=45,
            goal=Goal.CUT,
        )
        response = service.calculate_tdee(request)

        # BMR = 10*55 + 6.25*160 - 5*35 - 161 = 550 + 1000 - 175 - 161 = 1214
        # Clinical min for female = 1200
        # Floor should be max(BMR=1214, clinical=1200) = 1214
        assert response.macros.calories >= response.bmr
        assert response.macros.calories >= TDEEConstants.MIN_CALORIES_FEMALE
