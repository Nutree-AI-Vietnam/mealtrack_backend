---
phase: 2
title: Convert singleton UoW to factory
status: in-progress
effort: L
---

# Phase 2: Convert singleton UoW to factory

## Overview

`event_bus.py` still registers ~28 handlers with `uow=AsyncUnitOfWork()` (one instance at wiring time). That instance's `asyncio.Lock` serializes all concurrent requests for that handler. Newer handlers already use `uow_factory=AsyncUnitOfWork`.

## Implementation Steps

1. Inventory every `uow=AsyncUnitOfWork()` registration in `event_bus.py`.
2. For each handler: accept `uow_factory` (callable returning fresh UoW), use `async with self.uow_factory() as uow`.
3. Keep temporary dual support (`uow=` OR `uow_factory=`) only where tests need it; prefer factory-only for production wiring.
4. Wire `uow_factory=AsyncUnitOfWork` (the class) everywhere in `event_bus.py`.
5. Add/extend architecture or unit test: configured event bus never injects a pre-constructed shared UoW instance for request handlers.
6. Also un-nest remaining TDEE-inside-UoW callers if found during the sweep (`journey_progress`, `daily_breakdown`, `nutrition_bulk`) — same pattern as Phase 1.

## Success Criteria

- [ ] Zero production `uow=AsyncUnitOfWork()` in `event_bus.py`
- [ ] Handler unit tests updated for factory injection
- [ ] No nested checkout in remaining hot TDEE callers
