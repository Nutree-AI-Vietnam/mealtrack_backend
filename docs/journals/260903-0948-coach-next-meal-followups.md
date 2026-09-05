---
title: Coach next-meal discover + backend follow-ups
type: journal
date: 2026-09-03
---

# Coach next-meal + follow-ups

## Context

Coach next_meal is text-only. Follow-up chips are hardcoded on mobile.
User asked for recipes via web search + time-of-day meals + OpenAI follow-ups.

## What happened

Scouted chat + meal-suggestions. Discover already does slot + remaining-macro
targets + allergy-safe macros. Brave search is images/barcodes only. OpenAI
does not ship follow-up chips; ChatGPT UI is a second structured pass.

## Decisions

- Reuse discover (`count=3`). No web-search calorie path.
- Server clock → meal slot on chat context.
- Cards + follow_ups on `message.completed`, persisted.
- Recommend only. No log/save from chat.

## Next

Plan: `plans/260903-1012-coach-next-meal-followups/plan.md`.
