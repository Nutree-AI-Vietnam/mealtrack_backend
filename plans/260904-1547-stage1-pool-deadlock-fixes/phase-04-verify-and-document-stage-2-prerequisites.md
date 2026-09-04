---
phase: 4
title: "Verify and document Stage 2 prerequisites"
status: pending
effort: "S"
---

# Phase 4: Verify and document Stage 2 prerequisites

## Overview

Confirm Stage 1 is solid, then document the next ops steps (Neon pooler, workers) without implementing them yet unless explicitly approved.

## Implementation Steps

1. Run focused unit suite for weekly budget + event bus wiring + daily macros consolidation.
2. Update `docs/troubleshooting.md` with the nested-checkout deadlock symptom → Stage 1 root cause (not just "increase pool size").
3. Checklist for Stage 2: `DB_CONNECTION_MODE=neon_pooler`, `-pooler` URL, per-worker pool sizes, `/v1/health/db-pool` monitoring.
4. Explicitly mark Stage 3 (Redis shield + Render autoscaling) as follow-on — out of scope for this PR series.

## Success Criteria

- [ ] Troubleshooting doc mentions nested UoW as a QueuePool cause
- [ ] Stage 2/3 checklist written; no premature pooler cutover in this plan unless approved
