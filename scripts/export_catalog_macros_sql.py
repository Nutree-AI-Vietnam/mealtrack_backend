"""Generate a single, fast SQL script to update catalog macros across environments.

This script reads estimated macros from `scripts/data/imported-excel-recipes.json`
and exports `scripts/data/update_catalog_macros.sql`.
The exported SQL uses `jsonb_set` and a `VALUES (...)` table join to update
all 720 catalog recipes in a single atomic transaction in milliseconds.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def generate_sql(manifest_path: Path, output_sql_path: Path) -> int:
    with open(manifest_path, encoding="utf-8") as f:
        data = json.load(f)

    recipes = data.get("recipes", [])
    valid_entries: list[tuple[str, str]] = []

    for r in recipes:
        catalog_key = r.get("recipe_key")
        nutr = r.get("nutrition")
        if not catalog_key or not nutr:
            continue
        nutr_json = json.dumps(nutr, ensure_ascii=False)
        # Escape single quotes for SQL literal
        escaped_key = catalog_key.replace("'", "''")
        escaped_nutr = nutr_json.replace("'", "''")
        valid_entries.append((escaped_key, escaped_nutr))

    if not valid_entries:
        print("No valid recipe nutrition found in manifest.")
        return 0

    values_clause = ",\n    ".join(
        f"('{key}', '{nutr}')" for key, nutr in valid_entries
    )

    sql_content = f"""-- Update nutrition macros for {len(valid_entries)} catalog recipes
-- Generated from {manifest_path.name}
BEGIN;

UPDATE meal_catalog AS m
SET recipe_payload = jsonb_set(
    COALESCE(m.recipe_payload, '{{}}'::jsonb),
    '{{nutrition}}',
    v.nutrition::jsonb
)
FROM (
    VALUES
    {values_clause}
) AS v(catalog_key, nutrition)
WHERE m.catalog_key = v.catalog_key;

COMMIT;
"""

    with open(output_sql_path, "w", encoding="utf-8") as f:
        f.write(sql_content)

    print(f"Generated {output_sql_path} with {len(valid_entries)} recipe nutrition updates.")
    return len(valid_entries)


def main() -> None:
    parser = argparse.ArgumentParser(description="Export catalog macros SQL script.")
    parser.add_argument(
        "--manifest",
        default=str(Path(__file__).resolve().parent / "data" / "imported-excel-recipes.json"),
        help="Path to manifest JSON.",
    )
    parser.add_argument(
        "--output",
        default=str(Path(__file__).resolve().parent / "data" / "update_catalog_macros.sql"),
        help="Path to output SQL file.",
    )
    args = parser.parse_args()
    generate_sql(Path(args.manifest), Path(args.output))


if __name__ == "__main__":
    main()
