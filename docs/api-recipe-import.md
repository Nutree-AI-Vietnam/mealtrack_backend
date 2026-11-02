# Recipe Import API

**Status:** Feature contract — non-derivable rules only
**Routes:** `GET /v1/recipe-imports/capabilities`, `POST /v1/recipe-imports/extract`,
`POST /v1/recipe-imports/save`, and job type `recipe_import` on `/v1/ai-jobs`
(see OpenAPI at `/docs` for shapes)
**Code:** rules in `src/domain/model/recipe_import/`, orchestration in
`src/app/services/recipe_import/`, readers in `src/infra/adapters/recipe_import/`,
wiring in `src/bootstrap/recipe_import.py`

A user imports a recipe from a link (recipe web page, YouTube, TikTok,
Instagram, Facebook), from pasted text, or from a photo (cookbook page,
screenshot). The backend returns a draft priced with backend nutrition. The
user reviews and edits it, then saves it. Nothing is stored before the save.

---

## Flow

1. `POST /extract` with exactly one of `url`, `text`, or `image_url` +
   `image_id` (a photo uploaded like a meal scan). `url` may be share-sheet
   text: the first link inside it is used. Pasted text that is little more
   than a link is imported as that link.
2. The response is the draft: `import_id`, ingredients with `grams`,
   per-ingredient macros, `servings` (people) with `servings_estimated`,
   `serving_description` and `makes`, `nutrition.per_serving` /
   `nutrition.total`, `source`, and `warnings`.
3. `POST /save` with the edited draft. The server prices every ingredient
   again; macros sent by the app are ignored.

Video links can take a minute. The app should queue them as a
`recipe_import` job (`POST /v1/ai-jobs`, same `input` as `/extract`) and read
the draft from `job.result.recipe`; `/extract` stays available for web pages,
text, photos and testing.

## How a source is read

- **Web page:** schema.org `Recipe` JSON-LD first, page text as backup.
  Long blog posts are cut around the first "ingredients" heading.
- **Video:** caption, title and subtitles through yt-dlp, without
  downloading. When the caption does not hold a usable recipe (fewer than 3
  required ingredients, no steps, or under half with amounts the source
  states; hashtags, mentions and links do not count as caption), up to 3
  links in the caption are opened ("full recipe: https://…"; video
  platforms, shops and link lists skipped, recipe-looking links first). A
  linked page with a complete schema.org `Recipe` is read with the caption
  (`source.linked_recipe_url`, signal `linked_recipe`); the AI ignores it
  when it is another dish. Only then is the video downloaded (up to 15
  minutes and 150 MB), speech transcribed, and one frame every 2 s (at most
  48) read for on-screen captions, 4 frames per vision call. Short videos
  show each caption for only 2–6 s, so frames are dense. If the platform
  refuses yt-dlp, a YouTube video's full description comes from the YouTube
  Data API when `YOUTUBE_API_KEY` is set; otherwise oEmbed (TikTok, YouTube)
  and Open Graph tags supply the caption. A refused video is never analyzed.
  yt-dlp steps are tried up to 3 times when the platform answers with a bot
  check or a transient error, since YouTube's bot check on server IPs comes
  and goes between requests. YouTube serves picture and sound apart; ffmpeg
  merges them for analysis. A refused video whose fallback yields only a
  title fails with `RECIPE_VIDEO_UNAVAILABLE`, not `NOT_A_RECIPE`.
- **Text / photo:** read directly by the AI.
- The AI returns display text in the request language (`Accept-Language`),
  a canonical English name per ingredient for nutrition lookup, and groups
  lines by part of the dish (marinade, filling, dipping sauce).
- **Portion pass:** a second, smaller AI call sizes every line: the weight
  of lines whose amount the source does not give (`amount_estimated`), the
  `eaten_share` of each part, the servings when the source has none, and
  `serving_description` (what one serving is: "3 bánh", "1 tô"). Stated
  amounts are kept. If this call fails, the draft keeps the amounts as read
  and lists unweighed lines in `nutrition.unpriced_ingredients`.

## Servings

- `servings` always counts people: how many the whole recipe feeds, one
  serving each. `nutrition.per_serving` is `nutrition.total / servings`.
- Stated servings win: "Serves 4", "cho 4 người", a schema.org yield that
  counts people or is a bare number. A yield that counts pieces ("Makes 12",
  `["24", "24 cookies"]`) is not servings: it is returned as `makes`, and
  the portion pass estimates how many people that feeds (12 pancakes ≈ 4
  servings of 3) from typical portions (a main dish is one plate or bowl,
  about 75–100 g dry pasta or rice, or 120–200 g raw meat, per person).
- `servings_estimated` (and warning `servings_estimated`) marks an AI
  estimate. With no estimate at all, servings fall back to total kcal / 550
  (1–12).
- When the user changes servings, the app recomputes per serving as
  `nutrition.total / servings`; ingredient amounts stay those of the whole
  recipe and are never rescaled. `serving_description` describes one serving
  of the imported count; hide it or scale it once servings change.

## Nutrition (backend is the source of truth)

- Each line is weighed: weights convert exactly; volumes of liquids the
  density table knows convert by density; anything else (2 cloves, 1 can,
  1 cup flour) uses the AI's gram estimate for that line.
- `eaten_share` (0–1) is the part of a line that is eaten: below 1 for a
  dipping sauce or dressing made as a batch (a serving eats about 30–60 g),
  deep-frying oil (~0.1) or a drained marinade (~0.3). Macros cover
  `grams × eaten_share`; `grams` stays the weight the cook uses.
- Those grams go through the shared lookup: food reference, then FatSecret,
  then an AI estimate (`nutrition_source`). Calories always come from the
  backend formula over the summed macros.
- When the user changes the amount of a count ingredient, the app should
  scale `grams` in proportion, or send `grams: null` to drop the line from
  nutrition. `/save` takes `amount_estimated` and `eaten_share` back as the
  draft gave them.

## Saving

- A saved import is a saved suggestion with `suggestion_id =
  recipe_import_<import_id>`, so it appears in `GET /v1/saved-suggestions`
  and opens with the existing recipe screen. Saving the same `import_id`
  again returns the first save.
- The recipe is stored as imported (with the user's edits): ingredient
  amounts for the whole recipe, `servings` as saved, `total_macros`, and
  `macros` for one serving (`total_macros / servings`). A different serving
  count never rescales ingredients. `recipe_import` holds the source link,
  `linked_recipe_url`, platform, warnings, `original_servings` (the draft's
  count when the user saved another), `servings_estimated`,
  `serving_description` and `makes`.
- Rows: `saved_suggestions` (snapshot JSON in `suggestion_data`, plus name
  and macros columns), `saved_suggestion_items` (ingredients) and
  `saved_suggestion_steps`. Drafts are never stored, except as the result of
  a `recipe_import` job.
- Third-party images (expiring video thumbnails) are copied to the image
  store on save; if that fails the original link is kept.
- A saved import can be planned in a weekly meal-plan slot
  (`PATCH /v1/meal-plans/{plan_id}` with `saved_suggestion_id`, plan feature
  `saved_recipe_slots`) and logged from there; the slot keeps its own copy,
  so deleting the saved import leaves the week as planned. See
  "Saved recipes in slots" in `docs/api-endpoints.md`.

## Errors

| Status | `error_code` | When |
|--------|--------------|------|
| 400 | `INVALID_RECIPE_SOURCE` | No link in `url`, text too short, nothing to save |
| 400 | `RECIPE_URL_NOT_ALLOWED` | Non-http(s), credentials, non-standard port, private/local address (also after redirects or DNS) |
| 422 | `RECIPE_SOURCE_REJECTED` | The site answered 4xx, too many redirects, not an HTML page, file too large |
| 422 | `RECIPE_VIDEO_UNAVAILABLE` | Private video, login required, or the platform blocked the request |
| 422 | `NOT_A_RECIPE` | The source holds no recipe; `details.reason` when the AI gave one |
| 503 | `RECIPE_SOURCE_UNREACHABLE` | Network failure, timeout, or 5xx/429 from the site |
| 503 | `RECIPE_IMPORT_TIMEOUT` | `/extract` took longer than 120 s |
| 429 | `RECIPE_IMPORT_DAILY_LIMIT` | More than 30 imports this UTC day |

AI failures keep their shared codes (`AI_UNAVAILABLE`, `AI_OUTPUT_INVALID`,
`AI_CONTENT_REJECTED`). Rate limits: `/extract` 10 a minute, `/save` 30.

## Safety

- Every fetched address, redirects included, must resolve only to public
  IPs; the connection is pinned to the checked IP and TLS still verifies the
  host name. Pages are capped at 3 MB, images at 8 MB.
- yt-dlp runs only the platform's own extractors (never the generic one),
  only for YouTube, TikTok, Instagram and Facebook hosts.
- Source text is passed to the AI as fenced, untrusted data.
- The daily quota counts each `/extract` call and each queued job.

## Limits and configuration

Recipe import is always on. Its limits are fixed in code, not settings:

| Limit | Value | Where |
|-------|-------|-------|
| Imports per user per UTC day (`/extract` calls and queued jobs) | 30 | `DAILY_IMPORTS_PER_USER`, `src/domain/model/recipe_import/limits.py` |
| `/extract` deadline; minimum `recipe_import` job attempt deadline | 120 s | `IMPORT_DEADLINE_SECONDS`, same file |
| One page, file or subtitle fetch | 15 s | `FETCH_TIMEOUT_SECONDS`, `safe_fetch.py` |
| Longest / largest video analyzed | 15 min / 150 MB | `video_media_analyzer.py` |
| Frames read for on-screen captions | 1 every 2 s, at most 48 | `video_media_analyzer.py` |
| Videos analyzed at once per process | 2 | `video_media_analyzer.py` |

Settings: `RECIPE_IMPORT_TRANSCRIPTION_MODEL` (default
`gpt-4o-mini-transcribe`), the OpenAI speech-to-text model, and
`YOUTUBE_API_KEY` (optional), a YouTube Data API v3 key used only when
YouTube refuses yt-dlp. Each lookup costs 1 unit of the 10,000-unit default
daily quota; it gives the description only, never subtitles or the video.

Video analysis needs `ffmpeg` and `ffprobe` (installed in the Docker image);
without them `capabilities.video_media_analysis` is false and imports use
captions only. The job type needs migration `20261101091406080591`.

## Not built yet

- Data-center IPs are often refused by YouTube and Instagram; login cookies
  or a proxy are not supported. In the live test, YouTube answered every
  other metadata request with its bot check, then after a few dozen
  requests sent the whole site to Google's CAPTCHA page (HTTP 429) for that
  IP. `YOUTUBE_API_KEY` restores the description only; reading a refused
  video's subtitles or speech from a server needs cookies or a residential
  proxy, or the text read on the user's device.
- YouTube Shorts are no safer: `/shorts/<id>` is the same video and player
  API. In the live test a Short whose description holds the recipe imported
  in 10 s (description and subtitles, no download); the same link hit the
  bot check half an hour later, then the CAPTCHA page.
- Live-tested from a cloud IP: one TikTok video with no stated amounts
  (speech and on-screen lists, 31–51 s); one Instagram reel, read by yt-dlp
  without login, whose caption has no amounts but links to the creator's
  recipe page (20 s from speech and on-screen text before links were
  followed; 15 s and all 12 amounts after); web pages read from schema.org
  JSON-LD (Cookpad, BBC Good Food, 7–9 s; "Makes 12" pancakes became 4
  servings of 3, "Makes 10" cookies 5 servings of 2). Simply Recipes
  answered the server with 402 (bot protection), which fails as
  `RECIPE_SOURCE_REJECTED`. Facebook has not been tried. The YouTube Data
  API fallback is tested with a stubbed API only (no key in the test
  environment).
- Amounts and servings the source does not state are AI estimates and vary
  between runs; the draft says so in `warnings` and per line in
  `amount_estimated`.
- No edit endpoint for a saved import: delete it and save again.
- Push copy for languages other than English and Vietnamese.
