# Accuracy baseline privacy checklist

Required before any scan photo or meal text is published to QA or committed as a
reviewed fixture. This PR does not publish samples and does not draw a new pool.

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
   `^[a-z0-9]+(?:-[a-z0-9]+)+$`. When a row survives trim, rewrite
   `eval_scan_001` → `eval-scan-001` and `eval_prompt_001` → `eval-prompt-001`.
   Do not hash or embed `meal_id` or `user_id`.
4. Git fixtures in this PR (`scan-en-dev-001`, `text-en-dev-001`, and the rest
   of the 100 + 100) are placeholders. They are not the first 100 pool rows
   and they are not the post-review trim. Replace them only after dual privacy
   review, still with synthetic ids and no `meal_id`, `user_id`, or URL.

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

## This PR's fixtures

`tests/fixtures/accuracy_baseline/meal_scan_scaffold_manifest.json` and
`parse_text_scaffold_manifest.json` are 100 + 100 synthetic placeholders:

- `privacy_reviewed` is false and `review_status` is `unreviewed`
- `loaded_into_this_manifest` is false
- no `user_id`, `meal_id`, image id, or URL

The harness refuses those keys, UUID-shaped strings, email addresses, and
`http` URLs anywhere in a scaffold document.

## Grounding

`--grounding on|off` is accuracy package item #2
(`AI_MEAL_SCAN_REFERENCE_GROUNDING_ENABLED`). This harness accepts only
`--grounding not-implemented` and records that arm as not implemented.

## Reproduce the report

```bash
python scripts/development/run_accuracy_baseline.py
```

That prints one JSON report to stdout. It runs the existing scan contract
path and the existing parse-text offline path, then attaches the scaffold
counts. It does not read `/workspace/ai-accuracy-eval/`. Optional `--output`
refuses to overwrite a file and, inside the repo, requires a gitignored path.

The report's `accuracy_claim` is null. Contract success is not photo or text
accuracy. Real sample trim, dual privacy review, and QA labeling are the next
handoff.

## HYPOTHESIS — not established by the pull

- A missing `meal_translation` row does not mean the meal was logged in English.
  Language was not selected, so this PR does not infer it.
- Pool `calories` omit fiber. They are not the backend calorie formula
  (`protein*4 + max(carbs-fiber, 0)*4 + fiber*2 + fat*9`) and must not be
  copied into `expected_calories_kcal`.
- `nutrition_snapshot` and per-item `food_item.confidence` were not selected.
  Do not re-query production to backfill them into this pool.
