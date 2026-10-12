---
phase: 4
title: "Flutter Refinement Result Application"
status: complete
priority: P1
dependencies: [1]
---

# Flutter Refinement Result Application

## Scope and files

Work context: `/Users/alexnguyen/Desktop/Nut/nutree/nutree_ai`. Modify existing `lib/features/meal_creation/application/providers/ai_meal_parser_provider.dart`, `application/mappers/parse_response_to_cart_mapper.dart`, and `presentation/screens/create_meal_screen.dart` under that feature. Reuse `meal_creation_provider.dart:74` (`replaceAllItems`). Read the Nutree README/instructions before implementation; preserve unrelated edits. No backend/API field changes, new screen, name delay, or mobile calorie derivation.

## TODOs

- [x] Capture an immutable submitted cart snapshot with the current parse generation; distinguish a new parse from a refinement using submitted state, not the current cart after the request finishes. Clear snapshot on reset and preserve `_parseGen` stale-result guards.
- [x] For initial parse, apply new returned rows once using existing behavior. For refinement, backend response is the complete updated meal: reconcile it with submitted rows and atomically call `replaceAllItems`, never append it to the old list.
- [x] Match old/new rows one-to-one using durable identity where available and unique normalized names when an identity is absent; repeated/ambiguous rows cannot steal metadata or overrides. Return no synthetic IDs to the API and add no public correlation field.
- [x] Reuse unchanged `CartFoodItem`s so IDs, source metadata, serving options, snapshots, manual overrides, and backend calories survive. Changed rows retain an ID when unambiguous and use updated backend values; new rows get fresh UUIDs and removed rows disappear.
- [x] Protect the draft against stale requests and in-flight edits; lock incompatible actions during parsing/saving and apply a completed result once.
- [x] Keep resume-after-collapse behavior; UI renders only `AiParseSuccess`, so no generic ingredient card or artificial name delay is needed.

## Validation planned for implementation

Focused mapper/provider/screen suite: 34 passed. Focused Flutter analyzer: no issues across 8 targets. iOS simulator debug build succeeded. Includes complete replacement, same-name catalog/custom fallback override retention, duplicate ambiguity, stale responses, pending-state UI, and no unnamed ingredient row before a response. Authenticated simulator/API save-read flow remains a staging release gate.

## Risks and success criteria

API rows lack client IDs; ambiguous matching must avoid accidental metadata reassignment. Preserve exact unchanged objects where possible and report unresolved matching cases before widening the API. This phase is complete when a refinement updates the cart exactly once without duplicate old rows, lost unchanged metadata, or stale result overwrites.
