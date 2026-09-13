# Nutree tester playbook — local backend + Flutter Dev (iOS)

**Audience:** The next human or agent tester. Follow this end-to-end; copy the result table at the bottom into a new dated report.

**Last executed:** 2026-09-13 ~00:11–12:13 ICT  
**Device:** iOS Simulator “Alex local” `3A2A02E3-5FDC-4B1D-9B05-1F7311D15860` (iOS 18.4)  
**App:** `com.nutreeai.mobile.dev` — Flutter flavor **dev** (`lib/main_dev.dart`)  
**API:** `http://127.0.0.1:8000` (`ENVIRONMENT=development`)  
**Locale this run:** Vietnamese (vi)  
**Signed-in user:** `dev@example.com` (Firebase uid suffix `7rO2`, backend `a787f345-da12-472b-be5c-d78f06f15456`)

Related reports (history, not a substitute for this playbook):

- `plans/reports/qa-local-dev-full-2026-09-12.md` — first exploratory pass + D1–D10
- `plans/reports/qa-local-dev-retest-2026-09-12.md` — defect retest after fixes

---

## 1. What this playbook covers

Nutree is a signed-in nutrition / fitness / coach app. Testers must exercise:

| Area | In-app entry |
|------|----------------|
| Boot + local API | Health, logs, flavor |
| Nutrition home | Tab **Dinh dưỡng** |
| Meals | FAB **Thêm món** (text, recents, confirm) |
| Scan | FAB **Quét món** (camera / simulator limits) |
| Coach | FAB **Hỏi Nutree** |
| Water | FAB **Thêm nước** + home **Nước uống** |
| Movement | Tab **Thể chất** + FAB **Ghi hoạt động** |
| Progress | Tab **Tiến độ** + drill-ins |
| Me / settings | Tab **Tôi** |
| Auth / onboarding / paywall | Cold install, sign-out, `/subscription-required` |

Mark every case **PASS / FAIL / BLOCKED / NOT RUN**. Never skip the “how to record” columns.

---

## 2. Before you start (environment)

### 2.1 Repos and commands

```bash
# Backend (must be this venv; never a random system python)
cd mealtrack_backend
./.venv/bin/python -m uvicorn src.api.main:app --host 0.0.0.0 --port 8000 --reload

# Mobile — NEVER `flutter run` without flavor + target
cd nutree/nutree_ai
flutter run --flavor dev -t lib/main_dev.dart -d 3A2A02E3-5FDC-4B1D-9B05-1F7311D15860
```

Confirm Flutter log contains `Environment: dev`.

Hot-restart (`R`) after Dart-only fixes. **Rebuild** (`flutter run --flavor dev …`) after `Info.plist` / xcconfig / native name changes.

Simulator home may show **both** “Nutree Dev” and “Nutree Staging”. Always launch **Nutree Dev** / `com.nutreeai.mobile.dev`.

### 2.2 Sanity probes (do these first)

```bash
curl -s -o /dev/null -w '%{http_code} %{time_total}s\n' http://127.0.0.1:8000/health
curl -s http://127.0.0.1:8000/health
```

**Expected (warm process):** HTTP 200, `status: healthy`, `deployment.environment` = `development`, time **under ~0.05s**.

If `/health` is **several seconds**, the process is still starting/reloading, or a middleware regression is back. **Do not time feature APIs during a uvicorn reload.**

Optional contract checks (dev auth bypass if `ENABLE_DEV_AUTH_BYPASS=1`):

| Call | Expected |
|------|----------|
| `GET /v1/user-profiles/body-fat-visual` | **404** `BODY_FAT_VISUAL_UNSET` if unset (empty home card). Not a missing route. |
| `POST /v1/notifications/tokens` `{"fcm_token":"…","device_type":"ios"}` | **200** `{success:true}` (local stub; no persistence) |

### 2.3 Parse-text / AI (local)

Confirm via `get_settings()` (do not paste `.env` secrets):

- `AI_PRIMARY_PROVIDER=openai`
- `OPENAI_PARSE_TEXT_MODEL` (this stack: `gpt-5.6-luna`)
- `PARSE_TEXT_PURE_AI_ENABLED=True` (FatSecret skipped)

Parse is **OpenAI**, not Gemini.

### 2.4 Calories and weekly budget (product rules)

- Backend is source of truth for calories. Do **not** “fail” the app because a client recomputed a different kcal.
- Weekly `remaining_days` includes today: Mon=7 … Sun=1.
- Nutrition home remaining ≈ **daily target − food eaten today**. Coach **new** replies must match that, not week leftover.

---

## 3. How to record a case

Copy this row:

```
ID | Area | Steps | Expected | Actual | Time (API if known) | Status | Screenshot/log
```

Severity: P0 ship-blocker · P1 core path broken/unusable · P2 wrong/incomplete · P3 cosmetic.

---

## 4. Scenario catalog (execute in this order)

### A. Environment and boot

| ID | Steps | Expected | This run (2026-09-13) |
|----|--------|----------|------------------------|
| ENV-01 | `GET /health` twice after uvicorn is idle | 200, healthy, fast | **PASS** 11ms then **0.9ms** |
| ENV-02 | App traffic in uvicorn logs is `127.0.0.1` `/v1/*` | Local API | **PASS** (prior + this session) |
| BOOT-01 | Cold `flutter run --flavor dev` signed-in home | Tabs + macros + FAB | **PASS** |
| BOOT-02 | Startup logs | RC invalid key, FCM timeout OK locally | **PASS with warnings** |
| BOOT-03 | Status bar / home icon | Dev, not Staging carrier leftover | **PASS** time-only status bar; both icons exist on SpringBoard |

### B. Nutrition home (tab Dinh dưỡng)

| ID | Steps | Expected | This run |
|----|--------|----------|----------|
| HOME-01 | Read calories: eaten + remaining = target | 221 + 1711 = **1932** | **PASS** `221 trên 1932`, còn **1.711 kcal**; P 3/119, C 54/222, F 1/63 |
| HOME-02 | Body-fat card | Empty: `0/100%`, `Mục tiêu: —` | **PASS** + API 404 `BODY_FAT_VISUAL_UNSET` (14.2s — still slow) |
| HOME-03 | Date strip | Today selected (T7 12 this run) | **PASS** |
| HOME-04 | Scroll **Nước uống** + **Lịch sử dinh dưỡng** | Water total; meal times in **24h for vi** | **PASS** 250/2450 ml; banana rows **22:57** and **12:34** (no AM/PM) |
| HOME-05 | Streak / fire badge | Visible, non-crash | **PASS** fire **2** |

### C. FAB overlay (from any tab)

Open **+**. Required actions (vi):

1. **Thêm món** — create meal  
2. **Quét món** — camera scan  
3. **Hỏi Nutree** — coach  
4. **Thêm nước** — hydration  
5. **Ghi hoạt động** — log movement  

| ID | Steps | Expected | This run |
|----|--------|----------|----------|
| FAB-01 | Overlay lists all five | Labels visible | **PASS** |

### D. Meals

| ID | Steps | Expected | This run |
|----|--------|----------|----------|
| MEAL-01 | FAB → Thêm món | `Tạo bữa ăn`; confirm disabled at 0 kcal | **PASS** |
| MEAL-02 | Recents list | Distinct dishes (same name+kcal should appear **once**) | **FAIL** two `1 quả chuối` 111 kcal (running binary; rebuild/hot-restart required after D7 dart fix) |
| MEAL-03 | Tap a recent → confirm | Toast; home calories increase by that meal | **NOT RUN this clock** (validated 2026-09-12: banana 111→221) |
| MEAL-04 | Type `1 quả cam` → send | Item with kcal **or** clear error; never infinite `Tính toán` | **PASS** (2026-09-12 night): parse **200 / 3.2s**, UI **Cam 154g / 74 kcal**; error path **Máy chủ tạm thời không khả dụng** |
| MEAL-05 | Confirm empty composer | Confirm stays disabled | **PASS** |
| MEAL-06 | Multi-item composer (add second food, confirm) | Totals sum; home updates | **NOT RUN** |
| MEAL-07 | Favorite star on recent | Favorite persists in list / settings-adjacent favorites | **NOT RUN** |
| MEAL-08 | Diary row tap → meal detail / edit | Opens; back returns | **NOT RUN** |
| SCAN-01 | FAB → Quét món | Camera or simulator permission / empty camera | **NOT RUN** (simulator camera) |
| SCAN-02 | Barcode | `/barcode-scan` | **NOT RUN** |

### E. Coach

| ID | Steps | Expected | This run |
|----|--------|----------|----------|
| COACH-01 | FAB → Hỏi Nutree | Thread loads (`GET /v1/chat`) | **PASS** historically; live send can fail under load |
| COACH-02 | Ask remaining / eaten calories | **New** card matches home today (221/1932 / 1711 left) | **NOT RE-PROVEN this clock.** Code now uses today remaining. Old cards must show **date + time** stamp. Prior fail: 460/2038 vs home 221/1932 |
| COACH-03 | Historical cards | Must not look like live home without a stamp | **Partial** — reopen after hot-restart and assert stamp `YYYY-MM-DD · HH:mm` |
| COACH-04 | Failed send | `Couldn't reach Nutree` / Retry, not a silent hang | **PASS** (observed 2026-09-12) |

### F. Hydration

| ID | Steps | Expected | This run |
|----|--------|----------|----------|
| WATER-01 | FAB → Thêm nước → +250 ml nước lọc | Progress + log; `POST /v1/hydration/log` 201 | **PASS** historically 0→250 ml |
| WATER-02 | Log timestamps vs status bar | **vi = 24h**, no AM/PM | **PASS** on nutrition diary times this run |
| WATER-03 | Undo / delete 250 ml | Count decreases | **NOT RUN** |

### G. Fitness (tab Thể chất)

| ID | Steps | Expected | This run |
|----|--------|----------|----------|
| FIT-01 | Tab chrome | Connect Apple Health; burn card **kcal**; steps **bước** on Health card not on burn row | **PASS** `Apple Health` **0 kcal** (not `Bước 0 kcal`) |
| FIT-02 | History empty with no workouts | `Chưa có hoạt động` | **PASS** (meals are not workouts) |
| FIT-03 | Quick-add type → save | History row + kcal | **NOT RUN** |
| FIT-04 | **Bắt đầu đi bộ** start → stop → save | Session saved | **NOT RUN** |
| FIT-05 | Apple Health **Kết nối** | Instruction / Health CTA | **PASS** nav historically |

### H. Progress (tab Tiến độ)

| ID | Steps | Expected | This run |
|----|--------|----------|----------|
| PROG-01 | Open tab | Content **without** a 40s blank spinner; may show **Đang hiện dữ liệu đã lưu** | **PASS** week view score **33**, badge cached |
| PROG-02 | Ngày / Tuần / Tháng / Năm | Each range paints | **Partial** (Tuần this run) |
| PROG-03 | Open score / budget / protein / hydration / burn / weight / intake / milestones / recap / customize | Each route opens, back works | **NOT RUN** (routes exist under `/progress/*`) |
| PROG-04 | `GET /v1/progress/summary` | 200; note duration | Historically **42–45s**. UX must not look hung |

### I. Me / settings (tab Tôi)

| ID | Steps | Expected | This run |
|----|--------|----------|----------|
| SET-01 | Profile card | `dev@example.com` | **PASS** User / `dev@example.com` |
| SET-02 | Theme | Light/Dark/system | **PASS** Chủ đề **Sáng** |
| SET-03 | Apple Health row | Opens connect | Surface **PASS** |
| SET-04 | Kế hoạch của tôi | Goals / weight / metrics | **NOT RUN** |
| SET-05 | Adaptive weekly adjust toggle | Persists | **PASS** visible ON |
| SET-06 | Language | VI persists after restart | **PASS** Tiếng Việt (no switch this run) |
| SET-07 | Live Activity | Toggle off on simulator OK | **PASS** OFF |
| SET-08 | Notifications prefs | Loads | **NOT RUN** |
| SET-09 | Support / legal | Links open | **Partial** (support email visible) |
| SET-10 | View plans / paywall | Paywall or “has access” | **NOT RUN** (local RC key invalid) |
| SET-11 | Invite friends / promo | Opens | **NOT RUN** |
| SET-12 | Sign out / delete account | **Do not run delete** unless explicitly requested | **NOT RUN** (destructive) |

### J. Auth, onboarding, paywall (cold paths)

| ID | Steps | Expected | This run |
|----|--------|----------|----------|
| AUTH-01 | Sign-in dialog / email | Session to home | **NOT RUN** (already signed in) |
| AUTH-02 | Email link / activate plan | Completes or errors clearly | **NOT RUN** |
| ONB-01 | New user onboarding + AI handshake / first meal | Completes to home | **NOT RUN** |
| PAY-01 | `/subscription-required` sources | view_plans / trial_expiry / post_onboarding | **NOT RUN** |
| OFF-01 | Airplane mode | Clear errors, no corrupt totals | **NOT RUN** |

---

## 5. Defect retest matrix (D1–D10)

Use this every regression pass.

| ID | Sev | How to test | 2026-09-13 result |
|----|------|-------------|-------------------|
| D1 | P0 | Parse `1 quả cam` | **PASS** (prior night + OpenAI 200). Re-run after any parse/handler change |
| D2 | P0 | Home remaining vs **new** coach card | **OPEN on device until new live turn.** Code: today remaining. Old cards must be dated |
| D3 | P1 | Progress first paint | **PASS UX** (cached badge). API still can be tens of seconds |
| D4 | P1 | Fitness burn row | **PASS** Apple Health + kcal |
| D5 | P1 | Empty fitness vs meals | **PASS** empty is correct |
| D6 | P1 | `/health` then `/v1/meals/recent` | **PASS health** (~1ms warm). **FAIL/watch** recents/body-fat still multi-second (14s / 14s this morning) |
| D7 | P2 | Recents one banana | **FAIL on running app** (two rows). Hot-restart latest Dart before signing off |
| D8 | P2 | Body-fat GET | **PASS contract** 404 unset |
| D9 | P2 | vi timestamps | **PASS** 12:34 / 22:57 |
| D10 | P3 | Dev branding | **PASS** no Staging carrier on this launch |

---

## 6. Backend log cheat sheet

Watch `uvicorn` while tapping:

- Meals: `POST /v1/meals/parse-text`, `GET /v1/meals/recent`, meal create
- Home: `GET /v1/nutrition/bulk`, `GET /v1/meals/weekly/budget`
- Progress: `GET /v1/progress/summary`
- Coach: `GET /v1/chat`, `POST /v1/chat/messages`
- Water: `POST /v1/hydration/log`, `GET /v1/hydration/daily`
- Fitness: `GET /v1/activities/daily` (meals ≠ workouts)

Ignore Cloudflare queue skip and RevenueCat invalid key on local.

---

## 7. Sign-off for this execution

**Overall:** Core shell works (home math, tabs, FAB, fitness units, progress cache paint, vi 24h times, health probe). **Not a full-app green.** Next tester must still:

1. Hot-restart Flutter and recheck **one** banana in recents (D7).  
2. Send a **new** coach calorie question and match **221 / 1932 / 1711**.  
3. Run SCAN, movement save, progress drill-ins, paywall, auth, offline.  
4. Treat `/v1/progress/summary` and some GETs as **slow local** until timed under 3s.

**Do not delete the account** as part of a default pass.

When you finish, copy section 4–5 into `plans/reports/qa-local-dev-YYYY-MM-DD.md` and update the dates.
