# Architecture — health-by v0.0.1

[Chinese](zh/architecture.md)

## Goals

- **Self-hosted first** — data lives only in local SQLite; no telemetry; binds `127.0.0.1` by default.
- **One internal contract** — views and analysis never depend on a remote API's shape, so the data
  source is replaceable.
- **Local synthetic data** — `devtools/` builds a database with the same shape as real data, so the
  interface can be developed and reviewed without credentials.
- **Restraint** — no diagnosis, no composite score; every conclusion traces back to a measurement, a
  user goal, or a visible baseline.

## Data flow

```
[Fitbit/Pixel device] → Bluetooth → Fitbit/Google phone app → cloud sync
        │
        ▼  Google Health API v4 (OAuth 2.0, REST)
 provider.fetch_raw(date)      31 read tasks: 14-day trend windows + 90-day ECG + intraday for the selected day
        │
        ▼  provider.translate(raw)  (pure function, well tested)
 DaySnapshot (single contract; every field nullable)
        │
        ▼  storage.store_day
 days + daily_metrics + raw_days (raw payload archived per day, recomputable)
        │
        ▼  analysis.build_analysis
 baseline comparison / period deltas / coverage and freshness / rule-based headline and insights
        │
        ▼  insights.build_insights  (pure local computation, zero extra API calls)
 recovery (4 signals + sleep debt) / load (ACWR + MVPA) / rhythm (SRI + social jetlag + cosine)
  / correlations (r + 95% CI) / weekly / summary_table / bands / heatmap
        │
        ▼
 FastAPI (/api/v1/* + OAuth callback) + zero-build frontend (dashboard / settings)
```

## Modules

| Module | Responsibility |
|---|---|
| `health_by/config.py` | data directory / host and port / outbound rate-limit parameters |
| `health_by/db.py` | SQLite (WAL) connection and versioned migrations |
| `health_by/secrets.py` | Fernet credential encryption (`data/secret.key`, 0600) |
| `health_by/models.py` | `DaySnapshot` contract and metric extraction |
| `health_by/storage.py` | read/write for days / daily_metrics / raw_days / sync_runs / settings |
| `health_by/providers/base.py` | data source contract (fetch_raw / translate / optional OAuth methods) |
| `health_by/providers/google_health/` | real data source: OAuth (PKCE) + task registry + fetch + translate |
| `health_by/engine.py` | sync engine: shared throttle, atomic token rotation, queue + concurrent workers, auto-sync thread |
| `health_by/analysis.py` | baseline comparison / period deltas / coverage and freshness / rule-based insights |
| `health_by/metrics.py` | SRI / social jetlag / cosine rhythm / ACWR / SWC / Fisher CI (pure functions) |
| `health_by/insights.py` | recovery, load, rhythm, correlations, weekly report, summary table (local archive only) |
| `health_by/api/app.py` | FastAPI factory: JSON API (including `/api/v1/insights`) + OAuth routes + static pages |
| `health_by/web/` | zero-build frontend (native ES modules + hand-written SVG charts) |
| `health_by/cli.py` | Click CLI: serve / sync / status / export / db (stats, check, vacuum) |
| `devtools/` | synthetic generator used for local development data; not part of the packaged module |

## Security boundaries

- Credentials (client_secret, tokens) are Fernet-encrypted into SQLite; the key file
  `data/secret.key` is 0600; API responses never echo the secret (only `has_client_secret: true/false`).
- OAuth flow: PKCE S256 plus a one-time `state` stored in the database with a 15-minute TTL; the code
  is exchanged only after the callback validates `state`.
- The service binds `127.0.0.1` by default; sync requests go to official Google domains;
  optional AI sends whitelisted metrics to the user-configured service;
  destructive admin writes (clearing data) require a verbatim confirmation.
- A credential decryption failure (replaced key) produces an explicit error rather than silently
  losing data.

## Database

Tables: `meta` / `settings` / `credentials` / `days` / `daily_metrics` / `raw_days` / `raw_measure_dates` /
`sync_runs` / `oauth_states` / `ai_analyses` / `ai_egress`.

`raw_measure_dates` indexes the measurement dates embedded in each archived payload, so a coverage
check is a table lookup instead of re-parsing every payload.

A schema change means appending a new version block to `MIGRATIONS` in `db.py` and incrementing
`SCHEMA_VERSION`; published fields are never changed destructively.

## Key design decisions

- **one translation pipeline** — the development generator emits GHA-shaped payloads that go through
  the same translation code as real data, so there is no second implementation to drift.
- **Partial failure is tolerated** — the 31 tasks run independently, one failure never cancels the
  rest, and coverage reaches the interface.
- **One meaning for 401** — every 401 means "reconnect required", matching Google's 7-day token policy
  in testing mode.
- **Raw archive** — each day's raw payload is archived, so it can be re-translated or backfilled with
  new metrics later without fetching again.
