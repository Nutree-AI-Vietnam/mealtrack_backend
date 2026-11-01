# Supplement Catalog Admin

**Status:** Content-ops satellite. Load it when importing the supplement catalog or reviewing scans.
It is **not** a default agent Tier-3 authority.

The shared supplement catalog has two sources:

- Curated products, built from public label data.
- People's label scans, once an admin approves them.

Both sit in `supplement_products` with no owner. Search and barcode lookups show a shared row only when it is `approved`.

## Seed files

| File | Contents |
| --- | --- |
| `scripts/data/supplement_catalog_vn.json.gz` | Products ready to share. The import approves them. |
| `scripts/data/supplement_catalog_review.json.gz` | Held-back products, each with its `issues`. The import puts them in the review queue. |

Each record is one product. Every record has these fields:

- `key`: stable. It is the import's identity.
- `brand`, `name` and `name_original`.
- `form`: one of `capsule`, `softgel`, `tablet`, `chewable`, `gummy`, `effervescent`, `powder`, `liquid`, `drops` or `sachet`.
- `unit_label`: the unit a dose counts in, for example "viên nang".
- `units_per_serving`.
- `barcodes`: any form of GTIN. The import stores each one as GTIN-14.
- `nutrients`: per unit, in the app's units. Use the 19 tracked keys, plus `vitamin_a_preformed` beside them.
- `label_rows`: as printed, `{name, name_original, amount, unit, equivalent}`.
- `origin_country`, `is_medicine_in_vn` and `sold_in_vn`. Both flags are required; a missing one would quietly rank the product as foreign, so the import refuses the record.
- `source_name` and `source_url`.
- `macro_bearing` (optional): true marks a product the app logs as food, never as a dose.

The build leaves food out of both files. It uses phase 4's label-scan rule:
- A powder, liquid or sachet is food at 10 g of protein or 100 kcal a serving.
- Any product is food at 10 g of protein or 50 kcal a unit.

The build also keeps each barcode on one record: one sold in Vietnam first, then one from a brand or registry page, then the newest DSLD label.

A record with a non-empty `issues` list is held back for review. Its `issues` are shown in the queue as the reasons.

## Import

```bash
python scripts/import_supplement_seeds.py scripts/data/supplement_catalog_vn.json.gz
python scripts/import_supplement_seeds.py scripts/data/supplement_catalog_review.json.gz
python scripts/import_supplement_seeds.py <file> --dry-run   # counts only, writes nothing
```

The script prints a JSON summary with these counts: `inserted`, `updated`, `unchanged`, `kept_reviewed`, `kept_approved`, `invalid` and `barcode_skips`. Add `--all` to list every invalid record and every skipped barcode instead of the first 20.

What the import does:

- **Upserts by `key`.** A second run of the same file changes nothing. When a product changes, its row keeps the same id.
- **Leaves reviewed rows alone.** Once an admin has edited, approved or rejected a row, re-imports skip it and count it as `kept_reviewed`.
- **Never takes an approved row back out.** When a rebuild holds back a product that is already approved, the import leaves the row as it is and counts it as `kept_approved`. People may already list that product and log doses of it.
- **Checks every record.** It refuses a record and reports the reason when any of these is true:
  - `unit_label` is missing.
  - The `form` is unknown.
  - A nutrient key is not tracked.
  - An amount is negative.
  - A key is repeated.
- **Reshapes the label rows:**
  - It drops amounts of 0, both in `nutrients` and in the rows, so "Sodium 0 mg" does not make the product look like a source of sodium.
  - A printed equivalent becomes part of the row name, for example "Calcium carbonate (= calcium 600 mg)".
  - Vietnamese labels keep their printed row names.
  - A printed range such as "0~0.031 g" stays as text in the row name and never counts.
- **Gives each barcode to one shared product.** When a barcode is already on another shared product, or an earlier record in the same file claimed it, the import skips that barcode and reports why. A barcode that fails its check digit is reported the same way.

`POST /v1/admin/supplements/import` with `{records: [...], dry_run}` does the same for up to 500 records.

Run the catalog file before the review file. The build never puts the same key in both files.

## Review

The queue holds held-back catalog rows and people's readable label scans. It never holds Ghi nhanh estimates.

- `GET /v1/admin/supplements/review?kind=catalog|scan&limit&offset` returns the queue, oldest first. Each item shows:
  - its `reasons`;
  - its `source_url`, or for a scan its `label_image_url`;
  - the label rows and the nutrients read from them;
  - for a scan, `barcode_match`: the shared row its barcode already belongs to. When it is approved, approving merges into it. When it is waiting, decide that row first.
- `POST /v1/admin/supplements/review/{id}` takes `{action: "approve" | "edit" | "reject", changes?}`. The `changes` are applied first.
  - Fields you can edit: `brand`, `name`, `form`, `unit_label`, `units_per_serving`, `nutrients`, `label_rows`, `sold_in_vn`, `is_medicine_in_vn` and `macro_bearing`. `macro_bearing` is for catalog rows only: a person marked as food could no longer log their own product, so reject such a scan instead.
  - Only `brand` can be cleared. An `edit` that changes nothing answers 422.

What each action does:

| Action | Held-back catalog row | Person's scan |
| --- | --- | --- |
| `edit` | Saves the changes. The row stays in the queue. | Same as for a catalog row. |
| `approve` | Shares the row. | Promotes the person's row to shared, in place, with its barcodes. Their list row and doses keep the same product. |
| `approve`, when an approved shared row already has the barcode | Cannot happen: a shared barcode belongs to one row. | Moves the person's list row and doses to that shared row (`merged_into`). The scan stays theirs. Answers 409 when that row logs as food. |
| `approve`, when a catalog row still waiting has the barcode | Cannot happen. | Answers 409 naming that row. |
| `reject` | Hides the row and frees its barcodes. | The scan stays private to its owner. |

When a catalog row still waiting holds a scan's barcode, decide that row first. Approving it turns the scan's approval into a merge. Rejecting it frees the barcode. This keeps one shared row per barcode.

Every action records `reviewed_by` and `reviewed_at`. These cases also answer 409:
- A product that is not waiting for review. Rejecting is final.
- A Ghi nhanh estimate.
- Two reviews racing on the same barcode, or on the same person's list. Reload and retry. Any other integrity error is a server error, not a 409.

## Rebuilding the catalog

The build lives with the plan, in `plans/261031-1740-supplement-logging/data/` in the nutree-universe workspace. The `plans/` folder is gitignored, so it exists only locally. The build reads three inputs:

- The NIH Dietary Supplement Label Database download (`dsld/`), limited to the brands in `scripts/dsld_brands.txt`.
- Collected label pages in `sources/*.json`.
- The nutrient mapping in `scripts/nutrient_mapping.py`.

To rebuild:

```bash
cd plans/261031-1740-supplement-logging/data
python3 scripts/build_catalog.py .
gzip -kf -9 supplement-catalog-vn.json supplement-catalog-review.json
cp supplement-catalog-vn.json.gz <backend>/scripts/data/supplement_catalog_vn.json.gz
cp supplement-catalog-review.json.gz <backend>/scripts/data/supplement_catalog_review.json.gz
```

Then import both files. Keys are stable across rebuilds, so the import updates rows in place. A product whose name changes gets a new key, a new row, and an orphaned old row.

Do not collect from Pharmacity. Its robots.txt disallows Claude agents.
