"""What and when the user ate or drank that held the week back."""

from __future__ import annotations

from src.domain.services.hydration_catalog_service import (
    catalog_drink_id,
    localized_name_for_catalog_name,
)
from src.domain.services.weekly_recap.compare import (
    FIBER,
    LATE_SNACKS,
    SUGARY_DRINKS,
    WATER,
    trend,
)
from src.domain.services.weekly_recap.context import RecapContext
from src.domain.services.weekly_recap.format import js_round
from src.domain.services.weekly_recap.types import Card, Issue, Move
from src.domain.services.weekly_recap.viz import bar, card, dots

# Languages that write a drink name ("milk tea") in lower case mid-sentence.
_LOWER_NOUNS = frozenset({"en", "vi", "es", "fr"})


def _fiber_low(ctx: RecapContext) -> bool:
    stats = ctx.stats
    return stats.fiber_avg < stats.fiber_target * ctx.config.fiber_low_ratio


def _fiber_low_note(ctx: RecapContext) -> str:
    days = ctx.stats.lunch_no_veg_days
    if days < ctx.config.lunch_no_veg_days:
        return ctx.t("fiber_low.note")
    # Matches the plan's "n/d days": every day of the week had a bare lunch.
    every = days >= ctx.facts.week_days
    return ctx.t("fiber_low.note.lunch_all" if every else "fiber_low.note.lunch")


def _fiber_low_card(ctx: RecapContext) -> Card:
    stats = ctx.stats
    return card(
        "fiber",
        "eco",
        ctx.t("fiber_low.title"),
        count=js_round(stats.fiber_avg),
        suffix="g",
        unit=ctx.t("card.unit.grams_per_day", t=ctx.num(stats.fiber_target)),
        note=_fiber_low_note(ctx),
        viz=bar(stats.fiber_avg, stats.fiber_target),
        trend=trend(ctx, FIBER),
    )


def _fiber_low_advice(ctx: RecapContext) -> Move:
    stats = ctx.stats
    gap = js_round(stats.fiber_target - stats.fiber_avg)
    if stats.lunch_no_veg_days > 0:
        why = ctx.t("fiber_low.why", n=stats.lunch_no_veg_days, d=ctx.facts.week_days)
    else:
        why = ctx.t("fiber_low.why.week")
    return Move(
        "fiber",
        "eco",
        ctx.t("fiber_low.advice"),
        ctx.t("fiber_low.impact", n=ctx.num(min(ctx.config.fiber_impact_cap_g, gap))),
        why,
        ctx.t("fiber_low.short"),
    )


def _water_low(ctx: RecapContext) -> bool:
    facts, target = ctx.facts, ctx.stats.water_target
    return facts.water_tracked and facts.water_avg < target * ctx.config.water_low_ratio


def _driest_day(ctx: RecapContext) -> int | None:
    """The logged day with the least water, unless another day ties it.

    A day with no food and no drink logged says nothing about water.
    """
    water, logged = ctx.stats.water_by_day, ctx.facts.logged
    days = sorted(
        (day for day in range(ctx.facts.first_day, 7) if logged[day] or water[day]),
        key=lambda day: water[day],
    )
    if len(days) > 1 and water[days[0]] == water[days[1]]:
        return None
    return days[0] if days else None


def _water_low_note(ctx: RecapContext) -> str:
    facts, driest = ctx.facts, _driest_day(ctx)
    hit = {"n": facts.water_days_hit, "d": facts.week_days}
    if driest is None:
        return ctx.t("water_low.note.plain", **hit)
    return ctx.t("water_low.note", day=ctx.day(driest), **hit)


def _water_low_card(ctx: RecapContext) -> Card:
    facts, target = ctx.facts, ctx.stats.water_target
    return card(
        "water",
        "water_drop",
        ctx.t("water_low.title"),
        value=ctx.t("card.value.litres", v=ctx.dec(facts.water_avg)),
        unit=ctx.t("water_low.unit", v=ctx.dec(target)),
        note=_water_low_note(ctx),
        viz=bar(facts.water_avg, target),
        trend=trend(ctx, WATER),
    )


def _water_low_advice(ctx: RecapContext) -> Move:
    facts, target = ctx.facts, ctx.stats.water_target
    gap = min(ctx.config.water_impact_cap_l, target - facts.water_avg)
    return Move(
        "water",
        "water_drop",
        ctx.t("water_low.advice"),
        ctx.t("water_low.impact", v=ctx.dec(gap)),
        ctx.t("water_low.why", n=facts.water_days_hit, d=facts.week_days),
        ctx.t("water_low.short"),
    )


def _late_snack_card(ctx: RecapContext) -> Card:
    stats = ctx.stats
    nights = len(stats.late_snack_days)
    return card(
        "calm",
        "bedtime",
        ctx.t("late_snack.title"),
        count=nights,
        unit=ctx.t(
            "late_snack.unit",
            count=nights,
            hour=ctx.hour(ctx.config.late_snack_hour),
        ),
        note=ctx.t("late_snack.note", kcal=ctx.num(stats.late_snack_kcal)),
        viz=dots(stats.late_snack_days, "text"),
        trend=trend(ctx, LATE_SNACKS),
    )


def _late_snack_advice(ctx: RecapContext) -> Move:
    stats = ctx.stats
    nights = len(stats.late_snack_days)
    return Move(
        "calm",
        "bedtime",
        ctx.t("late_snack.advice", hour=ctx.hour(ctx.config.late_snack_hour)),
        ctx.t("late_snack.impact", kcal=ctx.num(stats.late_snack_kcal)),
        ctx.t("late_snack.why", count=nights, n=nights),
        ctx.t("late_snack.short"),
    )


def _drink(ctx: RecapContext) -> str | None:
    """The week's main sugary drink, named the way the locale names it.

    Catalog drinks are stored by their English catalog name, so they are
    translated here, which also covers retelling a stored week in another
    language, and lower-cased where the language writes common nouns so
    mid-sentence. Custom names are kept as the user wrote them.
    """
    raw = ctx.stats.top_sugary_drink
    if not raw or catalog_drink_id(raw) is None:
        return raw
    name = localized_name_for_catalog_name(raw, ctx.locale) or raw
    if ctx.locale in _LOWER_NOUNS and not any(char.isupper() for char in name[1:]):
        name = name[:1].lower() + name[1:]
    return name


def _sentence(text: str) -> str:
    """Capitalize a line that starts with a lower-cased drink name."""
    return text[:1].upper() + text[1:]


def _sugary_card(ctx: RecapContext) -> Card:
    stats = ctx.stats
    drink, kcal = _drink(ctx), ctx.num(stats.sugary_kcal)
    if drink:
        note = _sentence(ctx.t("sugary.note.named", drink=drink, kcal=kcal))
    else:
        note = ctx.t("sugary.note", kcal=kcal)
    return card(
        "fat",
        "local_cafe",
        ctx.t("sugary.title"),
        count=stats.sugary_drinks,
        unit=ctx.t("sugary.unit", count=stats.sugary_drinks),
        note=note,
        trend=trend(ctx, SUGARY_DRINKS),
    )


def _sugary_advice(ctx: RecapContext) -> Move:
    stats = ctx.stats
    drinks = stats.sugary_drinks
    drink = _drink(ctx)
    if drink:
        title = _sentence(ctx.t("sugary.advice.named", drink=drink))
    else:
        title = ctx.t("sugary.advice")
    return Move(
        "fat",
        "local_cafe",
        title,
        ctx.t("sugary.impact", kcal=ctx.num(stats.sugary_kcal / max(drinks, 1))),
        ctx.t("sugary.why", count=drinks, n=drinks),
        ctx.t("sugary.short"),
    )


FOOD_ISSUES: tuple[Issue, ...] = (
    Issue("fiber_low", 90, _fiber_low, _fiber_low_card, _fiber_low_advice),
    Issue("water_low", 80, _water_low, _water_low_card, _water_low_advice),
    Issue(
        "late_snack",
        85,
        lambda ctx: len(ctx.stats.late_snack_days) >= ctx.config.late_snack_days,
        _late_snack_card,
        _late_snack_advice,
    ),
    Issue(
        "sugary",
        75,
        lambda ctx: ctx.stats.sugary_drinks >= ctx.config.sugary_drinks,
        _sugary_card,
        _sugary_advice,
    ),
)
