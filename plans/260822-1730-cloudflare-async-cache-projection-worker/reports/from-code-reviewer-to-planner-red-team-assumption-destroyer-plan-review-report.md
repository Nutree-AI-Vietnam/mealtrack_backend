# Red-Team Review: Assumption Destroyer

## Finding 1: “Every migrated meal write” is not an actionable caller contract — HIGH

- **Location:** Phase 2 related files, implementation step 6, success criteria
- **Flaw:** The current code has many `after_meal_write` callers across manual,
  edit, delete, photo, scan, catalog, recommendation, graph, and suggestion
  paths. The plan defers the exact inventory to Phase 1 but does not name an
  explicit inclusion/exclusion rule or gate.
- **Failure scenario:** One graph or photo path remains post-commit. Its meal
  commit succeeds without an outbox row, so the headline durability guarantee
  is false for a live route.
- **Evidence:** `rg` finds callers in `src/app/graphs/meal_analyze/nodes.py` and
  multiple command-handler files, including
  `create_manual_meal_command_handler.py:99,192,314` and
  `edit_meal_command_handler.py:256-260,396-400`.
- **Suggested fix:** Phase 1 must produce a caller matrix with count, UoW
  boundary, route, and disposition. Phase 2 success must say “all approved
  live meal write callers” and fail closed for an unclassified caller.

## Finding 2: Phase 2 mixes invalidation migration with population fencing — MEDIUM

- **Location:** Plan scope and Phase 2 implementation step 7
- **Flaw:** The overview defers cache population, but Phase 2 also requires
  adding nutrition revision fields to canary cache writers. This is a second
  consistency migration and may expand the first slice beyond invalidation.
- **Failure scenario:** A writer is changed to attach a revision, but its query
  reads a different revision source than the mutation event. The Worker
  invalidates correctly while an old writer still repopulates a stale value.
- **Evidence:** Existing cache writes use different fields and paths, e.g.
  `get_daily_macros_query_handler.py:358-367` uses `target_revision`, while
  `redis_client.py:160-188` fences only the supplied revision.
- **Suggested fix:** Make the canary key set an explicit Phase 1 output. Either
  complete fence propagation for that set before Phase 2 completion or exclude
  the key from Cloudflare routing; do not silently claim whole-cache fencing.

## Finding 3: Worker package ownership and deployment source are assumed — MEDIUM

- **Location:** Phase 4 Worker layout and Phase 5 CI workflow
- **Flaw:** The repository has no existing Wrangler Worker project; root
  `package.json` only contains the `claude` dependency. The plan proposes a
  nested Worker and optional GitHub workflow without deciding who owns its
  deployment and lockfile.
- **Failure scenario:** Backend CI validates Python but never installs/tests or
  deploys the Worker, leaving a green backend change with an unshipped consumer.
- **Evidence:** `package.json` contains only `claude`; no `wrangler*` or Worker
  source exists at repository root, while deployment workflows are under
  `.github/workflows/`.
- **Suggested fix:** Phase 4 must define the nested package's lockfile/test
  command; Phase 5 must make CI ownership explicit and require a deployed
  Worker revision in rollout evidence.

