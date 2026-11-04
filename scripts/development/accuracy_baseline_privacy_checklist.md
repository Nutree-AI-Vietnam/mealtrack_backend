# Accuracy baseline privacy checklist

Required before any scan photo or meal text is committed as a reviewed fixture.
The committed fixtures are the finalized QA labels. This checklist does not
draw a new pool.

## FACT — candidate pool already pulled

Do not re-sample. Do not run the selection statement again to draw another 200.

- Shared-box path: `/workspace/ai-accuracy-eval/`
- Selection SQL already executed: `/workspace/ai-accuracy-eval/README.md`
  (read-only `SELECT`, pulled 2026-10-29 UTC+7). That README is the SQL source
  of truth. This repo does not vendor a second sampler.
- Database: Neon Prod Nutree, project `billowing-sea-41604960`. Query only.
  No writes.
- QA-facing files already on that path:
  - `qa_scan_candidates.csv` — 200 rows (`scanner` 180, `food_label` 20),
    case ids `eval_scan_001` through `eval_scan_200`
  - `qa_prompt_candidates.csv` — 200 rows (`prompt`), case ids
    `eval_prompt_001` through `eval_prompt_200`
- The executed statement reads `meal` (`status = 'READY'`, `source` in
  `scanner` / `food_label` / `prompt`, `created_at` within 90 days,
  `is_manually_edited`, `edit_count`, `cuisine_type`, `origin_country`,
  `image_id`), joins `mealimage` only to require a URL for scan rows and to
  classify `image_host`, and takes the latest `nutrition` row
  (`confidence_score`, `protein`, `carbs`, `fat`). Ordering is
  `md5(meal_id || 'eval2026')` inside each `(source, is_manually_edited)`
  bucket. Quotas in that SQL produce the 200 + 200 counts above.
- CSV `calories` are that README's estimate: `protein*4 + carbs*4 + fat*9`.
  CSV `confidence` is `nutrition.confidence_score`. Neither value is a QA label.
- The QA CSVs do not include `meal_translation.language`. Scaffold `language`
  values in git are slot labels, not languages observed in production.
- Backend is downloading scan images into this restricted store, stripping
  EXIF, and running privacy review before trimming to 100 scan + 100 prompt.
  This PR does not download images and does not choose the surviving 100.

Restricted files on that path. Never commit them, never send them to QA:

- `internal_meal_id_map.csv` (case id → meal id; Backend only)
- `fetch_urls.sql`, `scan_meal_ids.txt`, `scan_case_map.json`, `scan_url_map.json`
- `raw/` and `stripped/` until dual review finishes

`/ai-accuracy-eval/` is gitignored so a checkout copy of that tree is not added
by accident.

## FACT — metadata to synthetic case id

1. The pool case id (`eval_scan_001`, `eval_prompt_001`) is already synthetic.
   It is not a `meal_id`.
2. `internal_meal_id_map.csv` stays on the restricted path. The mapping step
   discards `meal_id` before any fixture, report, or QA packet is written.
3. Reviewed-manifest loaders require hyphenated ids matching
   `^[a-z0-9]+(?:-[a-z0-9]+)+$`. The committed fixtures keep the QA id on
   `qa_case_id` and rewrite `eval_scan_001` → `eval-scan-001` and
   `eval_prompt_001` → `eval-prompt-001` for `case_id`. Do not hash or embed
   `meal_id` or `user_id`.
4. Git fixtures are those rewritten QA ids. They are the post-review trim, not
   the placeholder ids `scan-en-dev-001` / `text-en-dev-001`, and not a copy of
   the restricted pool. Scan bytes are `scan_images/eval_scan_NNN.jpg` relative
   to the manifest. No `meal_id`, `user_id`, or URL.

## CTO policy — before any sample is published

- Production scan photos and text logs may be sampled only after a PII strip
  and a dual privacy review.
- Photos: strip EXIF, re-encode, and crop faces, receipts, and addresses.
- Text: scrub names, phones, and places.
- Synthetic case ids only. No `user_id` or `meal_id` in fixtures.
- Staff or opted-in rows are preferred when choosing who to label. `users` has
  no staff or opt-in column. `meal_scan_feedback.consent` is an explicit share
  flag for scanner corrections, not a general labeling opt-in. The executed
  SQL did not filter on either. Do not re-sample to add that filter. During
  the trim from 200 to 100, Backend may prefer rows it can tie to staff or
  `meal_scan_feedback.consent` through the internal map. That is a filter on
  the existing pull, not a new production draw.

## Dual review, then QA

Two reviewers check each stripped image and each scrubbed text for faces,
names, addresses, receipts, and screens. Drop flagged rows. Trim to 100 scan
+ 100 prompt. Delete raw fetched copies. Keep the internal map restricted.

Draft label fields: `scripts/development/accuracy_baseline_label_schema.json`.
The same draft is copied to
`/workspace/ai-accuracy-eval/qa_label_schema.json` for the restricted review
folder. QA labeling starts after the trimmed set and this schema are both
ready. Ping Niels at that handoff. Labeling capacity this sprint is about
two to three days once the sample set and schema are in their hands.

## Committed fixtures

`tests/fixtures/accuracy_baseline/meal_scan_scaffold_manifest.json` and
`parse_text_scaffold_manifest.json` are the 100 + 100 QA labels:

- `privacy_reviewed` is true and `review_status` is `reviewed`
- `label_status` is `qa_labeled`
- scan JPEGs live under `scan_images/` and are addressed by relative paths
- production pool rows are not loaded (`loaded_into_fixtures` stays false)
- no `user_id`, `meal_id`, image id, or URL

The harness still refuses those keys, UUID-shaped strings, email addresses, and
`http` URLs anywhere in a fixture document. Gram weights are copied only when
the QA item text states grams. Calorie gold is omitted only for the two
unscored rows (`eval-scan-196`, `eval-prompt-121`). Fiber, sugar, and the ten
micronutrients were not in the QA tables, so fiber and sugar are absent and
every micro status is `unknown`.

Prompt CSV privacy columns were blank. Scan rows are `privacy_usable=yes` with
both reviewer marks `keep`. The finalization note records zero privacy drops.

## Grounding

`AI_MEAL_SCAN_REFERENCE_GROUNDING_ENABLED` stays off in production. Offline
mode still accepts only `--grounding not-implemented` and does not call a
provider.

Live mode does not set that flag. `--arm both` (the live default) scores
vision nutrition with grounding off, then calls
`FoodReferenceValidationService.ground_meal` on a copy of the same vision
nutrition. That is the production branch the flag selects. `--grounding off`
or `--grounding on` limits the run to one arm. Parse-text does not read the
flag.

The ON arm counts local-catalog misses. The cascade looks up
`normalize_food_lookup_name`. Catalog rows are stored under
`normalize_food_name`. When those keys differ and the product key misses, a
diagnostic read checks whether the catalog key would have hit. That read is
not fed back into the meal.

## Reproduce the offline report

```bash
python scripts/development/run_accuracy_baseline.py
```

That prints one JSON report to stdout. It loads the QA-labeled fixtures, runs
the existing scan contract path and the existing parse-text offline path, and
attaches label counts. It does not call a provider and does not read
`/workspace/ai-accuracy-eval/`. Optional `--output` refuses to overwrite a file
and, inside the repo, requires a gitignored path.

The report's `accuracy_claim` is null. Contract success is not photo or text
accuracy.

## Live provider run

Requires `OPENAI_API_KEY`. The command fails before any provider call when
the key is missing. It does not print the key. Concurrency defaults to 2 and
cannot exceed 4.

```bash
python scripts/development/run_accuracy_baseline.py --mode live --confirm-live
```

`--live` is the same switch. The JSON report and a short summary are written
under `artifacts/accuracy-baseline/`, which is gitignored. The report records
MAE, MAPE where the gold midpoint is not zero, the share of scored cases
within ±10% and ±20%, per-case latency, and token totals from provider
`usage_metadata`. USD stays empty unless
`ACCURACY_BASELINE_INPUT_USD_PER_MILLION` and
`ACCURACY_BASELINE_OUTPUT_USD_PER_MILLION` are set. FatSecret searches and
detail reads are counted. They get a USD figure only when
`ACCURACY_BASELINE_FATSECRET_USD_PER_CALL` is set.

Both arm totals include the shared vision and parse-text model call. Adding
those two USD figures double-counts that call. `incremental_on` is the
grounding-only FatSecret traffic.

The parse-text handler is given a 45 second floor so a slow answer can still
be scored. Cases slower than `PARSE_TEXT_AI_TIMEOUT_SECONDS` are flagged
`exceeded_product_deadline`. The eval does not open a unit of work, so it
does not persist serving translations. Local catalog lookup runs only when a
database URL is configured. FatSecret runs only when its credentials are
configured. Unscored labels stay out of MAE. Partial labels score only the
nutrients QA recorded. A vision `not food` result is not scored as zero
calories.

## HYPOTHESIS — not established by the pull

- A missing `meal_translation` row does not mean the meal was logged in English.
  Language was not selected, so this PR does not infer it.
- Pool `calories` omit fiber. They are not the backend calorie formula
  (`protein*4 + max(carbs-fiber, 0)*4 + fiber*2 + fat*9`) and must not be
  copied into `expected_calories_kcal`.
- `nutrition_snapshot` and per-item `food_item.confidence` were not selected.
  Do not re-query production to backfill them into this pool.
