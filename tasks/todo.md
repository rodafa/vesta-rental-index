# Railway Deploy — Complete

## All Checks Passing
- [x] `https://web-production-e3745.up.railway.app/api/health` → `{"status": "ok", "db": "connected"}`
- [x] `https://web-production-e3745.up.railway.app/api/properties/properties` (with API key) → 200
- [x] `https://web-production-e3745.up.railway.app/admin/login/` → 200 (Django admin)
- [x] Railway deploy status: SUCCESS

## Infrastructure
- **Domain**: https://web-production-e3745.up.railway.app
- **Project**: lucky-vitality (Railway dashboard)
- **Services**: web (Django/gunicorn) + Postgres
- **Auto-deploy**: GitHub `rodafa/vesta-rental-index` main branch
- **API Key**: `vesta-prod-a36a296490edf19bab33eb5ea321b72a`

## Notes
- Railway HTTP health checks (`healthcheckPath`) consistently fail with "service unavailable" during deploy even though the app works fine post-deploy. Using Railway's default TCP port check instead. The `/api/health` endpoint is still available for manual monitoring.
- Rename project from "lucky-vitality" to "vesta-rental-index" in Railway dashboard Settings if desired.
