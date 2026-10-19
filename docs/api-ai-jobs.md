# Background AI Jobs API

**Status:** Feature contract — non-derivable rules only  
**Routes:** `/v1/ai-jobs`, `POST /v1/chat/messages` with `ai_job_id`,
`GET /v1/capabilities/ai-jobs`, `POST|DELETE /v1/notifications/tokens`
(see OpenAPI at `/docs` for shapes and auth)  
**Code:** rules in `src/domain/model/ai_jobs/`, use cases, worker and notifier
in `src/app/services/ai_jobs/`, wiring in `src/bootstrap/ai_jobs.py`

A meal scan, a text parse or a chat reply can run as a background job. The
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
  fails with `AI_TIMEOUT`, which is transient.
- A worker that dies keeps its lease (`AI_JOBS_LEASE_SECONDS`) until the lease
  expires. Another worker then takes the job, and the lost attempt still
  counts. If it was the last attempt, the job fails with `WORKER_INTERRUPTED`.
  A worker that shuts down cleanly records `WORKER_INTERRUPTED` at once, so
  the job does not wait for its lease.
- Failed and cancelled jobs stay readable, so nothing is dropped silently.
  `GET /v1/ai-jobs` lists only the last 24 hours, but `GET /v1/ai-jobs/{id}`
  reads any of the user's jobs.

## Who runs what

- Every API process accepts new jobs and answers reads.
- Processes with `AI_JOBS_WORKER_ENABLED` also run the worker
  (`AI_JOBS_WORKER_CONCURRENCY` lanes) and the push notifier. Unless at least
  one process does, queued jobs never run.
- `AI_JOBS_GLOBAL_CAPACITY` caps running meal scans and text parses across
  all workers. Chat turns do not count against it, because chat has its own
  concurrency limits.
- A chat turn runs in the API process that received it, because that process
  streams the reply. A worker picks up a chat job only to retry it.

## Queue a job — `POST /v1/ai-jobs`

Body: `{"id": "<client UUID>", "type": "meal_scan" | "parse_text", "input": {…}}`

| Status | Meaning |
|--------|---------|
| `202` | Job created |
| `200` | This id was already queued with the same input; the existing job is returned |
| `409 AI_JOB_CONFLICT` | This id belongs to a different type, input or user |

- The client makes the id, so resending the request never creates a second
  job.
- `input` is checked before anything is queued, with the same rules as
  `POST /v1/meals/scan-by-url`, `POST /v1/meals/food-label/scan-by-url` and
  `POST /v1/meals/parse-text`. Field errors are `422` with `loc` starting
  `["body", "input", …]`.
- `chat` cannot be queued here (`400 INVALID_JOB_TYPE`); see
  [Chat turns](#chat-turns).
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

A meal scan's meal id is fixed when the job is queued, so a retry after a lost
lease returns the meal that was already saved and never analyzes the image
twice.

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
the parse review for `job_id`, `chat` opens the coach thread, and a failure
opens the job's failure view.

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
  - a running `parse_text` or `chat` attempt stops within a heartbeat
    (`AI_JOBS_HEARTBEAT_SECONDS`);
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
| `NOT_FOOD_IMAGE`, `NOT_FOOD_LABEL_IMAGE`, `AI_CONTENT_REJECTED`, `CHAT_DAILY_LIMIT`, `CHAT_STOPPED`, `INVALID_JOB_INPUT` | permanent | false |
| `AI_OUTPUT_INVALID`, `NUTRITION_INTEGRITY_REJECTED`, `NOT_FOOD_TEXT` | permanent | true |
| `AI_UNAVAILABLE`, `AI_TIMEOUT`, `NUTRITION_PROVIDER_UNAVAILABLE`, `CHAT_PROVIDER_UNAVAILABLE`, `CHAT_BUSY`, `CHAT_RATE_LIMITED`, `CHAT_TURN_FAILED`, `WORKER_INTERRUPTED`, `INTERNAL_ERROR`, `FAILED_TO_ANALYZE_MEAL_IMAGE`, and any code not listed here | transient | true |

A transient failure the app sees has already used all of its automatic
retries.

## HTTP errors

Errors use the shared body `{"detail": {"error_code", "message", "details"}}`.
The rate limiter is the exception (below).

| Status | `error_code` | When |
|--------|--------------|------|
| `400` | `INVALID_JOB_TYPE` | `type` is not `meal_scan` or `parse_text`; `details.supported` lists the types |
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
  the thread.
- Resending the same message with the same `Idempotency-Key` and `ai_job_id`
  takes over a queued or failed job, or a running one whose lease has
  expired. A replayed turn streams without `ai_job_id`.
- The same `ai_job_id` sent with a different message is
  `409 AI_JOB_CONFLICT` in the shared error body, and the turn is abandoned.
- `GET /v1/chat` returns `in_flight.ai_job_id` while a job is generating the
  reply.
- If jobs are off or not migrated, or background chat is disabled, the turn
  streams as a plain turn with no `ai_job_id`.
- A regenerated reply (`regenerate_assistant_id`) never becomes a job: any
  `ai_job_id` sent with it is ignored and the reply streams as a plain turn.
- Stopping a chat job ends it `cancelled` with no `error`; the stream's final
  `message.error` has the code `CHAT_STOPPED`. Cancelled jobs are never
  pushed.

## Capabilities — `GET /v1/capabilities/ai-jobs`

`{"ai_jobs", "types", "max_attempts", "poll_interval_ms", "chat_background"}`

- `ai_jobs` is false while `AI_JOBS_ENABLED` is off. The app then keeps using
  the synchronous endpoints.
- `chat_background` is true only when both `AI_JOBS_ENABLED` and
  `AI_JOBS_CHAT_BACKGROUND_ENABLED` are on.
- `503 AI_JOBS_UNAVAILABLE` until the migration has run.

## Configuration

| Setting | Default | Use |
|---------|---------|-----|
| `AI_JOBS_ENABLED` | `false` | Accept new jobs and advertise the capability |
| `AI_JOBS_WORKER_ENABLED` | `false` | Run the worker and notifier in this process |
| `AI_JOBS_WORKER_CONCURRENCY` | `2` | Worker lanes per process (1–16) |
| `AI_JOBS_GLOBAL_CAPACITY` | `4` | Running meal scans and text parses across all workers (1–64) |
| `AI_JOBS_LEASE_SECONDS` | `120` | How long a silent worker keeps a job (30–3600) |
| `AI_JOBS_EXECUTION_DEADLINE_SECONDS` | `90` | Longest single attempt (10–600) |
| `AI_JOBS_POLL_SECONDS` | `1.0` | Worker poll when idle; a new job in the same process wakes it at once (0.1–60) |
| `AI_JOBS_HEARTBEAT_SECONDS` | `2.0` | Lease renewal and cancel check (0.5–60) |
| `AI_JOBS_NOTIFY_GRACE_SECONDS` | `8` | Wait after a job finishes before it may be pushed (0–600) |
| `AI_JOBS_CHAT_BACKGROUND_ENABLED` | `true` | Let chat turns become jobs |

## Rollout

1. Run the migration with both flags off. Token registration starts storing
   tokens at once, so devices are known before any push is due.
2. Set `AI_JOBS_WORKER_ENABLED=true` on at least one process.
3. Set `AI_JOBS_ENABLED=true`.

Turning `AI_JOBS_ENABLED` off stops new jobs and manual retries. Workers keep
draining what is already queued until `AI_JOBS_WORKER_ENABLED` is off too.

## Not built yet

- Nothing deletes old `ai_jobs` rows yet.
- Push copy for languages other than English and Vietnamese.
- Out of scope for this ticket: quiet hours, and job types other than chat,
  meal scan and text parse.
