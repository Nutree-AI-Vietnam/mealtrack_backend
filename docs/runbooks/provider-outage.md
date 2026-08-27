# Provider Outage Runbook

Use this when FatSecret, USDA FoodData Central, OpenAI, Cloudflare Workers AI, or
Redis has elevated errors or latency.

## First Checks

- Check `/health` and application 5xx rate.
- Check provider dashboards or status pages from the vendor console.
- Check recent deploys and env-var changes in Render.
- Check `food_search.requests` by `source` and `status`.
- Check `meal_catalog.snapshot.refresh` and `meal_catalog.snapshot.last_good`.

Never paste provider credentials into incident notes, Slack, tickets, or logs.
Credentials must stay in the configured secret manager or Render environment
settings.

## Expected Degraded Behavior

| Dependency | Expected behavior |
|---|---|
| Redis optional cache | Local-first food search treats cache errors as misses and continues. Required Redis-backed state, such as legacy meal suggestion sessions, may fail fast. |
| FatSecret search | `/v1/foods/search` returns verified local `food_reference` and candidate provider hits when available. Full `food.get.v5` details on select may be absent. |
| USDA barcode/details | Barcode cascade skips unavailable providers and continues to the next configured source or editable estimate when safe. |
| OpenAI translation | Non-English search falls back to canonical local/provider results without translated names. |
| Cloudflare Workers AI | AI manager uses configured fallback chain when available; catalog image generation can be paused. |

## Translation Cutover Rollback

For every release that changes the translation provider, record the UTC
`translation_cutover_at` timestamp in the deployment record before enabling the
new code. Keep the pre-cutover `food-search` cache namespace available during
the observation window; a rollback must switch reads back to that namespace
instead of trusting entries written by the new provider.

If a rollback is required:

1. Revert the application release and restore the previous translation
   configuration.
2. Switch search reads back to the retained pre-cutover cache namespace.
3. Review translation rows with `translated_at >= translation_cutover_at`, then
   delete or retranslate only the affected release window. The schema has no
   provider provenance, so code rollback alone cannot identify or repair
   provider-written rows.
4. Confirm localized reads now return canonical provider/local values only, and
   keep the observation window open until the reverted stack is stable.

## Response Steps

1. Confirm the affected dependency and error class from logs or metrics.
2. Confirm local-first search still returns bounded results:

```bash
curl -fsS "$HOST/v1/foods/search?q=rice&limit=5" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Accept-Language: en"
```

3. If recommendations fail because catalog snapshot cannot load, stop exposing
   the recommendation entry point and restore the last known healthy app image if
   the issue began with a deploy.
4. If provider quota is exhausted, rotate only through approved provider account
   controls. Do not paste replacement credentials into ad hoc scripts.
5. When the provider recovers, run the same smoke request and confirm metrics
   return to normal for at least 10 minutes.

## Recovery Evidence

Record UTC start/end time, affected provider, error class, operator action,
dashboard link, and recovery duration. Do not record raw payloads, tokens,
search text, user IDs, or secrets.
