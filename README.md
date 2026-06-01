# Vesta Dashboard

Owner communications engine for Vesta Property Management. See `ARCHITECTURE.md` for the full plan.

## Setup

```bash
# Install uv (if not already)
curl -LsSf https://astral.sh/uv/install.sh | sh

# Install dependencies
uv sync

# Copy and fill in environment variables
cp .env.example .env
# Edit .env with real values
```

## Environment

All configuration is via environment variables. See `.env.example` for the full list.

Key variables:
- `DJANGO_SECRET_KEY` — required
- `DATABASE_URL` — Postgres connection string
- `DJANGO_SETTINGS_MODULE` — `config.settings.dev` (local) or `config.settings.prod`

## Database

```bash
uv run python manage.py migrate
```

## Run

```bash
# Development
uv run python manage.py runserver

# Production
gunicorn config.wsgi
```

## Test

```bash
uv run pytest
```

## Health check

`GET /healthz` returns `{"status": "ok"}` with HTTP 200.
