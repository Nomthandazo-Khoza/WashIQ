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
