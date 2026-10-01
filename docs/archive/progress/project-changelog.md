# Project Changelog

**Status:** Stateful release notes — archived; not evergreen product authority  
**Evergreen route:** see `docs/codebase-summary.md` and root `README.md`

## 2026-09-30

### Fixed

- Weekly plan nutrition readiness now requires all 21 micronutrients before a
  recipe is marked ready. Partial cache rows are reclaimed and completed, and
  incomplete estimates cannot block readiness silently.
- Recipe micronutrient estimates now route to Luna first with an OpenAI fallback,
  require numeric values for the full schema, and use the catalog's full-recipe
  ingredient quantity basis so linked USDA values and AI estimates stay aligned.

## 2026-08-21

### Fixed

- Barcode lookup now returns the durable local `food_reference_id` created by a
  verified provider cache write, preventing first-time OpenFoodFacts and USDA
  barcode logs from falling through the canonical nutrition identity guard. It
  now fails closed with a retryable 503 when that identity cannot be materialized.

## 2026-08-18

### Fixed

- Versioned meal ingredient adds now allow client-generated item IDs; ownership
  validation remains restricted to updates and removals.

## 2026-08-07

### Changed

- Profile and TDEE age floor is **12** (App Store alignment); web-funnel birthdate
  validation remains its own lead contract until that path is aligned.
- Meal edit now supports independent meal- and ingredient-level nutrition
  overrides: absolute calories/macros while set, with clear/restore back to
  source nutrition.
- Documentation refresh: evergreen docs no longer hand-maintain inventory
  counts; web-funnel auth path and nutrition-override contracts are documented.

## 2026-08-04

### Changed

- Updated the active web purchase handoff to resume through normal Firebase
  passwordless email-link sign-in, while retaining RevenueCat verification and
  idempotent backend finalization.
- Replaced the temporary Google/Apple-only activation surface with normal
  Firebase passwordless email-link sign-in followed by silent RevenueCat
  redemption, hash-only preflight, provider-verified finalization, idempotent
  retries, and direct Home routing.
- Removed the dedicated Flutter activation screen while retaining the
  client-side RevenueCat customer-ID mapping replacement: authenticated users
  use their Firebase UID consistently.
- Kept legacy backend claim endpoints disabled by default for migration safety.
- Redemption preflight and provider-alias correlation close anonymous web
  checkout to authenticated Firebase UID attachment.

## 2026-08-01

### Fixed

- Catalog image generation reliability: catalog-job DB transactions are now short-lived, Cloudinary error logging and job output are sanitized, and the GitHub Actions production Cloudinary secret trio must be reconciled before the workflow is expected to run cleanly.
