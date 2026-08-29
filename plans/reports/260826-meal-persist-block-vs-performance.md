# Persist vs performance — all write endpoints

**Date:** 26 Aug 2026  
**Scope:** FastAPI mutating routes under `src/api/routes/v1/` plus GET food paths that adopt into `food_reference`  
**Mitigation rule:** persist first. Do **not** use timeouts as the control. External I/O (LLM, FatSecret, translation, Queue, RevenueCat) is not required to commit the business row.

Two clocks:

- **Time-to-persist** — until SQL commit of the business row
- **HTTP latency** — until the client gets a response

## Cross-cutting patterns

| Pattern | Blocks persist? | Slows? | Mitigation (no timeouts) |
| --- | --- | --- | --- |
| Vision / download / Cloudinary **before** save | Delays save (payload needed) | Dominant on scan | Keep UoW closed until save. Do not add extra pre-save I/O |
| Translation / FatSecret / provider **before** save | Hang possible if unbounded | Time-to-persist | Persist canonical payload first; enrich after commit |
| `FOR UPDATE` then **HTTP** before commit | **Yes** — row + connection held | Time-to-persist + pool | Read provider first; then short lock + commit |
| Queue / affiliate / provider HTTP **inside** open UoW | **Yes** — commit waits on HTTP | Time-to-persist + pool | Commit SQL first; then publish/validate outside the session |
| Await Cloudflare Queue **after** commit | No | HTTP only | Return after SQL commit; publish from outbox / post-response |
| Local DB only (no publisher) | No | No | None |
| LLM with **no SQL persist** | N/A | HTTP only | Out of persist scope |

Shared `AsyncUnitOfWork` is cloned per `event_bus.send()` (`_fresh_uow_copy()`). Nested `async with` on the same instance can hang; do not hold UoW across external I/O.

---

## Meals

| Pri | Endpoint | Blocks persist? | Slows performance? | Where | Mitigation (no timeouts) |
| --- | --- | --- | --- | --- | --- |
| P1 | `POST /v1/meals/food-label/scan-by-url` | **Hang possible** | Time-to-persist | `localize_food_label_display` in `parse_nutrition` before `persist_meal` | Persist canonical names; localize after commit |
| P1 | `POST /v1/meals/image/analyze` | Delays save | Dominant + HTTP | Cloudinary + vision before save; Queue after commit | Keep UoW closed during vision (already). Return after SQL; do not await Queue |
| P1 | `POST /v1/meals/scan-by-url` | Delays save; hang if translation on label path | Dominant + HTTP | Download + vision before save; FatSecret before save when enabled; Queue after | Persist parsed meal first; FatSecret/Queue off save path |
| P2 | `POST /v1/meals/manual` | Delays save when items need resolve | Time-to-persist + HTTP | v2: `nutrition_resolver` (provider) **before** write UoW; Queue after | Persist prepared items first; resolve leftover identities after commit |
| P2 | `PUT /v1/meals/{meal_id}/ingredients` | Delays save when items need resolve | Time-to-persist + HTTP | v2 resolve UoW then write UoW; Queue after | Same as manual: persist first, adopt after |
| P2 | `POST /v1/meal-catalog/{catalog_id}/log` | No hang | HTTP after persist | Catalog read then short write; **Queue + translation persist + recommendation recalculate** all awaited | Return after meal commit; translation/recalc after |
| P2 | `POST /v1/meal-recommendations/{plan_id}/slots/{slot_id}/log` | No hang | HTTP after persist | DB log then Queue + `persist_meal_translation` | Return after meal commit |
| P2 | `POST /v1/meal-suggestions/save` | No hang | HTTP after persist | Redis session + DB meal; Queue after | Return after SQL; do not await Queue |
| P3 | `POST /v1/meals/parse-text` and guest-trial | No meal row | HTTP; may write `food_reference` | FatSecret + translation **before** response; adopt into DB | Do not gate meal save (none). Adopt references after returning parse |
| P3 | `PUT /v1/meals/{meal_id}/photo` | No | HTTP after persist | URL already uploaded; DB attach then Queue | Return after SQL |
| P3 | `DELETE /v1/meals/{meal_id}` | No | HTTP after persist | DB then Queue (meal and/or hydration delete events) | Return after SQL |
| P3 | `DELETE /v1/meals/{meal_id}/photo` | No | HTTP after persist | DB then Queue | Return after SQL |
| P3 | `POST /v1/ingredients/recognize` | No persist | HTTP only | Vision + optional translation | N/A for persist |
| P4 | Double commit in graph `persist_meal` | No | Negligible | Inner `commit()` + UoW exit | Drop inner `commit()` |

---

## Hydration, movement, weight, cheat days

DB-first, then **await Queue**. Persist is not blocked. HTTP is.

| Pri | Endpoint | Blocks persist? | Slows? | Mitigation |
| --- | --- | --- | --- | --- |
| P1 | `POST /v1/hydration/log` | No | HTTP | Return after commit; publish off-request |
| P1 | `POST /v1/hydration/log/drink` | No | HTTP | Same |
| P1 | `DELETE /v1/hydration/{entry_id}` | No | HTTP | Same |
| P1 | `POST /v1/movement/log` | No | HTTP | Same |
| P1 | `PATCH /v1/movement/{entry_id}` | No | HTTP | Same |
| P1 | `DELETE /v1/movement/{entry_id}` | No | HTTP | Same |
| P3 | `POST /v1/weight-entries` | No | Usually DB-only | None if no publisher |
| P3 | `DELETE /v1/weight-entries/{entry_id}` | No | DB-only | None |
| P3 | `POST /v1/weight-entries/sync` | No | DB-only | None |
| P3 | `POST /v1/cheat-days` | No | HTTP if Queue | Return after SQL |
| P3 | `DELETE /v1/cheat-days/{date_str}` | No | HTTP if Queue | Return after SQL |

---

## Users, profile, notifications

| Pri | Endpoint | Blocks persist? | Slows? | Where | Mitigation |
| --- | --- | --- | --- | --- | --- |
| P1 | `POST /v1/user-profiles/` (onboarding save) | No | HTTP after persist | DB then Queue | Return after SQL |
| P1 | `PUT /v1/users/firebase/{uid}/onboarding/complete` | No | HTTP | DB then Queue | Same |
| P1 | `POST /v1/user-profiles/metrics` | No | HTTP | DB then Queue | Same |
| P1 | `PUT /v1/user-profiles/custom-macros` | No | HTTP | DB then Queue | Same |
| P1 | `PUT /v1/users/timezone` | No | HTTP | DB then Queue | Same |
| P1 | `PATCH /v1/users/language` | No | HTTP | DB then Queue | Same |
| P1 | `PUT /v1/notifications/preferences` | No | HTTP | DB then Queue | Same |
| P1 | `DELETE /v1/users/firebase/{uid}` | No | HTTP | DB then Queue | Same |
| P3 | `POST /v1/users/sync` | No | DB (+ Firebase already done in auth) | Short UoW | None |
| P3 | `PUT /v1/users/firebase/{uid}/last-accessed` | No | DB-only | Short UoW | None |
| P3 | `PUT /v1/user-profiles/body-fat-visual` | No | DB-only | Short UoW | None |

---

## Foods (GET that can persist)

| Pri | Endpoint | Blocks persist? | Slows? | Where | Mitigation |
| --- | --- | --- | --- | --- | --- |
| P2 | `GET /v1/foods/search` (and autocomplete) | Delays `food_reference` adopt | HTTP + adopt write | Provider + translation then `adopt_provider_food` | Return search hits first; adopt after response |
| P2 | `GET /v1/foods/barcode` | Delays adopt | HTTP + adopt write | Provider lookup then UoW adopt | Same |
| P3 | `GET /v1/foods/details` / provider-details | Usually no meal persist | HTTP | Provider HTTP | N/A for meal persist |
| P4 | `GET /v1/foods/popular-staples` | No | Local DB | No live FatSecret | None |

---

## Recommendations, suggestions, catalog admin

| Pri | Endpoint | Blocks persist? | Slows? | Where | Mitigation |
| --- | --- | --- | --- | --- | --- |
| P2 | `POST /v1/meal-recommendations/three-day` | Delays plan persist | Time-to-persist | UoW held during snapshot/affinity then `save_new_active_plan` | Load snapshot **outside** write lock; persist plan in a short UoW |
| P3 | `POST .../slots/{slot_id}/swap` | No | DB | Short UoW | None |
| P3 | `POST .../slots/{slot_id}/skip` | No | DB | Short UoW | None |
| P2 | `POST /v1/meal-suggestions/discover` | Redis session, not meal SQL | HTTP (LLM) | Generation before session save | N/A for meal persist |
| P2 | `POST /v1/meal-suggestions/recipes` | Redis session | HTTP (LLM + translation) | `generate_selected_recipes` then session | N/A for meal persist |
| P3 | `POST /v1/saved-suggestions` | No | HTTP after persist | DB then Queue | Return after SQL |
| P3 | `DELETE /v1/saved-suggestions/{id}` | No | HTTP after persist | DB then Queue | Return after SQL |
| P1 | `POST /v1/admin/meal-catalog/enrich` | **Yes** — session open during FatSecret | Time-to-persist | Request `AsyncSession` still open while `enrich_missing_candidates` hits FatSecret, then `db.commit()` | Enrich in a job/session after a short write, or close DB before provider HTTP |
| P3 | Admin `POST /v1/admin/meal-catalog/import` / resolve / generate-image | Delays catalog persist | HTTP + LLM/images | Long admin jobs; generate-image does HTTP before `set_missing_image_url` | Keep off user persist path; do image HTTP outside the write session |

---

## Web funnel, webhooks, codes

| Pri | Endpoint | Blocks persist? | Slows? | Where | Mitigation |
| --- | --- | --- | --- | --- | --- |
| P1 | `POST /v1/web-funnel/leads/{id}/revenuecat-correlation` | **Yes** | Time-to-persist + pool | `SELECT … FOR UPDATE` **then** RevenueCat HTTP **then** commit | Call RevenueCat **before** locking the lead; lock only for the short write |
| P1 | `POST /v1/webhooks/revenuecat` | **Yes** | Time-to-persist + pool | Lead UUID branch: RC `get_subscriber_info` **inside** UoW. Native purchase/renewal: `_notify_affiliate` Queue HTTP **before** UoW exit | Fetch RC before opening UoW. Commit subscription row first; Queue/affiliate after commit |
| P1 | `POST /v1/referrals/apply` (affiliate path) | **Yes** | Time-to-persist + pool | `AffiliateServiceAdapter.validate_code` + Queue publish run **inside** `async with AsyncUnitOfWork` | User-referral path is DB-only (OK). Affiliate: close UoW (or never open it) before HTTP; publish after |
| P2 | `POST /v1/web-funnel/redemptions/finalize` | Delays if RC then DB | HTTP + persist | RC read then `finalize()` DB | Provider read first (already); keep DB work short |
| P3 | `POST /v1/web-funnel/leads` | No | DB | Insert lead | None |
| P3 | `POST /v1/web-funnel/leads/{id}/reset` | No | DB | Short lock | None |
| P3 | `POST /v1/web-funnel/redemptions/preflight` | No business persist | HTTP | RC/eligibility | N/A |
| P3 | `POST /v1/web-funnel/leads/{id}/resend` | No | DB | Claim generation | None |
| P3 | `POST /v1/web-funnel/claims/exchange` / complete | No hang if no extra HTTP in tx | DB | Claim write | Keep provider I/O outside the transaction |
| P3 | `POST /v1/referrals/payout` | No | DB | Short UoW | None |
| P3 | `POST /v1/promo-codes/redeem` | No | DB | Short UoW | None |
| P4 | `POST /v1/referrals/validate`, `POST /v1/promo-codes/validate`, `POST /v1/codes/*` | No persist | DB read | Validate only | None |
| P4 | `POST /v1/tdee/preview` | No persist | CPU | Preview only | None |
| P4 | `POST/PUT /v1/feature-flags` | No | DB | Admin | None |

---

## Suggested order of work (no timeouts)

1. **Unlock web-funnel correlation:** RevenueCat HTTP must not run under `FOR UPDATE`.
2. **RevenueCat webhook:** RC fetch and Queue/affiliate publish must not run inside the open UoW (`webhooks.py`, `webhook_subscription_lifecycle.py`).
3. **Affiliate `POST /v1/referrals/apply`:** validate/publish outside UoW.
4. **Admin catalog enrich:** FatSecret outside the request session.
5. **Food-label scan:** persist canonical names; localize after commit.
6. **Stop awaiting Queue** on the HTTP response for meal, hydration, movement, user, and catalog writes (post-commit await is HTTP-only).
4. **Manual/edit v2 and FatSecret:** commit parsed/prepared nutrition; adopt after commit.
5. **Catalog/recommended log:** return after meal commit; translation + recalc after.
6. **Three-day plan:** do not hold the generation lock across snapshot I/O.
7. **Food search/barcode adopt:** return hits first; persist `food_reference` after.

## What is not an issue

- Local CRUD with no external I/O (weight, last-accessed, cheat days without Queue, swap/skip, feature flags).
- Vision before scan persist — required to build the meal; UoW is not held during it.
- Worker cache/insights after `*.v1` events — does not block API SQL.
