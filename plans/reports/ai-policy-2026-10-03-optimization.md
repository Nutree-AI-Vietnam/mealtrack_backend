# Weekly planner AI policy — 3 October 2026

Status: locally implemented; provider quality and deployed capacity gates pending.
Work context: `/Users/alexnguyen/Desktop/Nut/mealtrack_backend`, base `cbc31522` plus shared uncommitted optimization work.

## Final behavior

- `meal_plan_adjustment` is a separate purpose in the domain enum, string map and manager chain. Manager chooses OpenAI explicitly; neither configured Cloudflare text purposes nor vision purposes may add planner routes, even when their configuration lists the planner purpose. Its model is the configured `OPENAI_TEXT_MODEL`; changing this deployment setting still changes its model.
- Dedicated cached planner LangChain clients retain Responses API strict output and prompt-cache policy, with SDK `max_retries=0` and transport timeout at most 25 seconds. Shared scan/parse client retry settings remain unchanged. Per-attempt timeout is the remaining provider budget; installed LangChain payload and actual SDK retry settings are asserted locally.
- One provider budget of at most 25 seconds includes admission, retry delay and both possible attempts. The manager allows one transient retry for connection/timeout/429/5xx; deterministic validation/input failures and permanent auth/input failures do not retry. Numeric or date Retry-After is honored only when it fits. Circuit-open planner calls fail without a forced attempt or generic fallback.
- Provider admission is bounded by one process-local semaphore and at most 1-second initial wait by default. Deadline expiration or user cancellation releases held capacity; a waiting request never creates a provider call after admission times out.
- Middleware starts the request deadline before authentication/dependencies and bounds the full planner AI ASGI request to 30 seconds. Before headers, timeout returns the existing 503 `AI_MEAL_PLAN_UNAVAILABLE` envelope; delivered headers are never replaced by a second response. Other routes retain prior timeout exception behavior.
- The adapter receives the integrator's eligible shortlist and refuses more than 40 recipes instead of silently taking the first 200. App-owned named matching, saved/profile constraints and deterministic post-provider validation remain separate authorities.
- A single strict structured response supplies explanation, optional additive diff_summary and at most 21 slot changes in the requested language. Output cap is 1,800 tokens, down from 3,000. Existing broad explanation-schema compatibility remains; the system prompt asks for a concise sentence.
- Phase timing records bounded admission/attempt labels. Existing OpenAI prompt-cache metrics cover input/cached tokens; the planner additionally records output tokens and engine attempts. New policy failure descriptions retain exception class rather than private provider text.

## Deployment allocation

| Configuration | Default | Meaning |
|---|---|---|
| `MEAL_PLAN_AI_CAPACITY_PER_PROCESS` | `2` | Simultaneous interactive planner provider calls per API worker process |
| `MEAL_PLAN_AI_ADMISSION_WAIT_SECONDS` | `1` | Maximum initial wait, also capped by remaining provider/request deadline |
| `OPENAI_TEXT_MODEL` | Existing configured model | Planner's explicit OpenAI model; no inherited `general` provider chain |

Fixed deployment capacity is `replicas × API workers per replica × MEAL_PLAN_AI_CAPACITY_PER_PROCESS`. For example, two replicas with four API workers and capacity two permit at most 16 interactive provider calls. This is arithmetic, not a measured recommendation.

Pin replica/worker count and choose each process's share of the owner-approved global interactive quota before rollout. Account separately for preparation worker calls and other OpenAI purposes. A process-local semaphore cannot enforce one global quota during autoscaling; shared leased admission and crash recovery must precede autoscaling. No distributed admission authority or preparation quota was added in this work package.

## Validation

- Project `.venv` Python 3.13.2 compilation passed for changed AI/policy/middleware files and tests.
- Focused Ruff passed. Final combined AI/routing/adapter/phase/middleware/error-owner/pool/HTTP contract run: 163 passed, 4 warnings, 3.70 seconds.
- Tests cover explicit routing despite both CF purpose lists, no CF fallback without OpenAI, actual separate cached SDK retry policy, remaining transport timeout, permanent/validation failure classification, at most two transient attempts, retry delay exceeding deadline, cancellation/released semaphore, saturated admission, locale/diff in one output, oversize shortlist rejection, explicit expired deadline, and full pre-header dependency timeout.
- Existing parse-text, vision, LangChain, meal-generation and middleware exception-owner tests remain in the focused validation set.

## Remaining release gates

- Live OpenAI latency, actual generated/cached tokens, quality and requested locale behavior across named recipes, hard diets/allergies, no-op/clear requests and target slots remain unmeasured.
- The 40-candidate and 1,800-output-token budgets need provider-isolated evaluation. Local tests do not establish production usefulness or latency.
- Confirm worker/replica allocation, model, rate limits and preparation quota; run deadline/degradation/cancellation canaries and compare latency/errors under agreed load before deploy approval.
- Main owns shared service/route deadline, language forwarding, named shortlist, stale apply and domain checks. Tester/reviewer own cross-package verification and complete PostgreSQL/load/CI evidence.

Docs impact: minor. This report documents local policy/configuration and explicit rollout boundaries.

Unresolved questions: deployed provider quality, quota allocation and agreed SLOs.
