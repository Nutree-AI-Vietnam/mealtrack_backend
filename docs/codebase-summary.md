# Backend Codebase Summary

**Status:** Primary discovery map (WHERE) — not a hand-maintained inventory  
**Runtime:** FastAPI on Python 3.13 with async SQLAlchemy 2.0

---

## Docs route

### Evergreen (load by need)

| Role | File |
|------|------|
| Product intent (WHY) | `project-overview-pdr.md` |
| Architecture boundaries | `system-architecture.md` |
| CQRS conventions | `cqrs-guide.md` |
| API conventions (not route tables) | `api-endpoints.md` |
| Database ops + schema judgment | `database-guide.md` → `standards/db-api.md` |
| Integration policy | `external-services.md` |
| Code / test judgment | `code-standards.md`, `testing-standards.md` |
| Ops recovery | `troubleshooting.md`, `runbooks/` |
| Accepted decisions | `decisions/` |
| Content import (when needed) | `meal-catalog-import-schema.md` |

### Stateful archive (not default load)

Historical dual tracks, journals, specs, plans under docs, feature notes, and
progress records: [`archive/`](./archive/) — see `archive/README.md`.

Executable truth always wins: `src/`, `tests/`, OpenAPI `/docs`, `migrations/`,
scripts, dependency manifests. Active execution plans live under repo-root
`plans/` (not under `docs/`).

---

## How to discover inventory

| Need | Owner |
|------|-------|
| Live HTTP surface | OpenAPI: `/docs`, `/redoc`, `/openapi.json`; registration in `src/api/main.py` |
| Offline OpenAPI dump + conventions | `api-endpoints.md` (Live OpenAPI contract; not a route table) |
| Public catalog browser route | `src/api/routes/v1/meal_catalog.py` (`GET /v1/meal-catalog`, `GET /v1/meal-catalog/{catalog_id}`) |
| Layer layout | `src/api/`, `src/app/`, `src/domain/`, `src/infra/` plus root/bootstrap/cron |
| Tests | `pytest tests/unit --cov=src --cov-fail-under=65` (default CI path) |
| Migrations | `migrations/versions/` via Alembic |
| Settings / env keys | `src/infra/config/settings.py`, `.env.example` |

Do not hand-maintain file, LOC, or endpoint counts in this document.

---

## Key directories

| Directory | Purpose |
|-----------|---------|
| `src/api/routes/v1/` | Versioned REST route modules (meals/webhooks use thin entry files plus sibling modules by flow) |
| `src/api/schemas/` | Pydantic DTOs |
| `src/app/commands/` | Write-operation command packages |
| `src/app/queries/` | Read-operation query packages |
| `src/app/handlers/` | CQRS handlers and support modules |
| `src/domain/model/` | Domain entities and value objects |
| `src/domain/services/` | Domain services and policies |
| `src/infra/database/models/` | ORM model packages and table declarations |
| `src/infra/repositories/` | Data access adapters |
| `src/infra/services/` | Infrastructure services and AI providers |
| `tests/` | Unit, architecture, migration, and explicit integration tests |

---

## Core runtime pointers

- DB: PostgreSQL/Neon via `src/infra/database/config_async.py` (async SQLAlchemy).
- Redis: optional cache-aside; required state is modeled separately (see
  `external-services.md` and `decisions/260608-2223-selective-cache-admission-policy.md`).
- Vector cache: active meal-image-name path uses `pgvector`.
- AI: OpenAI default; optional Cloudflare Workers AI for configured text/vision.
- Integration events: backend writes the transactional outbox and publishes one
  versioned `IntegrationEvent` to the environment-specific ingress Queue. The
  Worker orchestrator invokes registered handlers in order and retries the
  whole ingress message on failure. Hydration creation uses this path for cache
  invalidation; `cache_invalidation.v1` remains the compatibility owner for
  other cache deletes.
- Translation: OpenAI-backed read-path localization via
  `src/app/services/food_name_localizer.py` and
  `src/infra/adapters/openai_translation_adapter.py`; Responses API payload
  storage is disabled, persisted meal translation rows are versioned against
  the active translation contract so older rows are invalidated and
  retranslated, and only complete translations are eligible for cache or
  persistence.
- Event bus: singleton PyMediator from `src/api/dependencies/event_bus.py`.
- Migrations: `migrations/versions/` via Alembic / `migrations/run.py`.
- Meal catalog browse: authenticated list/detail live in
  `src/api/routes/v1/meal_catalog.py`; the browse contract is documented in
  `api-endpoints.md` and the import manifest contract in
  `meal-catalog-import-schema.md`.

Non-derivable calorie and weekly-budget rules: `AGENTS.md` / `CLAUDE.md`
MUST-Follow. Meal scan vs hydration and other system rules:
`system-architecture.md`.

---

## Entry points

- **FastAPI app:** `src/api/main.py`
- **Dev server:** `uvicorn src.api.main:app --reload`
- **Default tests:** `pytest tests/unit --cov=src --cov-fail-under=65`
- **Migrations:** `alembic upgrade head` or `python migrations/run.py`
