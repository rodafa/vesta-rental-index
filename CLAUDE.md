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

## Workflow Orchestration

### 1. Plan Node Default
- Enter plan mode for ANY non-trivial task (3+ steps or architectural decisions)
- If something goes sideways, STOP and re-plan immediately - don't keep pushing
- Use plan mode for verification steps, not just building
- Write detailed specs upfront to reduce ambiguity

### 2. Subagent Strategy
- Use subagents liberally to keep main context window clean
- Offload research, exploration, and parallel analysis to subagents
- For complex problems, throw more compute at it via subagents
- One task per subagent for focused execution

### 3. Self-Improvement Loop
- After ANY correction from the user: update `tasks/lessons.md` with the pattern
- Write rules for yourself that prevent the same mistake
- Ruthlessly iterate on these lessons until mistake rate drops
- Review lessons at session start for relevant project

### 4. Verification Before Done
- Never mark a task complete without proving it works
- Diff behavior between main and your changes when relevant
- Ask yourself: "Would a staff engineer approve this?"
- Run tests, check logs, demonstrate correctness

### 5. Demand Elegance (Balanced)
- For non-trivial changes: pause and ask "is there a more elegant way?"
- If a fix feels hacky: "Knowing everything I know now, implement the elegant solution"
- Skip this for simple, obvious fixes - don't over-engineer
- Challenge your own work before presenting it

### 6. Autonomous Bug Fixing
- When given a bug report: just fix it. Don't ask for hand-holding
- Point at logs, errors, failing tests - then resolve them
- Zero context switching required from the user
- Go fix failing CI tests without being told how

## Task Management

1. **Plan First**: Write plan to `tasks/todo.md` with checkable items
2. **Verify Plan**: Check in before starting implementation
3. **Track Progress**: Mark items complete as you go
4. **Explain Changes**: High-level summary at each step
5. **Document Results**: Add review section to `tasks/todo.md`
6. **Capture Lessons**: Update `tasks/lessons.md` after corrections

## Core Principles

- **Simplicity First**: Make every change as simple as possible. Impact minimal code.
- **No Laziness**: Find root causes. No temporary fixes. Senior developer standards.
- **Minimal Impact**: Changes should only touch what's necessary. Avoid introducing bugs.