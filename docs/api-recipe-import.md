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
   per-ingredient macros, `nutrition.per_serving` / `nutrition.total`,
   `source`, and `warnings`.
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
  required ingredients, no steps, or under half with amounts), the video is
  downloaded (up to 15 minutes and 150 MB), speech is transcribed and 8
  frames are read for on-screen text. If the platform
  refuses yt-dlp, oEmbed (TikTok, YouTube) and Open Graph tags supply the
  caption, and the video itself is not analyzed.
- **Text / photo:** read directly by the AI.
- The AI returns display text in the request language (`Accept-Language`)
  and a canonical English name per ingredient for nutrition lookup.

## Nutrition (backend is the source of truth)

- Each line is weighed: weights convert exactly; volumes of liquids the
  density table knows convert by density; anything else (2 cloves, 1 can,
  1 cup flour) uses the AI's gram estimate for that line.
- Grams go through the shared lookup: food reference, then FatSecret, then
  an AI estimate (`nutrition_source`). Calories always come from the
  backend formula over the summed macros.
- `servings`: as stated by the source, else schema.org yield, else the AI
  estimate; with none of these, total kcal / 550 (1–12).
- When the user changes the amount of a count ingredient, the app should
  scale `grams` in proportion, or send `grams: null` to drop the line from
  nutrition.

## Saving

- A saved import is a saved suggestion with `suggestion_id =
  recipe_import_<import_id>`, so it appears in `GET /v1/saved-suggestions`
  and opens with the existing recipe screen. Saving the same `import_id`
  again returns the first save.
- `macros` is one serving; ingredient amounts are for the whole recipe and
  `servings` says how many it feeds. `recipe_import` holds the source link,
  platform and warnings.
- Third-party images (expiring video thumbnails) are copied to the image
  store on save; if that fails the original link is kept.

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
| Frames read for on-screen text | 8 | `video_media_analyzer.py` |
| Videos analyzed at once per process | 2 | `video_media_analyzer.py` |

The only setting is `RECIPE_IMPORT_TRANSCRIPTION_MODEL` (default
`gpt-4o-mini-transcribe`), the OpenAI speech-to-text model.

Video analysis needs `ffmpeg` and `ffprobe` (installed in the Docker image);
without them `capabilities.video_media_analysis` is false and imports use
captions only. The job type needs migration `20261101091406080591`.

## Not built yet

- Data-center IPs are often refused by YouTube and Instagram; login cookies
  or a proxy are not supported. Not verified against live platforms.
- No edit endpoint for a saved import: delete it and save again.
- Push copy for languages other than English and Vietnamese.
