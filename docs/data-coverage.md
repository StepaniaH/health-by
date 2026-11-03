# Data coverage and known pitfalls — Google Health API v4

[Chinese](zh/data-coverage.md)

Sources: cross-checked against the OpenFit source review, Google's documentation, and this project's
implementation. Each item below has a matching comment in the code.

## Read tasks (31)

| Category | Tasks | Endpoint | Window |
|---|---|---|---|
| Metadata | identity / profile / settings / pairedDevices / irnProfile / userinfo | GET `/users/me/*`, `oauth2/v3/userinfo` | — |
| Daily rollup ×12 | steps / total calories / distance / floors / active minutes / active zone minutes / sedentary / weight / body fat / water / nutrition / core temperature | POST `dataPoints:dailyRollUp` (civil range, `windowSizeDays=1`) | 14 days |
| Daily records ×6 | resting heart rate / HRV / blood oxygen / breathing rate / sleep skin temperature / VO₂max | GET `dataPoints:reconcile` | 14 days |
| Sessions | sleep (`google-wearables`) / exercise | GET `dataPoints:reconcile` | 14 days |
| Standalone | ECG (`list`) / irregular rhythm notifications (`list`) / blood glucose | GET `dataPoints` | 90 / 14 / 14 days |
| Intraday | intraday steps / intraday heart rate (`google-wearables`, `list`) | GET `dataPoints` | selected day |

`reconcile` is the official reconciliation stream: it de-duplicates and merges across every source
(device, third-party apps, manual entries) and matches what the user sees in the Fitbit app. Use
`list` where no cross-source merge is wanted.

## Confirmed pitfalls (handled in code)

1. **`dailyRollUp` range end** — in practice the range must end at 23:59:59 of the last day
   (`civilDateTime(end_of_day=True)`); the documented half-open semantics drops the final day. See the
   comment in `client.py`.
2. **Reconcile double aggregation** — the same stage can come back more than once with different
   counts; de-duplicate by taking the `max` per original type.
3. **`ASLEEP` counted twice** — in `stagesSummary`, `ASLEEP` is the total of `LIGHT/DEEP/REM`; when
   detailed stages exist it must be skipped, or sleep minutes double.
4. **Sleep attribution day** — a session is dated by the `civil_end_time` date, so a session crossing
   midnight belongs to the waking day; naps carry `metadata.nap == true` and are counted separately.
5. **Unit conversion** — distance `millimetersSum` → km (÷10⁶); duration as `"1920s"`; sedentary
   `durationSum` seconds → minutes; weight `weightGramsAvg` grams → kg.
6. **Outbound rate limit <5 req/s** — one throttle across the client (default 225 ms spacing); 429
   honours `retry-after` or falls back to exponential backoff (cap 30 s, at most 2 retries);
   pagination guard at 100 pages.
7. **401 = authorization expired** — raised uniformly as "reconnect required" (see the testing-mode
   token policy below).
8. **Filter syntax** — field paths are assembled per record type: daily `{type}.date`; sleep
   `sleep.interval.civil_end_time`; ECG `electrocardiogram.interval.start_time`; sample
   `{type}.sample_time.civil_time`; interval `{type}.interval.civil_start_time`. A `-` in a type name
   becomes `_`.

## Raw payload shape

**Normalization rules**

1. **Record endpoints carry no `civil*` fields** — `sleep`, `activities`, and `ecg` return physical
   time, for example `2026-01-15T17:03:00Z` plus `startUtcOffset: "28800s"` (synthetic example).
   Ignoring the offset can place an overnight session on the wrong local day and shift displayed
   times by 8 hours. The translation layer converts through `_civil_iso()` to ISO with a local
   offset. (`dailyRollUp` and the sample/interval endpoints — `stepsDaily`, `restingHeartRaw`,
   `stepsIntraday`, `heartIntraday` — already carry `civil*` and need no conversion.)
2. **One metric, several sources** — a day's `stepsIntradayRaw` may include several sources, such as
   `FITBIT/Fitbit Air`, `FITBIT/MobileTrack`, and `HEALTH_KIT`. Summing sources can double-count
   observations. Normalization selects one dominant source by precedence
   ("Fitbit platform device > other Fitbit platform > unnamed"); heart rate follows the same rule.
3. **Minute aggregation of heart-rate samples** — samples are averaged into minutes on write,
   while `hr_min`/`hr_max` retain the original extremes. Raw payloads are archived per day for
   recomputation.

**What one typical day looks like (synthetic data)**

| Metric | Example values |
|---|---|
| Steps / distance / calories | 9,120 steps / 6.7 km / 2,240 kcal |
| Active minutes / sedentary / floors | 62 min / 540 min / 5 flights |
| Sleep | 432 min (23:40–07:12, efficiency 95%, deep 76 / light 210 / REM 128 / awake 18) |
| Resting HR / HR range | 58 bpm / 52–128 bpm (after minute aggregation) |
| HRV / SpO₂ / breathing rate | 42.0 ms / 97.0% / 15.0 /min |
| Exercise | walking 30 min (3,100 steps) |

> The table above is a **synthetic** shape example, not anyone's measurements. `devtools/`
> reproduces the same field structure for local development.

**Optional data:** availability of weight, body fat, water, nutrition, blood glucose, ECG,
irregular rhythm, and core temperature depends on the device and account. The interface hides
sections with no corresponding data.

**History depth:** how far back the data reaches depends on the account and the device; daily step
rollups and the sleep session list each stop at the account's first day of data. A single `dailyRollUp` range is capped at 14 days for heart rate,
active minutes, calories, and heart-rate zones, and 90 days for other types; this project fetches
14-day windows day by day, so backfill is not limited by the per-request cap. Dates earlier than the
account's first data return empty, and the engine writes nothing (`empty`).

**Read validation in the analysis layer:** upstream occasionally returns an invalid reading (for
example an HRV of 0 when the device had no valid value). The analysis layer range-checks HRV
(5–250 ms), resting heart rate (25–220 bpm), blood oxygen (50–100%), and sleep duration (0–16 h);
out-of-range values enter no baseline or correlation computation, while the raw payload stays intact
in `raw_days` and remains traceable. An extreme low that is a genuine measurement — a night far
shorter than usual — is kept and shown as measured, not discarded as an anomaly.

## Hard limits (product level)

- **No direct Bluetooth** — a third-party app can only use the cloud API, so freshness depends on the
  user's phone syncing.
- **Availability differs** — ECG, irregular rhythm, and blood glucose depend on model, region,
  firmware, and granted scopes. Absence is normal, not an error, and the interface hides those
  sections entirely.
- **7-day tokens in testing mode** — for External user type with Testing status, the refresh token
  expires every 7 days (Google's OAuth policy, no exemption). Settings shows the state and offers a
  one-click reconnect.
- **API in motion** — Google states changes may require follow-up during 2026, so the translation
  layer is fully tolerant and the raw archive can be recomputed.

## Official references

- Data and endpoints: `developers.google.com/health/data-types`, `/health/endpoints`
- Migration guide (legacy Fitbit Web API → GHA): `developers.google.com/health/migration`
  (legacy API: unsupported from 2026-09-30, **fully shut down 2026-10-30**)
- OAuth token policy: `developers.google.com/identity/protocols/oauth2`
- Official CLI (fetch reference): `github.com/Google-Health-API/google-health-cli` (Apache-2.0)
