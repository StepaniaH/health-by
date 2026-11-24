# Changelog

User-visible changes, newest first. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- **Deployment & governance**: added `SECURITY.md`, expanded `.dockerignore`, generated runtime constraint lockfile `requirements-lock.txt` (Dependabot-discoverable, shared across Docker and CI checks), added multi-arch `linux/amd64,linux/arm64` + SBOM/provenance CI publishing and `workflow_call` checks verification, and added non-medical self-check disclaimers in the UI and docs.
- **External key injection**: added `HEALTH_BY_SECRET_KEY` and `HEALTH_BY_SECRET_KEY_PATH` support with Fernet key validation and atomic `0600` key file creation.
- **Offline maintenance & import CLIs**: added `health-by db compress-raw` (with automatic `sqlite3` backup, `--dry-run`, `--vacuum`, and `--batch-size`), `health-by retranslate` (offline snapshot/metric recomputation from `raw_days`), and `health-by import-fitbit` (`health_by/importers/fitbit_zip.py` for Fitbit / Google Takeout ZIP archives and folders with zip-slip/symlink/size guards).
- **Dark mode**: added `auto` / `light` / `dark` theme tokens in CSS, CSP-safe `web/js/theme.js` (`localStorage` + `prefers-color-scheme`), and theme pickers on both dashboard and settings pages.
- **End-to-end smoke suite & CI browser/Docker checks**: added HTTP + Node ES-module DOM smoke coverage (`tests/test_e2e_smoke.py`), real Chromium Playwright E2E smoke test (`tests/test_browser_e2e.py`), CI `docker-build` and `browser-e2e` jobs (`.github/workflows/checks.yml`), and weekly `.github/dependabot.yml` updates.
- **Documented single-user design boundaries**: recorded intentional single-user trade-offs and deferred enhancements in `SECURITY.md`, `docs/architecture.md`, and `docs/zh/architecture.md`.

### Security

- **Local web security baseline**: validate incoming `Host` headers (with `HEALTH_BY_ALLOWED_HOSTS` opt-in for reverse proxies/domains), enforce same-origin `Origin`/`Sec-Fetch-Site`/`Referer` CSRF checks on state-changing routes and `/oauth/start`, disable `/openapi.json`, and emit `CSP`, `X-Frame-Options`, `X-Content-Type-Options`, `Referrer-Policy`, and `Cache-Control: no-store` on HTML pages and API routes (`Cache-Control: no-cache` on `/static/*`).
- **BYOK AI endpoint hardening**: reject link-local/metadata/unspecified hosts (including after DNS resolution), require HTTPS when sending an API key to a non-loopback host, automatically clear the stored AI key when changing `ai_base_url` origin without supplying a new key, and cap AI response reads at 2 MiB.
- **Database file permissions**: newly created SQLite databases default to `0600`.
- **OAuth log redaction, cleanup & complete deletion**: redacted query strings on `/oauth/callback` and `/settings` in `uvicorn.access` logs, pruned expired `oauth_states` rows on write, and updated `delete_all_data()` to enable `PRAGMA secure_delete = ON`, clear all 8 tables (`raw_measure_dates`, `oauth_states`), `VACUUM`, and checkpoint WAL.

### Changed

- **Storage compression, per-job raw merge & streaming export**: stored `raw_days.payload` zlib-compressed while decoding legacy JSON transparently, merged same-source re-sync payloads per top-level job key, and switched `health-by export` / `GET /api/v1/export` to row-by-row streaming JSON (`0600` file output).
- **Removed unused Google Health glucose job & unused location scope**: reduced `GoogleHealthProvider` jobs from 31 to 30 and `SCOPES` from 11 to 10 (`8` health read-only + `openid` + `profile`).
- **AI prompt & validation v3 (`v4-input-3`, `v4-window-2`, `rules v3`)**: gave multi-day windows a dedicated `WINDOW_SYSTEM_PROMPT`, computed `trend.direction` via half-split medians for `len >= 3`, tightened truncation repair guidance, stripped one Markdown code fence, fixed English sentence splitting & numbered list false positives, and added sign/metric-unit binding plus clinical diagnosis/prescription checks (while permitting neutral seek-care wording when `irregular_rhythm_alerts > 0`).
- **Lean browser i18n catalog & CI pinning**: excluded `server.*` messages from generated `i18n-messages.js` and pinned third-party GitHub Actions to full commit SHAs with semver tag verification.
- **Modularization & docs sync**: split `insights.py` into `health_by/insights/` package, split `api/app.py` into `security.py` + `APIRouter` modules, split `web/js/dashboard.js` into per-section ES modules, and synchronized architecture, statistical method, i18n, and life-science-basis documentation.
- **Editorial web controls & settings redesign**: replaced native OS dropdown/date popups with bespoke warm-paper custom selects and localized calendar popovers with archived-day indicators (`web/js/ui-controls.js`), redesigned `settings.html` into numbered `01`–`05` editorial blocks, converted AI window presets into segmented controls, added `forced-colors` native checkbox fallback, and added one-shot stale-HTML reload recovery.

### Fixed

- **Empty-install dashboard crash & `v0.0.2` upgrade path**: restored missing `web.dashboard.showEmpty.*` translations, updated `scripts/check_i18n.py` to validate JS `t()` keys, added SQLite migration v9 (and runtime fallback) for legacy `provider="demo"` settings, and recorded `schema_version` per migration step.
- **Sync & concurrency correctness**: preserved `raw.parse_issues` warnings as `list[dict]`, serialized shared connection access in the API layer, isolated background auto-sync onto its own SQLite connection, and joined the auto-sync thread on shutdown.
- **Metric & correlation accuracy**: fixed `hourly_activity()` per-point averaging bias and `cosinor()` half-hour bin centering, excluded midnight-split boundary fragments in `sri()`, fixed afternoon `sleep_timing()` 12-hour wrap-around, paired lagged correlations by calendar date on both backend and frontend, and corrected negative-CI correlation ranking.
- **Out-of-bounds filtering, lookback buffering, AR(1) correction & stability floor**: applied centralized `metrics.BOUNDS` consistently before `field_appears()` and `calendar_averages()`, expanded `/api/v1/summary`, `/api/v1/insights`, and single-day AI lookback buffers to `BASELINE_WINDOW_DAYS + 8` without altering display windows, applied Bartlett effective sample size `n_eff` to daily correlations, and fixed `MIN_ROLLING_SAMPLES = 2` and `STABLE_WITHIN_SD` noise floor in `reliability.py`.
- **Dashboard, charts, settings & i18n polish**: fixed `.block-details` CSS, contrast (`--ink-3`, `--m-sleep-rem`), `prefers-reduced-motion`, `:focus-visible`, collapsible `#today-criteria-details` & `#today-summary` visibility across empty states, `bandChart` tooltips & calendar-day gap spacing/line breaks, `hrDayChart` Y-axis, `scatterChart` regression `<clipPath>`, wall-clock `timelineRibbon` parsing, non-link sub-section cards, responsive `runs-table` cells, `pollSync()` button lock & progress keys, date-switch 503/404 stale-state isolation, SpO₂-only/water-only section visibility, `clinical_safety` AI output suppression, and 10+ EN/ZH translation fixes.
- **Reliability, packaging, migration, sync, AI, CLI & importer hardening**: included `LICENSE` in Docker build context, pinned `requirements-lock.txt` (`certifi==2026.11.20`, `opentelemetry-api==1.45.2`) across Docker and CI with `httpx2>=2.0` dev dep, made per-version SQLite migrations crash-atomic inside explicit transactions with idempotent `ALTER TABLE ADD COLUMN` recovery for pre-existing half-migrated legacy databases, stripped `Content-Encoding` after streaming AI decompression to fix gzip responses, closed `clear_all()` vs. multi-date queued/in-flight/auto-sync/cross-process sync races (`meta.clear_generation` + atomic `BEGIN IMMEDIATE` writeback) and cleared in-memory `last_result`, fixed single-worker queue dedupe & coordinator exit race, retained intraday-only days & auto-sync retry of failed middle days / `empty` throttling, enforced strict `goals`/`sync` validation & partial goal merge, made `serve --host/--port` propagate into `create_app`, hardened `export` (refuse DB overwrite, atomic temp replace + read snapshot), made `--dry-run` CLIs strictly read-only (supporting legacy `fitbit-by.db` and `immutable=1` without creating `-wal`/`-shm`), preserved `coverage` on `retranslate`, protected existing non-synthetic rows in `make_dev_data` (plus rejecting unmigrated legacy directories), filtered zero sentinels from daily display/AI payloads, fixed AI window preset/custom state transitions and in-place retry on 503, fixed `hr_min`/`hr_max` raw extremes, Google Health `>=`-only ECG UTC lower-bound padding & same-day ECG storage checks, SRI split invariance, circular `sleep_timing()`, near-goal `_ratio()` rounding, calendar-window `achievements_summary`, unrounded correlation significance cutoffs, calendar-axis weight `lineChart`, and Fitbit importer distance scaling / strict CSV header & row truncation detection / true `--overwrite` blocking on corrupt archives / corrupt-file reporting.

## [0.0.3] - 2026-11-03

### Changed

- The demo data source is gone: no settings switch, no `demo` command, no seed endpoint, and a new
  installation starts empty. A local dataset with the same shape as a real library is built by
  `devtools/make_dev_data.py`, which is not part of the published package.
- The layout, the interface and the screenshots were rebuilt on that dataset.
- A missing browser suite no longer passes silently in CI.

## [0.0.2] - 2026-11-02

First release with the current interface and data contract. Fixes for paths that failed silently:
nothing raised an error, the numbers were just wrong.

### Fixed

- **Switching data sources no longer mixes them.** Changing a day's source now replaces the whole day
  (snapshot, metrics, raw payload). Re-syncing the same source still merges field by field.
- **A fully failed fetch is reported as failed.** Zero successful endpoints used to fall through to the
  total, so a failed day looked like a normal empty day and became the backfill anchor. It now reports
  failure, keeps the zero count, and does not advance the backfill cursor.
- **One baseline, one number.** `/summary` claimed a 28-day baseline while reading 14 days, so the same
  day could show two different personal baselines (80 vs 50). Analysis now reads the full window; charts
  still draw 14 days.
- **No more "0 kg".** A body card with body fat but no weight no longer renders a zero.
- **Recorded zero is not missing.** A day with recorded but zero activity returns no percentage instead
  of `0%`.

### Added

- **English and Chinese interface**, chosen from `?lang=`, then the saved preference, then the browser.
  All UI text comes from a catalog; `scripts/check_i18n.py` fails on unlocalized literals and on locale
  formatting outside the i18n module.
- **7-day averages for noisy metrics.** Metrics whose day-to-day variation is mostly noise are shown as a
  rolling mean with a visible label; counts such as steps stay daily. Which one applies is decided by
  measured reliability, not by metric name.
- **Coverage gaps are reported.** Raw archive dates that never reached the normalized day table are
  listed, so "no record" and "not normalized" are distinguishable.
- **AI readings carry both time scopes.** Single-day analysis sends today's value and the 7-day average
  for the six state metrics, with the scope in the field name. Analysis cache keys include the prompt and
  payload versions, so text generated under an older contract is never reused.
- Optional BYOK analysis through any OpenAI-compatible endpoint, with an explicit outbound allow-list,
  per-request audit rows, and one repair attempt on validation failure.

## [0.0.1] - 2026-10-30

First public release: a self-hosted dashboard that syncs Google Health / Fitbit data to your own
machine and keeps it.

### Dashboard

- Six numbered areas — Today · AI analysis · Sleep · Activity · Cardiovascular · Body & training — plus
  a Data page. Each area page follows today → last 30 days → patterns → detail.
- Desktop-first density with a working 390px layout.
- Missing data is stated, never filled with placeholders.

### Analysis

- Personal baselines instead of population standards: comparisons are against your own history.
- Documented metrics: Sleep Regularity Index, social jetlag, 24-hour cosine rhythm, ACWR, SWC, and
  correlation confidence intervals. Definitions in [docs/methods.md](docs/methods.md).
- Every conclusion carries its sample size.
- Multiple-comparison correction: relationships that do not pass are not reported as findings, but their
  scatter plots are still drawn.
- Recovery is four fixed thresholds, printed on the page, not a composite score.

### Data

- Single SQLite file (WAL, automatic schema migration), raw responses archived per day and recomputable.
- "Upstream has no data" and "parsing failed" are separate states.
- Transient failures retry; a partially failed sync exits non-zero and names the affected days.
- Concurrent sync of the same day is locked; automatic sync fills gaps instead of only fetching the last
  two days.

### Interface

- Build-free frontend: native ES modules and hand-written SVG charts, no toolchain.
- Every chart has a text alternative describing its content and sample size.
- A window label follows the number it describes; labels without information are dropped.

### Deployment

- Docker Compose, bound to `127.0.0.1` by default.
- Demo mode needs no credentials.
- Real data uses BYO OAuth: your own Google Cloud project, credentials encrypted in the data directory.
