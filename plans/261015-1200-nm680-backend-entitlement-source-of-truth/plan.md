---
title: "NM-680: backend entitlement ledger as subscription source of truth"
description: "Keep RevenueCat as the purchase rail; make a provider-neutral entitlement ledger in Postgres the only runtime authority for access."
status: proposed
priority: P0
branch: "claude/backend-source-of-truth-uwn82i"
tags: [revenuecat, subscriptions, entitlements, flutter, nm-680]
blockedBy: []
blocks: []
created: "2026-10-15"
source: research
---

# NM-680 plan: make our backend the source of truth for subscriptions

**Status: proposal awaiting CTO sign-off (see section 12). No code changes yet.**

**Paths.** `BE:` = this repo (`mealtrack_backend`), `APP:` = `nutree_ai`. Citations were checked against `main` at
`bfef3d5` (backend) and `2b2bca2` (app) on 2026-10-15. RevenueCat (RC) and store behaviour is tagged
*(RC docs: URL)* when checked against revenuecat.com, or *(RC docs, unverified)* when not yet checked.

**Basis.** Three independent designs (incremental, provider-agnostic, risk-first) were scored by three reviewers;
this plan starts from the risk-first design, grafts the provider port and admin grants from the provider-agnostic
one, and applies four adversarial verification passes (code accuracy, failure modes and security, migration and
rollout, completeness and sizing). See "Verification notes" at the end.

## 0. Fix now (independent of this epic)

- **Possible account takeover via silent login (security review needed).** `POST /v1/web-funnel/redemptions/session`
  (`BE:src/api/routes/v1/web_funnel_redemption_session.py:40-70`, added in #655) mints a Firebase custom token for the
  existing account that owns the lead email, given only `redemption_link_hash`
  (`BE:src/infra/services/web_funnel_redemption_identity.py:17-35`). The app's own rule SEC-03 says "hash possession
  alone is insufficient" (`APP:docs/passwordless-redemption-edge-cases.md:157`). Exploitability depends on whether the
  BFF verifies lead email ownership and whether a purchaser can see the hash; neither was verified.
  `require_admin` (`BE:src/api/dependencies/auth.py:346-371`) trusts the email claim alone, which raises the impact.
- **Grace-period lockout in the app.** An RC-active entitlement with `billingIssueDetectedAt` maps to `billingRetry`
  (`APP:lib/features/subscriptions/domain/entities/subscription.dart:103-104`), which has no access
  (`APP:lib/features/subscriptions/domain/entities/subscription_status.dart:25`).
- **Refunds never revoke referral credit.** RC sends refunds as `CANCELLATION`; the `REFUND` branch
  (`BE:src/api/routes/v1/webhooks.py:243-244`) never fires.

---

## 1. TL;DR

- **Recommendation.** RC stays as the purchase rail and receipt validator: purchase, offerings, restore, web billing and attribution. Our Postgres becomes the only runtime answer to "does this user have access?", stored in a provider-neutral schema.
  - The app reads `GET /v1/me/entitlements`.
  - RC webhooks only trigger work. Each one makes us re-fetch the customer from RC, so duplicate or out-of-order events stop mattering.
  - "Moving RC auth to the backend" means the backend holds the only RC secret and does every RC read. The app keeps `Purchases.logIn(firebaseUid)`, because RC needs it to attribute store purchases.
- **Why.** Today no server code decides entitlement:
  - No paid route is gated, and `premium_check.py` is dead code.
  - `GET /v1/users/firebase/{uid}` returns `false` in production.
  - The app trusts only the RC SDK.

  A provider-neutral ledger plus our own grants answers "manage ourselves". It also leaves RC as one adapter we can replace later (P7) without touching any reader.
- **Scope versus the ticket.** NM-680 has 8 story points. Its Done line is "App reads subscription/entitlement state from our backend/database; RevenueCat is no longer the runtime source of truth" (Jira NM-680, customfield_10036 = 8). That line is met at the **end of P4**.
  - P0–P4 come to about **80 SP**, roughly 10× the ticket.
  - P5 (server enforcement) and P6 (referral/promo on the ledger, contract migrations) add about 20 SP more.
  - P7 (leaving RC) is not estimated.
  - **Proposal:** turn NM-680 into an epic. Re-scope NM-680 itself to P0–P4, and file P5, P6 and P7 as separate tickets. No honest 8-SP version of "DB is source of truth" exists; 8 SP covers P0 plus the P1 schema story.
- **Effort.** Converted at the draft's implied rate of about 6–7 SP per engineer-week (team velocity is not recorded in either repo, so Niels must re-point):
  - P0–P4 is about 11–13 engineer-weeks, or 10–13 calendar weeks with one backend and one app engineer. The backend is the critical path, and 7-day shadow gates are included.
  - P5 and P6 add about 3–4 engineer-weeks.
- **Biggest risk: locking out paying users.** This can happen in six ways:
  - The current status values cut off cancelled-but-paid and grace-period users.
  - Webhooks lag, usually 5–60 s *(RC docs: https://www.revenuecat.com/docs/integrations/webhooks)*.
  - RC rate limits: v1 has no published limit, and RC staff suggest about 1 req/s.
  - A false "no" from the new backend while the app ramps over to it.
  - App Review and TestFlight buy in sandbox.
  - Old builds cannot handle a 402.

  Mitigations:
  - Access is decided by expiry, grace and revocation, never by a status string, and it is evaluated at read time.
  - Purchase, restore and redeem open an optimistic window and send a purchase-hinted `/sync`. That result is never cached as "no".
  - All RC calls share one global, prioritised budget.
  - From P4 to P6, the app has an asymmetric guard: if the backend says "no" while the RC SDK says `standard` is active, the app allows access and reports it.
  - Sandbox access is a policy setting (D3).
  - Server enforcement is a separate ticket. It waits for a client-capability header.
- **Security prerequisite.** Admin grants and the enforcement kill switches both sit behind `require_admin`. That dependency checks only the token's `email` claim (`BE:src/api/dependencies/auth.py:331-371`). It must be hardened before P2 ships grants (D7).
- **Decisions needed (CTO).** The full list is in §12. The ones that block P1 or P2 are:
  - D1 scope and epic split
  - D2 worker host
  - D3 access policy
  - D5 where grants live
  - D7 admin authentication
  - D10 RC API v1 or v2

---

## 2. Where we are today

### 2.1 Who decides entitlement, per decision point

| Decision point | Who decides today | Data used | Evidence |
|---|---|---|---|
| App splash exit and hard paywall | Client router | `subscriptionStatusProvider`, built from the RC SDK (`restorePurchases()`, falling back to the cached `getCustomerInfo`) | `APP:lib/features/auth/presentation/router/app_router_redirect.dart:186-192,333-356`; `APP:lib/features/subscriptions/application/providers/subscription_state_provider.dart:131-134,161-167` |
| Premium routes (meal suggestion, food search, barcode, coach, recipes, …) | Client router; access is allowed while state is loading | Same provider | `app_router_redirect.dart:52-70` (list), `:405-412` (loading = allow) |
| Lapsed allowance (hydration, movement, walking) | Client router | Provider status `expired` | `app_router_redirect.dart:270-281` |
| Free routes (home, profile, fitness, progress, catalog, catalog detail, first meal) | Client | Static list | `app_router_redirect.dart:38-49` |
| Widget gates (34 call sites: shell FAB scan/describe/import, coach, meal planner, catalog "log", …) | Client helpers `hasSubscriptionAccess` / `requireSubscriptionAccess` | Same provider | `subscription_state_provider.dart:407-475`; e.g. `APP:lib/core/theme/shell_layout.dart:179-185`; `APP:lib/features/meal_recommendation/presentation/screens/meal_catalog_detail_screen.dart:141-142` |
| Access rule itself | Client | Any active entitlement (`values.first`), not specifically `standard`. `billingIssueDetectedAt` maps to `billingRetry`, which has no access | `APP:lib/features/subscriptions/domain/entities/subscription.dart:86-104`; `subscription_status.dart:25` |
| Backend AI cost endpoints (image analyze, scan-by-url, label scan, parse-text, chat, suggestions, recommendations, meal plans, ingredients, barcode, recaps, **`POST /v1/progress/recap`**) | Nobody. Only Firebase auth plus per-minute rate limits | – | `BE:src/api/routes/v1/meals_analyze.py:129-138`; `BE:src/api/routes/v1/progress.py:104-127` (recap calls the LLM via `generate_progress_recap_command_handler.py:87-96`, and the app calls it from the free `/progress` route, `APP:lib/core/network/api_service.dart:644`); `BE:docs/external-services.md:21` ("Premium route gates are not enforced yet") |
| Client-gated features whose endpoint is ungated | Nobody on the server | – | Manual meal create via "describe food" (`shell_layout.dart:179-185` → `POST /v1/meals/manual`, `api_service.dart:464`); catalog meal log (`meal_catalog_detail_screen.dart:141-142` → `POST /v1/meal-catalog/{id}/log`, `api_service.dart:572`; `BE:src/api/routes/v1/meal_catalog_log.py:45`) |
| Backend premium gate | Dead code, imported only by its own unit test | `request.state.user`, which only the dev bypass sets. RC is called with `firebase_uid` in one function and `user.id` in the other | `BE:src/api/middleware/premium_check.py:23,36-41,64,86,126`; `BE:tests/unit/api/test_premium_middleware.py:11-13` |
| Chat quota | Server, the same for every user | 40 turns per UTC day; not tier-aware | `BE:src/domain/model/chat/models.py:46`; `BE:src/infra/config/settings.py:223`; `BE:src/app/services/chat_turn_orchestrator.py:1264-1279` |
| Guest text-parse trial (before login) | Server, once per install id | Hashed `X-Guest-Install-Id` | `BE:src/api/routes/v1/meals_manual_text.py:339-345`; `BE:src/api/services/guest_parse_quota.py:44-72` |
| `POST /v1/users/sync` `has_subscription` | Server, but the app ignores it | Local `subscriptions` filtered on `status=='active'` only: expiry ignored, no ORDER BY | `BE:src/app/handlers/command_handlers/sync_user_command_handler.py:101-125`; `BE:src/infra/repositories/subscription_repository_async.py:51-61`; the app model has no such field: `APP:lib/features/auth/data/models/user_profile_response.dart:7-55` |
| `GET /v1/users/firebase/{uid}` `has_subscription` | Hard-coded | The domain model always returns `False` (`BE:src/domain/model/user/core_user.py:80-85`). The query handler forces `True` when `ENVIRONMENT=development` and turns exceptions into `False` | `BE:src/app/handlers/query_handlers/get_user_by_firebase_uid_query_handler.py:37-45` |
| Web funnel payment and finalize | Server, using a live RC GET plus the `standard` entitlement. Finalize writes a `subscriptions` row. The app discards the finalize response | RC REST v1 | `BE:src/api/routes/v1/web_funnel.py:256,377-441`; `BE:src/infra/services/web_funnel_redemption_completion.py:262-286`; `APP:lib/features/auth/application/providers/web_purchase_redemption_provider.dart:125-152` |
| Promo codes | Price comes from the RC Offering the client buys. The backend only counts usage and trusts the client. No entitlement is granted | `promo_codes.rc_offering_id` | `BE:src/infra/database/models/promo_code/promo_code.py:18`; `BE:migrations/versions/20260515000000_add_promo_code_tables.py:25`; `BE:src/app/handlers/command_handlers/promo_code/redeem_promo_code_handler.py:15-49` |
| Referrals | The client creates a pending conversion after purchase without awaiting it. Credit runs the **first time a `subscriptions` row is inserted for that `app_user_id`**: an INITIAL_PURCHASE with no row, or a RENEWAL with no row. A replayed INITIAL_PURCHASE whose row exists goes to `handle_renewal` and never credits. Revoke happens only on `REFUND`. The wallet is a cash payout balance, not an entitlement | `referral_conversions` | `BE:src/app/handlers/command_handlers/referral/apply_referral_code_handler.py:43-67`; `BE:src/api/routes/v1/webhook_subscription_lifecycle.py:99-104,124,159-162`; `BE:src/app/handlers/command_handlers/referral/request_payout_handler.py:49-67` |
| Trial eligibility | Client heuristic | `entitlements.all.isEmpty` | `APP:lib/features/subscriptions/data/datasources/revenuecat_service.dart:745` |
| Analytics `hasSubscription` | A direct RC read that bypasses the provider | RC SDK | `APP:lib/core/services/timezone_monitor.dart:110-112` |

**RC identity.** The RC `app_user_id` is the Firebase UID (`APP:lib/features/subscriptions/data/datasources/revenuecat_service.dart:387`; `APP:lib/features/auth/data/repositories/auth_repository.dart:598-601`). Every signed-in user calls `Purchases.logIn` on login and again at cold start (`APP:lib/main.dart:655`, `syncRevenueCatUserIdentity`). Older builds logged into RC with claimed lead ids; the app now only cleans those keys up (`APP:lib/features/subscriptions/data/datasources/revenuecat_identity_store.dart:3-11`). The backend's only RC call is `GET https://api.revenuecat.com/v1/subscribers/{id}` (`BE:src/infra/adapters/revenuecat_adapter.py:26,41`). Its docstring wrongly says the id is "same as user.id" (`:36`).

### 2.2 Bugs and gaps that block "DB is truth"

Ranked by severity. Each line names what fixes it.

**High**

1. **Status semantics lock out payers.**
   - CANCELLATION sets `cancelled` (`webhook_subscription_lifecycle.py:206`), and BILLING_ISSUE sets `billing_issue` (`:264`).
   - `is_active()` requires `active` (`BE:src/infra/database/models/subscription.py:65-71`).
   - The app also denies `billingRetry` (`subscription.dart:103-104`).
   - Fixed in P1 by the projection and its read-time rule (§5).
2. **No dedupe or ordering on the native webhook path.** A replayed INITIAL_PURCHASE goes to `handle_renewal`, which sets the row back to `active` (`:99-104,154`). `event_timestamp_ms` is never read. Fixed by the inbox plus re-fetch (§6).
3. **No uniqueness on `subscriptions`.** Rows are check-then-insert, and `find_by_revenuecat_id` uses `.first()` with no ORDER BY (`subscription_repository_async.py:63-72`). Fixed by the new tables (§5).
4. **Several RC event types are not handled.** UNCANCELLATION, NON_RENEWING_PURCHASE, SUBSCRIPTION_EXTENDED, SUBSCRIPTION_PAUSED and TEMPORARY_ENTITLEMENT_GRANT all fall through:
   - When the user is resolved, they return success without writing anything (`BE:src/api/routes/v1/webhooks.py:216-250`).
   - When no user is found, they are not in `NON_RETRYABLE_USERLESS_EVENTS`, so they get 404 (`:50-57,204-214`). RC then retries events that will never write anything.

   Fixed by re-fetching on every event and parking unknown users (item 11).
5. **No server-side enforcement** (§2.1). Fixed in P5, under its own ticket.
6. **The backend never hears about a native purchase.** The app sends nothing (`APP:lib/features/subscriptions/application/providers/paywall_provider.dart:753-786`). An app that reads from the backend would lock out a new buyer until the webhook lands. Fixed by `POST /v1/me/entitlements/sync` with a purchase hint (§6.2).
7. **Admin authorization trusts the email claim alone.** This blocks admin grants and the enforcement kill switches (D7).
   - `require_admin` compares `token.get("email")` with `ADMIN_EMAILS`. It never checks `email_verified` or a custom claim (`BE:src/api/dependencies/auth.py:331-371`).
   - The web-funnel silent login looks up the existing user who owns the lead email, sets `email_verified=True` on that Firebase account and mints a custom token for it (`BE:src/api/routes/v1/web_funnel_redemption_session.py:58-64`; `BE:src/infra/services/web_funnel_redemption_identity.py:17-35`). The lead email arrives in the BFF payload (`BE:src/api/routes/v1/web_funnel.py:147,164`).
   - Today this guards the feature-flag `PUT` (`BE:src/api/routes/v1/feature_flags.py:135-140`). This plan would add lifetime grants and enforcement switches behind it.
   - *Unverified:* whether Email/Password sign-up is enabled in the production Firebase project (the app uses password sign-in only for the dev bypass, `APP:lib/features/auth/application/providers/auth_flow_notifier.dart:150`), and whether the BFF verifies lead emails.

**Medium**

8. **RC failures read as "not subscribed".** The adapter returns `None` on any `httpx.HTTPError` (`revenuecat_adapter.py:58-60`). Legacy callers rely on that `None`: the web-funnel reconcile turns `None` into a retry job (`BE:src/app/services/web_funnel_claim_payment.py:57-60`). Fixed by typed errors in the **new** port only, with a `None` shim for legacy callers until P6.
9. **Sandbox state is written but never read.** `is_sandbox` is written (`webhook_subscription_lifecycle.py:117`) and never read. Fixed by storing an `environment` column and applying policy D3.
10. **`REFUND` is not an RC event type.** Refunds arrive as CANCELLATION, and REFUND_REVERSED exists only for the App Store *(RC docs: https://www.revenuecat.com/docs/integrations/webhooks/event-types-and-fields)*. So `handle_refund` and the referral revoke never fire (`webhooks.py:243-244`), and neither does the `REFUND` check in the web-funnel reconcile (`web_funnel_claim_payment.py:61`). Fixed by re-fetching and driving side effects from projection transitions.
11. **Users not found.** Retryable types get 404 (`webhooks.py:212-214`), which uses up RC's 5 retries over about 2.6 h. Userless CANCELLATION, EXPIRATION and similar events are ACKed as `ignored` and lost (`:50-57`). Fixed by parking events plus replay (§6).
12. **TRANSFER moves `user_id` only in the happy path.**
    - `handle_transfer` re-keys `revenuecat_subscriber_id` only (`BE:src/api/routes/v1/webhook_referral_funnel.py:39-71`, re-key at `:65`).
    - The route then calls `sync_redeemed_target_subscription` (`webhooks.py:185-186`), which sets `user_id` on the row keyed by the resolved user's `firebase_uid` (`webhook_referral_funnel.py:101`).
    - So `user_id` is reassigned only when the resolved user is the **target** and RC returns an active subscription. It stays stale when the source user is resolved, or when RC errors or returns nothing (early return at `:76-79`).

    Fixed by snapshot-replace sync of both users (§6.4).
13. **Queue events are fire-and-forget.** They are published by a task started before commit (`BE:src/infra/adapters/best_effort_integration_event_publisher.py:18-58`), while the docs claim post-commit publishing (`BE:docs/external-services.md:25`). Fixed by an outbox dispatched after commit, from P2 onwards. The current publisher stays until then.
14. **The renewal notification purge sits in a try/except with no SAVEPOINT.** A failed statement aborts the Postgres transaction (`webhook_subscription_lifecycle.py:180-196`; exact outcome not verified). Fixed with `begin_nested()` in P0.
15. **Referral race and trial credit.** The client's unawaited `/referrals/apply` can arrive after the webhook. Credit fires on trial starts and depends on whether a row exists (§2.1). Fixed in P6 by crediting on ledger transitions.
16. **An RC call runs inside an open DB transaction.** `sync_redeemed_target_subscription` calls RC (`webhook_referral_funnel.py:76`) inside the UoW that has already queried the user (`webhooks.py:178-179`). That holds a pooled connection for up to the 10 s adapter timeout (`revenuecat_adapter.py:48-49`). The production pool is 10+5 per worker (`BE:src/infra/database/connection_policy.py:115-117`). Fixed by the fetch-outside-transaction rule (§6.1.3).
17. **Deleted accounts still resolve.** Deletion soft-deletes and anonymizes the user but keeps `firebase_uid` (`BE:src/app/handlers/command_handlers/delete_user_command_handler.py:72-91`). Webhook lookup does not filter on `is_active` (`BE:src/api/routes/v1/webhook_lookup_parsing.py:42-50`), so renewals keep updating a deleted user. Fixed in §6.4 and D11.
18. **The app's RC listener is removed on logout and only re-registered at cold start** (`revenuecat_service.dart:767-770`; `APP:lib/main.dart:614-640`). Fixed in P0 (app).
19. **The app's version header is sparse and wrong.**
    - `X-App-Version` is sent on only two endpoints (`APP:lib/core/network/api_service.dart:469,486`).
    - Its value is a compile-time constant that defaults to `'2.8.3'` (`APP:lib/core/network/nutrition_contract_headers.dart:9-12`). No build script defines `APP_VERSION`; the scripts only pass `GIT_SHA` and `GIT_BRANCH` (`APP:scripts/shorebird-release.sh:57-58`). Meanwhile pubspec is `2.11.6+36` (`APP:pubspec.yaml:19`).
    - Shorebird Dart patches do not change the store version (`APP:shorebird.yaml:8-12`; `APP:docs/runbook.md:94-113`).

    Fixed in P0 (app) with a version and capability interceptor (§8.2).
20. **App `Subscription` equality compares only `userId` and `status`** (`subscription.dart:132-141`). A change in `willRenew` or expiry alone may not notify Riverpod 3 listeners. Fixed in P0 (app).

**Low**

21. **`PRODUCT_CHANGE` writes `event.product_id` and forces `active`** (`webhook_subscription_lifecycle.py:285-296`). RC docs say it can be an advance notice, and that the target product is `new_product_id` *(RC docs, unverified)*. Fixed by re-fetching.
22. **No scheduler or worker is deployed.**
    - `render.yaml` defines only the web service (`BE:render.yaml:1-22`), and the container runs only uvicorn (`BE:docker-entrypoint.sh:58-66`).
    - `BE:docs/decisions/ADR-cron-jobs-removal.md:90-98` says the removal is not operationally complete until a replacement trigger exists. Option 2 is a Render Background Worker.
    - The ADR's "What Was Kept" list (`:70-84`) is stale: the outbox ORM, engine, repository and handlers are gone from `src`.
    - The `outbox_events` table exists, created under a `has_table` guard (`BE:migrations/versions/20260822000001_create_outbox_events_table.py:29-33`), and may hold legacy rows. Decision D2.
23. **No server-side feature-flag reader.**
    - `FeatureFlagService` only creates and updates (`BE:src/infra/services/feature_flag_service.py:11-43`).
    - The only readers are the two client-facing GET routes (`feature_flags.py:32-100`).
    - `PUT` already invalidates both cache keys (`:161-163`).

    A reader is needed for any server kill switch (§4).

---

## 3. Options considered

| | A. Incremental: an RC-fed projection on top of the existing `subscriptions` table | **B. Recommended: a provider-neutral ledger fed by RC (RC as one adapter)** | C. Direct StoreKit 2 / Play Billing / own web billing, no RC |
|---|---|---|---|
| What changes | Patch the table, add an inbox and a projection; the syncer stays RC-shaped | New identity, inbox, store-subscription, grant and projection tables. A `BillingProviderPort` returns normalized subscriptions. Admin grants | The app drops `purchases_flutter` for `in_app_purchase`. The backend ingests App Store Server Notifications V2 and Play RTDN, validates with the App Store Server API and `subscriptionsv2.get`, and runs its own web checkout and claim |
| Provider independence | Low–medium. Readers are neutral; the schema and sync are RC-keyed | Medium–high. Readers, grants and policy are ours. Replacing RC later means a new adapter plus a product→entitlement table (P7), not a new data model | High |
| Effort | ~55–60 SP for P0–P4 | ~80 SP for P0–P4 (§11) | Not estimated with confidence. Anecdotally 2–4 weeks per store rail *(unverified)*, plus web billing, attribution, offerings, analytics and subscriber migration. Several engineer-months |
| Risk to paying users | Low–medium, with the safeguards below | Low, with the safeguards below | High. Unacknowledged Play purchases are refunded after 3 days; Family Sharing, account hold, linkedPurchaseToken and old builds must all be handled |
| What we lose | Nothing now; a second migration later if we leave RC | Nothing now. RC is still a dependency for purchase, receipt validation, Web Billing and redemption links, RC→Airbridge/Meta S2S revenue, and offering metadata | RC Web Billing and Redemption Links (`web_purchase_redemption_provider.dart:33,96`); RC→Airbridge S2S revenue (today the app sends a price-0 placeholder, `revenuecat_service.dart:980-989`); Offerings and metadata strikethrough prices (`paywall_provider.dart:98-129`); Charts; cross-platform identity and transfer |
| Prerequisite we lack | – | – | Raw store identifiers. We store only `store_transaction_id` (`subscription.py:52`) and no Play purchase tokens |

**Recommendation.** Do B now, with A's low-risk rollout discipline. Treat C as optional phase P7 under its own ADR. Start collecting C's prerequisites in P1 at low cost: store any store identifiers that the re-fetch returns, and check identity tagging (§6.4).

---

## 4. Recommended target architecture

```
Flutter app
  RC SDK (unchanged): configure, logIn(firebaseUid), offerings, purchase, user-tapped restore,
                      redeemWebPurchase, Airbridge attributes, SKAN listener analytics
  |  after purchase/restore/redeem (hint) / login / resume ---> POST /v1/me/entitlements/sync
  |  all gating reads ---------------------------------------> GET  /v1/me/entitlements  (ETag)
  |  every request carries X-App-Version (package_info) + X-Client-Caps
  |  [P5, own ticket] paid API calls ---> require_entitlement("standard") ---> 402
  v
FastAPI (web service, 4 uvicorn workers)                 Worker (Render background worker, P2)
  /v1/webhooks/revenuecat                                  claims inbox rows FOR UPDATE SKIP LOCKED
    auth -> inbox row -> (P1: inside legacy UoW)            SyncService.sync(user)
    -> inline sync after commit (3 s budget) -> 200         Reconciler (15 min / nightly)
  EntitlementService <--reads-- user_entitlements           Outbox dispatcher (allow-listed types)
        (evaluates expiry at read time)                       |
        ^                                                     v
        | short txn: pg_advisory_xact_lock(hashtext('ent:'||user_id)),
        |            write only if fetch_started_at > last_provider_sync_at
  SyncService
    1. RcBudget.acquire(lane)    <- global token bucket, priority lanes, circuit breaker
    2. provider.fetch_customer(firebase_uid [+ legacy ids])  -- OUTSIDE any DB transaction
    3. normalize (RC entitlements map) -> snapshot-replace store_subscriptions
    4. union entitlement_grants -> recompute user_entitlements (version++, next_change_at)
    5. outbox rows (from P2)
  Admin (hardened require_admin, D7): grant/revoke -> entitlement_grants -> recompute
```

### Components and layers

The repo enforces layering in CI through import-linter (`BE:.importlinter:1-8`; `BE:.github/workflows/ci.yml:40`). The existing webhook routes reach infra only through frozen baseline exceptions (`.importlinter:276-282`). New code adds **no** new baseline entries.

1. **`BillingProviderPort`** (domain port). It replaces `SubscriptionServicePort` for the new path (`BE:src/domain/ports/subscription_service_port.py:14-50`).
   - Interface: `fetch_customer(customer_id) -> CustomerSnapshot{canonical_customer_id, subscriptions: list[NormalizedSubscription], entitlements, non_subscriptions, aliases, fetched_at}`.
   - It raises `ProviderUnavailable` or `ProviderRateLimited` and never returns `None` for an error.
   - The infra adapter `RevenueCatBillingProvider` is new. The legacy `RevenueCatAdapter` keeps returning `None` for its existing callers until P6 (§2.2 item 8).
   - It is composed in `BE:src/api/base_dependencies.py:726-741`, which today always builds `RevenueCatAdapter`.
2. **SyncService** (application service, used through commands and handlers on the event bus). It is the only writer of `store_subscriptions` and `user_entitlements`.
   - Order of work: budget, fetch, then a short transaction.
   - The lock uses `hashtext()`, as the existing code does at `BE:src/api/routes/v1/webhook_referral_funnel.py:109-114`, because `user_id` is `String(36)`.
   - Repositories are infra classes registered in `AsyncUnitOfWork` (`BE:src/infra/database/uow_async.py:143-173`).
3. **EntitlementService** (application query). It is the only reader. It replaces three definitions of "active" that disagree:
   - `BE:src/infra/database/models/subscription.py:65-71`
   - `BE:src/domain/model/subscription.py:18-23`
   - `subscription_repository_async.py:51-61`

   It also backs `/v1/users/sync` and `GET /v1/users/firebase/{uid}`. The dev override in `get_user_by_firebase_uid_query_handler.py:37-45` is removed.
4. **Webhook route.** It stays thin: auth, then dispatch a `RecordProviderEventCommand`. No new direct infra imports.
5. **Worker and Reconciler.**
   - Composed in `BE:src/bootstrap/` with a runner script, like the existing `BE:src/bootstrap/catalog_preparation.py` and `BE:scripts/catalog_preparation_worker.py`.
   - The worker class lives in `BE:src/infra/workers/` and imports only domain and infra, like `catalog_preparation_worker.py:7-17`.
   - It reuses that file's lease pattern (`:23-76`).
   - This carries out the **outstanding follow-up** in `ADR-cron-jobs-removal.md:93-98` (option 2). It does not reverse the ADR (D2).
6. **RcBudget.** One token bucket for every RC call, shared through Redis when available. Otherwise each process gets a static share of the budget; Redis is optional (`BE:docs/external-services.md:22`). It has a process-wide concurrency semaphore and a circuit breaker. Lanes, highest priority first:
   1. purchase/restore/redeem-hinted `/sync`
   2. webhook sync
   3. verify-on-deny (P5)
   4. login/resume `/sync`
   5. reconciler
   6. backfill
7. **Kill switches.** These use the DB-backed `feature_flags` table and its admin `PUT` (`BE:src/infra/database/models/feature_flag.py:11-20`; `feature_flags.py:135-140,161-163`).
   - **A new `FeatureFlagReader` port** is required: a cached infra adapter with an in-process TTL of about 30 s, and Redis-optional, because `CacheService` can be `None` (`BE:src/api/base_dependencies.py:246-248`).
   - Defaults are fail-safe: read paths default to the legacy/RC behaviour, and gates default to off.
   - Flags are booleans only, so modes are paired booleans, e.g. `ent_gate_ai_shadow` and `ent_gate_ai_enforce`.
   - `GET /v1/feature-flags` exposes every flag name to authenticated users (`feature_flags.py:32-58`). Filter out the `ent_` prefix.
   - Every change to an `ent_*` flag raises an alert.

---

## 5. Data model

All migrations are generated with the CLI, are additive until P6, and have a valid `downgrade()` (`BE:AGENTS.md:25-29`). They go through the manual `migrate.yml` workflow in expand → migrate → deploy → contract order (`BE:docs/runbooks/schema-migration.md:18`).

CI builds the integration database with `Base.metadata.create_all` and then stamps Alembic; it does not run migrations (`BE:scripts/init_postgres_db.py:1-10,279-286`; `BE:tests/integration/postgres/conftest.py:28-34`; `ci.yml:129`). Therefore:
- Every partial index, CHECK and unique constraint below is also declared in ORM `__table_args__` (`postgresql_where=…`, `CheckConstraint`).
- Each table gets a migration test under `BE:tests/migrations/`.
- A parity test compares the migrated schema with the `create_all` schema for the new tables.

```sql
-- Identity: one user, many provider ids. Replaces write-only users.revenuecat_customer_id (BE:src/infra/database/models/user/user.py:21)
CREATE TABLE billing_customer_identities (
  id varchar(36) PRIMARY KEY,
  user_id varchar(36) NOT NULL REFERENCES users(id),
  provider varchar(32) NOT NULL,                 -- 'revenuecat' | later 'app_store','play','stripe'
  project varchar(64) NOT NULL DEFAULT 'default',
  provider_customer_id varchar(255) NOT NULL,    -- firebase uid, $RCAnonymousID:..., legacy lead UUID
  kind varchar(16) NOT NULL CHECK (kind IN ('primary','alias','anonymous','legacy_lead')),
  retired_at timestamptz,                        -- set on account deletion (D11)
  first_seen_at timestamptz NOT NULL, last_seen_at timestamptz NOT NULL,
  created_at timestamptz NOT NULL, updated_at timestamptz NOT NULL,
  CONSTRAINT uq_bci_provider_customer UNIQUE (provider, project, provider_customer_id)
);
CREATE UNIQUE INDEX uq_bci_one_primary ON billing_customer_identities (user_id, provider, project)
  WHERE kind = 'primary' AND retired_at IS NULL;
CREATE INDEX ix_bci_user ON billing_customer_identities (user_id);
-- Alias inserts use ON CONFLICT DO NOTHING + log; alias rows only route re-syncs, never grant access.

-- Inbox and sync job queue: generalises web_funnel_provider_events (BE:src/infra/database/models/web_funnel_claim.py:155-166)
CREATE TABLE billing_provider_events (
  id varchar(36) PRIMARY KEY,
  provider varchar(32) NOT NULL,
  provider_event_id varchar(255) NOT NULL,       -- RC event.id; if missing: 'sha256:'||hex(sha256(raw body))
  event_type varchar(64) NOT NULL,
  environment varchar(16),                       -- PRODUCTION | SANDBOX
  app_user_id varchar(255),
  event_ts timestamptz,                          -- event_timestamp_ms, audit only
  raw jsonb,                                     -- subscriber_attributes stripped; nulled after D9 window or on deletion
  user_id varchar(36) NULL REFERENCES users(id),
  status varchar(16) NOT NULL CHECK (status IN ('received','processing','processed','unmatched','retry','ignored','dead')),
  park_reason varchar(32),                       -- user_not_found | deleted_user | ...
  attempts int NOT NULL DEFAULT 0, next_attempt_at timestamptz,
  lease_owner varchar(64), lease_expires_at timestamptz, last_error text,
  received_at timestamptz NOT NULL, processed_at timestamptz,
  CONSTRAINT uq_bpe_event UNIQUE (provider, provider_event_id)
);
CREATE INDEX ix_bpe_due ON billing_provider_events (next_attempt_at) WHERE status IN ('received','retry');
CREATE INDEX ix_bpe_unmatched ON billing_provider_events (received_at) WHERE status = 'unmatched';
CREATE INDEX ix_bpe_user ON billing_provider_events (user_id, received_at);

-- Per store subscription, as reported by the provider's CURRENT snapshot.
CREATE TABLE store_subscriptions (
  id varchar(36) PRIMARY KEY,
  user_id varchar(36) NOT NULL REFERENCES users(id),
  provider varchar(32) NOT NULL,                 -- 'revenuecat'
  store varchar(24) NOT NULL,                    -- app_store|play_store|stripe|rc_billing|promotional|amazon|other
  environment varchar(16) NOT NULL CHECK (environment IN ('production','sandbox')),
  subscription_key varchar(255) NOT NULL,        -- derived ONLY from the fetched snapshot (see below)
  source_customer_id varchar(255) NOT NULL,      -- RC canonical original_app_user_id from the response
  store_identifier varchar(255),                 -- any store id the snapshot exposes (attribute, not key)
  original_transaction_id varchar(255),          -- attribute; filled when known, never part of the key
  product_id varchar(255) NOT NULL,
  entitlement_key varchar(64),                   -- from RC's own entitlements map; NULL = RC grants nothing for it
  ownership_type varchar(16),                    -- purchased | family_shared (D12)
  period_type varchar(16),                       -- trial|intro|normal|promotional|prepaid
  state varchar(16) NOT NULL CHECK (state IN ('trialing','active','in_grace','billing_retry','paused','expired','refunded','revoked','absent')),
  will_renew boolean, is_lifetime boolean NOT NULL DEFAULT false,
  purchased_at timestamptz, expires_at timestamptz, grace_expires_at timestamptz,
  unsubscribe_detected_at timestamptz, billing_issue_detected_at timestamptz, refunded_at timestamptz,
  absent_since timestamptz,                      -- set when the customer's snapshot stops listing it (transfer etc.)
  fetched_at timestamptz NOT NULL,               -- snapshot time; older snapshots never overwrite newer
  last_synced_at timestamptz NOT NULL, version int NOT NULL DEFAULT 1,
  created_at timestamptz NOT NULL, updated_at timestamptz NOT NULL,
  CONSTRAINT uq_ss_key UNIQUE (provider, environment, subscription_key)
);
CREATE INDEX ix_ss_user ON store_subscriptions (user_id);
CREATE INDEX ix_ss_customer ON store_subscriptions (provider, source_customer_id);
CREATE INDEX ix_ss_expiry ON store_subscriptions (expires_at);

-- Manual/own grants: append-only, revoke-only. Store-derived access is NOT copied here.
CREATE TABLE entitlement_grants (
  id varchar(36) PRIMARY KEY,
  user_id varchar(36) NOT NULL REFERENCES users(id),
  entitlement_key varchar(64) NOT NULL DEFAULT 'standard',
  source varchar(16) NOT NULL CHECK (source IN ('admin','support','qa','migration')),
  source_ref varchar(255),
  reason varchar(255) NOT NULL,
  starts_at timestamptz NOT NULL,
  ends_at timestamptz NOT NULL CHECK (ends_at <= starts_at + interval '366 days'),  -- no lifetime grants in NM-680 (D5)
  revoked_at timestamptz, revoke_reason varchar(255),
  created_by varchar(255) NOT NULL,              -- admin email from hardened require_admin (D7)
  rc_mirror_ref varchar(255),                    -- RC promotional grant id while mirrored (D5)
  created_at timestamptz NOT NULL
);
CREATE UNIQUE INDEX uq_eg_source ON entitlement_grants (source, source_ref, entitlement_key) WHERE source_ref IS NOT NULL;
CREATE INDEX ix_eg_user_live ON entitlement_grants (user_id, entitlement_key) WHERE revoked_at IS NULL;

-- Projection: O(1) reads for app + gates
CREATE TABLE user_entitlements (
  user_id varchar(36) NOT NULL REFERENCES users(id),
  entitlement_key varchar(64) NOT NULL,
  has_access boolean NOT NULL,     -- as of computed_at; readers re-check access_until/next_change_at
  state varchar(20) NOT NULL,      -- trial|active|grace|cancelled_active|expired|none
  access_until timestamptz, is_lifetime boolean NOT NULL DEFAULT false,
  next_change_at timestamptz,      -- earliest future expiry/grace end/grant start/grant end
  will_renew boolean, period_type varchar(16),
  primary_source varchar(8) NOT NULL, primary_ref varchar(255),   -- 'store'|'grant'
  product_id varchar(255), store varchar(24), is_sandbox boolean NOT NULL DEFAULT false,
  ever_had_access boolean NOT NULL DEFAULT false,
  version bigint NOT NULL, computed_at timestamptz NOT NULL, last_provider_sync_at timestamptz,
  last_purchase_hint_at timestamptz,
  PRIMARY KEY (user_id, entitlement_key)
);
CREATE INDEX ix_ue_next_change ON user_entitlements (next_change_at);
```

**Subscription key.** The key is derived only from the fetched snapshot, so the webhook path, `/sync`, the reconciler and the backfill all compute the same key. Payload fields are never used.
- **RC v1:** `rc:{subscriber.original_app_user_id}:{product_id}`. RC v1 lists subscriptions per product. The canonical `original_app_user_id` is already read at `BE:src/api/routes/v1/web_funnel.py:414`. Including it is required so that two users who each bought the same product do not collide. Upgrades within a subscription group appear as separate products, so they do not share a key.
- **RC v2 (if D10 picks v2):** the subscription's store identifier.
- `original_transaction_id` and any store id stay non-key attributes.
- Before writing, the normalizer de-duplicates snapshot entries by key across every customer fetched for a user (primary plus legacy ids). Each legacy fetch is accepted only when its `original_app_user_id` matches, using the same check finalize does (`web_funnel.py:402-427`).

**Snapshot replace.** Each sync covers one `(user, provider, canonical customer)`:
- Rows previously sourced from that customer that are missing from the new snapshot become `state='absent'`, with no access.
- On a key conflict with another user, the row moves to the user whose customer reports it now. The previous owner's projection is recomputed in the same transaction, with both users locked in `user_id` order.
- A snapshot whose `fetched_at` is older than the row's `fetched_at` is discarded.

**Entitlement mapping.** `entitlement_key` comes from RC's own `entitlements` object: `entitlements[k].product_identifier`, `expires_date` and `grace_period_expires_date`. Today's code trusts the same source (`revenuecat_adapter.py:73-95`; `BE:src/app/services/web_funnel_claim_common.py:44-56`; `BE:src/app/services/web_funnel_redemption_verification.py:35-37`).
- An active RC entitlement with no matching subscription entry is normalized as a `store='promotional'` row. Examples are dashboard promotional grants and temporary entitlement grants *(RC docs, unverified)*.
- No `product_catalog` table is part of the NM-680 slice. New offerings appear without a backend deploy: PostHog payloads choose offerings (`APP:lib/features/subscriptions/application/providers/paywall_annual_trial_test_provider.dart:13-26`); the discount flow uses separate products (`APP:lib/features/subscriptions/application/providers/discount_offer_provider.dart:57-61`); retired offerings still have renewing subscribers (`APP:lib/features/subscriptions/application/utils/paywall_pricing_payload_cache.dart:27-30`). A catalog the backend owns would lock out buyers of any product that was not seeded.
- The catalog becomes a P7 deliverable, needed only when RC's mapping goes away.
- The hard-coded `'standard'` sites to consolidate behind one constant are `revenuecat_adapter.py:77`, `web_funnel_claim_common.py:47,62`, `web_funnel_redemption_verification.py:35` and `web_funnel.py:284`.

**Access rule.** It is implemented once, in `EntitlementService` and the projection recompute, and **evaluated at read time**:

```
has_access(u, key, now) =
     ∃ grant g: g.revoked_at IS NULL AND g.starts_at <= now AND now < g.ends_at
  OR ∃ store_subscription s (s.user_id = u, s.entitlement_key = key):
        s.state NOT IN ('paused','refunded','revoked','absent')
        AND (s.is_lifetime OR now < GREATEST(s.expires_at, COALESCE(s.grace_expires_at, s.expires_at)))
        AND (s.environment = 'production' OR policy.sandbox_grants_access(u))   -- D3
```

- The stored `has_access` is a cache. A reader returns `has_access AND (is_lifetime OR access_until > now)`.
- When `now >= next_change_at`, the reader recomputes inline before answering. This is a DB-only operation with no RC call.
- The reconciler is needed only for side effects, not for correctness of reads.
- `will_renew`, `unsubscribe_detected_at` and `billing_issue_detected_at` are recorded but never remove access. `billing_retry` after grace ends has no access *(store docs)*.

**Jobs.**
- The inbox (`billing_provider_events`) doubles as the sync job queue.
- Side-effect jobs use `outbox_events`, which has lease, retry and dead-letter columns (`migrations/versions/20260822000001_create_outbox_events_table.py:33-87`), but only after a P0 audit of existing rows (§9.1).
- The dispatcher claims only an allow-list of new event types. Legacy rows of retired types (push, account cleanup, affiliate; `ADR-cron-jobs-removal.md:37-47`) are archived and never dispatched.
- The table needs a new ORM model and repository.

**Legacy `subscriptions`.**
- Keep it, dual-written, until P6. `promo_code_redemptions.subscription_id` has a foreign key to it (`BE:migrations/versions/20260515000000_add_promo_code_tables.py:37`).
- In P6, rows to be deleted go to an archive table first (§9.4). Drop the duplicate index on `revenuecat_subscriber_id` (`subscription.py:28,62`) and fix the docstring at `:14-18`.

**Retention and deletion (D9, D11).**
- The new tables reference `users(id)`. Users are only soft-deleted (`delete_user_command_handler.py:85-91`), so the FKs need no `ON DELETE`.
- The deletion flow retires identities, nulls `raw` on the user's inbox rows, and stops syncing.

---

## 6. Sync design

### 6.1 Webhook hardening (`BE:src/api/routes/v1/webhooks.py:102-250`)

1. **Auth (P0).**
   - `REVENUECAT_WEBHOOK_SECRET` is already declared in settings and `.env.example` (`BE:src/infra/config/settings.py:236`; `BE:.env.example:217`), but the route reads `os.getenv` (`webhooks.py:111,119`). Add only `REVENUECAT_WEBHOOK_AUTH_HEADER` to both files.
   - Switch the route, `revenuecat_adapter.py:29` and `base_dependencies.py:738` to read from settings.
   - Keep the existing precedence (AUTH_HEADER over SECRET, `webhooks.py:111-127`) and add a test for it.
   - Accept two values during a secret rotation.
   - Verify HMAC over the raw body if our RC dashboard offers it *(RC docs, unverified; header name unconfirmed)*.
2. **Idempotency.** `INSERT … ON CONFLICT (provider, provider_event_id) DO NOTHING`.
   - A duplicate returns 200 immediately **only if the existing row is `processed` or `ignored`**. Otherwise the event is reprocessed, which is safe because sync is based on a re-fetch.
   - **In P1, the native-path inbox row is written inside the legacy unit of work** (`webhooks.py:178`). A legacy failure then rolls back the inbox row too, the route returns 500, and RC retries exactly as today (rollback at `uow_async.py:175-211`). Referral credit (cash, `request_payout_handler.py:49-67`) and lifecycle events cannot be lost to an early 200.
3. **Ordering and fetching.** Payload fields are never used as state. For each event:
   1. Resolve the user (active users only, §6.4).
   2. Acquire `RcBudget` and fetch `GET /v1/subscribers/{primary firebase_uid}` (plus legacy ids) **outside any DB transaction**.
   3. Open a short transaction: take the advisory lock, apply the snapshot only if `fetch_started_at > last_provider_sync_at`, recompute the projection, write the outbox rows, commit.

   Out-of-order or replayed events become harmless. `event_ts` is for audit only.
4. **Response.**
   - **P1** (no worker yet): after the legacy UoW commits, run SyncService inline in its own transaction with a 3 s budget. Failures are logged and counted, with no retry, because legacy is still the truth in P1.
   - **P2 onward:** commit the inbox row, try the sync inline, return 200. The worker re-syncs at +5 s, because RC staff advise waiting 1–2 s to avoid stale reads *(unverified)*, and handles anything unfinished.
   - Back off on 429 and code 7638.
   - RC's 60 s timeout is not at risk *(RC docs: https://www.revenuecat.com/docs/integrations/webhooks)*.
5. **Unknown and deleted users.**
   - Park the row as `unmatched` (`park_reason`). Lookup resolves only `is_active` users; today it does not filter (`webhook_lookup_parsing.py:45-47`).
   - Replay parked rows when `/v1/users/sync` creates or links the user, and in the reconciler sweep.
   - **Return 200 for unknown users only while `ent_reconcile` is on.** While it is off, keep the current 404 (`webhooks.py:212-214`), so RC retries remain the recovery path.
6. **Web-funnel stages** (`:144-165`) stay as they are, but also enqueue a sync for any resolved user. Move the UUID pre-fetch (`:151-160`) after the lead-existence check: v1 GET is get-or-create, so the current order can create phantom RC customers *(RC docs: https://www.revenuecat.com/docs/api-v1/customers.md)*.
7. **Side effects.** Exactly one owner exists for each flag state:

   | Side effect | `ent_ledger_write` off | P1–P5 (ledger on) | P6 |
   |---|---|---|---|
   | Referral credit | Legacy, on row insert (`webhook_subscription_lifecycle.py:124`) | Legacy | Ledger: first transition to a paid (non-trial) period; sandbox excluded |
   | Referral revoke | Legacy `REFUND` branch (never fires, §2.2 item 10) | Legacy | Ledger: projection sees `refunded`; re-credit on REFUND_REVERSED |
   | `subscription.lifecycle.v1` and affiliate events | Legacy best-effort publisher (`:125-141`) | Legacy publisher, kept until the P2 dispatcher is live | Outbox after commit, emitted only for transitions the re-fetch confirmed |
   | Trial-expiry notification purge | Legacy, with a SAVEPOINT from P0 | Legacy | Ledger: when `access_until` increases, inside `begin_nested()` |

   - The outbox stores the event id as computed by `_subscription_event_id()`: the RC event id when it is UUID-shaped, else uuid5, else uuid4 (`webhook_subscription_lifecycle.py:52-59`). Retries reuse the stored id.
   - Downstream consumers are the sibling `nutreeai_async` repository and `nutree-affiliate` (`BE:docs/external-services.md:30,76`; `BE:src/app/events/affiliate/affiliate_events.py:19-23`). Whether they dedupe on this id is **unverified**; a P0 discovery task reads both consumers.
   - Test: flipping `ent_ledger_write` both ways produces exactly one of each effect.
8. **Sandbox.** Store `environment`. Policy D3 decides access. Sandbox is always excluded from referral credit, lifecycle and revenue events.

### 6.2 Fast path after purchase: `POST /v1/me/entitlements/sync`

- **When the app calls it:**
  - with a `client_hint` {kind: purchase|restore|redeem, product_id, transaction_id}: after `Purchases.purchase`, a user-tapped restore (including `one_time_offer_screen.dart:993-1002`) and `redeemWebPurchase`;
  - without a hint: after `Purchases.logIn`, which replaces restore-on-build (§8.2), on resume, and when the RC CustomerInfo listener reports a change.
- **Unhinted calls** return the projection without re-fetching unless `last_provider_sync_at` is older than 15 min. They use RC lane 4.
- **Hinted calls** always re-fetch (lane 1) and set `last_purchase_hint_at`.
  - A negative result from a hinted call is **never cached**.
  - The backend also enqueues re-syncs at +5 s and +30 s.
  - `client_hint` is logged and never trusted as state.
- **Rate limit.**
  - 6 per minute per user (the limiter key is the token `sub`, `BE:src/api/middleware/rate_limit.py:14-34,42`), plus a per-IP limit.
  - Over the limit, the endpoint returns the current projection without re-fetching.
  - The global RC budget applies on top.
- **RC unavailable or rate-limited.** Returns 200 with the last known state, `stale: true` and `sync_error`. The app keeps its optimistic window open while the answer is stale (§8.2).

### 6.3 Reconciler (worker, P2) and RC budget

- **Every 15 min:**
  - inbox rows that are due (`received` or `retry`);
  - `unmatched` rows whose candidate ids now resolve;
  - users whose `next_change_at` has passed (side effects only, because reads are correct without this);
  - users with `has_access = true` and `last_provider_sync_at` older than 24 h;
  - a duplicate-active check: one store identifier, when the snapshot exposes one, active on more than one user.
- **Nightly:** compare the full projection against the RC Scheduled Data Export (`effective_end_time > now`), if our plan includes exports (§9.1). Otherwise re-sync a random sample. Emit drift metrics.
- **Budget.**
  - v1 has no published limit, and RC staff suggest about 1 req/s *(RC community thread 4946; unverified)*.
  - v2 publishes 480/min for customer information *(RC docs: https://www.revenuecat.com/docs/api-v2/subscription.md)*. That feeds decision D10.
  - Before the P4 ramp, size the expected requests per second from DAU, cold starts and resumes. Today `restorePurchases()` runs from the device on every provider build, using RC's SDK path and not our key (`subscription_state_provider.dart:131-134`). After P4 those calls move to our key.
- **Recovery.** RC retries 5 times at 5/10/20/40/80 min. Failed webhooks can be resent from the RC dashboard once our server is fixed *(RC docs: https://www.revenuecat.com/docs/integrations/webhooks)*. This is recorded in the runbook as the recovery step.
- **Locking.** Jobs are claimed with `FOR UPDATE SKIP LOCKED` and a lease. That stays safe if an in-process loop later runs under the 4 uvicorn workers.

### 6.4 Identity, alias, transfer and deletion

- **Canonical key.** `users.id`. The RC primary id is `users.firebase_uid`, with one `primary` row per user. `users.id` is never sent to RC.
- **Seeding.** `/v1/users/sync` upserts the primary identity.
- **Resolution order** stays as in `BE:src/api/routes/v1/webhook_lookup_parsing.py:14-31,42-80`, with two changes: `billing_customer_identities` is checked first, and only active users are returned.
- **Aliases.** Every `aliases[]`, `original_app_user_id`, `$RCAnonymousID:*`, redemption `provider_app_user_ids` and legacy lead UUID is inserted with `ON CONFLICT DO NOTHING`, and each conflict is logged. An alias only routes re-syncs. It never grants access, and an alias conflict can never roll back a sync.
- **TRANSFER.** Re-sync every user resolved from `transferred_from` and from `transferred_to`. Snapshot replace (§5) marks the source's rows `absent`. If the TRANSFER event is lost, the 24 h re-sync of `has_access` users and the duplicate-active check catch it.
- **Legacy lead-UUID customers.** `users.revenuecat_customer_id` was written as `lead.id` (`BE:src/app/services/web_funnel_claim_completion.py:94`). SyncService also fetches every `legacy_lead` identity, accepts only snapshots whose `original_app_user_id` matches, and de-duplicates by key.
- **Family Sharing.** Not modelled anywhere today: a grep for `ownership_type|family` over both codebases finds nothing. The key includes the canonical customer, so family members cannot collide. Whether family-shared purchases grant access is decision D12. Add a fixture for it.
- **Account deletion (D11).**
  - Deletion retires the identities (`retired_at`), stops syncing, and keeps `store_subscriptions` for audit, out of all reads.
  - Webhooks for deleted users are parked with `deleted_user`, and never credit referrals.
  - Re-registration creates a new user with the same Firebase UID (`sync_user_command_handler.py:64-76`); the new user gets a fresh `primary` identity because of the `retired_at` filter on `uq_bci_one_primary`. How that interacts with `users.firebase_uid UNIQUE` (`user.py:20`) is unverified.
  - The decision also covers whether to call RC's delete-customer API *(unverified)*.
- **Groundwork for leaving RC (P7).** Check whether `purchases_flutter` 10.10.1 (locked at `APP:pubspec.lock:1678-1685`) can set Apple `appAccountToken` = `users.id` (already a uuid4, `BE:src/infra/database/models/base.py:18`) and Play `obfuscatedAccountId` = a hash of `users.id`. This is unverified, and the app sets neither today.

---

## 7. API contract and backend enforcement

### 7.1 `GET /v1/me/entitlements`

- **Auth:** `get_current_user_id`, which returns `users.id` and blocks inactive users (`BE:src/api/dependencies/auth.py:208-328`).
- **Caching:**
  - `ETag: W/"{user_id}:{version}:{next_change_at_epoch}"`. `If-None-Match` returns 304 only while `now < next_change_at`.
  - `Cache-Control: private, no-cache`.
  - The app's owner-scoped `ConditionalGetInterceptor` can be extended to this path (`APP:lib/core/network/interceptors/conditional_get_interceptor.dart:9-14`).

```json
{
  "schema_version": 1,
  "authority": "backend",
  "version": 42,
  "server_time": "2026-10-15T08:00:00Z",
  "has_access": true,
  "state": "cancelled_active",
  "ever_subscribed": true,
  "entitlements": {
    "standard": {
      "active": true,
      "state": "cancelled_active",
      "access_until": "2026-11-01T00:00:00Z",
      "will_renew": false,
      "period_type": "normal",
      "product_id": "nutree_2999_1y_0",
      "store": "app_store",
      "source": "store",
      "is_sandbox": false
    }
  },
  "synced_at": "2026-10-15T07:59:58Z",
  "stale": false,
  "sync_error": null,
  "ttl_seconds": 300
}
```

- **States:** `trial | active | grace | cancelled_active | expired | none | unknown`.
  - `unknown` is returned when no projection row exists or the user has never been synced. The app treats it as "use the RC fallback".
  - Mapping in the app: `grace` → `gracePeriod` (has access); `cancelled_active` → `active` with `willRenew=false`; `expired` and `none` stay as they are.
- **`authority`.** It is `"rc"` while the server kill switch `ent_read_api_defer` is on. Clients then decide from RC, so a P4 rollback does not depend on PostHog reaching every device.
- **`ever_subscribed`.** Keeps the router's split between `none` (hard paywall) and `expired` (lapsed lifestyle allowance), at `app_router_redirect.dart:186-192,270-281,338-356`.
- **Entitlement key** stays `standard`, matching RC and every existing check.
- **`POST /v1/me/entitlements/sync`** returns the same body (§6.2).
- **`POST /v1/web-funnel/redemptions/finalize`** also returns this body under `entitlements`.
- **Contract test.** The exact JSON is pinned in a contract test shared with the Flutter Freezed DTO.

### 7.2 Enforcement on paid endpoints (P5, separate ticket)

- **`require_entitlement("standard")`** is a FastAPI dependency built on `get_current_user_id`, not on `request.state.user`. Delete `BE:src/api/middleware/premium_check.py` **and** `BE:tests/unit/api/test_premium_middleware.py` in P0.
- **Response on deny.** A new `SubscriptionRequiredException` is mapped to 402 inside the existing envelope:
  - shape: `{"detail": {"error_code": "SUBSCRIPTION_REQUIRED", "message": …, "details": …}}`;
  - `create_http_exception` has no 402 mapping today (`BE:src/api/exceptions.py:96-118`; `BE:src/api/exception_handlers.py:35-47`).
- **Mode** per route group, through the new flag reader: off, shadow (log would-deny), or enforce.
- **Verify-on-deny.** When the projection says no, run an on-demand sync (lane 3, 2 s budget) if either:
  - `last_provider_sync_at` is older than 10 min, or
  - `last_purchase_hint_at` is less than 15 min old (throttled to 1 per 30 s per user).

  "No" results are cached for 10 min, except during a purchase-hint window.
- **Failure policy.**
  - **Internal error** (our bug or DB): allow the request, emit `entitlement.gate.fail_open`, and alert on the rate.
  - **RC unavailable or rate-limited:** use the projection as it stands. Fail open only for users with `ever_had_access` whose `access_until` is within the last 72 h, or who have a purchase hint less than 15 min old. **Everyone else fails closed.** RC brownouts and 429 storms then cannot unlock paid features for free users.

| Feature group | Server gate | On our internal error | On RC outage or 429 | App when backend unreachable |
|---|---|---|---|---|
| AI cost: image analyze, scan-by-url, label scan, parse-text, chat, meal suggestions and recipes, 3-day recommendations, meal plans, ingredient recognition | `require_entitlement`, shadow then enforce | Fail open + metric | Projection decides; narrow fail-open as above | Cached backend answer (§8.2) |
| `POST /v1/progress/recap` (LLM; called from the free `/progress` route) | **BA decision** | – | – | – |
| Manual meal create (`POST /v1/meals/manual`) and catalog meal log (`POST /v1/meal-catalog/{id}/log`), both client-gated only | **BA decision** | – | – | – |
| Barcode, food search, recaps | **BA decision**. The client gates the food search and barcode routes; it is unclear whether free flows also call these endpoints | – | – | – |
| Chat daily budget | Unchanged (40 per UTC day) for entitled users; non-entitled users get 402 before the budget check | – | – | – |
| Hydration, movement, walking | No server gate; the lapsed allowance is a client concern | – | – | – |
| Guest parse trial (pre-login) | Unchanged; separate endpoint (`meals_manual_text.py:339-345`) | – | – | – |
| Web funnel endpoints | Unchanged (their own RC verification) | – | – | – |

**Inventory method (P5 discovery).** Map every client gate to the backend endpoints behind it: the router's `subscriptionRoutes` plus the 34 widget call sites. Add every handler that calls AI.

**Old builds.** They do not understand 402 (§8.3). Enforcement is therefore gated on the `X-Client-Caps` header (§8.2), not only on store version.

---

## 8. App changes (nutree_ai)

### 8.1 Stays on the RC SDK

- `configure`, `logIn` and `logOut`
- offerings and offering metadata
- `Purchases.purchase` (`paywall_provider.dart:753`, `discount_offer_provider.dart:240`, `multi_step_paywall_screen.dart:1341,1411`)
- user-tapped restore, required by App Store guideline 3.1.1; automatic restore does not satisfy it
- the offer-code sheet
- `redeemWebPurchase`
- Airbridge and RC attributes (`revenuecat_service.dart:325-356`)
- listener-driven SKAN and lifecycle analytics
- the pre-ATT identity check, which compares `Purchases.appUserID` with the Firebase UID and is not an entitlement read (`APP:lib/features/onboarding/presentation/screens/pre_att_screen.dart:195-204`)

### 8.2 Moves to the backend

The switch is the PostHog flag `entitlement_source = rc | shadow | backend`.
- It **defaults to `rc`** when the flag cannot be evaluated. Live flag evaluation can be unavailable at startup, which is why a payload cache already exists (`paywall_pricing_payload_cache.dart:18-21`).
- The last evaluated value is persisted per install and applied only at the next cold start, so a session never flips source midway.
- Every entitlement decision logs the source in effect.

1. **Version and capability header (P0).** A Dio interceptor sends three headers on every request:
   - `X-App-Version` from `package_info_plus`: already a dependency (`APP:pubspec.yaml:131`) and already used for Sentry (`APP:lib/main.dart:319`);
   - `X-Platform`;
   - `X-Client-Caps`: a Dart constant such as `entitlements-v1,http-402`.

   Remove or fix the `'2.8.3'` constant (`nutrition_contract_headers.dart:9-12`).
   - Shorebird patches change Dart code but not the store version (`APP:docs/runbook.md:94-113`), and the app has no `shorebird_code_push` dependency to read the patch number. Changing that would mean adding a native plugin and a store release.
   - The capability constant **can** ship in a Shorebird patch. Server gating keys on `X-Client-Caps`, not on version.
2. **Equality (P0).** Convert `Subscription` to a Freezed value class, or extend `==` and `hashCode` to `willRenew`, `expirationDate`, `accessUntil` and the backend `version` (`subscription.dart:132-141`). Add a test that the provider notifies on a `willRenew`-only change. Run `build_runner`.
3. **Entitlement reads (P3 shadow, P4 decide).** `SubscriptionStatus.build` (`subscription_state_provider.dart:81-221`) reads `GET /v1/me/entitlements` through a Freezed DTO and a new `Subscription.fromBackendJson`, next to `fromCustomerInfo`. `unknown`, or `authority: "rc"`, means use the RC path.
4. **Optimistic window.** Only the results of purchase, restore and redeem may grant access optimistically. This generalises the existing 30 s in-memory web grant (`:53-59`).
   - The window lasts 120 s and ends early when the backend confirms.
   - It **stays open (and persisted) while the backend answers `stale:true` or `sync_error`**, bounded by the RC purchase result's own expiry.
   - Ordinary listener or cache updates never grant access; they only trigger `/sync`.
5. **Asymmetric false-negative guard (P4 to P6 only, flag-controlled).** If the backend says no access but RC CustomerInfo shows `standard` active:
   - grant access locally;
   - call `/sync`;
   - log `entitlement_backend_false_negative`.

   This is a deliberate, temporary exception to item 4. Without it, the hard paywall for `none` (`app_router_redirect.dart:186-192`) would trap a payer during the ramp. It is removed in P6.
6. **Offline cache.**
   - Store the last backend answer in `AccountPreferences`, which is owner-scoped and deny-read until bound (`APP:lib/core/storage/account_preferences.dart:8-13`), and clear it in `LogoutService` (`APP:lib/features/auth/domain/services/logout_service.dart:112-131`).
   - Compute expiry against `server_time` plus the device offset, with a maximum offline window of 72 h, so setting the device clock back does not extend access.
   - During P4 and for 2 releases after, fall back to RC SDK CustomerInfo only when there is no backend answer and no cache.
7. **402 interceptor (P5 client half).**
   - Single-flight.
   - On 402: invalidate the provider, call `/sync` once, and retry the original request once if the answer is now `has_access=true`.
   - Push the paywall only for user-initiated calls.
   - The coach chat uses its own streaming Dio request (`APP:lib/features/coach/data/repositories/http_coach_repository.dart:150-170`) and the photo-upload path has its own progress handling. Both need explicit 402 handling and are sized in §11.
8. **Restore-on-build.** The current `restorePurchases()` call in build was a deliberate fix for stale entitlements after the anonymous-to-identified transfer (`subscription_state_provider.dart:131-134`). Replace it with `logIn` followed by `POST /sync`, plus a test for "anonymous purchase, then login". Ship it only with the backend flag, and after D6.
9. **Grace access.** Grace now grants access, which fixes `subscription.dart:103-104`. The entitlement checked is `standard`, not `values.first` (`:86-96`).
10. **Direct RC reads.** Route only `timezone_monitor.dart:110-112` through the provider.
    - `one_time_offer_screen.dart:993-1002` is a user-tapped restore. It already flows into the provider through `applyPurchaseCustomerInfo`; add a hinted `/sync` after it.
    - The pre-ATT identity check stays on RC (§8.1).
11. **Web redemption.** `_completeActivation` uses the `entitlements` returned by finalize instead of the best-effort 8 s RC refresh (`web_purchase_redemption_coordinator.dart:991-994`).
12. **Refresh on resume,** respecting `ttl_seconds`.
13. **Trial eligibility stays on the device.** Consider RC's `checkTrialOrIntroductoryPriceEligibility` instead of `entitlements.all.isEmpty` (`revenuecat_service.dart:745`).
14. **Bug fix (P0).** Re-register the RC listener after logout and login in the same process (`revenuecat_service.dart:767-770`; `main.dart:614-640`).
15. **Settings "won't renew" and grace UI** (BA AC2 and AC3). Not present today: `willRenew` is read only by notifications, Live Activities and the entity. This needs new strings in all 7 locales (`APP:lib/core/l10n/app_*.arb`) plus `flutter gen-l10n`.
16. **QA in staging.** The app's `bypassSubscription` dev guard (`subscription_state_provider.dart:447-461`) does not reach a backend gate. The backend dev bypass needs `ENVIRONMENT=development` and `ENABLE_DEV_AUTH_BYPASS=1` (`BE:src/api/middleware/dev_auth_bypass.py:155-163`). For staging QA, use `qa` grants through the admin endpoint, with a runbook.
17. **Docs.** Update `APP:docs/contracts.md:134-136,143-152,233-238`, which currently say RC is the commercial truth.

### 8.3 Old app versions and Shorebird

- Old builds keep deciding locally from the RC SDK, so their UI is unaffected until P5.
- Dart-only changes can reach existing releases as Shorebird patches (`APP:shorebird.yaml:8-12`; `APP:docs/runbook.md:94-113`). That applies to the header interceptor, listener re-registration, equality, shadow reads and the 402 interceptor. Native code, plugins and assets need a store release.
  - **Plan:** ship P0 app fixes and P3 shadow reads as patches to eligible releases where the release owner agrees. Changes that need new plugins go to the store.
- **Enforcement (P5)** keys on `X-Client-Caps` containing `http-402`, not on `X-App-Version`. Raise `ForceUpdateService` min-version as a backstop only: it fails open, with a `1.0.0` fallback (`APP:lib/core/services/force_update_service.dart:43-57`).

---

## 9. Migration, backfill and rollout

### 9.1 Read-only production checks and discovery (P0)

These block the schema design:

```sql
-- duplicates per subscriber / per user
SELECT revenuecat_subscriber_id, count(*) FROM subscriptions GROUP BY 1 HAVING count(*) > 1;
SELECT user_id, count(*) FROM subscriptions WHERE status='active' GROUP BY 1 HAVING count(*) > 1;
-- anonymous / lead-UUID keys, sandbox rows
SELECT count(*) FROM subscriptions WHERE revenuecat_subscriber_id LIKE '$RCAnonymousID:%';
SELECT count(*) FROM users WHERE revenuecat_customer_id ~ '^[0-9a-f-]{36}$';
SELECT is_sandbox, status, count(*) FROM subscriptions GROUP BY 1,2;
-- backfill size
SELECT count(*) FROM users WHERE is_active;
-- legacy outbox contents before reviving it
SELECT event_type, status, count(*), min(created_at) FROM outbox_events GROUP BY 1,2;
-- real column types (007 declares named enums; 053 asserts VARCHAR)
SELECT column_name, data_type, udt_name, character_maximum_length
FROM information_schema.columns WHERE table_name='subscriptions';
```

Also confirm the following:
- **RC dashboard:** which auth variable is live; whether sandbox events go to the production URL; restore/transfer behaviour; whether HMAC is available; TEMPORARY_ENTITLEMENT_GRANT opt-in.
- **RC plan:** whether it includes Scheduled Data Exports. They need post-Sept-2023 pricing, legacy Grow/Pro, or Enterprise *(RC docs: https://www.revenuecat.com/docs/integrations/scheduled-data-exports)*.
- **RC v1 response shape:** capture real responses for trial, cancelled, grace, billing retry, refunded, promotional, transferred, aliased legacy lead, an upgrade within a group, and family shared. These record which store identifiers, `ownership_type` and `original_app_user_id` v1 exposes.
- **Firebase:** whether Email/Password sign-up is enabled in production (D7).
- **TestFlight and flavors:** whether a public TestFlight link exists, and how app flavors map to RC projects and backends (D3).
- **Downstream consumers:** read `nutreeai_async` and the `nutree-affiliate` consumers to learn their dedupe keys (§6.1.7).
- **Load:** expected RC requests per second from PostHog DAU, cold starts and resumes (§6.3).

### 9.2 Backfill (P2)

1. **Candidate set.**
   - Every non-deleted `users.firebase_uid`. Every signed-in user already calls `Purchases.logIn` (`auth_repository.dart:598-601`; `main.dart:655`), so an RC customer already exists and get-or-create is not a concern.
   - Every lead UUID or `$RCAnonymousID` from `users.revenuecat_customer_id`, `subscriptions.revenuecat_subscriber_id` and the web-funnel redemptions. These are queried only when they are known ids, because v1 GET is get-or-create.
   - The RC export, if available, is used only as an independent cross-check, not as the candidate source.
2. **Script.** `BE:scripts/backfill_entitlement_ledger.py`:
   - runs the same SyncService as production;
   - uses RC lane 6 of the global budget;
   - is resumable through a checkpoint table;
   - `--dry-run` writes a diff report and nothing else;
   - runs with all side effects off: no queue, no referral, no notifications.

   Run time is about the active-user count divided by the budget; for example, 100k users at 1 req/s is about 28 h.
3. **Verify.** Every export row with `effective_end_time > now` must map to a user with `has_access=true` *(RC docs: https://www.revenuecat.com/docs/integrations/scheduled-data-exports/data-export-version-3)*. Rows that cannot be mapped are reported separately. **Any user active in RC but not in our DB blocks P3.**

### 9.3 Shadow and compare

- **Server (P1 onward).**
  - Every inline sync compares the projection's `has_access` with RC's `entitlements.standard` in the same fetch.
  - From P2, a daily full comparison runs against the export, or a large re-sync sample if exports are unavailable.
  - Emits `entitlement.mismatch{direction}`.
- **App (P3).**
  - The app fetches the backend answer but decides with RC.
  - It compares backend `has_access` with **raw** RC `entitlements.active['standard'] != null`, not with the app's mapped status.
  - Expected differences are bucketed separately: grace (the app maps it to `billingRetry`), sandbox, `standard` versus `values.first`, legacy-lead merges, and an open optimistic window.
  - Logs `entitlement_shadow_mismatch` to PostHog.

### 9.4 Phases, flags, exit gates, rollback

| Phase | Content | Switch | Exit gate | Rollback |
|---|---|---|---|---|
| P0 | §9.1 checks and discovery; typed errors in the new port only (legacy `None` shim kept); delete premium_check and its test; `REVENUECAT_WEBHOOK_AUTH_HEADER` into settings plus a precedence test; SAVEPOINT for the purge; **`webhook.revenuecat.received` and non-2xx metrics plus a 401/5xx alert, deployed before the auth refactor**; app: header interceptor, listener re-registration, `Subscription` equality | none | Checks answered; metrics live; v1 fixtures captured | Revert deploy; resend failed webhooks from the RC dashboard |
| P1 | Expand migrations with ORM parity and migration tests; port and RC normalizer; SyncService (fetch outside the transaction; RcBudget); projection with read-time rule; inbox row inside the legacy UoW; inline sync after the legacy commit; response-derived keys; snapshot replace | `ent_ledger_write` | Over 7 days: inline sync success ≥ 99 %; on each inline fetch, projection `has_access` equals RC `entitlements.standard`, with every disagreement explained; alias conflicts reviewed | Flag off: legacy path only |
| P2 | **Prerequisite: hardened `require_admin` (D7).** Worker host (D2); inbox retry; reconciler; parked replay; outbox dispatcher (allow-listed types; legacy publisher retired here); 404→200 tied to `ent_reconcile`; backfill; `GET /v1/me/entitlements` and `/sync`; `/users/sync` and `GET /users/firebase/{uid}` read the projection; finalize returns entitlements; admin grants (with RC mirror per D5) | `ent_reconcile`, `ent_read_api`, `ent_read_api_defer` | 7 days with zero "RC active / DB inactive" in the daily **full** comparison | Flags off: old reads; 404 for unknown users returns automatically; replay parked rows (runbook) |
| P3 | App shadow mode, preferably via Shorebird patch where allowed; DTO; `timezone_monitor` routed through the provider | PostHog `entitlement_source=shadow` (default `rc`, sticky per cold start) | Mismatch against raw RC `standard` < 0.1 % of sessions for 7 days, intended-difference buckets excluded; every remaining mismatch explained | Flag `rc` |
| P4 | App decides from the backend: optimistic window, asymmetric guard, offline cache in `AccountPreferences`, restore-on-build replacement, Settings "won't renew" (7 locales) | PostHog `backend` at 5/25/50/100 %; server `ent_read_api_defer` as the fast switch | 2 weeks with zero unexplained `entitlement_backend_false_negative` and zero access tickets in the support channel the BA names. **NM-680 Done** | `ent_read_api_defer` on (server-side, immediate) or flag `rc`; RC path kept 2 releases |
| P5 (own ticket) | Server enforcement per group (AI cost first): shadow, then enforce; verify-on-deny; narrow fail-open; 402 envelope; app 402 interceptor including SSE and upload paths | `ent_gate_ai_shadow` / `ent_gate_ai_enforce`; requires `X-Client-Caps: http-402` | `would_deny ∩ RC-active == 0` after verify-on-deny, for 7 days | Enforce flag off |
| P6 (own ticket) | **Point of no return; needs CTO sign-off.** Referral and revoke on ledger transitions; remove the `REFUND` branches (`webhooks.py:243-244`; `web_funnel_claim_payment.py:61`); remove the asymmetric guard; stop legacy writes **at least 2 weeks after the last reader moved**; dedupe and contract migrations; docs (`BE:docs/external-services.md:21,25`, `BE:docs/database-guide.md:103`, app contracts) | per source | 2 stable weeks | **Not a downgrade.** Before the contract migrations, take a Neon branch or snapshot and copy rows to be deleted into an archive table. Rollback means restoring from the archive or branch. Downgrades in this area are forward-only (`BE:migrations/versions/20260801000002_remove_paddle_fulfillment_schema.py:110-111`), and the pipeline has no schema rollback (`BE:docs/runbooks/schema-migration.md:103-106`) |
| P7 (optional) | Leave RC: product catalog, ASSN via the RC forwarding URL, Play RTDN, `in_app_purchase`, own web billing, S2S attribution | separate ADR | – | – |

---

## 10. Testing and observability

### Backend tests

Today there are only mocked unit tests (`BE:tests/unit/api/test_webhook_handler.py`, which uses a MagicMock UoW and patches `os.getenv`).

- **Schema.** Constraints and partial indexes are declared in ORM `__table_args__`. A migration test per table goes under `BE:tests/migrations/`, plus a migrated-versus-`create_all` parity test for the new tables. CI builds its database from `create_all`, so without this the dedupe tests would pass falsely.
- **Layering.** `lint-imports` passes with no new baseline entries.
- **DB-backed integration tests** for webhook → inbox → sync → projection.
- **Replay tests.** A shuffled or duplicated event stream gives the same projection. A duplicate of a non-`processed` row is reprocessed.
- **Concurrency.** Two workers on the same customer produce one row. A stale snapshot fetched earlier never overwrites a newer one.
- **Key stability.** Backfill followed by a webhook-driven sync gives one row per subscription. An upgrade within a group, and an aliased legacy lead fetched twice, also give one row each.
- **Snapshot replace.** "TRANSFER event lost, only the target re-synced", then a source re-sync, leaves the source with no access.
- **Fault injection.** RC 5xx, 429/7638 or a timeout never reduces anyone's access, and never holds a DB connection during the fetch.
- **Flag flips.** Flipping `ent_ledger_write` both ways produces exactly one referral credit and one lifecycle event.
- **Unknown user.** Parked, then replayed on `/users/sync`. A deleted user is parked and gets no credit.
- **Normalizer fixtures** from captured RC v1 JSON: trial, cancelled within the paid period, grace, billing retry, paused, refunded, lifetime and non-subscription, sandbox, transferred, promotional or temporary entitlement grant, multiple entitlements, legacy lead alias, family shared, an event with no `id`.
- **Read-time expiry.** An expired row reads `has_access=false` with no reconciler running, and the ETag changes after `next_change_at`.
- **Gate tests (P5):** shadow, enforce, verify-on-deny rescue in the purchase-hint window, narrow fail-open versus fail-closed for never-subscribed users.
- **Admin auth:** an unverified email, or a missing custom claim, gets 403.
- **Contract test:** the JSON schema shared with the Flutter Freezed DTO, including the 402 envelope.

### App tests

- `fromBackendJson` mapping, including `grace`, `cancelled_active` and `unknown`.
- `Subscription` equality notifies on a `willRenew`-only change.
- Router states for `none`, `expired`, `unknown` and grace (today there is no `billingRetry` test).
- Opening and closing the optimistic window, including staying open while the answer is `stale`.
- Asymmetric guard.
- 402 interceptor: single-flight, retry once, coach SSE path.
- Offline cache: owner-scoped; logging out and logging in as another user while offline does not reuse it; turning the clock back does not extend access.
- Anonymous purchase followed by login without restore-on-build.
- Flag defaults to `rc` when PostHog is unavailable.

Run with `flutter analyze && flutter test`.

### End-to-end in staging sandbox (both stores)

- purchase
- restore after reinstall
- two Firebase accounts on one Apple ID or Google account
- web redemption
- refund
- account deletion while subscribed
- an App Review-style sandbox purchase against a production-config build

This is a checklist item for every phase that changes gating.

### Metrics and alerts

Today the only metric is the success-only `webhook.revenuecat.processed` (`webhooks.py:246-249`).

- **P0:**
  - `webhook.revenuecat.received` and `.non_2xx{status}`
  - alert on the 401/5xx rate
- **P1:**
  - `.duplicate`, `.unmatched`, `.failed` by type
  - `entitlement.sync.latency` and `.provider_error{code}`
  - `rc.budget.wait{lane}` and `rc.429`
  - `entitlement.mismatch{direction}`
  - `identity.alias_conflict`
  - `entitlement.unmapped_product`, for a product with no RC entitlement
- **P2:**
  - inbox lag p95
  - worker heartbeat
  - `entitlement.grant.created`; every grant and every `ent_*` flag change alerts
- **P3/P4:** PostHog `entitlement_shadow_mismatch{reason}` and `entitlement_backend_false_negative`.
- **P5:** `entitlement.gate.would_deny{group}`, `.denied`, `.verify_rescued`, `.fail_open`.

Sentry alerts:
- any "RC active / DB inactive" drift;
- inbox `dead` rows above 0;
- `fail_open` rate above baseline;
- worker heartbeat missing for more than 10 min;
- RC 429 rate above threshold.

---

## 11. Phased delivery and sizing

S ≈ 3 story points, M ≈ 5–8, L ≈ 13. These are estimates. Velocity is not recorded in the repos, and the eng-week figures use the draft's implied rate of about 6–7 SP per engineer-week. **Niels re-points per story.**

| Phase | Backend (mealtrack_backend) | App (nutree_ai) | Depends on |
|---|---|---|---|
| P0 | M (5). Production checks and discovery, adapter errors behind the new port, delete premium_check and its test, settings plus precedence test, SAVEPOINT, P0 metrics and alert, fixture capture | M (5). Header interceptor (`package_info_plus` + caps), listener re-registration, `Subscription` equality and Freezed | – |
| P1 | 21, split into three stories: (a) schema, ORM, repos, UoW registration, migration and parity tests: 8; (b) port, RC normalizer and fixtures, SyncService, RcBudget, projection: 8; (c) webhook inbox rewire, inline post-commit sync, shadow metric: 5 | – | P0 checks; D8, D10 |
| P2 | 23, split into four stories: (a) worker host, inbox retry, reconciler, replay, outbox ORM and dispatcher: 8; (b) backfill script and run: 5; (c) entitlements API, `/sync`, readers, finalize payload, `ent_read_api_defer`: 5; (d) `require_admin` hardening, admin grants, RC mirror: 5 | – | D2, D5, D7 |
| P3 | S (2). Comparison support | M (8). DTO and mapping, shadow compare against raw RC, provider routing, flag default and stickiness | P2 gate |
| P4 | S (3). RC budget tuning, false-negative support | L (13). Backend decides, optimistic window, asymmetric guard, offline cache, restore-on-build replacement, finalize payload, Settings UI in 7 locales | P3 gate; D6 |
| **NM-680 subtotal (P0–P4)** | **54** | **26** | **≈ 80 SP ≈ 11–13 eng-weeks; ≈ 10–13 calendar weeks** |
| P5 (own ticket) | M (8). Endpoint inventory, `require_entitlement`, 402 exception and envelope, `FeatureFlagReader` for gates, verify-on-deny, fail-open policy | S (3). 402 interceptor including SSE and upload paths, caps constant | P4 adoption; D4 |
| P6 (own ticket) | M (8). Referral and promo transitions, archive and contract migrations, docs | S (2). Remove the RC read path and the guard after 2 releases, docs | P5 stable; D13 |
| **Total through P6** | **~70** | **~31** | **≈ 100 SP ≈ 14–16 eng-weeks** |

The `FeatureFlagReader` is first needed in P1 or P2 for the server flags (`ent_ledger_write`, `ent_reconcile`, `ent_read_api_defer`). It is counted in P1(b).

---

## 12. Decisions for the CTO and questions for the BA

### CTO sign-off

| # | Decision | Recommendation |
|---|---|---|
| D1 | Scope: hybrid now (RC as the purchase rail, our DB as truth); leaving RC (P7) decided separately. NM-680 (8 SP) becomes an epic; NM-680 is re-scoped to P0–P4, with child tickets for P5, P6 and P7 | Approve hybrid and the split |
| D2 | Worker host. This **carries out the outstanding follow-up** in `ADR-cron-jobs-removal.md:93-98`, option 2; it does not reverse the ADR. Alternatives are the ADR's options 1 and 3. Also update the ADR's stale "What Was Kept" list (`:70-84`) | Render Background Worker using the catalog-preparation lease pattern; needed in P2 |
| D3 | Access policy: grace grants access; cancelled keeps access until expiry; billing retry after grace has no access. Sandbox: (a) grants access to everyone, with a metric and alert, or (b) only to an allowlist (App Review demo account, QA). Sandbox is always excluded from revenue, referral and lifecycle events | (a) only if P0 confirms there is no public TestFlight link; otherwise (b) |
| D4 | Which server endpoints are paid (§7.2, including progress recap, manual create, catalog log); the narrow fail-open policy; enforcement gated on `X-Client-Caps`; how anonymous Firebase users are treated | AI cost group first, in its own ticket; narrow fail-open; no server gate on lifestyle routes |
| D5 | Where manual grants live. The ledger is the truth. Until P6, mirror each grant as an RC promotional entitlement *(RC v1 promotional API: https://www.revenuecat.com/docs/api-v1/entitlements.md)* so that old builds and a P4 rollback still see it; exclude the mirrored promotional entry from the projection through `rc_mirror_ref`. Sources limited to admin, support, qa and migration. No lifetime grants; maximum 366 days; reason required | Approve mirror-until-P6; otherwise issue no grants to app users before P6 |
| D6 | Stop restore-on-build (depends on the RC transfer setting) | Approve once replaced by logIn→sync and tested |
| D7 | **Admin authentication and silent login. Now a P2 prerequisite.** `require_admin` checks only the email claim (`BE:src/api/dependencies/auth.py:331-371`). Silent login sets `email_verified=True` on the account that owns a lead email and mints a token for it: route `BE:src/api/routes/v1/web_funnel_redemption_session.py:40-70`; service `BE:src/infra/services/web_funnel_redemption_identity.py:17-35,55-60,72-73` | Require `email_verified` plus an Admin-SDK custom claim `admin=true`, verified with `verify_firebase_token_revocation_checked` (`auth.py:160`). Refuse silent-login minting for any account with the admin claim or on `ADMIN_EMAILS`. Security review of lead-email verification in the BFF |
| D8 | Re-add a provider-neutral schema. The Paddle schema was removed because "Paddle billing is now owned by RevenueCat Web" and Paddle rows could not satisfy "the RevenueCat cache identity invariant" (`BE:migrations/versions/20260801000002_remove_paddle_fulfillment_schema.py:1-6`) | Approve; write a short ADR that replaces that invariant with the ledger's identity model |
| D9 | Retention of raw webhook payloads (personal data; the app sets `$ip`, ad ids and `$airbridgeDeviceId` as subscriber attributes, `revenuecat_service.dart:325-356`) | Strip `subscriber_attributes` on insert; null `raw` after 90 days or on account deletion |
| D10 | RC REST API version for SyncService: v1 (no published limit, get-or-create GET, current code) or v2 (480/min for customer information; subscription objects with store identifiers) | Decide before P1. Evaluate v2 with the P0 fixtures; it affects the key, the normalizer and backfill throughput |
| D11 | Account deletion: retire identities, park webhooks for deleted users, no referral credit, purge payloads; whether to call RC delete-customer *(unverified)*; a support runbook for a user who gets a 402 but has a store receipt | Approve as listed; BA AC below |
| D12 | Family Sharing: does a family-shared purchase grant access? | Yes, matching RC's entitlement behaviour, unless the BA objects |
| D13 | P6 is a point of no return (data deletion, legacy writes stopped) | Separate sign-off at P6 start, with the archive and Neon branch taken first |

### BA questions, with acceptance-criteria candidates

1. **Paid endpoint list.** Do barcode, food search, recaps, `POST /v1/progress/recap`, manual meal create and catalog meal log require a subscription on the server?
   - *AC:* Given a user with no entitlement, when they call `POST /v1/meals/image/analyze` with enforcement on, then they receive 402 with `detail.error_code = SUBSCRIPTION_REQUIRED` and no AI call is made.
2. **Cancelled-but-paid users.**
   - *AC:* Given a user who turned off auto-renew with `expires_at` in the future, when the app starts, then premium routes are accessible and Settings shows "won't renew" (new strings in all 7 locales).
3. **Grace period.**
   - *AC:* Given a billing failure inside the store grace period, then `has_access=true` and `state=grace` until `grace_expires_at`, and `has_access=false` after it with no reconciler run needed.
4. **Post-purchase latency.**
   - *AC:* Given a successful native purchase, premium is unlocked in the UI within 2 s of the purchase sheet returning success.
   - If the backend has not confirmed within 120 s, the app keeps access while the backend answer is `stale` or `sync_error` and the RC purchase result's expiry is in the future.
   - It emits `entitlement_optimistic_timeout`.
   - It never shows the paywall because of a backend "no" from a hinted `/sync` within 15 min of purchase.
5. **RC outage.**
   - *AC:* Given RC returns 5xx or 429, then no user who had access in the projection loses it, never-subscribed users gain nothing, and `/sync` returns `stale:true`.
6. **Admin grant ("manage ourselves").**
   - *AC:* Given an admin with a verified email and the admin claim grants 30 days of `standard` to user X with a reason, X has access at the next app resume, pull-to-refresh, or within `ttl_seconds` (300 s). The grant row records the admin's email and the reason.
7. **Lapsed users.**
   - *AC:* Given an expired subscriber, then hydration, movement and walking remain usable and premium AI routes show the paywall.
8. **Migration of existing subscribers** (asked in the ticket).
   - *AC:* Given the backfill has completed, every RC export row with `effective_end_time > now` maps to a user with `has_access=true`, and rows that cannot be mapped are listed for support.
9. **Restore, transfer, refund, web redemption.**
   - *AC:* Restore after reinstall restores access.
   - *AC:* When the subscription moves to a second Firebase account on the same store account, the first account loses access within 24 h.
   - *AC:* A refund removes access at the next sync.
   - *AC:* A web redemption unlocks access from the finalize response.
10. **Account deletion while subscribed.**
    - *AC:* After deletion, renewals do not credit referrals or resurrect the deleted account. A new account on the same store account gets access after a user-tapped restore.
11. **Referral credit.** Credit on trial start or on the first paid period?
    - *AC candidate:* "credit on first paid period; revoke on refund".
12. **Chat budget.** Should it become tier-aware?
13. **Live Activities and notifications.** Their gating (`APP:docs/contracts.md:143-152`) moves to the backend-sourced provider. Confirm there is no product change.
14. **P4 exit gate owner.** Which support channel and query count as "access tickets"?

### Still unverified (check before sign-off)

- RC behaviour: the HMAC header name; PRODUCT_CHANGE `new_product_id` semantics; v1 rate limits; whether the promotional-grant API and Scheduled Data Exports are available on our plan; RC delete-customer semantics.
- Which store identifiers, `ownership_type` and `original_app_user_id` the v1 subscriber JSON exposes (P0 fixtures).
- Which `app_user_id` RC puts on renewals after a web redemption.
- Whether `purchases_flutter` 10.10.1 can set `appAccountToken` or `obfuscatedAccountId`.
- Whether Email/Password sign-up is enabled in production Firebase, and whether the BFF verifies lead emails.
- Whether a public TestFlight link exists, and how flavors map to RC projects and backends.
- Whether `nutreeai_async` and `nutree-affiliate` dedupe on the lifecycle event id.
- How re-registration with the same Firebase UID interacts with `users.firebase_uid UNIQUE` (`BE:src/infra/database/models/user/user.py:20`; `sync_user_command_handler.py:64-76`).
- The real production schema, duplicates, user count and `outbox_events` contents (§9.1).

---

## Verification notes

All blocker and major findings were re-checked against the code or Jira before the plan was changed. Corrections:

1. **D2 framing.** `ADR-cron-jobs-removal.md:25,93-98` requires a replacement trigger and lists a Render Background Worker as option 2. The worker now carries out the ADR's follow-up instead of "reversing" it. The ADR's "What Was Kept" list (`:70-84`) is stale; `outbox_dispatch_engine.py`, `outbox_repository.py`, `models/outbox_event.py` and `services/handlers/` are absent.
2. **Admin auth (blocker), confirmed.** `require_admin` checks only `token.get("email")` (`auth.py:331-371`). Silent login sets `email_verified=True` and mints a token (`web_funnel_redemption_identity.py:25-35`). Added as §2.2 item 7, a P2 prerequisite, and D7 moved onto the critical path. The lead-email citation was corrected to `web_funnel.py:147,164`. Whether Email/Password sign-up is enabled in production is marked unverified: the app uses password sign-in only for the dev bypass (`auth_flow_notifier.dart:150`).
3. **Fail-open abuse.** Fail-open on RC outage now applies only to recent payers and to users with a recent purchase hint; everyone else fails closed. A global prioritised RC budget was added (`rate_limit.py:14-34,42` is per-account only; the adapter has no budget, `revenuecat_adapter.py:47-60`).
4. **Just-paid denial.** The TL;DR no longer claims a re-check "before any 402". Hinted `/sync` results are never negatively cached, re-syncs run at +5 s and +30 s, verify-on-deny ignores the 10-minute rule inside the hint window, and the 402 interceptor retries once.
5. **Subscription key.** It is now derived only from the fetched snapshot (`rc:{original_app_user_id}:{product_id}` on v1). `original_transaction_id` is an attribute. Snapshot entries are de-duplicated across fetched customers, and legacy fetches are checked by `original_app_user_id` as in `web_funnel.py:402-427`. The two verifiers disagreed on whether to drop the customer id. It is kept, because v1 has no store-level key and dropping it would collide different buyers of the same product.
6. **TRANSFER and absence.** Snapshot replace with an `absent` state, ownership moves on key conflict, and both users are locked in id order.
7. **Clock-dependent projection.** Expiry is now evaluated at read time with `next_change_at`, and the ETag includes it.
8. **Connection held during the RC call.** Confirmed: the production pool is 10+5 (`connection_policy.py:115-117`), and existing code already calls RC inside an open UoW (`webhook_referral_funnel.py:76` after `webhooks.py:178-179`). Fetching now happens outside the transaction, a later short transaction applies the result only if the snapshot is newer, and a semaphore and circuit breaker were added.
9. **Product catalog.** It no longer gates access. RC's own `entitlements` map is the source, as current code already trusts (`revenuecat_adapter.py:73-95`, `web_funnel_claim_common.py:44-56`, `web_funnel_redemption_verification.py:35-37`). The catalog moves to P7.
10. **P1 inbox before worker.** Confirmed rollback-on-exception (`uow_async.py:175-211`). In P1 the inbox row is written inside the legacy UoW, a duplicate short-circuits only when `processed`, the legacy publisher is kept until P2, and a side-effect ownership table was added. The P1 gate no longer needs a worker.
11. **Backfill candidate set.** Now every active `firebase_uid` plus known lead and anonymous ids. The export is only a cross-check, and its availability is a P0 check.
12. **P4 safety net.** Added the asymmetric false-negative guard (P4–P6), the `unknown` state, the server-side `ent_read_api_defer` switch, and the PostHog flag defaulting to `rc` and sticky per cold start (`app_router_redirect.dart:186-192` hard paywall confirmed).
13. **P6 rollback.** Now a point of no return with archive and Neon branch (`schema-migration.md:18,103-106`; forward-only downgrade at `20260801000002…:110-111`).
14. **Sizing.** Jira NM-680 confirmed at 8 SP with Done line "App reads subscription/entitlement state from our backend/database". The plan now proposes an epic, NM-680 = P0–P4, and states the SP-to-week assumption. P1 and P2 were split and re-estimated (21 and 23 SP).
15. **Over-built schema.** Grant sources were trimmed to admin, support, qa and migration; lifetime grants were removed; `product_catalog` was deferred; P5 became its own ticket. Provider-neutral columns were kept, because the user asked for provider independence.
16. **Phase ordering.** The worker-dependent `retry` path moved to P2. The P1 gate is now measurable without a worker.
17. **Import-linter and layering.** Confirmed: `.importlinter:1-8`, baseline at `:276-282`, CI at `ci.yml:40`. Added a layer for each component and worker composition in `src/bootstrap/`.
18. **CI schema built from create_all.** Confirmed (`init_postgres_db.py:279-286`; `conftest.py:28-34`; `ci.yml:129`). Added ORM `__table_args__` parity and migration tests.
19. **Endpoint inventory.** Added `POST /v1/progress/recap` (LLM, `progress.py:104-127`, handler `:87-96`), manual meal create (`api_service.dart:464`) and catalog log (`api_service.dart:572`; `meal_catalog_log.py:45`).
20. **Account deletion.** Confirmed: `firebase_uid` is kept (`delete_user_command_handler.py:72-91`) and lookup has no `is_active` filter (`webhook_lookup_parsing.py:45-47`). Added §2.2 item 17, the §6.4 rules, D11 and an AC.
21. **Minor corrections applied.**
    - TRANSFER defect restated (`webhooks.py:185-186`; `webhook_referral_funnel.py:65,76-79,101`).
    - Referral credit is triggered by row insert (`lifecycle.py:99-104,124,159-162`).
    - `_subscription_event_id` semantics (`:52-59`).
    - Only `REVENUECAT_WEBHOOK_AUTH_HEADER` is missing from settings (`settings.py:236`; `.env.example:217`).
    - A server flag reader must be built, and PUT already invalidates (`feature_flag_service.py:11-43`; `feature_flags.py:161-163`).
    - Version header value is the `'2.8.3'` constant (`nutrition_contract_headers.dart:9-12`; `pubspec.yaml:19`); now sourced from `package_info_plus`, with an `X-Client-Caps` header for Shorebird patches (`shorebird.yaml:8-12`; no `shorebird_code_push` dependency).
    - `Subscription` equality (`subscription.dart:132-141`).
    - Query handler cited for the dev `True` (`get_user_by_firebase_uid_query_handler.py:37-45`).
    - D7 cites both the route and the service.
    - `'standard'` sites completed (`web_funnel_redemption_verification.py:35`; `web_funnel_claim_common.py:62`, not `:63`).
    - Direct RC reads limited to `timezone_monitor`.
    - Unhandled event types also hit the 404 branch.
    - D8 cites the removal docstring.
    - REFUND confirmed as not an RC event type, per RC docs from the verification pass.
    - 404→200 tied to `ent_reconcile`.
    - Exit gates corrected (full comparison, raw RC `standard`, `would_deny ∩ RC-active`).
    - `outbox_events` audit added (`has_table` guard at `20260822000001…:29-33`).
    - P0 observability moved ahead of the auth refactor; dashboard resend is the recovery path.
    - Missing event id falls back to a sha256 of the body; `subscriber_attributes` are stripped.
    - Alias inserts use `ON CONFLICT DO NOTHING`.
    - Offline cache in `AccountPreferences` using `server_time`.
    - 402 envelope in `{"detail": …}` (`exceptions.py:96-118`; `exception_handlers.py:35-47`), plus SSE handling (`http_coach_repository.dart:150-170`).
    - QA access via `qa` grants.
    - Downstream consumer discovery added.
    - RC API version added as D10; Family Sharing as D12; legacy `None` shim added (`web_funnel_claim_payment.py:57-60`).
    - Sandbox allowlist option added (D3).
    - Settings "won't renew" UI sized with 7 locales.
22. **Kept as drafted.** The re-fetch-on-every-event plus inbox design. All four verifiers confirmed that it neutralizes replayed and out-of-order events.