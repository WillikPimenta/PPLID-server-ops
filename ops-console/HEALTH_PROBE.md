# Health / OPS-console monitoring — Waitress & probe tunables

## Waitress (`scripts/deploy/lib.ps1` → `Start-PplidBackend`)

| Parameter | Default | Env override | Notes |
|-----------|---------|--------------|-------|
| `channel_timeout` | **90s** (was 600) | `PPLID_WAITRESS_CHANNEL_TIMEOUT` | Drop idle/stuck channels sooner; do not raise to mask saturation |
| `threads` | Waitress default (4) | `PPLID_WAITRESS_THREADS` | Raise only after measuring queue/latency/DB |
| `connection_limit` | Waitress default (100) | `PPLID_WAITRESS_CONNECTION_LIMIT` | Same — capacity is not the primary fix |
| `backlog` | Waitress default | `PPLID_WAITRESS_BACKLOG` | Optional |

Rollback: set `PPLID_WAITRESS_CHANNEL_TIMEOUT=600` before restart.

## OPS-console probe coordinator (`health_probe.py`)

| Setting | Default |
|---------|---------|
| success cache TTL | 5s |
| probe timeout | 1s |
| backoff initial / max | 5s / 60s |
| stale window | 120s |
| offline after N failures | 3 |
| global concurrency | 3 |

## Frontend polling (`public/js/utils.js` + `app.js`)

| Mode | Interval |
|------|----------|
| Normal | 5s (`LITE_REFRESH_MS`) |
| Deploy active | 2s |
| Hidden tab | paused |
| Overlap | coalesced (single in-flight) |

## Django health

| Route | Behavior |
|-------|----------|
| `GET /api/v1/health/live/` | O(1) liveness, no DB |
| `GET /api/v1/health/` / `ready/` | readiness (SELECT 1), no showmigrations |
| `GET /api/v1/health/deep/` | cached/async migrations diagnostics |
| `?deep=1` on `/health/` | compat: async cache only, never blocks Waitress |

## Migrations in OPS-console

`showmigrations` only via `server_ops.fetch_migrations_status` / `?migrations=1` on database metrics — not overview, overview-lite, or collector.
