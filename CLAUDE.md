# Vesta Rental Index

## Project Overview
Django app for Vesta Property Management — internal rental performance index.
Aggregates data from RentVine (portfolio/lease management) and RentEngine (market/leasing activity) into a unified analytics platform.

## Architecture
- **Django 5.2** + **django-ninja** for API, **psycopg** for Postgres, **gunicorn** for production
- **uv** for dependency management (pyproject.toml + uv.lock)
- Docker: `Dockerfile` uses gunicorn (production), `docker-compose.yml` overrides with `runserver` + volume mount for live reload in dev
- Entrypoint auto-runs migrations on container start

## Dev Workflow
- `docker compose up -d` — starts with live reload (no rebuild needed for code changes)
- `docker compose up -d --build` — only needed for dependency or Dockerfile changes
- `docker compose exec web python manage.py shell` — Django shell
- `docker compose exec web python manage.py seed_data --clear` — reset seed data (100 properties, 30% with leasing)

## Key Design Decisions
- **ListingCycle** (market app) bridges RentEngine market data to RentVine lease outcomes — tracks full lifecycle from listing to signed lease
- **DailyUnitSnapshot** status choices: Active, Leased Pending, Occupied, Make Ready
- **DailyMarketStats.average_price** tracks list prices; **average_portfolio_rent** tracks signed lease amounts for occupied units
- **PriceDrop** model only logs downward price changes — PriceChangeDetector must enforce this
- **Property.service_type**: Full Management, Leasing Only, Maintenance Only (default Full Management)

## Data Sources
- **RentVine**: portfolios, owners, properties, units, tenants, leases, applications
- **RentEngine**: prospects, leasing events, showings, multifamily properties, floorplans, market data
- **BoomPay/BoomScreen**: screening applications and reports

## Apps
- `properties` — Property, Unit, Portfolio, Owner, MultifamilyProperty, Floorplan
- `leasing` — Tenant, Lease, Prospect, LeasingEvent, Showing, Application, Applicant
- `market` — DailyUnitSnapshot, DailyMarketStats, DailyLeasingSummary, WeeklyLeasingSummary, MonthlyMarketReport, DailySegmentStats, PriceDrop, ListingCycle
- `screening` — ScreeningApplication, ScreeningReport
- `maintenance` — Vendor, VendorTrade, WorkOrderStatus, WorkOrder, Inspection
- `accounting` — ChartOfAccounts, Ledger, Transaction, TransactionEntry, Bill
- `integrations` — WebhookEvent, APISyncLog + all sync management commands

## VS Code
- Auto-save on focus change enabled (.vscode/settings.json)
- .gitattributes enforces LF for .py, .sh, .html, Dockerfile
