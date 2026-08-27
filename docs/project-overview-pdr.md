# MealTrack Backend — Product Intent

**Status:** Evergreen product intent (WHY)  
**Route map:** `codebase-summary.md`  
**Live inventory:** OpenAPI `/docs`, `src/`, `tests/` — not this file

---

## Vision

Help users understand nutrition with accurate tracking, deterministic meal
recommendations, and reliable AI-assisted meal analysis.

## Primary goals

1. **Accuracy** — validate AI meal analysis against backend contracts before persistence.
2. **Efficiency** — keep meal logging and recommendation retrieval fast enough for mobile.
3. **Personalization** — weekly-budget redistribution, profile state, historical affinity.
4. **Operational safety** — required state in durable stores; optional integrations degrade cleanly.

---

## Product capabilities (intent, not route tables)

| Capability | Non-derivable product rule |
|------------|----------------------------|
| AI meal analysis | Multiple analysis strategies; food-label scan is a separate validated path. Backend owns calorie presentation. |
| Meal scan vs hydration | Meal image/scan endpoints treat edible/drinkable intake as **meals**. They must **not** create `hydration_entries`. Zero-cal drinks use `/v1/hydration/*` explicitly. |
| Catalog recommendations | Deterministic ranking of curated catalog meals; **no LLM at recommendation time**. Separate from AI meal-suggestions. |
| Public catalog browser | `popular` is curated by explicit `popularity_rank` and fails closed with 503 until seeded; `for_you` is a browse surface that can fall back to curated global order and surface fallback metadata. |
| Meal suggestions | Session-based, additive AI discovery; not a fallback for catalog recommendations. |
| Local-first food search | Cache (optional) → local `food_reference` → FatSecret candidates. Full servings on select (`food.get.v5`). Calories from macros. Popular staples are curated catalog rows keyed by provider source id, not live search. |
| Weekly budget | Redistribution from prior consumption is the source of truth for adjusted daily targets. Movement credits balance without inflating baseline TDEE. See AGENTS MUST-Follow for `remaining_days`. |
| Paid web redemption | RevenueCat web checkout → Firebase passwordless → backend preflight/finalize. Same UID may redeem multiple distinct purchases (hash-keyed). Browser never grants app access alone. |
| Nutrition overrides | Absolute meal/ingredient overrides while set; clear restores source/macro-derived values. |

Discover endpoints and schemas from OpenAPI. HTTP conventions:
`api-endpoints.md`.

---

## Stack (stable choices)

- FastAPI + Python 3.13; 4-layer Clean + CQRS + PyMediator
- PostgreSQL (Neon) + SQLAlchemy 2.0 async; Alembic migrations
- Redis for selective optional cache and some transient session state
- `pgvector` for the active meal-image-name vector cache (Pinecone not runtime)
- AI: OpenAI default; optional Cloudflare Workers AI for configured text/vision
- Auth: Firebase JWT; Notifications: FCM; Billing: RevenueCat

---

## Non-functional constraints

- Optional dependencies degrade; Firebase Auth and primary DB fail fast
- CI unit coverage gate: **65%** (`testing-standards.md`)
- Security: JWT verification, webhook auth, soft deletes, no secret logging
- Observability: structured logging facade; Sentry optional connector

---

## Out of scope for this doc

Roadmap checklists, release notes, and historical version tables are stateful
records under `docs/archive/progress/`. They are not product authority.
