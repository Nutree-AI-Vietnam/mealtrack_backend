"""PostgreSQL trigger definitions installed by the generated migration.

Statement triggers lock before any source row lock, so direct SQL writers follow
the same publication ordering as application publishers. Row triggers update
only relevant facets. Incomplete query projections force authoritative fallback.
"""

SOURCE_TABLES = (
    "meal_catalog",
    "meal_catalog_ingredients",
    "meal_catalog_steps",
    "meal_catalog_allergens",
    "allergen_reference",
    "food_reference",
    "food_reference_serving_sizes",
    "food_reference_nutrients",
)


def publication_trigger_upgrade_sql() -> list[str]:
    statements = [
        """CREATE FUNCTION lock_catalog_publication() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            PERFORM id FROM catalog_publication_version WHERE id = 1 FOR UPDATE;
            IF NOT FOUND THEN RAISE EXCEPTION 'Catalog publication fence missing'; END IF;
            RETURN NULL;
        END $$""",
        """CREATE FUNCTION invalidate_truncated_catalog_projection() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            UPDATE catalog_publication_version SET selection = selection + 1,
                ingredients = ingredients + 1, translation = translation + 1,
                enrichment = enrichment + 1, updated_at = clock_timestamp() WHERE id = 1;
            UPDATE meal_catalog_projection SET query_dirty = true, nutrition_dirty = true,
                translation_dirty = true, enrichment_dirty = true, updated_at = clock_timestamp();
            RETURN NULL;
        END $$""",
        """CREATE FUNCTION invalidate_catalog_projection() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE
            old_value jsonb := CASE WHEN TG_OP = 'INSERT' THEN '{}'::jsonb ELSE to_jsonb(OLD) END;
            new_value jsonb := CASE WHEN TG_OP = 'DELETE' THEN '{}'::jsonb ELSE to_jsonb(NEW) END;
            selection_changed boolean := false;
            ingredients_changed boolean := false;
            translation_changed boolean := false;
            enrichment_changed boolean := false;
            query_changed boolean := false;
            recipe_ids text[];
            food_ids integer[];
            selection_keys text[] := ARRAY['name','source','is_verified','protein_100g','carbs_100g',
                'fat_100g','fiber_100g','sugar_100g','density','serving_sizes','serving_size'];
        BEGIN
            IF old_value = new_value THEN RETURN NULL; END IF;
            IF TG_TABLE_NAME = 'food_reference' THEN
                SELECT COALESCE(jsonb_object_agg(key, value), '{}'::jsonb) INTO old_value
                    FROM jsonb_each(old_value) WHERE key = ANY(selection_keys);
                SELECT COALESCE(jsonb_object_agg(key, value), '{}'::jsonb) INTO new_value
                    FROM jsonb_each(new_value) WHERE key = ANY(selection_keys);
                selection_changed := old_value IS DISTINCT FROM new_value;
                ingredients_changed := selection_changed;
                enrichment_changed := selection_changed OR TG_OP <> 'UPDATE' OR
                    (to_jsonb(OLD)->'extra_nutrients') IS DISTINCT FROM (to_jsonb(NEW)->'extra_nutrients') OR
                    (to_jsonb(OLD)->'fdc_id') IS DISTINCT FROM (to_jsonb(NEW)->'fdc_id');
                food_ids := ARRAY[CASE WHEN TG_OP = 'INSERT' THEN NULL ELSE OLD.id END,
                                  CASE WHEN TG_OP = 'DELETE' THEN NULL ELSE NEW.id END];
            ELSIF TG_TABLE_NAME IN ('food_reference_serving_sizes', 'food_reference_nutrients') THEN
                selection_changed := TG_TABLE_NAME = 'food_reference_serving_sizes';
                ingredients_changed := selection_changed;
                enrichment_changed := true;
                food_ids := ARRAY[CASE WHEN TG_OP = 'INSERT' THEN NULL ELSE OLD.food_reference_id END,
                                  CASE WHEN TG_OP = 'DELETE' THEN NULL ELSE NEW.food_reference_id END];
            ELSIF TG_TABLE_NAME = 'meal_catalog' THEN
                selection_changed := (old_value - ARRAY['created_at','updated_at','recipe_payload']) IS DISTINCT FROM
                    (new_value - ARRAY['created_at','updated_at','recipe_payload']) OR
                    ((old_value->'recipe_payload'->'nutrition') - ARRAY['micros','micros_sources']) IS DISTINCT FROM
                    ((new_value->'recipe_payload'->'nutrition') - ARRAY['micros','micros_sources']);
                query_changed := selection_changed;
                ingredients_changed := (old_value->'recipe_payload'->'ingredients') IS DISTINCT FROM
                                       (new_value->'recipe_payload'->'ingredients');
                translation_changed := (old_value - ARRAY['created_at','updated_at','recipe_payload']) IS DISTINCT FROM
                    (new_value - ARRAY['created_at','updated_at','recipe_payload']) OR
                    ((old_value->'recipe_payload') - 'nutrition') IS DISTINCT FROM ((new_value->'recipe_payload') - 'nutrition');
                enrichment_changed := selection_changed OR ingredients_changed OR
                    (old_value->'recipe_payload'->'nutrition') IS DISTINCT FROM (new_value->'recipe_payload'->'nutrition');
                recipe_ids := ARRAY[CASE WHEN TG_OP = 'INSERT' THEN NULL ELSE OLD.id END,
                                    CASE WHEN TG_OP = 'DELETE' THEN NULL ELSE NEW.id END];
            ELSIF TG_TABLE_NAME = 'allergen_reference' THEN
                selection_changed := true;
                query_changed := true;
                SELECT array_agg(catalog_meal_id) INTO recipe_ids FROM meal_catalog_allergens
                    WHERE allergen_id IN (old_value->>'id', new_value->>'id');
            ELSE
                selection_changed := TG_TABLE_NAME <> 'meal_catalog_steps';
                query_changed := selection_changed;
                ingredients_changed := TG_TABLE_NAME = 'meal_catalog_ingredients';
                translation_changed := TG_TABLE_NAME <> 'meal_catalog_allergens';
                enrichment_changed := ingredients_changed;
                recipe_ids := ARRAY[old_value->>'catalog_meal_id', new_value->>'catalog_meal_id'];
            END IF;
            IF food_ids IS NOT NULL THEN
                SELECT array_agg(catalog_meal_id) INTO recipe_ids FROM meal_catalog_ingredients
                    WHERE food_reference_id = ANY(food_ids);
            END IF;
            IF selection_changed OR ingredients_changed OR translation_changed OR enrichment_changed THEN
                UPDATE catalog_publication_version SET
                    selection = selection + selection_changed::int,
                    ingredients = ingredients + ingredients_changed::int,
                    translation = translation + translation_changed::int,
                    enrichment = enrichment + enrichment_changed::int, updated_at = clock_timestamp() WHERE id = 1;
            END IF;
            IF selection_changed OR query_changed OR translation_changed OR enrichment_changed THEN
                UPDATE meal_catalog_projection SET
                    query_dirty = query_dirty OR query_changed,
                    nutrition_dirty = nutrition_dirty OR selection_changed,
                    translation_dirty = translation_dirty OR translation_changed,
                    enrichment_dirty = enrichment_dirty OR enrichment_changed, updated_at = clock_timestamp()
                    WHERE catalog_meal_id = ANY(recipe_ids);
            END IF;
            RETURN NULL;
        END $$""",
    ]
    for table in SOURCE_TABLES:
        statements.extend(
            [
                f"CREATE TRIGGER catalog_publication_lock BEFORE INSERT OR UPDATE OR DELETE OR TRUNCATE ON {table} FOR EACH STATEMENT EXECUTE FUNCTION lock_catalog_publication()",
                f"CREATE TRIGGER catalog_projection_invalidation AFTER INSERT OR UPDATE OR DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION invalidate_catalog_projection()",
                f"CREATE TRIGGER catalog_projection_truncate AFTER TRUNCATE ON {table} FOR EACH STATEMENT EXECUTE FUNCTION invalidate_truncated_catalog_projection()",
            ]
        )
    return statements


def publication_trigger_downgrade_sql() -> list[str]:
    statements = []
    for table in SOURCE_TABLES:
        statements.extend(
            [
                f"DROP TRIGGER IF EXISTS catalog_projection_truncate ON {table}",
                f"DROP TRIGGER IF EXISTS catalog_projection_invalidation ON {table}",
                f"DROP TRIGGER IF EXISTS catalog_publication_lock ON {table}",
            ]
        )
    return [
        *statements,
        "DROP FUNCTION IF EXISTS invalidate_catalog_projection()",
        "DROP FUNCTION IF EXISTS lock_catalog_publication()",
        "DROP FUNCTION IF EXISTS invalidate_truncated_catalog_projection()",
    ]


def preparation_trigger_upgrade_sql() -> list[str]:
    """Install durable producers only after the job table exists.

    Projection reconciliation is keyed by the committed publication epoch. It
    rebuilds the latest canonical inputs and inserts exact facet/provider jobs.
    Direct SQL writers and API process crashes cannot lose that committed work.
    """
    function = next(
        sql
        for sql in publication_trigger_upgrade_sql()
        if sql.startswith("CREATE FUNCTION invalidate_catalog_projection()")
    )
    insertion = """
            IF selection_changed OR ingredients_changed OR translation_changed OR enrichment_changed THEN
                INSERT INTO catalog_preparation_jobs
                    (id, task, catalog_meal_id, input_facet_version, locale, contract_version)
                SELECT gen_random_uuid()::text, 'projection', source.id,
                    repeat(md5(concat_ws(':', version.selection, version.ingredients,
                        version.translation, version.enrichment)), 2), '', 'v1'
                FROM meal_catalog source CROSS JOIN catalog_publication_version version
                WHERE version.id = 1 AND source.is_active = true AND source.id = ANY(recipe_ids)
                ON CONFLICT (task, catalog_meal_id, input_facet_version, locale, contract_version) DO NOTHING;
            END IF;
"""
    marker = "            RETURN NULL;\n        END $$"
    function = function.replace(marker, insertion + marker)
    truncate = next(
        sql
        for sql in publication_trigger_upgrade_sql()
        if sql.startswith("CREATE FUNCTION invalidate_truncated_catalog_projection()")
    )
    truncate_insertion = insertion.replace(
        "selection_changed OR ingredients_changed OR translation_changed OR enrichment_changed",
        "true",
    ).replace(" AND source.id = ANY(recipe_ids)", "")
    truncate = truncate.replace(marker, truncate_insertion + marker)
    return [
        sql.replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION", 1)
        for sql in (function, truncate)
    ]


def preparation_trigger_downgrade_sql() -> list[str]:
    return [
        sql.replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION", 1)
        for sql in publication_trigger_upgrade_sql()
        if sql.startswith(
            (
                "CREATE FUNCTION invalidate_catalog_projection()",
                "CREATE FUNCTION invalidate_truncated_catalog_projection()",
            )
        )
    ]
