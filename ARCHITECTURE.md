# Vesta Dashboard — Architecture

**Status:** v1 scope, locked.
**Repo:** new and standalone (e.g. `vesta-dashboard`). The old `vesta-rental-index` repo is a read-only parts donor, kept running until this reaches parity, then retired.

---

## North star

This is not a "dashboard" in the charts-and-graphs sense. It is a **communications engine**: its core job is to produce three recurring owner emails, consistently and easily, with a human reviewing every send. Every screen exists to feed an email. Simplicity is the design goal — the system should be *one good pattern repeated three times*, never three bespoke subsystems.

The rule that protects this: if something doesn't fit the shared comms pattern, it probably doesn't belong in v1.

---

## Users & roles

Four users to start. Owners never log in — they only receive email.

| User | Role | Scope |
|---|---|---|
| Rodrigo | ADMIN | Everything |
| Bill | LEASING | Leasing email only |
| Zach, Camilo | MAINTENANCE | Maintenance email only |

Role lives on a custom `User` model from day one and gates which products a user can generate and send.

---

## The three products

All three are the same shape: pull data for an owner → apply that product's voice guide → render a branded template → draft → human reviews → send via SendGrid. Nothing sends automatically.

| Product | Sender | Cadence | Data source | Recipient |
|---|---|---|---|---|
| Leasing update | Bill | Weekly | RentEngine + RentVine + BoomPay (reconciled) | Owners |
| Maintenance update | Zach, Camilo | Weekly | PropertyMeld | Owners |
| Owner report | Rodrigo | Monthly | Operational/narrative (no financials in v1) | Owners |

Leasing is the data-hardest because it depends on three-system reconciliation. It is built **last** so it never blocks the shared pipeline.

---

## Layers

One Django project, one database, one deploy. Apps are subfolders, separated by responsibility — not by the three products.

```
vesta/
├── config/          # settings split (base/dev/prod), JSON logging, env-driven config
├── accounts/        # FRESH  — custom User + role (ADMIN/LEASING/MAINTENANCE)
├── core/            # PORTED — Owner, Property, Unit, Lease, Tenant (canonical entities)
├── integrations/    # PORTED — rentvine, rentengine, boompay, propertymeld (clients + sync)
├── comms/           # FRESH  — the engine: one pipeline, three product configs
├── leasing/         # FRESH  — selector building the leasing payload per owner
├── maintenance/     # FRESH  — selector building the maintenance payload per owner
├── reporting/       # FRESH  — selector building the owner-report payload per owner
└── dashboard/       # FRESH  — generate → review → send surface (role-scoped)
```

---

## The comms engine

The only "clever" part of the system, kept deliberately small. A *product* is a declarative config, not a code module. It plugs three things into a shared machine:

1. **Data selector** — a function in the relevant domain app that returns an owner's payload.
2. **Voice guide** — tone/style config for that product. Editable by ADMIN only.
3. **Template** — a Vesta-branded HTML email.

`comms/` owns:

- **`VoiceGuide`** — one row per product type, ADMIN-only editable. (This was missing last time; now first-class and tiny.)
- **`EmailDraft`** — a reviewable, auditable record: product type, owner, rendered subject/body, status (`draft` → `sent`), `sent_by`, `sent_at`. Drafts are stored data, not ephemeral.
- **`generate_drafts(product, owner_set)`** — calls the product's selector, feeds payload + voice guide to the Anthropic API, renders the template, writes `EmailDraft` rows.
- **`send_drafts(draft_ids, user)`** — role-gated, sends via SendGrid, stamps who/when.

The dashboard surface is the same page three times: owner list → **Generate Drafts** → review/edit → **Send**. Adding a fourth product later = one config entry + one selector. No new subsystem.

---

## Data flow & system of record

- **RentVine** → system of record for `core` entities (owners, properties, units, leases, tenants). Synced into `core` first; everything FKs to `core.Unit`.
- **RentEngine** → leasing performance (leads, showings, applications, vacancy) → `leasing`.
- **BoomPay / BoomScreen** → applications/screening → `leasing`.
- **PropertyMeld** → maintenance (melds, vendors, work orders) → `maintenance`.
- **SendGrid** → transactional delivery for all sends.
- **Anthropic API** → draft generation inside `comms`.

There is exactly one `Unit`. Its address-component fields (line1, unit_number, city, state, postal) are explicit and populated by the RentVine sync — their absence was the root cause of prior RentEngine linkage drift, so the model fixes it by design.

---

## Keep / rebuild / drop

- **Keep (port, don't retype):** `core` models, integration clients + sync/reconciliation logic, and all hard-won knowledge about why the three leasing systems disagree.
- **Rebuild fresh:** scaffolding, settings, auth, the comms engine, all staff surfaces.
- **Drop entirely:** Apps Script glue, unused surfaces, tangled organization.
- Vulcan and Minerva bots are out of scope for v1; leave them running independently.

---

## Build order

1. **Scaffold + standards** — uv, split settings, JSON logging, env, custom user + role, health check, tests.
2. **Port `core` models** from the old repo (review-and-clean, not blind copy).
3. **Port `integrations`**, starting with PropertyMeld (feeds the pilot product).
4. **Build `comms` end-to-end against maintenance** — one voice guide, one selector, real SendGrid send to a test address. Prove generate → review → send.
5. **Add the owner report** as the second product (reuse the pipeline).
6. **Close the leasing reconciliation** (RentEngine + RentVine + BoomPay; finish the address-drift fix), then add leasing as the third product.
7. **Role-scope the dashboard**, then begin adding further features.

---

## Engineering standards (non-negotiable)

- Service-layer architecture: logic in `services.py` / `selectors.py`; views and management commands are thin orchestrators.
- Dependency management via `uv` + `pyproject.toml`. No `requirements.txt`.
- Structured JSON logging (`python-json-logger`).
- HMAC-verified inbound webhooks; staff-auth-only, role-gated send triggers.
- Secrets only via environment variables (`.env` locally, Railway env in prod). Never committed.
- Celery-ready service layer, but Celery deferred — scheduling via GitHub Actions cron until scale demands otherwise.
- No code changes without explicit approval; propose with reasoning first.

---

## v1 non-goals (the "no" list)

- No fully automated sends — human review on every email, always.
- No owner login or owner portal.
- No financials in the owner report (operational/narrative only for now).
- No Celery.
- No new products or surfaces until the three core emails work end to end.

---

## Brand reference (for templates & voice guides)

- **Colors:** navy `#1E3D58` (primary), sky blue `#6EA5CD`, green `#39B54A`, light grey `#EFF5F9`, orange `#D67011`.
- **Type:** headings Helvetica, body Georgia.
- **Voice:** trustworthy, approachable, transparent — tuned per product via each `VoiceGuide`.
