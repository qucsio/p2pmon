# P2P Monitor

Django web application for Bybit P2P USDT/RUB accounting.

## Quick start (Docker)

```bash
cp .env.example .env
# Fill in SECRET_KEY, POSTGRES_PASSWORD, FIELD_ENCRYPTION_KEY and DJANGO_SUPERUSER_PASSWORD
# (the generate commands are in .env.example). Never copy key values from the example.

docker compose up --build
```

Open http://localhost:1337 — log in with DJANGO_SUPERUSER_USERNAME / DJANGO_SUPERUSER_PASSWORD from `.env`

## Setup Bybit account

1. Admin → Exchange Accounts → Add account (API key + secret)
2. Sync → Refresh Now (or `docker compose exec web python manage.py run_full_backfill --account-id=1`)
3. Rebuild ledger: `docker compose exec web python manage.py rebuild_ledger --account-id=1`

## Services

- **web** — Django (port 8000)
- **worker** — Celery worker
- **beat** — Hourly auto sync
- **db** — PostgreSQL
- **redis** — Celery broker + sync lock
