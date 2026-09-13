# QA Test Report — Nutree Local Dev (Backend + Mobile)

**Date:** 2026-09-12  
**Tester role:** Manual exploratory QA (Argent iOS + backend access logs)  
**Environment:** Local backend `http://127.0.0.1:8000` + Flutter **dev** flavor (`lib/main_dev.dart`)  
**Device:** iOS Simulator — Alex local (`3A2A02E3-5FDC-4B1D-9B05-1F7311D15860`), iOS 18.4  
**App:** `com.nutreeai.mobile.dev` (Nutree Dev) — Flutter log `Environment: dev`  
**Locale:** Vietnamese (vi)  
**Session:** Pre-authenticated as `User` / `dev@example.com` (Firebase uid suffix `7rO2`, backend user `a787f345-da12-472b-be5c-d78f06f15456`)

## Overall verdict

**CONDITIONAL PASS with blockers.** Core shell (boot, tabs, settings, recent meal log, water log, coach live turn) works against local API. Several **P0/P1 defects** block calling this a clean release-quality pass: meal text-parse 500, progress tab extreme latency / stuck spinner UX, coach historical nutrition cards stale vs home, movement steps unit wrong, activity history empty while API returns data.

| Area | Result | Severity notes |
|------|--------|----------------|
| Environment boot | **PASS** | Backend healthy; app launched |
| Connectivity to local API | **PASS** | Confirmed via `127.0.0.1` `/v1/*` logs |
| Nutrition home | **PASS** | Macros/remaining math consistent after log |
| Meal logging (recent) | **PASS** | Toast + home calories updated |
| Meal logging (text parse) | **FAIL** | `POST /v1/meals/parse-text` → 500; UI stuck calculating |
| Coach (live turn) | **PASS** | Fresh turn matched home (111/1932 → 1821 left) |
| Coach (history hydrate) | **FAIL** | Older cards showed `Đã ăn 0` / `1932 left` while home had 111 eaten |
| Movement / fitness | **FAIL** | Steps shows `kcal`; history empty despite API activity |
| Progress | **FAIL** | Spinner ~40–60s; poor UX / looks hung |
| Profile / Me | **PASS** | Settings list, toggles, support links visible |
| Hydration | **PASS** | +250 ml updates progress/log |
| Performance (local) | **FAIL** | Many endpoints 10–58s |

---

## Environment evidence

### ENV-01 — Local backend starts and serves health
- **Steps:** Start uvicorn `:8000`; `GET /health`, `GET /docs`
- **Expected:** 200 healthy; docs reachable
- **Actual:** `/health` → `{"status":"healthy",...,"environment":"development"}`; `/docs` → 200. Postgres + Redis already running.
- **Status:** **PASS**

### ENV-02 — Mobile points at local backend
- **Steps:** Launch app; observe backend request logs
- **Expected:** Authenticated `/v1/*` traffic from 127.0.0.1
- **Actual:** Home hydration calls hit local API (`/v1/meals/*`, `/v1/hydration/*`, `/v1/nutrition/bulk`, `/v1/chat`, etc.).
- **Status:** **PASS**

### BOOT-01 — Dev flavor cold launch reaches signed-in home
- **Steps:** `flutter run --flavor dev -t lib/main_dev.dart -d <udid>`
- **Expected:** Bottom tabs + nutrition summary
- **Actual:** Reached home with date strip, macros, FAB. Flutter log: `Environment: dev`.
- **Notes:** iOS status bar carrier text often rendered as “Nutree Staging” in screenshots despite `com.nutreeai.mobile.dev` / Flutter `Environment: dev`. Treat as **cosmetic/env-label risk** — verify status-bar branding for Dev.
- **Status:** **PASS** (with observation)

### BOOT-02 — Startup warnings (non-blocking for core QA)
- RevenueCat: `Invalid API Key` during identity sync
- FCM init timeout (5s)
- `POST /v1/notifications/tokens` → 404
- Cloudflare queue skipped (missing credentials) — expected for local
- **Status:** **PASS with warnings** (dev local expected gaps)

---

## Happy-path cases

### HOME-01 — Nutrition dashboard loads and math checks out
- **Expected:** Consumed + remaining = goal for calories/macros
- **Actual (baseline):** 111 / 1932, remaining 1.821 kcal; protein 1/119, carbs 27/222, fat 0/63 — remaining math OK
- **Actual (after banana log):** 221 / 1932, remaining 1.711 kcal; protein 3/119, carbs 54/222, fat 1/63 — remaining math OK
- **Status:** **PASS**

### HOME-02 — Body-fat card empty state
- **Actual:** `0 / 100%`, `Mục tiêu: —`
- **Backend:** `GET /v1/user-profiles/body-fat-visual` → **404** (8s)
- **Status:** **PASS as empty state UX**; **FAIL for API** (404 on expected profile visual)

### MEAL-01 — FAB → Thêm món opens composer
- **Actual:** `Tạo bữa ăn`, suggestions, recent list, confirm disabled at 0 kcal
- **Status:** **PASS**

### MEAL-02 — Log from recent meal (happy path)
- **Steps:** Tap recent `1 quả chuối` (111 kcal)
- **Expected:** Meal recorded; home calories increase
- **Actual:** Toast `Đã ghi nhận 1 quả chuối`; home later showed 221 kcal (was 111)
- **Status:** **PASS**

### WATER-01 — Add filtered water 250 ml
- **Steps:** FAB → Thêm nước → `+` on Nước lọc
- **Expected:** Progress and today’s log update
- **Actual:** 0→250 ml (10%), remaining 2200, toast `Đã thêm +250 ml`, log entry created; `POST /v1/hydration/log` → 201
- **Status:** **PASS**
- **Edge note:** Log timestamp showed **PM** while status bar time was morning — possible AM/PM formatting bug

### COACH-01 — Open coach + load history
- **Actual:** FAB → Hỏi Nutree; `GET /v1/chat?limit=50` → 200 (~12s); thread rendered
- **Status:** **PASS** (slow)

### COACH-02 — Live calorie question matches home
- **Steps:** Ask remaining / eaten calories after home showed 111 eaten
- **Actual:** Live card: remaining **1821**, `Đã ăn 111 / Mục tiêu 1932`; macros remaining aligned. `POST /v1/chat/messages` → 200 (~34s)
- **Status:** **PASS**

### SETTINGS-01 — Profile / settings surface
- **Actual:** User card `dev@example.com`; Theme Light; Adaptive ON; Language VI; Live Activity OFF; support section present
- **Status:** **PASS**

### FITNESS-01 — Fitness tab chrome
- **Actual:** Apple Health connect CTA, quick-add activity types, start CTA, empty history chrome
- **Status:** **PASS** (surface only)

### FITNESS-02 — Apple Health manage screen
- **Actual:** Instructional “Quản lý đồng bộ” screen with open Health CTA
- **Status:** **PASS** (navigation)

---

## Edge / negative / integrity cases

### MEAL-E1 — Text parse failure handling
- **Steps:** Type `1 quả cam` → submit parse
- **Expected:** Parsed item OR clear error; no indefinite progress
- **Actual:**
  - Backend: `POST /v1/meals/parse-text` → **500** `ConnectionResetError` (~16.6s)
  - UI: stayed on progress (`Phân tích` / `Tính toán …%`) without clear failure message
- **Status:** **FAIL (P0)** — backend error + weak client error UX

### MEAL-E2 — Recent list duplicates
- **Actual:** Two identical `1 quả chuối` 111 kcal entries
- **Status:** **FAIL (P2)** — dedupe / uniqueness unclear

### MEAL-E3 — Confirm disabled when empty
- **Actual:** `Xác nhận bữa ăn` disabled at 0 kcal
- **Status:** **PASS**

### COACH-E1 — Historical nutrition card vs home (integrity)
- **Expected:** Hydrated historical nutrition cards reflect truth at reply time *or* clearly dated; at minimum must not contradict current authoritative home when claiming “today”
- **Actual:** On first coach open, cards for “Hôm nay tôi còn bao nhiêu calo?” showed **`Đã ăn 0 / Mục tiêu 1932`** and **1932 left** while Nutrition home already showed **111 eaten / 1821 left**. Later live turn corrected to 111/1821.
- **Status:** **FAIL (P0)** — matches known coach nutrition snapshot integrity risk

### PROGRESS-E1 — Progress tab long hang
- **Steps:** Open Tiến độ (Week then Day)
- **Actual:** Centered spinner for a long period; `GET /v1/progress/summary` ~**42–59s** then 200
- **Status:** **FAIL (P1)** — functionally recovers eventually but UX appears broken

### FITNESS-E1 — Steps unit wrong
- **Actual:** Label `Bước` shows value unit **`kcal`**
- **Status:** **FAIL (P1)**

### FITNESS-E2 — History empty vs API data
- **Actual:** UI `Chưa có hoạt động` while backend earlier logged `Retrieved 1 activities ... on 2026-09-12`
- **Status:** **FAIL (P1)** — client/server inconsistency

### PERF-E1 — Local latency
- Sample elapsed times from access logs:
  - `/v1/nutrition/bulk` ~58s
  - `/v1/meals/weekly/budget` ~47–53s
  - `/v1/progress/summary` ~42–59s
  - `/v1/hydration/log` ~34s
  - `/v1/chat/messages` ~34s
  - Many GETs 8–25s
- **Status:** **FAIL (P1 for local usability)** — may be local DB/cold start; still blocks QA confidence

### API-E1 — Missing local endpoints
- `POST /v1/notifications/tokens` → 404
- `GET /v1/user-profiles/body-fat-visual` → 404
- **Status:** **FAIL (P2)** for completeness

---

## Defects summary (severityitized)

| ID | Severity | Title | Evidence |
|----|----------|-------|----------|
| D1 | **P0** | Meal text parse returns 500; UI stuck calculating | `POST /v1/meals/parse-text` 500 `ConnectionResetError`; UI progress stuck |
| D2 | **P0** | Coach historical “today” nutrition cards stale / wrong | Cards: eaten 0 / left 1932 vs home eaten 111 / left 1821 |
| D3 | **P1** | Progress tab appears hung | Spinner; `/v1/progress/summary` 40–60s |
| D4 | **P1** | Steps metric uses kcal unit | Thể chất card `Bước` → `0 kcal` |
| D5 | **P1** | Activity history empty despite API activity | UI empty vs `Retrieved 1 activities` |
| D6 | **P1** | Systemic local API latency | Multiple endpoints 10–58s |
| D7 | **P2** | Duplicate recent meals | Two identical banana rows |
| D8 | **P2** | Body-fat visual 404 | `/v1/user-profiles/body-fat-visual` |
| D9 | **P2** | Water log AM/PM mismatch | Status ~10:58 vs log “PM” |
| D10 | **P3** | Dev status bar shows “Staging” label | Screenshots vs Flutter `Environment: dev` |

---

## Coverage gaps (not fully exercised this session)

- Camera / Quét món (simulator camera permissions & scan pipeline)
- Full movement start → stop → save cycle (mis-tap opened Apple Health)
- Progress content after load (charts/metrics assertions beyond spinner recovery)
- Offline / airplane-mode behavior
- Auth logout / re-login / onboarding / paywall
- Language switch persistence
- Meal confirm multi-item composer path (non-recent)
- Scan Text OCR tooltip flow
- Destructive settings / account deletion

---

## Reproduction notes

```bash
# Backend
cd mealtrack_backend
./.venv/bin/python -m uvicorn src.api.main:app --host 0.0.0.0 --port 8000 --reload

# Mobile
cd nutree/nutree_ai
flutter run --flavor dev -t lib/main_dev.dart -d 3A2A02E3-5FDC-4B1D-9B05-1F7311D15860
```

Report path: `mealtrack_backend/plans/reports/qa-local-dev-full-2026-09-12.md`

---

## Sign-off

**QA recommendation:** Do **not** treat local stack as green. Fix **D1** and **D2** before relying on coach/meal AI paths. Address **D3–D6** before calling local E2E usable. Happy paths for recent meal + water + live coach turn are validated against local backend.
