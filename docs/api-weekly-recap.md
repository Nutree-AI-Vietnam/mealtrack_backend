# Weekly Recap API ("Weekly Wrapped")

**Status:** Feature contract — non-derivable rules only  
**Route:** `GET /v1/recaps/latest` (see OpenAPI at `/docs` for registration and auth)  
**Payload shape:** built by `generate_recap` in `src/domain/services/weekly_recap/`;
a full Vietnamese example is `tests/fixtures/weekly_recap_golden_vi_v1.json`

The route returns the recap of the user's newest finished week (Monday to
Sunday, local time).

---

## Request

| Header | Use |
|--------|-----|
| `Authorization` | Firebase bearer token, as on every `/v1` route |
| `Accept-Language` | First tag picks the copy: `en`, `vi`, `es`, `fr`, `de`, `ja`, `zh`; anything else gets `en` |
| `X-Timezone` | IANA zone, used only when the user has no stored zone other than UTC |

The week boundary follows the user's zone: stored zone, then a valid
`X-Timezone`, then UTC (`resolve_user_timezone_async`).

## Responses

| Status | Meaning |
|--------|---------|
| `200` | The recap payload |
| `204` | The newest finished week has nothing to recap (below). Nothing is stored, so a later read checks again |
| `404` | Unknown user. The app treats it like `204` |

A week has nothing to recap when no food was logged in it, when the account
existed for fewer than `min_week_days` (3) of its days, or when no calorie
target applies.

## When a week comes out

- A week comes out the following **Monday at 08:00 local time**
  (`PUBLISH_HOUR` in `get_latest_weekly_recap_query_handler.py`). Before
  that, the route still returns the week before.
- The app schedules around the same hour (`RecapSchedule.publishHour`).
  Change both together, or the app flags fresh recaps as outdated.
- The recap is built on the first read at or after that time. There is no
  scheduled job and no push notification yet.

## Snapshot rules

- The first build is stored in `weekly_recaps` (one row per user and week):
  the week's numbers (`stats`), the chosen story (`case_id`), and the payload
  in the language of that request. Later edits to the week's logs do not
  change it.
- A read in another language retells the stored numbers without storing
  them. If today's thresholds would pick a different story, or the stored
  numbers cannot be read, the stored payload is returned as it is.
- When two first reads race, one insert wins and both get the stored row.

## Calorie bars

- In a calorie `spark` (`invert: true`), `target` is the most a day can take
  in and still count as on budget: the calorie target × 1.05
  (`on_budget_tolerance`), the same limit the "days on budget" count uses.
  The app draws bars above it red.
- `targets` gives that limit for each day. The app falls back to `target`
  when `targets` is missing or doesn't have one value per bar.
- Bulk weeks also send `floors`, the least each day can take in and still
  count: the calorie target × 0.9 (`under_ratio`). The app mutes bars
  below it, and ignores `floors` without one value per bar.

## Personal details

All of these are fixed in the stored `stats`, so a retelling in another
language says the same thing.

- **Name.** The intro title greets the user by name
  (`{case}.title.named`, `recap_first_name`). It is read from
  `display_name`, then `first_name`; the stored `first_name` is only the
  display name's first word, which is the family name for a name written
  the Vietnamese way. So: behind a common Vietnamese surname, the last word
  ("Nguyễn Văn Linh" → Linh); a Chinese or Japanese name, whole; otherwise
  the first word. Handles, e-mails and names over 20 characters get no
  greeting. The share card never carries the name.
- **Go-to dish.** In `steady`, `bulk` and `perfect` weeks, a dish logged 3+
  times replaces the intro `sub` (`{case}.sub.dish`).
- **Diet.** The protein tip names foods the profile's diet allows
  (`vegetarian`, `vegan`, `keto`; anything else gets the default) and the
  main meal with the least protein per serving, logged at least twice.
- **No guessed causes.** Notes state what the logs show (driest day, the
  biggest meal on an over-budget weekend day, the sugary drink behind half
  or more of the week's sugary drinks) and never a cause the logs can't
  show, such as eating out or busy days.

## Last week

- `stats.last_week` summarizes the **stored** recap of the week before
  (`last_week_of`). There is none when that week was never read: no recap
  was built for it.
- A card can carry `trend: {text, up, good}`: which way its number moved
  since last week and whether that helps. Averages need a change of at
  least a step (protein 5 g, fiber 2 g, water 0.2 L); counts (nights,
  drinks, days, workouts) need 1, and compare only when both weeks counted
  the same number of days. Win cards show only good trends.
- `last_week.plan` lists the issues last week's plan took on. The first one
  that no longer holds and whose number moved a step the right way becomes
  the top win card (`follow_through`, icon `check_circle`): this week's
  number, with last week's in its trend.

## Configuration

`WEEKLY_RECAP_CONFIG_JSON` overrides recap thresholds by `RecapConfig` field
name, for example `{"low_data_meals": 8, "cases": {"steady": {"grow": 2}}}`.
Empty keeps the design defaults. Invalid JSON, unknown keys, and values of
the wrong type are ignored with a warning. The value is read once per
process, when the handlers are built, so a change needs a restart.

## Not built yet

- Push notification when a recap comes out (the app handles a
  `weekly_recap` notification and the `nutree://recap` link).
- The Settings switch to turn the recap off.
