# MealTrack Backend

A sophisticated FastAPI-based microservice for meal tracking and nutritional analysis.

## Quick Links

Primary evergreen docs (agents and people share this map):

- **[Project Overview & PDR](./docs/project-overview-pdr.md)** — vision, goals, product constraints.
- **[Codebase Summary](./docs/codebase-summary.md)** — discovery map (points to code/OpenAPI).
- **[System Architecture](./docs/system-architecture.md)** — layer boundaries and non-derivable rules.
- **[Code Standards](./docs/code-standards.md)** — style and structure judgment.
- **[Testing Standards](./docs/testing-standards.md)** — test strategy and CI unit coverage gate.
- **[Database Guide](./docs/database-guide.md)** — connection/migration policy (schema judgment in [`docs/standards/db-api.md`](./docs/standards/db-api.md)).
- **[API Conventions](./docs/api-endpoints.md)** — OpenAPI how-to (`/docs`, `/openapi.json`), auth, errors, non-derivable contracts.
- **[External Services](./docs/external-services.md)** — fail-fast vs degrade integration policy.
- **[Troubleshooting](./docs/troubleshooting.md)** — recovery; incidents in [`docs/runbooks/`](./docs/runbooks/).

Stateful progress/history and superseded docs: [`docs/archive/`](./docs/archive/)
(roadmap/changelog under `archive/progress/`).

## 🚀 Features

- **AI-Powered Meal Analysis**: Vision-based food recognition with 6 meal strategies, food-label image analysis, and provider fallback routing.
- **Catalog-Backed Meal Recommendations**: Deterministic three-day plans built from the curated catalog, with durable slot summaries and swap/log/skip flows.
- **API Surface**: Versioned REST under `/v1/*`; live inventory is OpenAPI at `/docs` and router registration in `src/api/main.py`.
- **Paid Web Redemption**: RevenueCat web checkout handoff via `/v1/web-funnel/*` with passwordless Firebase sign-in and backend finalization.
- **CQRS Architecture**: Commands, queries, events, and handlers wired through a PyMediator singleton event bus.
- **Vector Cache**: Active meal-image-name vector cache uses `pgvector`; Pinecone is legacy documentation only and is not a runtime adapter.
- **Multi-Language Support**: 7 languages (en, vi, es, fr, de, ja, zh) with translation service.
- **Smart Notifications**: FCM push with timezone-aware scheduling and preferences.

## 🛠 Technology Stack

- **Core**: FastAPI 0.136.3 (Python 3.13.2), SQLAlchemy 2.0 async runtime (`AsyncSession`, `AsyncUnitOfWork`).
- **Database**: PostgreSQL (Neon) with SQLAlchemy 2.0, Redis 7.0 for selective optional caching; required state is modeled separately.
- **AI**: OpenAI via LangChain/Responses API as the default text and vision provider, with optional Cloudflare Workers AI routing for configured text purposes and vision fallback. Gemini packages remain in dependencies, but the runtime provider registry is OpenAI + Cloudflare.
- **Infrastructure**: Firebase (JWT Auth + FCM), Cloudinary (image storage), RevenueCat (subscriptions).
- **Event Bus**: PyMediator with singleton registry for CQRS.
- **Testing**: pytest (unit-biased default config), ruff (linting), mypy (type checking).

### OpenAI Prompt Caching

- OpenAI calls run through LangChain `ChatOpenAI` with Responses API enabled.
- Enable provider-side prompt caching with `OPENAI_PROMPT_CACHE_ENABLED`.
- Optional retention uses `OPENAI_PROMPT_CACHE_RETENTION`; key namespace uses `OPENAI_PROMPT_CACHE_KEY_PREFIX`.
- Cache keys are derived from the model, purpose, and a hash of the system prompt. They never include raw user prompt text, images, emails, or IDs.
- Track `ai.openai.prompt_cache.request.count`, `ai.openai.prompt_cache.cached_tokens`, and `ai.openai.prompt_cache.input_tokens` before assuming savings.

## 🏗 Architecture

Follows a **4-Layer Clean Architecture** with **CQRS** and **Event-Driven Design**:

1. **API Layer** (`src/api/`): route modules, middleware, schemas, dependencies, and API mappers.
2. **Application Layer** (`src/app/`): CQRS command/query/event handlers and orchestration services.
3. **Domain Layer** (`src/domain/`): entities, services, ports, policies, and bounded contexts.
4. **Infrastructure Layer** (`src/infra/`): database models, repositories, external adapters, cache, observability, and event bus implementation.

See `docs/system-architecture.md` for boundaries and non-derivable rules.

## 🚦 Getting Started

```bash
# Clone and enter repo
git clone <repo-url> && cd backend

# Install dependencies
pip install -r requirements.txt

# Setup environment
cp .env.example .env # Configure variables

# Local development flow
./scripts/development/local.sh

# Or run the app directly after the database is ready
uvicorn src.api.main:app --reload
```

Production uses `python migrations/run.py` as the pre-deploy command. The
production container then starts through `docker-entrypoint.sh` without
re-running migrations; non-production containers run migrations at startup
unless `AUTO_MIGRATE=false` (also accepts `0`, `no`, or `off`).

- **Swagger Docs**: http://localhost:8000/docs
- **Tests**: `pytest tests/unit --cov=src --cov-fail-under=65` for the default CI-aligned suite. Broad unscoped `pytest` currently hits two duplicate-package import collisions, so prefer targeted paths.
