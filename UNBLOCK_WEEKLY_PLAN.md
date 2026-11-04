# Bật weekly plan AI trên staging — còn một bước

Mọi code đã merge và đã deploy lên staging. Cờ chưa bật vì DB staging
còn thiếu một migration. Bật cờ trước khi migrate = user ăn 500.

## Trạng thái đã kiểm (2026-11-04 15:2x)

```
staging alembic_version   20261104041432763875   ← thiếu 20261104095702973837
staging image             sha256:025a160b (live) ← đã có cả #885 #886 #887
cờ                        cả ba đang false
```

## Bước 1 — migration (cần anh)

GitHub → Actions → **Migrate Database** → Run workflow

    Use workflow from:  delivery
    environment:        staging
    dry_run:            false

Migration này chỉ nới hai CHECK constraint, `NOT VALID` rồi `VALIDATE`
nên không khoá bảng. Mọi dòng hiện có đều đã thoả danh sách rộng hơn.
Có `downgrade()` nếu cần lùi.

Nếu anh muốn chạy tay thay vì qua workflow, đây là đúng `upgrade()`:

```sql
ALTER TABLE ai_jobs DROP CONSTRAINT ck_ai_jobs_job_type;
ALTER TABLE ai_jobs ADD CONSTRAINT ck_ai_jobs_job_type
  CHECK (job_type IN ('chat','meal_scan','parse_text','recipe_import','weekly_plan'))
  NOT VALID;
ALTER TABLE ai_jobs VALIDATE CONSTRAINT ck_ai_jobs_job_type;

ALTER TABLE weekly_meal_plans DROP CONSTRAINT ck_weekly_meal_plan_status;
ALTER TABLE weekly_meal_plans ADD CONSTRAINT ck_weekly_meal_plan_status
  CHECK (status IN ('draft','confirmed','preparing')) NOT VALID;
ALTER TABLE weekly_meal_plans VALIDATE CONSTRAINT ck_weekly_meal_plan_status;

UPDATE alembic_version SET version_num = '20261104095702973837';
```

## Bước 2 — ba cờ (tôi làm, hoặc anh tự đặt)

Render → `staging-instance` → Environment:

```
AI_JOBS_ENABLED=true                 # không có: route không queue được job
AI_JOBS_WORKER_ENABLED=true          # không có: job nằm queue, không ai chạy
WEEKLY_PLAN_AI_DISHES_ENABLED=true   # cờ của tính năng
```

Đặt env sẽ tự redeploy. Xong thì log phải có `AI job worker started`.

## Bước 3 — kiểm

Mở app bằng `nutreeaidev@gmail.com`, tuần hiện tại, chưa có plan.
Log sẽ đi: plan `preparing` → job claim → ~21 món → plan `draft`.

Mất khoảng 100–200 giây. App poll lại mỗi 8 giây nên card tự hết loading.

## Lùi lại nếu hỏng

Đặt `WEEKLY_PLAN_AI_DISHES_ENABLED=false`. Đường catalog cũ chạy lại
ngay, không cần lùi migration (constraint rộng hơn không ảnh hưởng gì).

## Hai thứ còn mở

- **PR #888** (giỏ đi chợ) để draft: 62→31 nguyên liệu nhưng 19→16 slot,
  mới đo một lần mỗi bên. Cần 3–5 tuần mỗi bên mới quyết được.
- **Credential Neon** anh nói đã rotate thì vẫn đang sống.
