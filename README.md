# WashIQ (FastAPI)

## Run locally

From this folder (`WashIQ-Python`):

```powershell
cd "c:\Users\Teacher\Documents\Wash Bay\WashIQ-Python"
python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

## If you see “Internal Server Error” / “This page isn’t working”

1. **Restart** the server after pulling changes (`Ctrl+C`, then run the command above again).
2. With the **default dev** `SESSION_SECRET_KEY` in `app/main.py`, a 500 error should show a **plain-text traceback in the page body** (scroll past the browser’s generic message, or use **View page source**).
3. In the **terminal** where `uvicorn` runs, the same error is logged.
4. Optional: force detailed errors even after you change the secret:

   ```powershell
   $env:WASHIQ_DEBUG="1"
   python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
   ```

## Admin login (seeded in dev)

- Email: `admin@washiq.local` (constant `ADMIN_SEED_EMAIL` in `app/auth.py`)
- Password: `Admin12345`

After login, admins are sent straight to `/dashboard`. If an older database row had `is_admin = 0` for that email, the next successful login fixes it automatically.

## Customer portal URLs

- **`/customer`** — dashboard summary (stats + service cards). Customers are redirected here after login by default.
- **`/my-bookings`** — full bookings list.
- **`/profile`** — account details only.

Admin app uses **`/dashboard`**; customers use **`/customer`** so routes do not clash.

## Email, SMS, and receipts (Phase D)

WashIQ can send **booking confirmations** and **payment summaries** by email/SMS. If providers are not configured, messages are **logged** (server console) and stored in the **`communication_logs`** table so the flow still works in development.

### Environment variables (optional)

**SMTP (real email)**

| Variable | Example | Notes |
|----------|---------|--------|
| `SMTP_HOST` | `smtp.example.com` | Required with `SMTP_FROM_EMAIL` to send |
| `SMTP_PORT` | `587` | Default `587` |
| `SMTP_USE_TLS` | `true` | Default `true` |
| `SMTP_USERNAME` | | Optional for some relays |
| `SMTP_PASSWORD` | | Keep in env / secret manager, never in code |
| `SMTP_FROM_EMAIL` | `bookings@yourdomain.com` | From address |

**SMS (optional webhook)**

| Variable | Notes |
|----------|--------|
| `SMS_API_KEY` | Sent as `X-API-Key` header |
| `SMS_WEBHOOK_URL` | `POST` JSON body: `to`, `message`, `sender` |
| `SMS_SENDER_ID` | Display name (default `WashIQ`) |

### Where to look

- **Customer:** after payment, **View receipt** → `GET /receipt/{payment_id}` (print-friendly).
- **Admin:** **Communications** in the sidebar → `/dashboard/communications` (recent email/SMS log rows).

Do **not** commit real passwords or API keys; use environment variables or your host’s secret store.
