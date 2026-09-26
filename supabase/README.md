# Supabase Mirror — Setup Guide | راهنمای راه‌اندازی

The app stores everything locally (SQLite, WAL). Supabase is an **optional
cloud mirror** fed by an internal outbox queue — when it is offline, paused
(free-tier) or misconfigured, nothing is lost and nothing blocks.

اپلیکیشن همه‌چیز را به‌صورت محلی ذخیره می‌کند (SQLite). ساپابیس یک **آینه ابری
اختیاری** است که با صف outbox تغذیه می‌شود؛ اگر قطع باشد، متوقف شود (پلن رایگان)
یا اشتباه تنظیم شده باشد، هیچ داده‌ای از دست نمی‌رود و هیچ‌چیز بلاک نمی‌شود.

---

## 1. Create the project | ساخت پروژه

1. Sign up at [supabase.com](https://supabase.com) (free tier is enough).
2. **New project** → pick a name and a region near you → set the database
   password (you will not need it in the app).
3. Wait until the project is **Active**.

1. در [supabase.com](https://supabase.com) ثبت‌نام کنید (پلن رایگان کافی است).
2. **New project** → نام و نزدیک‌ترین region را انتخاب کنید → رمز دیتابیس را
   بگذارید (در برنامه به آن نیازی ندارید).
3. صبر کنید تا وضعیت پروژه **Active** شود.

## 2. Create the schema | ساخت جداول

1. Open **SQL Editor** in the Supabase dashboard.
2. Paste the whole content of [`schema.sql`](schema.sql) (this folder) and
   **Run**. It creates all tables, indexes, RLS policies and views.
   Re-running it is safe.

1. در داشبورد ساپابیس **SQL Editor** را باز کنید.
2. کل محتوای [`schema.sql`](schema.sql) (همین پوشه) را پیست کرده و **Run** بزنید.
   همه جدول‌ها، ایندکس‌ها، سیاست‌های RLS و ویوها ساخته می‌شوند. اجرای مجدد بی‌خطر است.

## 3. Get the credentials | گرفتن اطلاعات اتصال

From **Project Settings → API**:

- **Project URL** — e.g. `https://abcd1234.supabase.co`
- **service_role key** (`sb_secret_…` / the long secret one). Use the
  *service role* key: the desktop app connects without a signed-in user and
  RLS blocks the anonymous role by design.

از **Project Settings → API**:

- **Project URL** — مثلاً `https://abcd1234.supabase.co`
- **کلید service_role**. حتماً کلید *service role* را بردارید: برنامه دسکتاپ
  بدون کاربر لاگین‌شده وصل می‌شود و RLS نقش ناشناس را عمداً بسته است.

## 4. Configure the app | تنظیم برنامه

1. Run the app → **Settings** page → **Storage & Sync** card.
2. Paste the **Project URL** and the **service key**.
3. **Save** (the key goes to Windows Credential Manager — never to files),
   then **Test**. A green "Cloud reachable (… ms)" confirms the setup.
4. The card header shows `Cloud sync: On` and the queue drains every ~30 s.

1. برنامه را اجرا کنید → صفحه **Settings** → کارت **Storage & Sync**.
2. **Project URL** و **کلید service** را پیست کنید.
3. **Save** بزنید (کلید فقط در Windows Credential Manager ذخیره می‌شود) و بعد
   **Test**. پیام سبز «Cloud reachable» یعنی همه‌چیز درست است.
4. در کارت، `Cloud sync: On` نمایش داده می‌شود و صف هر ~۳۰ ثانیه خالی می‌شود.

## 5. Verify | بررسی نهایی

After saving, watch the card: `Sync queue: N pending` should drop to `0`
within a minute, and `Last sync` shows the latest timestamp. In Supabase →
**Table Editor** you will find `sessions`, `audit_log`, `app_logs` rows
appearing as you use the app.

بعد از ذخیره، عدد `Sync queue` باید در حد یک دقیقه به `0` برسد و
`Last sync` به‌روز شود. در ساپابیس → **Table Editor**، ردیف‌های
`sessions`، `audit_log` و `app_logs` را می‌بینید.

---

## Notes | نکات

- **Free tier pause**: paused projects put the worker into a 15-minute
  cooldown; local writes continue and sync automatically when the project
  wakes up.
  **توقف پلن رایگان**: اگر پروژه متوقف شود، worker وارد حالت انتظار ۱۵ دقیقه‌ای
  می‌شود؛ نوشتن محلی ادامه پیدا می‌کند و بعد از فعال‌شدن پروژه خودکار sync می‌شود.
- **Duplicates are impossible**: every row is upserted by its UUID, so
  offline sessions sync later without duplicates.
  **تکرار رخ نمی‌دهد**: هر ردیف با UUID خودش upsert می‌شود؛ بنابراین
  همگام‌سازیِ بعد از دوره آفلاین بدون ردیف تکراری است.
- **What is mirrored**: business data + WARNING+ logs. DEBUG/TRACE logs and
  high-frequency metrics stay local only.
  **چه چیزی mirror می‌شود**: داده‌های کاری + لاگ‌های WARNING به بالا. لاگ‌های
  DEBUG/TRACE و متریک‌های پرتکرار فقط محلی می‌مانند.
- **Key security**: the service key lives only in Windows Credential Manager
  (vault). It is never written to config files, logs, exports or crash reports.
  **امنیت کلید**: کلید سرویس فقط در Windows Credential Manager می‌ماند و هرگز
  در فایل تنظیمات، لاگ، خروجی یا گزارش کرش ظاهر نمی‌شود.
