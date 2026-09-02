---
type: brainstorm
date: 2026-08-29
status: accepted
---

# Brainstorm: Recent and Favorite Meals Architecture

## Summary

Add durable favorite membership with one minimal PostgreSQL join table. Serve both favorite and recent meal lists through five-minute Redis caches backed by PostgreSQL. Repeating a meal clones existing persisted nutrition immediately, without scan, text parsing, AI, or provider calls.

Redis is used for favorite-list speed, but not as the only favorite store. PostgreSQL preserves the user's selection through Redis eviction, flush, outage, or redeployment.

## Requirements

- Users can favorite and unfavorite their own meals.
- Favorites survive deletion of the original log from normal meal history.
- Users can list up to 10 distinct recent meals from the last 7 local calendar days.
- Users can hold at most 20 favorites; the 21st favorite is rejected without evicting older favorites.
- Selecting a recent or favorite meal logs a new meal immediately.
- Recent and favorite reads should normally be served from Redis.
- No new columns are added to `meal`.
- Normal meal deletion becomes soft deletion through the existing `INACTIVE` status.
- Redis failure degrades list reads to PostgreSQL and never loses durable favorite membership.

## Data Ownership

| Data | Source of truth | Redis role |
|---|---|---|
| Meal and nutrition details | PostgreSQL `meal` aggregate | Cached list projection only |
| Favorite membership | PostgreSQL `favorite_meals` | Cached list projection only |
| Recent meals | Derived from PostgreSQL meals | Cached list projection only |
| Repeat idempotency | PostgreSQL `meal_write_operation` | None |

## Favorite Schema

Use a composite primary key instead of a separate surrogate `id`:

```text
favorite_meals
--------------
user_id       FK -> users.id
meal_id       FK -> meal.meal_id
favorited_at  timestamp with time zone

PRIMARY KEY (user_id, meal_id)
INDEX (meal_id)
```

Constraints:

- `user_id` uses `ON DELETE CASCADE`.
- `meal_id` uses `ON DELETE CASCADE` for an eventual administrative hard purge.
- User-facing deletion does not activate either cascade because it only changes meal status.
- The composite primary key makes favorite creation naturally idempotent.
- `favorited_at` provides deterministic newest-first ordering.
- No favorite snapshot, `is_favorite`, or `updated_at` column is needed.

Favorites reference live stored meal details. Editing the source meal changes what the favorite displays and repeats.

## Soft Deletion

Change user-facing `DELETE /v1/meals/{meal_id}` from physical deletion to:

```text
meal.status = INACTIVE
```

Keep nutrition, food items, image references, recipe fields, and favorite membership. Continue clearing recommendation-log links where required and emit `meal.deleted.v1` after commit for downstream cache and metric behavior.

Access rules:

- Recent meals, history, macros, and normal meal-detail queries exclude `INACTIVE`.
- Favorite listing may load an owned `INACTIVE` meal.
- Repeat may load an `INACTIVE` meal only when `(user_id, meal_id)` exists in `favorite_meals`.
- Repeated deletion of an already inactive meal returns success.

## Recent Meal Definition

`GET /v1/meals/recent` derives candidates from PostgreSQL on cache miss:

- Resolve the user's effective timezone.
- Use today plus the preceding 6 local calendar days.
- Include only owned `READY` food meals.
- Exclude hydration and `INACTIVE`, failed, processing, or incomplete meals.
- Order newest first.
- Deduplicate by repeatable content and keep the newest representative.
- Return at most 10 distinct meals.

Two meals are the same when they contain the same foods in the same amounts. Calculate a non-persisted fingerprint from canonical ingredient identity or normalized name, quantities, and units. Exclude dish name, macros, nutrition overrides, record IDs, timestamps, image URLs, translations, and source metadata. Item-less meals fall back to normalized dish name so distinct manual entries do not collapse.

This fingerprint can later power frequent-meal ranking without a `meal` schema change.

## Redis Design

Cache complete list response DTOs, not only IDs. This avoids a PostgreSQL detail query on every cache hit.

```text
user:{user_id}:meal-lists-revision:v1
user:{user_id}:recent-meals:v1:{revision}:{timezone_hash}:{language}
user:{user_id}:favorite-meals:v1:{revision}:{language}
```

List-value TTL: 300 seconds.

The revision key avoids wildcard deletion and stale cache repopulation races:

1. A read obtains the current revision, defaulting to `0`.
2. It reads the corresponding locale/timezone-specific list key.
3. On a miss, it queries PostgreSQL, builds the response, and caches it under that revision.
4. After a relevant PostgreSQL commit, increment the user's revision.
5. A reader that started before the mutation may populate the old revision, but subsequent reads never use it.
6. Old list keys expire after five minutes.

If Redis is unavailable, both list endpoints query PostgreSQL and skip cache population. A failed cache write or invalidation does not roll back a committed meal or favorite mutation. The maximum stale window after an invalidation failure is five minutes.

### Invalidation Matrix

| Mutation | Increment list revision |
|---|---|
| Create or scan meal | Yes |
| Parse-text or manual meal save | Yes |
| Edit meal | Yes |
| Soft-delete meal | Yes |
| Favorite meal | Yes |
| Unfavorite meal | Yes |
| Repeat recent or favorite meal | Yes |

One revision intentionally invalidates both lists. This is simpler than maintaining two counters, and these mutations are low-volume compared with reads.

## API Contract

### List recent meals

```http
GET /v1/meals/recent?limit=20
```

Returns unique recent meal cards with `is_favorite` so the client can render and mutate favorite state without another request.

### List favorites

```http
GET /v1/meals/favorites?limit=50
```

Returns newest-favorited first and includes active and inactive source meals.

### Favorite meal

```http
PUT /v1/meals/{meal_id}/favorite
```

- Verify ownership.
- Require an active, reusable food meal when creating a new favorite.
- Insert `(user_id, meal_id, favorited_at)` with conflict-do-nothing.
- Commit, then increment the Redis list revision.

### Unfavorite meal

```http
DELETE /v1/meals/{meal_id}/favorite
```

- Delete only the authenticated user's relationship.
- Treat an absent relationship as success.
- Commit, then increment the Redis list revision.

### Repeat meal

```http
POST /v1/meals/{meal_id}/repeat
Idempotency-Key: <client-generated-request-id>
```

Optional body:

```json
{
  "meal_type": "lunch"
}
```

Repeat flow:

1. Verify meal ownership.
2. Permit an active recent meal, or an inactive meal still favorited by this user.
3. Reserve the existing durable meal-write idempotency operation.
4. Clone the persisted meal, nutrition, and food items with new IDs.
5. Use the current timestamp and an optional meal-type override.
6. Reapply the shared backend calorie and nutrition invariants.
7. Persist the new meal directly as `READY` in one unit of work.
8. Complete the idempotency operation and replay the same response on retry.
9. Increment the Redis list revision after commit.
10. Return the new meal without scan, parse-text, AI, or provider work.

## Why Redis Is Used for Favorites

Redis is appropriate for the favorite read path because favorite lists are user-scoped, frequently opened, small, and safe to serve stale for a bounded period. Caching the complete card list removes the join and meal hydration work from the hot path.

Redis is not appropriate as the only favorite store. If membership existed only in Redis, an eviction, flush, restore, or provider incident could silently remove user choices. The join table costs one indexed insert/delete and makes Redis a replaceable acceleration layer.

The resulting behavior is both fast and durable:

```text
normal read: Redis -> response
cache miss:  Redis -> PostgreSQL -> Redis -> response
Redis down:  PostgreSQL -> response
mutation:    PostgreSQL commit -> increment Redis revision
```

## Failure Behavior

| Failure | Behavior |
|---|---|
| Redis read failure | Query PostgreSQL |
| Redis cache write failure | Return PostgreSQL result without cache |
| Redis revision increment failure | Keep committed mutation; stale list bounded by five-minute TTL |
| PostgreSQL failure | Fail request; do not claim favorite/log success |
| Duplicate favorite request | Return existing favorite successfully |
| Duplicate repeat request | Replay existing idempotent result |
| Dangling relation after administrative purge | Database cascade removes favorite row |

## Acceptance Criteria

- Favorite and unfavorite operations are idempotent and ownership-scoped.
- Favorite membership remains after user-facing meal deletion.
- Deleted favorites remain listable and repeatable.
- Normal recent/history/detail reads exclude inactive meals.
- Recent returns no meal older than 30 local calendar days.
- Recent does not collapse materially different meals sharing a name.
- Repeat creates exactly one new meal for a retried idempotency key.
- Repeat performs no scan, parse-text, AI, or external-provider request.
- Redis hits avoid the PostgreSQL list query.
- Redis outages preserve correct favorite and recent behavior through PostgreSQL fallback.
- Every listed mutation advances the user list revision after commit.

## Recommendations

1. Keep the favorite table minimal: composite key plus `favorited_at`.
2. Cache complete recent and favorite list DTOs for five minutes.
3. Use one per-user revision key to invalidate both list families safely.
4. Centralize post-commit list revision advancement so no meal creation path is missed.
5. Reuse the existing meal-write idempotency repository for repeat logging.
6. Measure hit rate, fallback latency, Redis errors, and revision-increment failures before changing TTL.

## Unresolved Questions

None.
