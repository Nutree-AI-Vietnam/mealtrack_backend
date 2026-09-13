# QA retest — D1–D10 deploy readiness

**Date:** 2026-09-12 (evening, Asia/Ho_Chi_Minh)  
**Scope:** Retest defects in `qa-local-dev-full-2026-09-12.md` against current local backend + Flutter **dev**.  
**Backend:** `http://127.0.0.1:8000`, `ENVIRONMENT=development`, `DB_HOST=localhost`, `AI_PRIMARY_PROVIDER=openai`, `OPENAI_PARSE_TEXT_MODEL=gpt-5.6-luna`, `PARSE_TEXT_PURE_AI_ENABLED=True`  
**App:** `com.nutreeai.mobile.dev`, `flutter run --flavor dev -t lib/main_dev.dart` (rebuilt this session)  
**Device:** iOS Simulator `3A2A02E3-5FDC-4B1D-9B05-1F7311D15860`  
**User:** Firebase uid suffix `7rO2` / backend `a787f345-da12-472b-be5c-d78f06f15456`

## Deploy verdict

**Not ready to deploy.** P0 meal parse (D1) is fixed on this stack. **P0 coach nutrition integrity (D2) still fails on device.** Local **P1 latency (D6)** is still 8–45s on many `/v1/*` calls (Postgres is local, so this is not “Neon cold start”). Recents still show duplicate bananas (D7).

Do not treat this local pass as a production green light.

## Defect matrix

| ID | Sev | Retest result | Evidence |
|----|-----|---------------|----------|
| D1 | P0 | **PASS** | `POST /v1/meals/parse-text` **200 in 3.195s** (OpenAI 200). UI: composer showed **Cam 154 g / 74 kcal**, not stuck on calculating. Failure path also showed **Máy chủ tạm thời không khả dụng** + Thử lại (not infinite spinner). |
| D2 | P0 | **FAIL** | Nutrition home: **221 / 1932**, remaining **1.711 kcal**. Coach thread card: **460 / 2038 kcal**, remaining **1.578**, no as-of stamp on the live card. New send: **Couldn't reach Nutree**. Widget tests stamp “Remaining today” when `asOf` is present; **device history/live card still contradicts home**. |
| D3 | P1 | **PASS (UX)** / **FAIL (API time)** | Progress tab painted **immediately** (score 41, weekly 12.837 kcal). Same session `GET /v1/progress/summary` still **45.370s**. Cache-first UX works; endpoint is still too slow. |
| D4 | P1 | **PASS** after rebuild | Before rebuild: `Bước` **0 kcal**. After `flutter run --flavor dev`: burn row **Apple Health / 0 kcal**. Flutter test: `Vietnamese fitness strip keeps bước on steps and kcal on burn`. |
| D5 | P1 | **PASS (not a bug)** | History **Chưa có hoạt động** with no logged workouts. `GET /v1/activities/daily` **200 in 0.118s**. Original “1 activities” log counted meals. |
| D6 | P1 | **FAIL** | Timed this session: `/health` **6.7–9.4s**; `/v1/meals/recent` **8–38s**; weekly budget **36s**; progress summary **45s**; hydration daily **5.4s**. Not 10–58s on every call, but still unusable for QA/release confidence. |
| D7 | P2 | **FAIL (UI)** / API dedupe OK | Bypass `GET /v1/meals/recent` returned **one** `1 quả chuối` + pho. Rebuilt app still shows **two** banana rows. Client local/remote merge still duplicates. |
| D8 | P2 | **PASS as empty contract** | `GET /v1/user-profiles/body-fat-visual` **404** `BODY_FAT_VISUAL_UNSET`. Home empty body-fat card. Not a missing route. |
| D9 | P2 | **PASS (unit)** / **not device-logged this pass** | `formatTimelineTime` vi → `15:30` without PM (`customized_date_utils_test.dart`). Water history UI not captured after rebuild. |
| D10 | P3 | **PASS after native rebuild** | Pre-rebuild status bar **Nutree Staging**. Simulator home has both **Nutree Dev** and **Nutree Staging** icons. After this `flutter run --flavor dev`, status bar is time-only (no Staging carrier label). Flutter log: `Environment: dev`. |

## Extra (API-E1)

- `POST /v1/notifications/tokens` with `{fcm_token, device_type}` → **200** `Token registered` (stub; no DB persistence). Wrong body still 422.

## What is safe vs not

**Safe to land as local-dev fixes:** parse-text 503 mapping + UI error, progress cache-first paint, fitness unit copy, body-fat 404 contract, FCM token stub, CFBundleName → display name.

**Still blocking a “ready to deploy” claim:** coach cards vs home calories/targets (D2), systemic local API latency (D6), duplicate recents in the composer (D7). Chat send failed once under load.

## Commands used

```bash
# Backend (already running)
./.venv/bin/python -m uvicorn src.api.main:app --host 0.0.0.0 --port 8000 --reload

# Mobile rebuild for D4/D10
flutter run --flavor dev -t lib/main_dev.dart -d 3A2A02E3-5FDC-4B1D-9B05-1F7311D15860
```
