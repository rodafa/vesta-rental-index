# Integrations App

## Domain

The **integrations** app is the cross-cutting infrastructure layer that handles communication with all three external APIs: **RentVine**, **RentEngine**, and **BoomPay/BoomScreen**. It provides:

1. **Webhook ingestion** — Every incoming webhook is persisted as a raw `WebhookEvent` before being dispatched to domain-specific handlers.
2. **API sync logging** — Every daily/incremental API pull is logged in `APISyncLog` for auditing and monitoring.

This app does not contain business logic — it is the foundation that other apps' services build on.

## Models

### WebhookEvent
Raw webhook event from any external source. Stored permanently for audit trail and replay capability.
- `source` — RentEngine / RentVine / BoomPay
- `event_type` — INSERT / UPDATE / DELETE
- `table_name` — Source table (prospects, leasing_events, units, etc.)
- `record` — Current state of the record (JSON)
- `old_record` — Previous state for UPDATE events (JSON)
- `processed` — Whether this event has been handled by domain services
- `processed_at` — When processing completed
- `processing_error` — Error message if processing failed
- `received_at` — Webhook receipt timestamp
- **Composite indexes:** (`source`, `table_name`, `event_type`) and (`processed`, `received_at`)

### APISyncLog
Log of daily API sync operations.
- `source` — RentEngine / RentVine / BoomPay
- `endpoint` — API endpoint that was called
- `sync_type` — full / incremental / delta
- `status` — Started / Completed / Failed / Partial
- `records_fetched`, `records_created`, `records_updated` — Sync metrics
- `error_message` — Error details if sync failed
- `started_at`, `completed_at`

## Key Relationships
- WebhookEvent is consumed by handlers in other apps (leasing, properties, screening)
- APISyncLog is written by sync services in other apps

## Future Services
- **WebhookRouter** — Django Ninja endpoint that receives all webhooks, persists to WebhookEvent, and dispatches to domain handlers based on `source` + `table_name`
- **WebhookReplayService** — Re-process failed or missed webhook events
- **SyncScheduler** — Celery Beat schedule for daily API pulls across all sources
- **SyncHealthMonitor** — Alert on failed syncs, gaps in daily snapshots, or unprocessed webhook backlog
- **RateLimitManager** — Respect RentEngine's 20 req/5s rate limit and RentVine's pagination
- **APIClientFactory** — Centralized HTTP clients for RentVine (Basic Auth), RentEngine (Bearer JWT), and BoomPay
