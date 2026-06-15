# P2P Monitor

Django web application for Bybit P2P USDT/RUB accounting.

## Quick start (Docker)

```bash
cp .env.example .env
# Generate encryption key: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
# Set FIELD_ENCRYPTION_KEY in .env

docker compose up --build
```

Open http://localhost:8000 — login `admin` / `admin`

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
