"""A day's doses split into the parts Vi chất shows and warns on."""

from datetime import UTC, date, datetime

from src.domain.model.supplement import SupplementIntake, SupplementNutrients
from src.domain.model.supplement.day_totals import supplement_day_totals


def _dose(amounts, *, preformed=None, estimated=False, id="i"):
    return SupplementIntake(
        id=id,
        user_id="u",
        product_id="p",
        units=1,
        taken_at=datetime(2026, 11, 1, 1, 0, tzinfo=UTC),
        local_date=date(2026, 11, 1),
        logged_on_day=True,
        nutrients=SupplementNutrients(amounts, vitamin_a_preformed=preformed),
        estimated=estimated,
    )


def test_doses_add_up_and_estimates_are_split_out():
    totals = supplement_day_totals(
        [
            _dose({"vitamin_d": 25.0, "zinc": 10.0}),
            _dose({"zinc": 5.0}, estimated=True, id="e"),
        ]
    )

    assert totals.micros == {"vitamin_d": 25.0, "zinc": 15.0}
    assert totals.estimated_micros == {"zinc": 5.0}
    assert totals.doses == 2
    assert totals.as_micros().vitamin_d == 25.0


def test_preformed_vitamin_a_skips_estimates_and_counts_unknown_forms_in_full():
    totals = supplement_day_totals(
        [
            _dose({"vitamin_a": 900.0}, preformed=300.0),
            _dose({"vitamin_a": 600.0}, id="unknown-form"),
            _dose({"vitamin_a": 3000.0}, preformed=3000.0, estimated=True, id="e"),
        ]
    )

    assert totals.vitamin_a_preformed == 900.0
    assert totals.micros["vitamin_a"] == 4500.0


def test_a_day_without_doses_has_empty_fields():
    totals = supplement_day_totals([])

    assert totals.as_micros() is None
    assert totals.to_fields() == {
        "supplement_micros": {},
        "supplement_estimated_micros": {},
        "supplement_vitamin_a_preformed": 0.0,
        "supplement_doses": 0,
    }


def test_home_leaves_estimated_sodium_out_and_zero_amounts_never_count():
    totals = supplement_day_totals(
        [
            _dose({"sodium": 200.0, "zinc": 0.0}),
            _dose({"sodium": 500.0, "iron": 5.0}, estimated=True, id="e"),
        ]
    )

    assert totals.micros == {"sodium": 700.0, "iron": 5.0}
    assert totals.home_micros() == {"sodium": 200.0, "iron": 5.0}
    only_estimate = supplement_day_totals([_dose({"sodium": 500.0}, estimated=True)])
    assert only_estimate.home_micros() == {}
    assert only_estimate.home_highlight_micros() is None
