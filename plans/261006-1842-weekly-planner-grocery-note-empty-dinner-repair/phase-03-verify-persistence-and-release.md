---
phase: 3
title: "Verify persistence and release"
status: pending
priority: P1
effort: "0.5d"
dependencies: [2]
---

# Phase 3: Verify persistence and release

## Overview

Verify final code locally, then repeat the three user flows against the deployed staging revision and fresh reads on device. Keep environment-specific evidence separate.

## Implementation Steps

1. Backend: run focused planner tests, `pytest tests/unit --cov=src --cov-fail-under=65`, `ruff format --check`/`ruff check` on changed files, and Python compile check. Run targeted PostgreSQL integration tests if persistence code changes.
2. Flutter: run focused `test/features/recipes` tests, `flutter analyze`, and repository CI gate as appropriate to changed scope. Verify real request payloads and no hidden snackbars after failed writes.
3. On authenticated staging with Argent, save day note, mark/unmark/Undo, reopen and fresh-read groceries; verify Tuesday dinner appears or remains intentionally empty per persisted state; assign and replace a dinner recipe, reopen and check grocery projection and logged-slot rules. Capture deployed backend/mobile revisions.
4. Update existing release-blocker plan's validation evidence and project changelog only after actual proof; report any pending deployment/device gate clearly.

## Success Criteria

- [ ] Local targeted and CI-aligned checks pass without ignoring failures.
- [ ] Fresh staging reads match UI after every mutation, including Undo and route reopen.
- [ ] Exact revisions, requests, and any remaining deployment or device limits are recorded.
