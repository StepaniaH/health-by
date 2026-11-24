# Architecture — health-by

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
        ▼  Google Health API v4 (OAuth 2.0, REST)  or  Fitbit/Takeout offline ZIP (`import-fitbit`)
 provider.fetch_raw(date)      30 read tasks: 14-day trend windows + 90-day ECG + intraday for the selected day
        │
        ▼  provider.translate(raw)  (pure function, recomputable via `retranslate`)
 DaySnapshot (single contract; every field nullable)
        │
        ▼  storage.store_day
 days + daily_metrics + raw_days (zlib BLOB compressed, per-job merged)
        │
        ▼  analysis.build_analysis + reliability.assess
 baseline comparison / period deltas / variance-ratio rolling display / coverage & freshness
        │
        ▼  insights.build_insights  (pure local computation, zero extra API calls)
 recovery (4 signals + sleep debt) / load (ACWR + MVPA) / rhythm (SRI + social jetlag + cosine)
  / correlations (Pearson + Bartlett n_eff + Fisher CI) / weekly / summary_table / bands / heatmap
        │
        ▼
 FastAPI routers (/api/v1/* + OAuth callback) + zero-build ES-module frontend (light/dark/auto)
```

## Modules

| Module | Responsibility |
|---|---|
| `health_by/config.py` | data directory / host and port / outbound rate-limit parameters |
| `health_by/db.py` | SQLite (WAL) connection (`0600`), versioned migrations, online backup helper |
| `health_by/secrets.py` | Fernet credential encryption (`HEALTH_BY_SECRET_KEY*` or `data/secret.key`, `0600`) |
| `health_by/models.py` | `DaySnapshot` contract and metric extraction |
| `health_by/storage.py` | read/write for days / daily_metrics / zlib `raw_days` / sync_runs / streaming export |
| `health_by/providers/base.py` | data source contract (fetch_raw / translate / optional OAuth methods) |
| `health_by/providers/google_health/` | real data source: OAuth (PKCE, 10 scopes) + 30-task registry + fetch + translate |
| `health_by/importers/fitbit_zip.py` | offline importer for Fitbit / Google Takeout ZIP archives and extracted directories |
| `health_by/engine.py` | sync engine: shared throttle, atomic token rotation, queue + concurrent workers, auto-sync thread |
| `health_by/analysis.py` | baseline comparison / period deltas / coverage and freshness / rule-based insights |
| `health_by/reliability.py` | within/between-week variance ratio (`rolling7` vs `daily`) & noise-floor gating |
| `health_by/metrics.py` | centralized `BOUNDS`, SRI / social jetlag / cosine rhythm / ACWR / Bartlett `n_eff` / Fisher CI |
| `health_by/insights/` | modular package (`common`, `series`, `correlations`, `recovery`, `rhythm`, `builder`) |
| `health_by/ai/` | aggregate-egress allowlist (`v4-input-3` / `v4-window-2`), model call, validation `v3`, audit log |
| `health_by/api/` | FastAPI factory (`app.py`), `security.py`, `common.py`, and split `routes_*.py` `APIRouter` modules |
| `health_by/web/` | zero-build ES-module frontend (`theme.js`, `ui-controls.js`, split `dashboard-*.js`, SVG charts, light/dark CSS tokens) |
| `health_by/cli.py` | Click CLI: serve / sync / status / export / retranslate / import-fitbit / db (stats, check, vacuum, compress-raw) |
| `devtools/` | synthetic generator used for local development data; not part of the packaged module |

## Security boundaries

- Credentials (OAuth client secret, refresh/access tokens, AI API key) are Fernet-encrypted into SQLite;
  the key comes from `HEALTH_BY_SECRET_KEY`, `HEALTH_BY_SECRET_KEY_PATH`, or `data/secret.key` (`0600`);
  new SQLite DB and CLI export files default to `0600`, and API responses never echo secrets.
- OAuth flow: PKCE S256 plus a one-time `state` stored in `oauth_states` (15-minute TTL, expired rows
  pruned on write); HTTP access logs redact query strings on `/oauth/callback` and `/settings`.
- Local web security middleware (`api/security.py`): validates incoming `Host` headers (with
  `HEALTH_BY_ALLOWED_HOSTS` opt-in), rejects cross-origin/cross-site state-changing requests and
  `/oauth/start` via `Origin` / `Sec-Fetch-Site` / `Referer`, disables `/openapi.json` & `/docs`, and sets
  `CSP` (`script-src 'self'`), `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`,
  `Referrer-Policy: no-referrer`, and `Cache-Control: no-store` on HTML page and API routes (`Cache-Control: no-cache` on `/static/*`).
- BYOK AI endpoints validate outbound URLs against SSRF (rejecting link-local/metadata/unspecified IPs
  after DNS resolution), require HTTPS when sending API keys off-loopback, auto-clear stored keys if
  the base URL origin changes without a new key, and cap responses at 2 MiB.
- `delete_all_data()` enables `PRAGMA secure_delete = ON`, clears all 8 health/sync/AI/OAuth tables
  (preserving credentials and settings), runs `VACUUM`, and checkpoints WAL; web clearing requires
  `DELETE ALL DATA`.

## Database

Tables: `meta` / `settings` / `credentials` / `days` / `daily_metrics` / `raw_days` / `raw_measure_dates` /
`sync_runs` / `oauth_states` / `ai_analyses` / `ai_egress`.

`raw_days.payload` stores zlib-compressed JSON BLOBs while transparently decoding legacy JSON TEXT; offline
migration for existing archives is available via `health-by db compress-raw`. Same-source re-sync merges
top-level job keys to protect earlier jobs from partial failures, while cross-source writes replace `days`
and `daily_metrics` cleanly.

`raw_measure_dates` indexes the measurement dates embedded in each archived payload, so a coverage
check is a table lookup instead of re-parsing every payload.

A schema change means appending a new version block to `MIGRATIONS` in `db.py` and incrementing
`SCHEMA_VERSION`; published fields are never changed destructively.

## Key design decisions

- **One translation pipeline** — the development generator emits GHA-shaped payloads that go through
  the same translation code as real data, so there is no second implementation to drift.
- **Partial failure is tolerated** — the 30 tasks run independently, one failure never cancels the
  rest, and coverage reaches the interface.
- **One meaning for 401** — every 401 means "reconnect required", matching Google's 7-day token policy
  in testing mode.
- **Raw archive** — each day's raw payload is archived, so it can be re-translated offline (`health-by retranslate`)
  or backfilled with new metrics later without fetching again.

## Known Trade-offs & Deferred Enhancements

Because Health By targets **single-user, local-first, self-hosted** deployments, the following items are deliberately recorded as non-blocking future considerations rather than active blockers (see also [SECURITY.md](../SECURITY.md#known-design-boundaries--non-goals-single-user-scope)):

| Area | Current posture | Deferred future option |
|---|---|---|
| Outbound AI DNS rebinding (TOCTOU) | Resolves & rejects link-local/metadata/unspecified IPs before connecting; user configures own `ai_base_url`. | Socket-level IP pinning with custom TLS SNI transport if multi-tenant or untrusted endpoints are ever supported. |
| Default-port `Origin` scheme matching | `Sec-Fetch-Site` already blocks cross-scheme browser requests; `_same_authority` checks host/port. | Add explicit `Origin` scheme comparison in `api/security.py` as low-priority defense-in-depth. |
| Switching Google accounts in one `data/` dir | Assumes one personal archive per `data/` directory; separate accounts should use separate data directories. | Persist a hashed remote account fingerprint in `meta` and warn before mixing records from a different account. |
| Concurrent AI settings save vs. analysis start | `AIService.analyze()` reads settings and credentials sequentially; single-user concurrent collision is negligible. | Read settings and credentials in a single SQLite `BEGIN` snapshot transaction. |
| Transitive `opentelemetry-api` dependency | Pulled in by FastAPI; OpenTelemetry tracing is not imported or enabled in runtime. | Kept up to date via `requirements-lock.txt` and Dependabot. |
| Frontend layout regression coverage | Node ES-module tests + static `scripts/check_web_refs.py` + CI Chromium smoke test (`tests/test_browser_e2e.py`). | Add cross-viewport visual screenshot diffing if the UI surface grows significantly. |
