# Background AI Jobs API

**Status:** Feature contract — non-derivable rules only  
**Routes:** `/v1/ai-jobs`, `POST /v1/chat/messages` with `ai_job_id`,
`GET /v1/capabilities/ai-jobs`, `POST|DELETE /v1/notifications/tokens`
(see OpenAPI at `/docs` for shapes and auth)  
**Code:** rules in `src/domain/model/ai_jobs/`, use cases, worker and notifier
in `src/app/services/ai_jobs/`, wiring in `src/bootstrap/ai_jobs.py`

A meal scan, a text parse, a recipe import, a Ghi nhanh sentence or a chat
reply can run as a background job. The
app queues it and moves on, a worker runs it, and the result shows up in the
app. A user who has left the app gets a push instead.

---

## Lifecycle

`queued` → `running` → `completed`, `failed` or `cancelled`

- A job gets 4 attempts: the first run plus 3 automatic retries
  (`AI_JOB_MAX_ATTEMPTS`). Only transient errors are retried automatically.
- Between attempts the job is `queued` again with `next_attempt_at` set. The
  delay is 5 s × 2^(attempt − 1) with ±20 % jitter, capped at 1 h, so
  retries come roughly 5 s, 10 s and 20 s apart.
- An attempt that runs longer than `AI_JOBS_EXECUTION_DEADLINE_SECONDS`
  fails with `AI_TIMEOUT`, which is transient. A `recipe_import` attempt
  gets at least 120 s, since a video may be downloaded and transcribed.
- A worker that dies keeps its lease (`AI_JOBS_LEASE_SECONDS`) until the lease
  expires. Another worker then takes the job, and the lost attempt still
  counts. If it was the last attempt, the job fails with `WORKER_INTERRUPTED`.
  A worker that shuts down cleanly records `WORKER_INTERRUPTED` at once, so
  the job does not wait for its lease.
- Failed and cancelled jobs stay readable, so nothing is dropped silently.
  `GET /v1/ai-jobs` lists only the last 24 hours, but `GET /v1/ai-jobs/{id}`
  reads any of the user's jobs until retention deletes it
  (`AI_JOBS_RETENTION_DAYS`, 30 days by default).

## Who runs what

- Every API process accepts new jobs and answers reads.
- Processes with `AI_JOBS_WORKER_ENABLED` also run the worker and the push
  notifier. Unless at least one process does, queued jobs never run. In
  production that is `nutree_backend`. Preprod, which shares production's
  database, never runs a worker. See
  [Deployment topology](system-architecture.md#deployment-topology-render).
- The worker has two sets of lanes, each with its own cap across all
  workers:
  - **Short jobs** (meal scans, text parses, Ghi nhanh, recipe imports, chat
    retries): `AI_JOBS_WORKER_CONCURRENCY` lanes per process,
    `AI_JOBS_GLOBAL_CAPACITY` running at once. Chat turns do not count
    against the cap, because chat has its own concurrency limits.
  - **Long jobs** (`weekly_plan`, which runs for minutes):
    `AI_JOBS_LONG_WORKER_CONCURRENCY` lanes per process (0 runs none in this
    process), `AI_JOBS_LONG_GLOBAL_CAPACITY` running at once.

  A burst of weekly plans therefore never makes a meal scan wait.
- A worker claims only job types it has an executor for. A job of a type the
  running worker does not know yet (a web service or preprod deployed ahead
  of the worker) stays queued until a worker that knows it starts.
- Each worker process also runs a janitor. A minute after start-up and then
  hourly, it deletes finished jobs (completed, failed, cancelled) created
  more than `AI_JOBS_RETENTION_DAYS` ago, at most 50,000 rows a pass, in
  batches of 1,000.
- A job queued in the same process wakes that process's idle lanes at once.
  A worker in another process finds it on its next poll: short lanes poll
  every `AI_JOBS_POLL_SECONDS`, and long lanes back off to 5 s when idle.
- A chat turn runs in the API process that received it, because that process
  streams the reply. A worker picks up a chat job only to retry it.

## Queue a job — `POST /v1/ai-jobs`

Body: `{"id": "<client UUID>", "type": "meal_scan" | "parse_text" | "recipe_import" | "describe_log" | "describe_movement" | "describe_hydration", "input": {…}}`

| Status | Meaning |
|--------|---------|
| `202` | Job created |
| `200` | This id was already queued with the same input; the existing job is returned |
| `409 AI_JOB_CONFLICT` | This id belongs to a different type, input or user |

- The client makes the id, so resending the request never creates a second
  job.
- `input` is checked before anything is queued, with the same rules as
  `POST /v1/meals/scan-by-url`, `POST /v1/meals/food-label/scan-by-url`,
  `POST /v1/meals/parse-text` and `POST /v1/recipe-imports/extract`. Field
  errors are `422` with `loc` starting `["body", "input", …]`.
- A `recipe_import` job also counts against the recipe import daily quota
  (`429 RECIPE_IMPORT_DAILY_LIMIT`); see [Recipe import](api-recipe-import.md).
- `chat` cannot be queued here (`400 INVALID_JOB_TYPE`); see
  [Chat turns](#chat-turns).
- Ghi nhanh jobs (`describe_*`, see [below](#ghi-nhanh-jobs)) run the
  quick-log guard of their route when queued: switch, Premium, daily quota
  and sentence, with the same errors (`503 FEATURE_DISABLED`,
  `402 SUBSCRIPTION_REQUIRED`, `429 DESCRIBE_DAILY_LIMIT`,
  `400 DESCRIBE_TEXT_REJECTED`). The quota is spent once per job id; while
  jobs are switched off the request is refused before it.
- The job's language is the request's `Accept-Language`. It picks the push
  copy.
- Rate limit: 30 a minute per user, counted separately for queueing and
  for `/retry`.

### Results

| Type | `result` when `completed` |
|------|---------------------------|
| `meal_scan` | `{"meal_id": "…"}`. The meal is saved like a normal scan, so the app reads it from the meal routes |
| `parse_text` | The same body `POST /v1/meals/parse-text` returns. Finding no foods is still a result |
| `chat` | `{"thread_id": "…", "assistant_message_id": "…"}`. The app reloads the thread to show the reply |
| `recipe_import` | `{"recipe": {…}}`, the unsaved draft `POST /v1/recipe-imports/extract` returns. The app shows it for review and saves it with `POST /v1/recipe-imports/save` |
| `describe_log` | The body `POST /v1/log/describe` returns, without the saved meals' detail (`meal` is null; the app reloads the day). A sentence that saved nothing fails with `DESCRIBE_NOTHING_RECOGNIZED` instead of answering `nothing_found` |
| `describe_movement` | The body `POST /v1/movement/describe` returns |
| `describe_hydration` | The body `POST /v1/hydration/describe` returns |

A meal scan's meal id is fixed when the job is queued, so a retry after a lost
lease returns the meal that was already saved and never analyzes the image
twice.

### Ghi nhanh jobs

The water, activity, supplement and + panel quick-log boxes can hand their
sentence off as a job, so the person can leave the app and get a push when
it is logged.

| Type | `input` (same fields as the route) |
|------|------------------------------------|
| `describe_log` | `text`, `local_date`, `local_time`, `kinds` (default all four) |
| `describe_movement` | `text`, `target_date`, `weight_kg` |
| `describe_hydration` | `text`, `target_date` |

- `X-Timezone` is read when the job is queued and used for the day and the
  quota, as on the route. `Accept-Language` is the job's language.
- The job id is the sentence's replay key: an attempt that saved the entries
  but lost its lease replays the saved reply instead of logging twice, and
  `DELETE /v1/log/describe/{job_id}` undoes a `describe_log` job.
- Ghi nhanh errors that retrying cannot fix are permanent:
  `DESCRIBE_NOTHING_RECOGNIZED`, `DESCRIBE_TEXT_REJECTED`, `INVALID_DATE`,
  `INVALID_TIME`, `IDEMPOTENCY_KEY_CONFLICT`. `AI_UNAVAILABLE` and
  `IDEMPOTENCY_KEY_IN_PROGRESS` are retried automatically.
- A describe attempt is not interrupted once it runs, like a meal scan, so a
  cancelled sentence still finishes saving.

## Watch jobs

- `GET /v1/ai-jobs/{id}` returns one job.
- `GET /v1/ai-jobs?ids=a,b,c` returns up to 20 of the user's jobs (`ids`
  wins over `state`).
- `GET /v1/ai-jobs?state=…` returns the 20 newest jobs of the last 24 hours:
  - `open` (the default): active jobs plus finished ones the user has not
    seen yet
  - `active`: `queued` or `running`
  - `unseen`: finished and not seen yet
- While the app is open it polls every `poll_interval_ms` (2500) from
  capabilities. There is no socket or stream for queued jobs.

## Seen, and when a push goes out

- A finished job becomes due for a push `AI_JOBS_NOTIFY_GRACE_SECONDS` (8 s)
  after it finishes.
- When the app shows a result, it sends `POST /v1/ai-jobs/seen` with up to
  50 ids. A job seen before it is due never gets a push. This is how "push
  only when the user is away" works: the server does not know whether the app
  is open, so an open app has to mark the result seen within the grace period.
- Each job gets at most one push. Sending is tried up to 3 times, 30 s and
  then 60 s apart. A push still unsent an hour after it was due is dropped.
- Cancelled jobs are never pushed. A job's push is claimed before it is sent,
  so a crash can lose a push but never send it twice.
- Copy exists in English and Vietnamese
  (`src/domain/model/ai_jobs/notification_copy.py`). Every other language
  gets English.

### Push payload

`data`: `type: "ai_job"`, `job_id`, `job_type`, `status`, `title`, `body`,
plus `meal_id` for a completed meal scan, or `error_code` for a failure.

| Platform | Delivery |
|----------|----------|
| iOS | APNs alert with sound, priority 10, expires after 1 h, `apns-collapse-id` = job id, thread `ai_job` |
| Android | Data-only message, high priority, TTL 1 h, collapse key = job id. The app draws the notification itself, so it can skip one whose result is already on screen |

The push should open the result: `meal_id` opens the meal, `parse_text` opens
the parse review for `job_id`, `recipe_import` opens the recipe review for
`job_id`, `chat` opens the coach thread, `describe_log` opens Home,
`describe_movement` the activity list and `describe_hydration` the water
screen, and a failure opens the job's failure view.

### Device tokens

- `POST /v1/notifications/tokens` now stores the token (up to 1024
  characters) for the signed-in user. A token registered by a second account
  on the same phone moves to that account.
- `DELETE /v1/notifications/tokens` stops pushes to that device, for example
  on sign-out. Tokens that FCM reports as invalid are switched off
  automatically.
- Pushes go to the user's 10 most recently registered devices. Rows saved
  before this change have no `last_registered_at`, so they get nothing until
  the device registers again.
- Both routes answer `200` before the table is migrated. The register
  message is then `"Token not stored"`, and the app registers again on its
  next launch.
- Since #632 these routes only acknowledged the token, and #569 removed the
  old push code. No other code in this repository reads `user_fcm_tokens`.
  Before turning this on, check that no service outside this repository
  pushes to the tokens in that table.

## Retry and cancel

- `POST /v1/ai-jobs/{id}/retry` runs a failed job once more, with no new
  automatic retries. It works only when `error.retryable` is true. Anything
  else is `409 AI_JOB_NOT_RETRYABLE`, including cancelled jobs. A chat turn
  can also be retried by resending the same message (below).
- `POST /v1/ai-jobs/{id}/cancel`:
  - a `queued` job is cancelled at once;
  - a running `parse_text`, `recipe_import` or `chat` attempt stops within
    a heartbeat (`AI_JOBS_HEARTBEAT_SECONDS`);
  - a running `meal_scan` finishes. If it succeeds, the job ends `completed`
    and the meal is saved, but no push goes out. If it fails, the job ends
    `cancelled`;
  - a finished job is returned unchanged.

## Errors on a job

`job.error` is `{code, kind, retryable}`. `kind` says whether the server
retries on its own; `retryable` says whether the app should offer a retry
button. The codes match the synchronous endpoints, so the app can reuse its
failure copy.

| Codes | `kind` | `retryable` |
|-------|--------|-------------|
| `NOT_FOOD_IMAGE`, `NOT_FOOD_LABEL_IMAGE`, `AI_CONTENT_REJECTED`, `CHAT_DAILY_LIMIT`, `CHAT_STOPPED`, `INVALID_JOB_INPUT`, `INVALID_RECIPE_SOURCE`, `RECIPE_URL_NOT_ALLOWED`, `RECIPE_SOURCE_REJECTED` | permanent | false |
| `AI_OUTPUT_INVALID`, `NUTRITION_INTEGRITY_REJECTED`, `NOT_FOOD_TEXT`, `NOT_A_RECIPE`, `RECIPE_VIDEO_UNAVAILABLE` | permanent | true |
| `AI_UNAVAILABLE`, `AI_TIMEOUT`, `DB_UNAVAILABLE`, `NUTRITION_PROVIDER_UNAVAILABLE`, `CHAT_PROVIDER_UNAVAILABLE`, `CHAT_BUSY`, `CHAT_RATE_LIMITED`, `CHAT_TURN_FAILED`, `WORKER_INTERRUPTED`, `INTERNAL_ERROR`, `FAILED_TO_ANALYZE_MEAL_IMAGE`, `RECIPE_SOURCE_UNREACHABLE`, and any code not listed here | transient | true |

A transient failure the app sees has already used all of its automatic
retries.

## HTTP errors

Errors use the shared body `{"detail": {"error_code", "message", "details"}}`.
The rate limiter is the exception (below).

| Status | `error_code` | When |
|--------|--------------|------|
| `400` | `INVALID_JOB_TYPE` | `type` is not `meal_scan`, `parse_text` or `recipe_import`; `details.supported` lists the types |
| `400` | `INVALID_AI_JOB_ID`, `TOO_MANY_AI_JOB_IDS` | Bad `ids` in a list request |
| `400` | `INVALID_MEAL_TEXT`, `INVALID_LABEL_CROP_IMAGE`, `INVALID_DATE_FORMAT`, `INVALID_IMAGE_URL`, `INVALID_IMAGE_ID`, `IMAGE_ID_URL_MISMATCH` | Input the synchronous routes reject too |
| `404` | `AI_JOB_NOT_FOUND` | Unknown id, or another user's job |
| `409` | `AI_JOB_CONFLICT` | The id belongs to a different request |
| `409` | `AI_JOB_NOT_RETRYABLE` | Retry of a job that is not a retryable failure; `details.status` |
| `429` | — | Rate limited. Body `{"error": "rate_limit_exceeded", "code": "preview_rate_limited"}` with `Retry-After` |
| `503` | `AI_JOBS_UNAVAILABLE` | `details.reason`: `disabled` (queueing and retry are off) or `schema_missing` (not migrated) |

Reads, cancel and seen still work while `AI_JOBS_ENABLED` is off, so the app
can finish what it already queued.

## Chat turns

- `POST /v1/chat/messages` takes an optional `ai_job_id` (client UUID). With
  it, the reply keeps generating if the app closes, and the job records the
  outcome. Without it, chat works as before.
- `message.started` and the final event carry `ai_job_id`. The final event
  also carries `ai_job_status`: `completed`, `queued` (an automatic retry is
  coming), `failed`, `cancelled`, `running` or `null`. Anything but
  `completed` means the app should follow `GET /v1/ai-jobs/{id}`, then reload
  the thread. There is no `interrupted` status. `WORKER_INTERRUPTED` is a
  transient error code: while attempts remain the job is `queued` again and
  retried after backoff.
- Resending the same message with the same `Idempotency-Key` and `ai_job_id`
  takes over a queued or failed job, or a running one whose lease has
  expired. A replayed turn streams without `ai_job_id`.
- The same `ai_job_id` sent with a different message is
  `409 AI_JOB_CONFLICT` in the shared error body, and the turn is abandoned.
- `GET /v1/chat` returns `in_flight.ai_job_id` while a job is generating the
  reply.
- If jobs are off or not migrated, or background chat is disabled, the turn
  streams as a plain turn with no `ai_job_id`.
- A regenerated or edited reply (`regenerate_assistant_id` plus `ai_job_id`)
  is a chat job. The job stores `prepare_regeneration` and
  `regenerate_assistant_id`, and a retry reopens that same turn. Its
  `dedupe_key` is the stored user message's idempotency key, so
  `in_flight.ai_job_id` still matches. Without `ai_job_id` the reply streams
  the legacy way.
- When `ai_job_id` already belongs to this same request and another attempt
  owns it, the response does not generate again. It sends `message.started`
  with `ai_job_id`, then a terminal event whose `ai_job_status` tells the
  app to follow that job. A second regenerate of a running or queued job
  gets that follow stream. Resending a completed regenerate replays the
  saved reply and does not reopen it.
- Stopping a chat job ends it `cancelled` with no `error`; the stream's final
  `message.error` has the code `CHAT_STOPPED`. Cancelled jobs are never
  pushed.

## Capabilities — `GET /v1/capabilities/ai-jobs`

`{"ai_jobs", "types", "max_attempts", "poll_interval_ms", "chat_background", "chat_regenerate_background"}`

- `ai_jobs` is false while `AI_JOBS_ENABLED` is off. The app then keeps using
  the synchronous endpoints.
- `chat_background` and `chat_regenerate_background` are true only when both
  `AI_JOBS_ENABLED` and `AI_JOBS_CHAT_BACKGROUND_ENABLED` are on.
- `503 AI_JOBS_UNAVAILABLE` until the migration has run.

## Configuration

| Setting | Default | Use |
|---------|---------|-----|
| `AI_JOBS_ENABLED` | `false` | Accept new jobs and advertise the capability |
| `AI_JOBS_WORKER_ENABLED` | `false` | Run the worker and notifier in this process |
| `AI_JOBS_WORKER_CONCURRENCY` | `2` | Short-job lanes per worker process (1–16) |
| `AI_JOBS_GLOBAL_CAPACITY` | `4` | Short jobs running across all workers, chat aside (1–64) |
| `AI_JOBS_LONG_WORKER_CONCURRENCY` | `2` | Long-job (weekly plan) lanes per worker process; 0 runs none here (0–16) |
| `AI_JOBS_LONG_GLOBAL_CAPACITY` | `4` | Long jobs running across all workers (1–64) |
| `AI_JOBS_RETENTION_DAYS` | `30` | Worker processes delete finished jobs created longer ago than this; queued and running jobs are kept (1–3650) |
| `AI_JOBS_LEASE_SECONDS` | `120` | How long a silent worker keeps a job (30–3600) |
| `AI_JOBS_EXECUTION_DEADLINE_SECONDS` | `90` | Longest single attempt (10–600) |
| `AI_JOBS_POLL_SECONDS` | `1.0` | How often idle short lanes look for work; a new job in the same process wakes them at once (0.1–60) |
| `AI_JOBS_HEARTBEAT_SECONDS` | `2.0` | Lease renewal and cancel check (0.5–60) |
| `AI_JOBS_NOTIFY_GRACE_SECONDS` | `8` | Wait after a job finishes before it may be pushed (0–600) |
| `AI_JOBS_CHAT_BACKGROUND_ENABLED` | `true` | Let chat turns become jobs |

## Rollout

1. Run the migration with both flags off. Token registration starts storing
   tokens at once, so devices are known before any push is due.
2. Set `AI_JOBS_WORKER_ENABLED=true` on production's service. Never set it
   on preprod, which shares the production database.
3. Set `AI_JOBS_ENABLED=true`.

If the worker later moves to its own service, deploy it before the web
service, so it already knows any new job type the web service starts
queuing.

Turning `AI_JOBS_ENABLED` off stops new jobs and manual retries. Workers keep
draining what is already queued until `AI_JOBS_WORKER_ENABLED` is off too.

## Not built yet

- Push copy for languages other than English and Vietnamese.
- Out of scope for this ticket: quiet hours, and job types other than chat,
  meal scan, text parse and recipe import.
