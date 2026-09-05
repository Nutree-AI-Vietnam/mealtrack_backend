# Red-Team Review: Security Adversary

## Finding 1: Queue token compromise permits arbitrary cache deletion — HIGH

- **Location:** Phases 1, 3, and 4; event validation/security
- **Flaw:** The plan authenticates the HTTP publisher to Cloudflare but does not
  authenticate the event body to the Worker. A stolen Queue token could publish
  a syntactically valid event containing destructive keys/patterns.
- **Failure scenario:** An attacker with the Queue API token publishes a valid
  `delete_pattern` for another user's namespace. Queue and Worker accept it;
  cache state is wiped broadly and can create a hot-cache/database load event.
- **Evidence:** Current Redis supports destructive `delete` and SCAN-based
  pattern deletion at `src/infra/cache/redis_client.py:190-227`; the planned
  Worker is the new cross-service boundary.
- **Suggested fix:** Add a canonical-payload HMAC signature using a separate
  backend/Worker secret, verify it before Redis access, and require operation
  namespaces to match the signed aggregate. Keep Queue token and event-signing
  secret separate.

## Finding 2: Internal identifiers remain in Queue payloads — MEDIUM

- **Location:** Phases 1 and 4 security considerations
- **Flaw:** The plan bans sensitive payloads but still sends user-derived Redis
  keys and aggregate IDs. “Opaque” is not a privacy control if logs, DLQ
  inspection, or provider retention expose the value.
- **Failure scenario:** A DLQ operator or misconfigured Worker log includes a
  raw user ID embedded in a key. It is not an auth token, but it is unnecessary
  identity exposure.
- **Evidence:** Existing cache keys are built with user IDs at
  `src/app/services/cache_invalidation_service.py:129-138` and passed to Redis
  invalidation by `src/infra/cache/cache_service.py:190-232`.
- **Suggested fix:** Keep exact keys only because the Worker needs them, but
  sign them, hash them in all logs/metrics, prohibit payload logging, and
  restrict DLQ access. Do not claim the payload contains no user-derived data.

## Finding 3: Provider HTTP capability is not an authorization decision — MEDIUM

- **Location:** Phase 1 provider proof
- **Flaw:** The plan correctly requires an HTTP/atomic provider proof, but does
  not require separate read/write credentials or a namespace-limited Redis
  account.
- **Failure scenario:** The Worker secret is valid for the whole Redis DB. A
  Worker bug or injected operation can delete unrelated transient state.
- **Evidence:** Backend currently constructs one broad Redis URL in
  `src/infra/config/settings.py:84-100` and the client exposes general cache
  operations in `src/infra/cache/redis_client.py:144-227`.
- **Suggested fix:** Require a dedicated cache namespace and provider credential
  scoped to that namespace where the provider supports it; otherwise document
  the shared-DB residual risk and enforce the Worker operation allowlist.

