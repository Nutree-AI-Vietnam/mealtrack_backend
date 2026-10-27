"""Vacation ORM <-> domain mapping."""

from src.domain.model.vacation.vacation import Vacation
from src.infra.database.models.vacation.vacation import VacationORM


def vacation_orm_to_domain(orm: VacationORM) -> Vacation:
    return Vacation(
        vacation_id=orm.id,
        user_id=orm.user_id,
        start_date=orm.start_date,
        end_date=orm.end_date,
        ended_on=orm.ended_on,
        frozen_calories=float(orm.frozen_calories),
        frozen_protein=float(orm.frozen_protein),
        frozen_carbs=float(orm.frozen_carbs),
        frozen_fat=float(orm.frozen_fat),
        created_at=orm.created_at,
    )


def vacation_domain_to_orm(domain: Vacation) -> VacationORM:
    return VacationORM(
        id=domain.vacation_id,
        user_id=domain.user_id,
        start_date=domain.start_date,
        end_date=domain.end_date,
        ended_on=domain.ended_on,
        frozen_calories=domain.frozen_calories,
        frozen_protein=domain.frozen_protein,
        frozen_carbs=domain.frozen_carbs,
        frozen_fat=domain.frozen_fat,
        created_at=domain.created_at,
    )
