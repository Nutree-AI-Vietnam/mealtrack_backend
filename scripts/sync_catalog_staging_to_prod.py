"""Synchronize meal catalog tables from Staging Neon DB to Production Neon DB safely and efficiently.

Uses batched operations to complete in seconds over WAN while strictly preserving
existing Production UUIDs for overlapping catalog keys.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger(__name__)

DEFAULT_STAGING_URL = "postgresql+asyncpg://neondb_owner:npg_J0rl7UGaMzNB@ep-round-base-ani0tihw.c-6.us-east-1.aws.neon.tech/neondb?ssl=require"
DEFAULT_PROD_URL = "postgresql+asyncpg://neondb_owner:npg_Snwt8Af9mWrg@ep-silent-bar-aklthiqy.c-3.us-west-2.aws.neon.tech/neondb?ssl=require"


async def run_sync(
    staging_url: str,
    prod_url: str,
    *,
    dry_run: bool = True,
) -> None:
    logger.info("Connecting to Staging and Production databases...")
    staging_engine = create_async_engine(staging_url, pool_pre_ping=True)
    prod_engine = create_async_engine(prod_url, pool_pre_ping=True)

    s_session_factory = async_sessionmaker(staging_engine, expire_on_commit=False)
    p_session_factory = async_sessionmaker(prod_engine, expire_on_commit=False)

    async with s_session_factory() as s_session, p_session_factory() as p_session:
        logger.info("Reading source data from Staging...")

        # 1. Allergen Reference mapping (code -> id)
        s_allergens = (
            await s_session.execute(text("SELECT id, code FROM allergen_reference"))
        ).all()
        p_allergens = (
            await p_session.execute(text("SELECT id, code FROM allergen_reference"))
        ).all()
        s_allergen_id_to_code = {row[0]: row[1] for row in s_allergens}
        p_code_to_allergen_id = {row[1]: row[0] for row in p_allergens}

        # 2. Food Reference IDs in Prod to prevent FK violations
        prod_food_ref_ids = set(
            (await p_session.execute(text("SELECT id FROM food_reference")))
            .scalars()
            .all()
        )
        logger.info("Prod contains %d food references.", len(prod_food_ref_ids))

        # 3. Read Staging Catalog Meals
        staging_meals_res = await s_session.execute(
            text("""
                SELECT 
                    id, catalog_key, content_hash, name, cuisine, description, image_url,
                    source_name, source_url, prep_time_minutes, cook_time_minutes, tag,
                    allergens, summary, equipment, base_servings, serving_source, serving_confidence,
                    recipe_payload, payload_schema_version, payload_digest, publication_status,
                    nutrition_status, popularity_rank, breakfast_eligible, lunch_eligible,
                    dinner_eligible, snack_eligible, is_active
                FROM meal_catalog
                ORDER BY catalog_key
            """)
        )
        staging_meals = staging_meals_res.mappings().all()
        logger.info("Read %d catalog meals from Staging.", len(staging_meals))

        # 4. Read Staging child tables grouped by staging catalog_meal_id
        staging_ingredients_res = await s_session.execute(
            text("""
                SELECT id, catalog_meal_id, position, food_reference_id, display_name,
                       quantity, unit, category, quantity_text, raw_text, is_optional, notes
                FROM meal_catalog_ingredients
                ORDER BY catalog_meal_id, position
            """)
        )
        ingredients_by_meal: dict[str, list[dict[str, Any]]] = {}
        for row in staging_ingredients_res.mappings().all():
            m_id = str(row["catalog_meal_id"])
            ingredients_by_meal.setdefault(m_id, []).append(dict(row))

        staging_steps_res = await s_session.execute(
            text("""
                SELECT id, catalog_meal_id, step_number, title, description
                FROM meal_catalog_steps
                ORDER BY catalog_meal_id, step_number
            """)
        )
        steps_by_meal: dict[str, list[dict[str, Any]]] = {}
        for row in staging_steps_res.mappings().all():
            m_id = str(row["catalog_meal_id"])
            steps_by_meal.setdefault(m_id, []).append(dict(row))

        staging_allergens_res = await s_session.execute(
            text("""
                SELECT catalog_meal_id, allergen_id, source, confidence
                FROM meal_catalog_allergens
            """)
        )
        allergens_by_meal: dict[str, list[dict[str, Any]]] = {}
        for row in staging_allergens_res.mappings().all():
            m_id = str(row["catalog_meal_id"])
            allergens_by_meal.setdefault(m_id, []).append(dict(row))

        staging_micros_res = await s_session.execute(
            text("""
                SELECT catalog_meal_id, content_hash, micros, sources, status
                FROM meal_catalog_micronutrient_enrichment
            """)
        )
        micros_by_meal: dict[str, dict[str, Any]] = {}
        for row in staging_micros_res.mappings().all():
            m_id = str(row["catalog_meal_id"])
            micros_by_meal[m_id] = dict(row)

        # 5. Check Existing Prod Catalog Keys
        prod_existing_res = await p_session.execute(
            text("SELECT id, catalog_key FROM meal_catalog")
        )
        prod_existing_map = {row[1]: row[0] for row in prod_existing_res.all()}
        logger.info("Prod contains %d existing catalog meals.", len(prod_existing_map))

        to_insert = []
        to_update = []
        for meal in staging_meals:
            key = meal["catalog_key"]
            if key in prod_existing_map:
                to_update.append((prod_existing_map[key], meal))
            else:
                to_insert.append(meal)

        logger.info(
            "Sync Plan: %d to INSERT (new), %d to UPDATE (existing, preserving Prod UUID), %s mode.",
            len(to_insert),
            len(to_update),
            "DRY-RUN" if dry_run else "LIVE APPLY",
        )

        if dry_run:
            logger.info("Dry-run complete! Zero changes applied to Production.")
            return

        # LIVE EXECUTION inside a single transaction
        logger.info("Executing Production database transaction...")
        try:
            # Prepare Batches
            new_meals_params = []
            update_meals_params = []
            update_prod_ids = [p_id for p_id, _ in to_update]

            all_ingredients_params = []
            all_steps_params = []
            all_allergens_params = []
            all_micros_params = []

            # 1. Process Updates
            for prod_id, s_meal in to_update:
                s_id = str(s_meal["id"])
                update_meals_params.append(
                    {
                        "id": prod_id,
                        "content_hash": s_meal["content_hash"],
                        "name": s_meal["name"],
                        "cuisine": s_meal["cuisine"],
                        "description": s_meal["description"],
                        "image_url": s_meal["image_url"],
                        "source_name": s_meal["source_name"],
                        "source_url": s_meal["source_url"],
                        "prep_time_minutes": s_meal["prep_time_minutes"],
                        "cook_time_minutes": s_meal["cook_time_minutes"],
                        "tag": s_meal["tag"],
                        "allergens": s_meal["allergens"],
                        "summary": s_meal["summary"],
                        "equipment": s_meal["equipment"],
                        "base_servings": s_meal["base_servings"],
                        "serving_source": s_meal["serving_source"],
                        "serving_confidence": s_meal["serving_confidence"],
                        "recipe_payload": json.dumps(s_meal["recipe_payload"] or {}),
                        "payload_schema_version": s_meal["payload_schema_version"],
                        "payload_digest": s_meal["payload_digest"],
                        "publication_status": s_meal["publication_status"],
                        "nutrition_status": s_meal["nutrition_status"],
                        "popularity_rank": s_meal["popularity_rank"],
                        "breakfast_eligible": s_meal["breakfast_eligible"],
                        "lunch_eligible": s_meal["lunch_eligible"],
                        "dinner_eligible": s_meal["dinner_eligible"],
                        "snack_eligible": s_meal["snack_eligible"],
                        "is_active": s_meal["is_active"],
                    }
                )

                for ing in ingredients_by_meal.get(s_id, []):
                    f_id = ing["food_reference_id"]
                    if f_id is not None and f_id not in prod_food_ref_ids:
                        f_id = None
                    all_ingredients_params.append(
                        {
                            "id": str(uuid.uuid4()),
                            "mid": prod_id,
                            "position": ing["position"],
                            "fid": f_id,
                            "display_name": ing["display_name"],
                            "quantity": ing["quantity"],
                            "unit": ing["unit"],
                            "category": ing["category"],
                            "quantity_text": ing["quantity_text"],
                            "raw_text": ing["raw_text"],
                            "is_optional": ing["is_optional"],
                            "notes": ing["notes"],
                        }
                    )

                for st in steps_by_meal.get(s_id, []):
                    all_steps_params.append(
                        {
                            "id": str(uuid.uuid4()),
                            "mid": prod_id,
                            "step_number": st["step_number"],
                            "title": st["title"],
                            "description": st["description"],
                        }
                    )

                for al in allergens_by_meal.get(s_id, []):
                    code = s_allergen_id_to_code.get(al["allergen_id"])
                    p_aid = p_code_to_allergen_id.get(code) if code else None
                    if p_aid:
                        all_allergens_params.append(
                            {
                                "mid": prod_id,
                                "aid": p_aid,
                                "source": al["source"],
                                "confidence": al["confidence"],
                            }
                        )

                if s_id in micros_by_meal:
                    mc = micros_by_meal[s_id]
                    all_micros_params.append(
                        {
                            "id": str(uuid.uuid4()),
                            "mid": prod_id,
                            "content_hash": mc["content_hash"],
                            "micros": json.dumps(mc["micros"] or {}),
                            "sources": json.dumps(mc["sources"] or {}),
                            "status": mc["status"],
                        }
                    )

            # 2. Process Inserts
            for s_meal in to_insert:
                new_id = str(uuid.uuid4())
                s_id = str(s_meal["id"])

                new_meals_params.append(
                    {
                        "id": new_id,
                        "catalog_key": s_meal["catalog_key"],
                        "content_hash": s_meal["content_hash"],
                        "name": s_meal["name"],
                        "cuisine": s_meal["cuisine"],
                        "description": s_meal["description"],
                        "image_url": s_meal["image_url"],
                        "source_name": s_meal["source_name"],
                        "source_url": s_meal["source_url"],
                        "prep_time_minutes": s_meal["prep_time_minutes"],
                        "cook_time_minutes": s_meal["cook_time_minutes"],
                        "tag": s_meal["tag"],
                        "allergens": s_meal["allergens"],
                        "summary": s_meal["summary"],
                        "equipment": s_meal["equipment"],
                        "base_servings": s_meal["base_servings"],
                        "serving_source": s_meal["serving_source"],
                        "serving_confidence": s_meal["serving_confidence"],
                        "recipe_payload": json.dumps(s_meal["recipe_payload"] or {}),
                        "payload_schema_version": s_meal["payload_schema_version"],
                        "payload_digest": s_meal["payload_digest"],
                        "publication_status": s_meal["publication_status"],
                        "nutrition_status": s_meal["nutrition_status"],
                        "popularity_rank": s_meal["popularity_rank"],
                        "breakfast_eligible": s_meal["breakfast_eligible"],
                        "lunch_eligible": s_meal["lunch_eligible"],
                        "dinner_eligible": s_meal["dinner_eligible"],
                        "snack_eligible": s_meal["snack_eligible"],
                        "is_active": s_meal["is_active"],
                    }
                )

                for ing in ingredients_by_meal.get(s_id, []):
                    f_id = ing["food_reference_id"]
                    if f_id is not None and f_id not in prod_food_ref_ids:
                        f_id = None
                    all_ingredients_params.append(
                        {
                            "id": str(uuid.uuid4()),
                            "mid": new_id,
                            "position": ing["position"],
                            "fid": f_id,
                            "display_name": ing["display_name"],
                            "quantity": ing["quantity"],
                            "unit": ing["unit"],
                            "category": ing["category"],
                            "quantity_text": ing["quantity_text"],
                            "raw_text": ing["raw_text"],
                            "is_optional": ing["is_optional"],
                            "notes": ing["notes"],
                        }
                    )

                for st in steps_by_meal.get(s_id, []):
                    all_steps_params.append(
                        {
                            "id": str(uuid.uuid4()),
                            "mid": new_id,
                            "step_number": st["step_number"],
                            "title": st["title"],
                            "description": st["description"],
                        }
                    )

                for al in allergens_by_meal.get(s_id, []):
                    code = s_allergen_id_to_code.get(al["allergen_id"])
                    p_aid = p_code_to_allergen_id.get(code) if code else None
                    if p_aid:
                        all_allergens_params.append(
                            {
                                "mid": new_id,
                                "aid": p_aid,
                                "source": al["source"],
                                "confidence": al["confidence"],
                            }
                        )

                if s_id in micros_by_meal:
                    mc = micros_by_meal[s_id]
                    all_micros_params.append(
                        {
                            "id": str(uuid.uuid4()),
                            "mid": new_id,
                            "content_hash": mc["content_hash"],
                            "micros": json.dumps(mc["micros"] or {}),
                            "sources": json.dumps(mc["sources"] or {}),
                            "status": mc["status"],
                        }
                    )

            # Execute Bulk Operations
            if update_prod_ids:
                logger.info(
                    "Clearing old child records for %d updated meals in Prod...",
                    len(update_prod_ids),
                )
                await p_session.execute(
                    text(
                        "DELETE FROM meal_catalog_ingredients WHERE catalog_meal_id = ANY(:ids)"
                    ),
                    {"ids": update_prod_ids},
                )
                await p_session.execute(
                    text(
                        "DELETE FROM meal_catalog_steps WHERE catalog_meal_id = ANY(:ids)"
                    ),
                    {"ids": update_prod_ids},
                )
                await p_session.execute(
                    text(
                        "DELETE FROM meal_catalog_allergens WHERE catalog_meal_id = ANY(:ids)"
                    ),
                    {"ids": update_prod_ids},
                )

                logger.info(
                    "Applying bulk updates to %d meal_catalog records...",
                    len(update_meals_params),
                )
                await p_session.execute(
                    text("""
                        UPDATE meal_catalog SET
                            content_hash = :content_hash,
                            name = :name,
                            cuisine = :cuisine,
                            description = :description,
                            image_url = :image_url,
                            source_name = :source_name,
                            source_url = :source_url,
                            prep_time_minutes = :prep_time_minutes,
                            cook_time_minutes = :cook_time_minutes,
                            tag = :tag,
                            allergens = :allergens,
                            summary = :summary,
                            equipment = :equipment,
                            base_servings = :base_servings,
                            serving_source = :serving_source,
                            serving_confidence = :serving_confidence,
                            recipe_payload = CAST(:recipe_payload AS jsonb),
                            payload_schema_version = :payload_schema_version,
                            payload_digest = :payload_digest,
                            publication_status = :publication_status,
                            nutrition_status = :nutrition_status,
                            popularity_rank = :popularity_rank,
                            breakfast_eligible = :breakfast_eligible,
                            lunch_eligible = :lunch_eligible,
                            dinner_eligible = :dinner_eligible,
                            snack_eligible = :snack_eligible,
                            is_active = :is_active,
                            updated_at = NOW()
                        WHERE id = :id
                    """),
                    update_meals_params,
                )

            if new_meals_params:
                logger.info(
                    "Inserting %d new meal_catalog records in Prod...",
                    len(new_meals_params),
                )
                await p_session.execute(
                    text("""
                        INSERT INTO meal_catalog (
                            id, catalog_key, content_hash, name, cuisine, description, image_url,
                            source_name, source_url, prep_time_minutes, cook_time_minutes, tag,
                            allergens, summary, equipment, base_servings, serving_source, serving_confidence,
                            recipe_payload, payload_schema_version, payload_digest, publication_status,
                            nutrition_status, popularity_rank, breakfast_eligible, lunch_eligible,
                            dinner_eligible, snack_eligible, is_active
                        ) VALUES (
                            :id, :catalog_key, :content_hash, :name, :cuisine, :description, :image_url,
                            :source_name, :source_url, :prep_time_minutes, :cook_time_minutes, :tag,
                            :allergens, :summary, :equipment, :base_servings, :serving_source, :serving_confidence,
                            CAST(:recipe_payload AS jsonb), :payload_schema_version, :payload_digest, :publication_status,
                            :nutrition_status, :popularity_rank, :breakfast_eligible, :lunch_eligible,
                            :dinner_eligible, :snack_eligible, :is_active
                        )
                    """),
                    new_meals_params,
                )

            if all_ingredients_params:
                logger.info(
                    "Inserting %d ingredients in Prod...", len(all_ingredients_params)
                )
                await p_session.execute(
                    text("""
                        INSERT INTO meal_catalog_ingredients
                            (id, catalog_meal_id, position, food_reference_id, display_name,
                             quantity, unit, category, quantity_text, raw_text, is_optional, notes)
                        VALUES
                            (:id, :mid, :position, :fid, :display_name,
                             :quantity, :unit, :category, :quantity_text, :raw_text, :is_optional, :notes)
                    """),
                    all_ingredients_params,
                )

            if all_steps_params:
                logger.info(
                    "Inserting %d cooking steps in Prod...", len(all_steps_params)
                )
                await p_session.execute(
                    text("""
                        INSERT INTO meal_catalog_steps
                            (id, catalog_meal_id, step_number, title, description)
                        VALUES
                            (:id, :mid, :step_number, :title, :description)
                    """),
                    all_steps_params,
                )

            if all_allergens_params:
                logger.info(
                    "Inserting %d allergen links in Prod...", len(all_allergens_params)
                )
                await p_session.execute(
                    text("""
                        INSERT INTO meal_catalog_allergens
                            (catalog_meal_id, allergen_id, source, confidence)
                        VALUES
                            (:mid, :aid, :source, :confidence)
                        ON CONFLICT DO NOTHING
                    """),
                    all_allergens_params,
                )

            if all_micros_params:
                logger.info(
                    "Upserting %d micronutrient enrichment records in Prod...",
                    len(all_micros_params),
                )
                await p_session.execute(
                    text("""
                        INSERT INTO meal_catalog_micronutrient_enrichment
                            (id, catalog_meal_id, content_hash, micros, sources, status)
                        VALUES
                            (:id, :mid, :content_hash, CAST(:micros AS jsonb), CAST(:sources AS jsonb), :status)
                        ON CONFLICT (catalog_meal_id, content_hash) DO UPDATE SET
                            micros = EXCLUDED.micros,
                            sources = EXCLUDED.sources,
                            status = EXCLUDED.status,
                            updated_at = NOW()
                    """),
                    all_micros_params,
                )

            await p_session.commit()
            logger.info("Transaction committed successfully to Production!")
        except Exception:
            await p_session.rollback()
            logger.error("Sync failed, rolled back all changes!", exc_info=True)
            raise

    await staging_engine.dispose()
    await prod_engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Sync meal catalog from Staging to Production Neon DB"
    )
    parser.add_argument(
        "--staging-url", default=DEFAULT_STAGING_URL, help="Staging DB URL"
    )
    parser.add_argument(
        "--prod-url", default=DEFAULT_PROD_URL, help="Production DB URL"
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply changes to Production (defaults to dry-run)",
    )
    args = parser.parse_args()

    asyncio.run(
        run_sync(
            staging_url=args.staging_url,
            prod_url=args.prod_url,
            dry_run=not args.apply,
        )
    )


if __name__ == "__main__":
    main()
