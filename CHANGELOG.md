# Changelog

User-visible changes, newest first.

## Unreleased

Fixes for paths that failed silently: nothing raised an error, the numbers were just wrong.

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

## v0.0.1 — 2026-10-30

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
