# CLAUDE.md — Vesta Dashboard

Operating rules for Claude Code in this repo. Read `ARCHITECTURE.md` for the full plan and target structure. This file is the working discipline — follow it every session.

## Golden rules
- **Build forward in small, testable steps.** Never refactor or "match" the whole codebase in one operation. One concern per step.
- **No code changes without explicit approval.** Propose the plan with reasoning first, wait for go-ahead, then implement.
- **Every step ends green:** migrations apply, tests pass, the app runs. Don't start the next step until the current one is clean.
- **Logic lives in `services.py` / `selectors.py`.** Views, webhooks, and management commands are thin orchestrators with no business logic in them.

## `_salvage/` — read-only reference
- `_salvage/` holds proven code from the previous build: core models, integration clients, and sync/reconciliation logic.
- It is **reference to port FROM, never to import.** Do not add it to `INSTALLED_APPS`, do not wire it into the running app, and keep it out of test collection. Its code contains intentional broken imports.
- Port code out of it into clean apps as each step calls for it. It gets deleted once everything worth keeping has been ported.

## Standards
- **Dependencies:** `uv` + `pyproject.toml`. Never create `requirements.txt`.
- **Logging:** structured JSON via `python-json-logger`.
- **Secrets:** environment variables only — `.env` locally, Railway env in prod. Never hardcode, never commit `.env`, never overwrite the existing `.env`.
- **Webhooks:** HMAC-verified.
- **Email sends:** staff-authenticated and role-gated. Nothing sends without a human triggering it — every email is draft → human review → send.
- **Async:** no Celery yet. Scheduling via GitHub Actions cron. Keep service functions pure and callable so they are Celery-ready later.

## Roles
- **ADMIN** (Rodrigo) — everything.
- **LEASING** (Bill) — leasing only.
- **MAINTENANCE** (Zach, Camilo) — maintenance only.
- Owners never log in; they only receive email.

## Build order (detail in ARCHITECTURE.md)
1. Scaffold + standards
2. Port `core` models (from `_salvage/`)
3. Port `integrations` — PropertyMeld first
4. Comms engine against **maintenance** (the pilot product)
5. Owner report (second product)
6. Leasing (last — hardest data: three-system reconciliation)
7. Dashboard + role-scoping
