# Scan confidence and reviewed correction evidence

Photo-scan ingredient rows expose qualitative confidence and remain editable at every confidence level. Labels use model estimates: **Confident** (>= 0.8), **Please check** (>= 0.5), **Uncertain** (< 0.5), or **Not rated** (missing/invalid). These thresholds are not calibrated probabilities or an accuracy guarantee. After a saved manual edit the API omits model confidence, because the old estimate does not validate the correction. Unsaved ingredient changes also hide that ingredient's estimate.

## User flow

1. Scan a meal and inspect each ingredient, including small toppings.
2. Use the existing ingredient editor to correct names, amounts, additions/removals, or nutrition. Save changes.
3. Choose the kind of feedback and tap **Share feedback**. Confirm optional sharing of the photo, original result, and saved corrections.
4. **Withdraw feedback** removes the shared correction payload and makes it ineligible for future export/use.

Feedback sharing is separate from saving a meal. Nothing is shared automatically. UI text is included in all seven supported locales. Share requests are online operations and are not queued silently for later consent.

## Server behavior

- The original completed scan snapshot is captured once in the same transaction that saves a `scanner` meal at edit count zero. Edits never overwrite it. Scans predating this feature may lack a baseline and cannot be shared; scan again rather than fabricate an original result.
- The baseline includes food identities, quantities, units, model confidence, macros, nutrition overrides, image ID, and available provider/model/prompt/image hashes. The image hash identifies the compressed bytes sent to vision; no extra image copy is made.
- `POST /v1/meals/{meal_id}/scan-feedback` accepts only `consent: true`, `expected_edit_count`, and `correction_kind` (`confirmation`, `identity`, `portion`, `nutrition`, or `mixed`). Snapshots come from the server's saved meal, never arbitrary client labels.
- The meal row is locked before reading. Ownership, completed photo-scan status, saved version, and original image identity are checked. A stale edit count returns 409; invalid/missing-baseline feedback returns 400; a missing or other user's meal returns 404. Both endpoints are authenticated and rate limited.
- Identical submissions are idempotent. A changed submission returns to pending review and clears the prior approval. Human review requires the exact submission hash and a reviewer/note.
- `DELETE /v1/meals/{meal_id}/scan-feedback` is an idempotent withdrawal for the owner. It clears shared corrections, the submission hash, and review details. The operational original scan snapshot remains with the meal.
- Hard deletion of a meal or user cascades to its feedback. Inactive meals and replaced/missing photos are excluded from review/export.
- Feedback does not change meal nutrition, so it does not invalidate meal read caches.

## Review and export

Run this operator-only CLI with an authorized database connection. There is no public review endpoint. Inspect the original photo through authorized storage access before approving identity corrections; assess portion/nutrition claims separately. User assertions alone do not establish visual ground truth.

```bash
python scripts/development/review_scan_feedback.py list --limit 20
python scripts/development/review_scan_feedback.py review \
  --meal-id <uuid> --expected-hash <submission-hash> \
  --decision approved --reviewer <operator-id> \
  --note "Verified visible food identity against the original photo"
python scripts/development/review_scan_feedback.py export --output reviewed-scans.jsonl
python scripts/development/review_scan_feedback.py validate --input reviewed-scans.jsonl
```

Use `--decision rejected` for unsupported feedback. Only currently consented, approved evidence with the matching photo is exported. Export files are exclusively created with mode 0600; they contain internal image/feedback IDs but no user IDs, photo URLs, or image bytes. Treat them as private data. `validate` fails on withdrawal, deletion, replaced photos, changed submissions, or modified evidence. Generate fresh exports and validate immediately before downstream use; existing files are not automatically deleted when someone withdraws. Any future retrieval/training pipeline must enforce live consent checks and deletion propagation rather than relying on a historical export.

## What “learning” means here

Implemented: collection, provenance, explicit consent, immutable originals, review, approved evidence export, and revalidation. No automatic model training or production RAG is enabled. RAG would retrieve relevant approved examples during a future scan; fine-tuning would change model weights. Both require a separate evaluated rollout.

Use approved evidence to build a held-out visual evaluation set, keeping duplicate images/users out of both training and evaluation. Track food precision/recall, small-item recall, false detections on negative photos, and calibration by model/prompt version. Portion errors and hidden ingredients need separate evaluation. Collecting corrections by itself does not establish 99.99% accuracy.

## Rollout and validation

Apply generated migration `20261027154254949604` **before** deploying the backend; new scan saves depend on the feedback table. Deploy the API before the mobile UI. Migration generation and an upgrade/downgrade/upgrade cycle were checked against an isolated PostgreSQL database, not production. Async PostgreSQL smoke verification exercises real meal persistence, baseline capture, corrections, withdrawal, and deletion.

The mobile cache persists food confidence beside its existing nutrition metadata, and meal edit revision beside its existing insight metadata, without a Drift schema migration. DTOs/cache models and Retrofit require normal code generation:

```bash
dart run build_runner build --delete-conflicting-outputs
flutter gen-l10n
flutter analyze
flutter test test/features/nutrition_tracking/data/models/scan_feedback_metadata_test.dart \
  test/features/meal_edit/presentation/widgets/scan_feedback_test.dart
```

Flutter/Dart were unavailable in the implementation workspace, so generation and mobile execution must be completed in a Flutter-enabled environment before shipping. Added tests cover confidence/cache revision round trips, consent/cancel, disabled sharing with unsaved changes, and withdrawal. Backend tests cover owner/version enforcement, immutable baseline, re-review, withdrawal/export eligibility, original-photo matching, and nutrition overrides. Repository-wide Python lint/type checks have existing failures; targeted feedback-module checks are separate.
