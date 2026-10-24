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
  (`{case}.title.named`, `weekly_recap_names.recap_first_name`). It is read
  from `display_name`; `first_name` stands in only when the display name is
  missing or is not a name (a handle, an e-mail), because the stored
  `first_name` is just the display name's first word. Vietnamese names are
  written family name first ("Nguyễn Văn Linh" → Linh) or given name first
  ("Hoàng Nam Nguyễn" → Nam): the name's middle marker (Văn/Thị) and which
  end is a family name decide, and when that can't be told ("Nguyễn Lê")
  there is no greeting. Chinese, Japanese and Korean names are used whole.
  Honorifics are skipped; initials, handles and names with a part longer
  than 12 letters (it would break mid-word in the headline) get no
  greeting. The share card never carries the name, and an account's recaps
  are deleted with the account.
- **Go-to dish.** In `steady`, `bulk` and `perfect` weeks, a dish logged 3+
  times replaces the intro `sub` (`{case}.sub.dish`).
- **Diet.** The protein tip names foods the profile's diet allows
  (`vegetarian`, `vegan`, `keto`; anything else gets the default) and the
  main meal with the least protein (its items summed per day), eaten on at
  least two days.
- **No guessed causes.** Notes state what the logs show and never a cause
  they can't show, such as eating out or busy days: the driest day among
  days with something logged; the biggest meal on an over-budget weekend
  day, only when that meal has a name; the sugary drink behind more than
  half of the week's sugary drinks, translated when it is a catalog drink;
  the days fiber hit its target, only when that is more than half the
  days logged.

## Last week

- `stats.last_week` summarizes the **stored** recap of the week before
  (`last_week_of`). There is none when that week was never read: no recap
  was built for it.
- Its plan is the one that recap showed. Each stored recap keeps the issue
  ids of its plan in `stats.plan_issues`. Recaps stored before that rebuild
  the plan and keep an issue only when its advice reads exactly like the
  stored move at the same place, so a config change never credits advice
  the user did not see.
- A card can carry `trend: {text, up, good}`: which way its number moved
  since last week and whether that helps. Averages over logged days need a
  change of at least a step (protein 5 g, fiber 2 g, pre-workout carbs
  5 g). Counts (nights, drinks, days, workouts) need 1 and water 0.2 L,
  and these compare only when both weeks logged the same number of days
  that feed them (days logged, days with a drink, counted weekend or rest
  days), so a logging gap never reads as progress. Win cards and `tough`
  stories show only good trends. Day and workout counts carry their unit
  ("Up from 4 days last week").
- `last_week.plan` lists the issues last week's plan took on. The first one
  that no longer holds and whose number moved a step the right way, by the
  rule above, becomes the top win card (`follow_through`, icon
  `check_circle`): this week's number, with last week's in its trend. Its
  title claims the whole plan only when every item of it was fixed; a fix
  that is more of something (fiber, water, protein, carbs) also gets a
  share tile.

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
