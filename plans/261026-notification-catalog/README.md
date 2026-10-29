# Engagement notifications: static (everyone) and personalized (behavior)

Spec for the notification catalog that the mobile app (`nutree_ai`) and the backend
implement. The app plans almost everything on the device. The backend stores the
toggles and sends the one server push (payment failed).

## Why

- Before this change, every reminder was a daily-repeating local notification.
  It fired whether or not the user had already logged, and its text was frozen at
  the last app open. A user who left the app 20 days ago still got yesterday's
  "98% of goal" summary every night.
- Android never fired those reminders. The app manifest did not declare
  `ScheduledNotificationReceiver`, and flutter_local_notifications 22 does not
  declare it either.
- Non-subscribers received 4 paywall hooks a day without opting in, which breaks
  Apple App Store guideline 4.5.4.
- Duolingo-style pull comes from persistence and emotion, not volume: one nudge at
  the right time, a last-chance streak saver, and a lapse ladder that eventually
  stops.

## Decisions

| # | Decision |
|---|---|
| D1 | Cap: **3 engagement notifications per local day**, and **10 per rolling 7 days**. Account notices (trial ending, payment failed) and AI results are exempt. |
| D2 | Only **one** notification may fire in the evening window (17:30–21:30). Every notification is **at least 60 min** apart from the previous one. |
| D3 | Quiet hours: notifications the system picks a time for fire only between 07:00 and 21:30 local. User-chosen meal and water times are kept but clamped to 06:00–21:30. |
| D4 | Every reminder **cancels itself** once the user logs. The device rebuilds the plan on every app open, log, settings change and day change. |
| D5 | Copy rotates. A pool of variants per type, picked deterministically per day, and a template used in the last 14 days is skipped. Titles are dynamic and hooky; the app name already shows on iOS and Android. The voice is a small, dramatic buddy ("I" / "tui") that begs and sulks about itself, never about the user's food or body. The buddy word (`bro` / `bestie`) appears in about 1 of every 4 variants. Unknown gender gets neutral copy. |
| D6 | No shaming numbers. The under/over-goal summaries are retired. The evening check-in is either an on-plan celebration (at most 2 a week) or a neutral "day closed" note. |
| D7 | Promotional items (renewal offer, paywall follow-up) require the **Offers & new features** toggle, **off by default**, per Apple 4.5.4. Paywall hooks no longer replace meal reminders. |
| D8 | Users without access can't log, so they get no logging reminders. They get only the paywall follow-up, and only after opting in to offers. |
| D9 | The lapse ladder is anchored to the last log date: D2 fresh start · D3 comeback (last dish) · D5 comeback (just type it) · D7 reminders paused · D14 best streak · D30 last message, then silence until the next log. If the user opens the app on a lapse day, that day's message is cancelled, and at most one "today still counts" evening nudge is allowed. |
| D10 | Delivery: everything is a **local** one-shot on the device, because all inputs change only through in-app actions. **Payment failed** is the one server push (RevenueCat webhook → outbox → FCM). Moving lapse messages server-side for multiple devices is future work. |
| D11 | New settings toggles, stored by the backend: **Streak alerts** (default on), **Comeback nudges** (on), **Progress & recaps** (on), **Offers & new features** (off). |
| D12 | iOS interruption levels: the streak saver uses Time Sensitive, with fallback to Active. Logging nudges and account notices use Active. Progress notifications use Passive. Engagement notifications don't present while the app is in the foreground. |

## Catalog

Times are local. "Slot" means the user's meal-reminder time.

### Static, for everyone

| Kind | When | Frequency | Toggle |
|---|---|---|---|
| `meal_breakfast` / `meal_lunch` / `meal_dinner` | Meal slot, when that meal's own switch is on; skipped once that meal is logged; only on D0–D1 | ≤1 per meal per day | Meal reminders |
| `evening_checkin` | Summary time, only on logged days and only if the evening slot is free; on-plan or day-close copy | ≤1/day; on-plan ≤2/week | Daily summary |
| `win_the_day` | 20:00 when nothing is logged today (streak 0–2), or after an app open on a lapse day. At streak 2 the copy names the 3-day medal only if the best streak never reached 3 | ≤1/day | Streak alerts |
| `streak_saver` | Streak ≥3 with nothing logged today, at 20:30 (later for late loggers, ≤21:15) | ≤1/day, ≤4/week | Streak alerts |
| `first_meal` | Never logged: anchor +2h, then the next day's lunch, then day 3 lunch | 3 total | Meal reminders |
| `lapse_fresh_start` (D2), `lapse_paused` (D7), `lapse_final` (D30) | Around lunch | Once per lapse | Comeback nudges |
| `hydration` | The user's water reminder time (default 15:30), for water trackers under 50% of goal | ≤1/day, ≤3/week | Water reminders |
| `trial_ending` | ~24h before a trial converts (the paywall promises this reminder) | Once per trial | Account (always on) |
| `renewal_offer` | 14:00 the day before a monthly renewal (needs the one-time-offer flag) | Once per billing cycle | Offers |
| `paywall_follow_up` | 1 and 8 days after access is first seen missing, at 14:00 | 2 per episode | Offers |
| `billing_issue` (server) | Next 09:00–20:00 local window after RevenueCat `BILLING_ISSUE`; dropped if resolved | Once per episode | Account (always on) |

### Personalized, simple ones (implemented)

| Kind | Signal → message | Frequency |
|---|---|---|
| `lapse_comeback` (D3) | Last logged dish and its time ("Cơm tấm at 12:10 was your last log"). A drink logged later does not change the time. If the last meal is older than the last logged day, the copy names no dish | Once per lapse |
| `lapse_type_it` (D5) | Lower the friction: "just type it", using the last dish as the example | Once per lapse |
| `lapse_best_streak` (D14) | Best streak ≥3: "your 12-day record is still yours" | Once per lapse |
| Badge-day eve (copy overlay) | Logging today reaches a streak medal (3/10/50/100/365/1000) the user has not earned yet, or a new personal best. Medals follow the best streak, so a rebuilt streak gets no medal copy; the streak saver follows the same rule | Replaces the first slot's copy |
| Comeback day 2 (copy overlay) | Yesterday was the first log after a gap of 3+ days | Once per comeback |
| First-week journey (copy overlay) | Days 2–7 after the first log: one lesson a day (streak, text log, scan, water, coach, recap). Only for users the device saw before their first log. The day-2 and day-7 lessons ask for the day's first log, so they skip a day that is already logged | 6 total |
| `weekly_recap` | Monday 08:15 if the recap flag is on and the user logged in the last 7 days | 1/week |
| `hydration` with pace | Remaining litres and glasses in the copy | (same as hydration) |
| `protein_gap` | 16:30, user has logged today and protein is under 45% of target | ≤2/week |

### Left for later (complex)

- **Habit-time smart nudges**: needs a profile of the user's log times.
- **Missed-meal pattern**: needs weekly stats on the device.
- **Personal evening dish suggestion and remaining kcal**: needs the user's top dishes and the eating-disorder guard.
- **Weekend budget pacing.**
- **Weigh-in cadence.**
- **Goal checkpoints.**
- **Drink-time prompt.**
- **Coach follow-up.**
- **Subscription win-back**: a server push.
- **Streak freeze**: needs the feature itself.
- **What's-new broadcast**: needs release tooling.
- **Eating-disorder guard over intake history.**
- **Lock-screen detail toggle.**
- **Holdouts and a send/open ledger**: today only opens are tracked, as a PostHog event.
- **Moving lapse messages to the server** for users with more than one device.

## Implementation status

| Area | Where | Status |
|---|---|---|
| Planner, kinds, caps, overlays | `nutree_ai/lib/features/notifications/domain/engagement/` | Done; persona tests in `test/features/notifications/domain/engagement/` |
| Copy pools (≈190 variants per language) | `.../engagement/copy/engagement_copy_{en,vi,es,fr,de,ja,zh}.dart` | Done; EN is the master, a parity test checks the other languages |
| Memory (history, anchors) | `.../engagement/engagement_memory.dart`, `data/engagement_memory_store.dart` | Done; stored in SharedPreferences and cleared on sign-out |
| Scheduler (one-shots, diff, legacy cleanup) | `application/notification_scheduler_service.dart` | Done |
| Android receivers | `android/app/src/main/AndroidManifest.xml`, `MainApplication.kt` | Done. At startup, before the boot/update receiver re-arms the plugin's cache, `ScheduledNotificationCacheCleaner` drops the old daily reminders (ids 1001–1007, alarms included) and one-shots more than 15 minutes past. An update or a reboot therefore does not fire a burst of stale reminders |
| Channels / iOS levels / tap routing / analytics | `notification_channels.dart`, `notification_display_service.dart`, `app_router_wrapper.dart`, `fcm_message_handler.dart` | Done; `engagement_notification_opened` (kind, template) |
| Settings toggles | `settings/presentation/widgets/notification_settings.dart` + ARB keys | Done |
| Backend toggles + migration | `20261026185557393061_add_engagement_notification_toggles.py` | Done |
| Payment-failed push | `src/app/services/engagement_push/`, `bootstrap/engagement_push.py` | Done, behind `ENGAGEMENT_PUSH_ENABLED` (default off). The flag also gates queuing. A repeat `BILLING_ISSUE` in one episode queues nothing. Renewal, purchase or product change deletes the pending row |

Follow-ups (not done):
- FCM temporary failures are marked `failed` instead of retried.
- Nothing cleans up old outbox rows.
- Turn on `ENGAGEMENT_PUSH_ENABLED` on the API service. Every API process runs
  the dispatcher, and the webhook queues nothing while the flag is off.

## Rotation and the per-day cap

The device keeps a 14-day history of delivered notifications. An item counts as
delivered when it was still in the last plan and its fire time has passed.
Candidates for each day are ranked:

1. Account notices
2. Streak saver
3. First meal
4. Lapse messages
5. Win the day
6. Meal slots
7. Evening check-in
8. Weekly recap
9. Hydration
10. Protein gap
11. Offers

Then the caps are applied: 3 per day, the single evening slot, 60-min spacing, and 10 per rolling 7 days.

## Verification

- Pure planner unit tests simulate these users and check their daily and weekly counts:
  - an engaged logger;
  - a user at risk of losing the streak;
  - users lapsed 2, 3, 5, 7, 14 and 30 days;
  - a user who has never logged;
  - a user without access;
  - a user in trial.
- Scheduler tests check the diff/idempotency behaviour and that legacy ids are cancelled.
- Backend: preference tests, the migration up/down cycle, and the billing outbox and dispatcher tests.
