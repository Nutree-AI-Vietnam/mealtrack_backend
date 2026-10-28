# Catalog recipe seeds

Reviewed recipe manifests that are safe to commit. `scripts/data/*.json` stays
ignored for fetched or licensed corpora.

| File | Cuisine | Recipes | For |
|------|---------|---------|-----|
| `us-everyday-v1.json` | american | 49 (15 breakfast, 16 lunch, 26 dinner-eligible) | US users of the weekly planner |

Each US recipe is one adult serving with ingredient amounts in grams, so the
importer derives nutrition from food references. Popularity ranks start at 5001
so these recipes sort after the existing catalog in browse feeds that are not
region-aware yet.

## Import

Review the ingredient matches first, then apply:

```bash
python scripts/import_catalog_recipe_seeds.py --manifest data/catalog_seeds/us-everyday-v1.json --partial --dry-run --resolver-report plans/reports/us-everyday-v1-resolver-report.json
```

```bash
python scripts/import_catalog_recipe_seeds.py --manifest data/catalog_seeds/us-everyday-v1.json --partial --skip-existing --resolver-report plans/reports/us-everyday-v1-import-report.json
```

The planner leads US plans (`preferences.region = "US"`) with `american`
recipes, then familiar cuisines, then everything else. See
`src/domain/model/weekly_meal_planner/regional_meal_profile.py`.
