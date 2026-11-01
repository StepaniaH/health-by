# BYOK AI analysis

[Chinese](zh/ai.md)

Off by default. In Settings under **AI analysis**, enter a Base URL (including `/v1`), a model name,
and an API key, then enable and save. Enabling shows which service and model will be used, and what a
failed repair will send. Any OpenAI Chat Completions compatible endpoint works; for a local Ollama
the typical values are `http://127.0.0.1:11434/v1` and `qwen2.5:3b`, and the key may be empty when
the server requires no authentication. The service must support `/chat/completions`, non-streaming
JSON output, and `response_format: json_object`. A Base URL that ends in `/chat/completions` has that
suffix removed to avoid duplication; `/v1` is never added or guessed.

Generate from **02 AI analysis** on a daily report. Reading the page only reads the cache and never
calls the model; an existing result can be regenerated. Results are looked up by date range, model, prompt
version, payload schema version, and Base URL, so switching back to a previous model surfaces its earlier results. Syncing
data does not recompute anything — regeneration is explicit.

## Time window

The AI block accepts a single day, the last 7 / 30 / 90 days, or a custom start and end date. Presets
end on the date selected in the daily report; ranges are inclusive and cover at most 366 days. After
changing dates, **Apply range** only reads existing analyses; only **Generate** sends a request. A
selected day with no archive does not prevent choosing a range that contains other archived days.

The legacy request `{"date":"YYYY-MM-DD"}` is equivalent to a single day with `start == end`.
Multi-day requests send **window aggregates computed on this machine**, not N daily records; the
request object contains no arrays at all. A single day keeps its original statistics plus `window`
metadata, and correlations become an object indexed by metric pair. Multi-day requests have only
`window`, `metrics`, and `goals` at the root:

| Object | Contents |
|---|---|
| `window` | `days` (calendar days), `days_with_archive`, `days_with_data` (any valid whitelisted measurement); `missing_days` and `max_missing_streak` count against the full calendar; `date_range` uses relative dates only |
| `metrics` | grouped by the single-day whitelist; each entry carries `mean` / `median` / `min` / `max`, valid `coverage_days` / missing `missing_days` / longest gap `max_missing_streak` / excluded `excluded_days` |
| extremes | one `min_day` / `max_day` per metric, relative dates only; ties take the earliest valid day; null when missing; no ranking |
| `trend` | `direction` / `first` / `last` only, comparing the earliest and latest valid observations in the window — not the two calendar endpoints, and not a significance test; null direction with fewer than two observations |
| `goals` | for steps and sleep: `goal`, valid/missing days, `achieved_days`, `achievement_rate`; the denominator is days with valid measurements, missing days are not failures; null when there is no valid sample or no goal |
| goal extremes | one `best_day` / `worst_day` plus its ratio per goal; ratios cap at 1, ties take the earliest day; these express goal completion only, not best or worst health |

Measured zeroes are preserved; an all-missing mean, median, or extreme is null. Metrics already
covered by the analysis layer's `BOUNDS` reuse the same ranges — no second set of thresholds — and
out-of-range readings count as missing while their day count is reported separately. Duration means
accept the usual hours-and-minutes spelling; coverage days are never converted into minutes. Daily
arrays are used for local computation only and never enter the prompt, a retry, or the egress audit;
a longer window does not add fields linearly. Usage is recorded from the tokens the service reports,
never estimated from character counts.

Migration v6 adds `start_date` / `end_date` to `ai_analyses`, backfilling old rows with their
original `date` without rewriting content or usage. Cache and OpenCode sessions are keyed by start
and end date, model, and service, so different windows ending on the same day never share results.
The system prompt is v4; multi-day requests add input-field notes as `v4-window-1`. A new version
never hits a v3 cache entry; old results stay, are read as they are, and are regenerated only on
request.

## What is actually sent

The first single-day request carries the system prompt, window metadata, and the aggregate objects
below. `date` is a relative label such as `D-0` / `D-1` against the current civil date; the weekday
is kept. Absolute dates, the profile, devices, workout names, exact sleep/wake times, sleep-stage timelines,
daily series, minute-level series, heatmaps, raw responses, and free-text health fields are never
sent.

Each row below, path plus field name, forms the egress contract; new fields are not sent by default.
The independent expectation table and value assertions live in `tests/test_ai_payload.py`, and the
only egress point is `health_by/ai/service.py::build_payload`.

| Object path | Fields |
|---|---|
| root | `date`, `weekday`, `rolling_window_days`, `window`, `day`, `goals`, `statistics` |
| `window` | `days`, `days_with_data`, `days_with_archive`, `missing_days`, `max_missing_streak`, `date_range` |
| `day` | `activity`, `heart`, `sleep`, `body`, `naps_count_today`, `naps_minutes_today`, `alerts` |
| `day.activity` | `steps_today`, `distance_km_today`, `calories_out_today`, `floors_today`, `total_active_minutes_today`, `light_active_minutes_today`, `moderate_active_minutes_today`, `vigorous_active_minutes_today`, `zone_minutes_today`, `sedentary_minutes_today` |
| `day.heart` | `resting_hr_today`, `resting_hr_7d`, `hrv_ms_today`, `hrv_ms_7d`, `spo2_today`, `spo2_7d`, `spo2_min_today`, `spo2_max_today`, `breathing_rate_today`, `breathing_rate_7d`, `vo2max_today`, `hr_min_today`, `hr_max_today`, `hr_avg_today`, `skin_temp_delta_c_today`, `skin_temp_delta_c_7d`, `skin_temp_nightly_c_today`, `skin_temp_baseline_c_today`, `skin_temp_stddev_30d_c_today`, `core_temp_c_today`, `non_rem_hr_today`, `hrv_deep_rmssd_ms_today`, `hrv_entropy_today` |
| `day.sleep` | `total_minutes_today`, `total_minutes_7d`, `efficiency_today`, `minutes_awake_today`, `minutes_to_fall_asleep_today`, `stages` |
| `day.sleep.stages` | `deep_today`, `light_today`, `rem_today`, `wake_today` |
| `day.body` | `weight_kg_today`, `body_fat_pct_today`, `water_ml_today`, `calories_in_today` |
| `day.alerts` | `irregular_rhythm_alerts_today` |
| `goals` | `steps`, `sleep_minutes` |
| `statistics` | `window_days`, `n_days`, `comparisons`, `period_deltas`, `goal_progress`, `bands_outside`, `recovery`, `load`, `rhythm`, `coverage`, `correlations` |
| `statistics.comparisons` | `steps`, `sleep_minutes`, `resting_hr`, `hrv_ms`, `breathing_rate`, `spo2`, `skin_temp_delta` |
| `statistics.comparisons.steps` | `current_today`, `baseline`, `difference_today`, `percent_change_today`, `sample_count`, `window_days`, `min_samples` |
| `statistics.comparisons.sleep_minutes` | `current_today`, `current_7d`, `baseline`, `difference_today`, `difference_7d`, `percent_change_today`, `percent_change_7d`, `sample_count`, `window_days`, `min_samples` |
| `statistics.comparisons.resting_hr` | `current_today`, `current_7d`, `baseline`, `difference_today`, `difference_7d`, `percent_change_today`, `percent_change_7d`, `sample_count`, `window_days`, `min_samples` |
| `statistics.comparisons.hrv_ms` | `current_today`, `current_7d`, `baseline`, `difference_today`, `difference_7d`, `percent_change_today`, `percent_change_7d`, `sample_count`, `window_days`, `min_samples` |
| `statistics.comparisons.breathing_rate` | `current_today`, `current_7d`, `baseline`, `difference_today`, `difference_7d`, `percent_change_today`, `percent_change_7d`, `sample_count`, `window_days`, `min_samples` |
| `statistics.comparisons.spo2` | `current_today`, `current_7d`, `baseline`, `difference_today`, `difference_7d`, `percent_change_today`, `percent_change_7d`, `sample_count`, `window_days`, `min_samples` |
| `statistics.comparisons.skin_temp_delta` | `current_today`, `current_7d`, `baseline`, `difference_today`, `difference_7d`, `percent_change_today`, `percent_change_7d`, `sample_count`, `window_days`, `min_samples` |
| `statistics.period_deltas` | `steps_7d`, `sleep_7d` |
| `statistics.goal_progress` | `steps`, `sleep_minutes` |
| `statistics.goal_progress.steps` | `ratio_today`, `remaining_today` |
| `statistics.goal_progress.sleep_minutes` | `ratio_today`, `remaining_today` |
| `statistics.bands_outside` | `steps`, `sleep_minutes`, `hrv_ms`, `resting_hr`, `active_minutes` |
| `statistics.bands_outside.steps` | `points`, `outside`, `percent` |
| `statistics.bands_outside.sleep_minutes` | `points`, `outside`, `percent` |
| `statistics.bands_outside.hrv_ms` | `points`, `outside`, `percent` |
| `statistics.bands_outside.resting_hr` | `points`, `outside`, `percent` |
| `statistics.bands_outside.active_minutes` | `points`, `outside`, `percent` |
| `statistics.recovery` | `need_minutes`, `hrv_delta_pct_today`, `rhr_delta_today`, `sleep_debt` |
| `statistics.recovery.sleep_debt` | `minutes`, `nights`, `window`, `avg_deficit` |
| `statistics.load` | `steps_avg_7`, `steps_avg_28`, `steps_ratio`, `active_avg_7`, `active_avg_28`, `active_ratio`, `who_weighted_minutes`, `who_weekly_minutes`, `who_pct`, `window_days` |
| `statistics.rhythm` | `sri`, `sri_pairs`, `midpoint_sd_min`, `bed_sd_min`, `wake_sd_min`, `social_jetlag_min`, `n_nights`, `rhythm_days`, `baseline_days` |
| `statistics.coverage` | `window_days`, `days_with_archive`, `partial_days`, `per_metric_days`, `excluded_out_of_range` |
| `statistics.coverage.per_metric_days` | `sleep_minutes`, `sleep_efficiency`, `resting_hr`, `hrv_ms`, `steps`, `active_minutes`, `sedentary_minutes` |
| `statistics.coverage.excluded_out_of_range` | `sleep_minutes`, `sleep_efficiency`, `resting_hr`, `hrv_ms`, `spo2`, `breathing_rate`, `weight_kg`, `body_fat_pct` |
| `statistics.correlations` | `sleep_minutes__resting_hr`, `sleep_minutes__hrv_ms`, `deep_minutes__resting_hr`, `sleep_efficiency__hrv_ms`, `sleep_mid__resting_hr`, `sleep_mid__hrv_ms`, `sleep_minutes__steps`, `steps__sleep_minutes`, `steps__deep_minutes`, `steps__resting_hr`, `active_minutes__sleep_minutes`, `sedentary_minutes__resting_hr` |
| `statistics.correlations.sleep_minutes__resting_hr` | `x`, `y`, `offset_days`, `r`, `n`, `ci_low`, `ci_high` |
| `statistics.correlations.sleep_minutes__hrv_ms` | `x`, `y`, `offset_days`, `r`, `n`, `ci_low`, `ci_high` |
| `statistics.correlations.deep_minutes__resting_hr` | `x`, `y`, `offset_days`, `r`, `n`, `ci_low`, `ci_high` |
| `statistics.correlations.sleep_efficiency__hrv_ms` | `x`, `y`, `offset_days`, `r`, `n`, `ci_low`, `ci_high` |
| `statistics.correlations.sleep_mid__resting_hr` | `x`, `y`, `offset_days`, `r`, `n`, `ci_low`, `ci_high` |
| `statistics.correlations.sleep_mid__hrv_ms` | `x`, `y`, `offset_days`, `r`, `n`, `ci_low`, `ci_high` |
| `statistics.correlations.sleep_minutes__steps` | `x`, `y`, `offset_days`, `r`, `n`, `ci_low`, `ci_high` |
| `statistics.correlations.steps__sleep_minutes` | `x`, `y`, `offset_days`, `r`, `n`, `ci_low`, `ci_high` |
| `statistics.correlations.steps__deep_minutes` | `x`, `y`, `offset_days`, `r`, `n`, `ci_low`, `ci_high` |
| `statistics.correlations.steps__resting_hr` | `x`, `y`, `offset_days`, `r`, `n`, `ci_low`, `ci_high` |
| `statistics.correlations.active_minutes__sleep_minutes` | `x`, `y`, `offset_days`, `r`, `n`, `ci_low`, `ci_high` |
| `statistics.correlations.sedentary_minutes__resting_hr` | `x`, `y`, `offset_days`, `r`, `n`, `ci_low`, `ci_high` |

This table describes `daily-v2`. Correlation slots keep their fixed `x` / `y` names; only approved statistics fill `offset_days`, `r`, `n`, `ci_low`, and `ci_high`, otherwise those leaves are null. `payload_schema_version` is stored with analysis and egress records, but is not sent to the model.

`total_active_minutes_today` is derived by the existing authoritative function from the light, moderate, and
vigorous components; it does not collapse to MVPA when light activity is missing. `remaining_today` is the
shortfall against the goal, zero once achieved. `null` means missing and is never zero; a zero
out-of-range count is sent explicitly as `0`. Band statistics are sent per metric for five metrics,
never as a merged four-metric percentage.

### Request headers

Beyond `accept` and `authorization` (sent only when a key exists), headers depend on the target:

```
host is opencode.ai or a subdomain → send x-opencode-session and User-Agent: health-by/<version>
any other host                     → send User-Agent only
```

`x-opencode-session` is an OpenCode server contract (omitting it returns 400 `MissingSessionID`) and
is meaningless to other services. Identifiers appear only where they are required.

Session scope: **analyses and repairs with the same start/end date, model, and service reuse one
UUID, and it survives a restart.** Different dates, models, or services use different UUIDs; this is
OpenCode's cache-routing identifier and it links requests in one group. Test connection uses a
separate throwaway UUID, never reuses the analysis identifier, and leaves no analysis record;
non-OpenCode services never receive the header.

The trade-off is explicit: **several requests on the same day are linkable to each other by the
receiving service**, while different dates are not. Reducing the correlation surface further would
mean one UUID per analysis, at the cost of missing the peer's session cache on the second generation
of the same day.

> **Known limitation:** the decision is made from the host. Put OpenCode behind your own domain or a
> reverse proxy and the header is not sent, and the service returns 400. The error message must say
> so ("this address may require a session header"), otherwise the user sees an unexplained 400.
>
> **No automatic header-retry:** a `400 MissingSessionID` is reported, never guessed at with a second
> request. One analysis is therefore at most two requests (generate, then one repair) — there is no
> third request.

## Listing models

Save the address and key first, then **List models**. A first setup may keep AI disabled and the
model name empty; the dropdown shows the names the service returns, falling back to ids. Selecting
one fills the model name, and it takes effect only after saving. The list never auto-selects the
first model and never rewrites the Base URL from returned metadata; typing a name by hand always
works.

Exactly one `GET {base_url}/models` is sent: with a saved key it carries authentication and the
project User-Agent, no session UUID, no health data, no prompt, and it writes no analysis, no egress
record, and no configuration change. Failure never blocks typing or saving: 401 reports an invalid
key, a timeout suggests checking address and network, 404 reports "this address has no model list
endpoint; type the model name by hand". No other path is guessed and nothing is retried.

The OpenCode address verified working with an unauthenticated GET is
`https://opencode.ai/zen/go/v1`. The [official Go documentation](https://opencode.ai/v2/docs/console/go/#models)
lists a separate model-list path; whether a given Base URL supports `/models` must be checked
individually, and a 404 from the list does not imply the generation endpoint is unavailable. A
successful list proves nothing about quota, access, or output quality — **Test connection** remains
separate.

## Test connection

After saving, **Test connection** sends one minimal call (`max_tokens: 256`, asking only for "OK")
using the same saved address, model, and key, with no health data and no `response_format`. It works
while AI analysis is disabled; edited fields must be saved first. A success proves only that the
minimal call works — not JSON mode, long output, or quota. The result is one line:

```
success  Connected · 1.2s · deepseek-v4.1-flash
failure  <one-line reason> — <next step>
```

Success is shown too, with model name and latency: otherwise "connected" and "connected to a
different model" look identical.

| Case | Message | Next step |
|---|---|---|
| 401 | Invalid or expired key | Check the key was copied completely (no surrounding spaces) and belongs to this service |
| 402 | Quota exhausted | This key or model has no remaining quota; use another key or add quota |
| 403 | No access to this model | Verify the model name or the account permissions |
| 404 | Wrong endpoint | Check whether `/v1` is missing, and verify the model name |
| 429 | Too many requests | Retry later; for a local model, reduce concurrency or use a smaller model |
| 5xx | Upstream service error | Retry later; if it persists the peer service is unhealthy, not the configuration |
| timeout | Cannot reach this address | Check address and network; a local Ollama must be started first |
| DNS failure | DNS lookup failed | Check the spelling, or whether the domain needs a proxy |
| 200, body not JSON | Response is not JSON | Confirm the address points at an OpenAI Chat Completions compatible service |
| 200, no `choices[0].message.content` | Unexpected response shape | Confirm the endpoint speaks OpenAI Chat Completions |
| empty content | Connected, but the model returned no content | Does not affect the connection verdict; if generation is also empty, try another model |

### Upstream response bodies are never shown verbatim

Many providers echo **the request or the API key** in the error body ("invalid key: sk-…").
Therefore:

```
✓ show: the status code and this project's own message
✗ never show: the upstream response body verbatim
✓ if a record is needed: redact first — sk-…, Bearer …, and long alphanumeric runs all become ***
```

**A key leaking into an error message is worse than showing no message**: it gets screenshotted,
pasted into issues, and written to logs.

The preferred approach is to discard the upstream body entirely and keep only the status code —
no body, no redaction burden, clean in all three places. When a record is genuinely needed, redact at
display, in logs, and in storage: masking only in the interface while writing the body to a log is no
masking at all, and logs usually outlive interfaces.

**Any HTTP 200 counts as a successful connection; empty or incompatible content adds a `warning` and
is never reported as a connection failure.** Test connection answers "is the address reachable and
authenticated", not "does the model answer well" — conflating the two marks a perfectly usable
configuration as broken (with a tiny `max_tokens` the model spends its budget elsewhere, `content` is
an empty string, and both connectivity and authentication succeeded).

The same fact means something else elsewhere: **empty content during generation is a failure**, because
there the content is the point. The test prompt is "Reply with OK only."; an empty, whitespace, or
null content never turns a working connection into a failure. A 200 whose body is not JSON, or lacks
the compatible shape, likewise only warns about incompatibility.

```
test connection   200 + empty content  → success (connectivity and auth both passed)
generation        200 + empty content  → failure (nothing usable produced)
```

**These two must not be collapsed into one `is_ok(response)`.** They look alike and ask different
questions; share the request-sending code, but test the connection verdict and the content check
separately. The two budgets follow the same rule: the test needs 256 because it only needs one word;
generation needs its full budget. Test connection uses `max_tokens: 256` and judges only by HTTP
status and reachability.

**That value cannot be lowered.** Measured on one endpoint, one key, one prompt:

```
max_tokens=8    → 200 · finish=length · content empty
max_tokens=32   → 200 · finish=length · content empty
max_tokens=128  → 200 · finish=stop   · content "OK"
max_tokens=256  → 200 · finish=stop   · content "OK"
```

Such models spend the budget on internal reasoning first and emit nothing at small limits. Lowering it
makes empty content more likely; the interface still reports the connection as successful and reminds
the user to check content when generating.

**Next-step advice must match the failure type:**

```
401 / 402 / 403 / 404   configuration — a hundred retries will not help; do not suggest retrying
429 / 5xx / timeout     transient — suggest retrying
timeout ≠ DNS failure   unreachable versus unresolvable (check the network versus fix the address)
```

These sentences are a **contract, not a suggestion**: implement them as one table (status/exception →
sentence) that the code looks up, never re-written per call site, and assert in tests that a given
status produces its sentence.

## Output end and truncation

Test connection keeps a 256-token limit; generation and its single repair use 2000. `finish_reason`
is preserved (unknown when absent) and each attempt's reason is recorded with the validation. A
`length` reason explicitly shows "output was truncated (limit this run: 2000 tokens)" with advice to
use a more concise model or a larger budget, and keeps the received text; it is never reported as
merely a JSON format error. A first truncation still gets at most one repair: if the repair finishes
normally the final result is shown, and if both truncate the failure and the raw text are shown. Empty
content keeps its finish reason too. A genuine parse failure with no reported truncation is still
treated as a format problem. This budget is a ceiling, not a guarantee that every model completes
every request; real consecutive generation is verified on the instance.

## Validation and the single repair

Model output is a JSON object with `phenomenon`, `causes`, and `advice`; the JSON shape allows null
causes and advice, but missing or blank causes receive at least a `warn` under rules v2. A JSON object encoded twice inside a string is decoded one extra
level only, never recursively.

Validation checks the shape and whether every sentence **has something checkable** — a concrete
number, or a key that actually exists in the input (a sentence like "today `hrv_ms` and `resting_hr`
are both null" rests on key names, not values, and counts) — and whether numbers match input values.
Absolute values, rounding at the same precision, the usual hours-and-minutes spellings of minutes,
and percentages of ratios are all accepted.

A sentence counts as sourced when it matches one number or cites a real field; extra unmatched
numbers still record a `warn`. Sentences without provenance are graded by share of the whole: zero is
`ok`; a few at or under 30% is `warn`, still showing all content with per-sentence marks; above 30%,
or nothing matching at all, is `failed`. The proportion threshold takes precedence: 1 in 3 still
fails, while 1 in 4, 2 in 7, and exactly 3 in 10 warn; format errors and banned boilerplate fail
outright. `matched` counts numbers and field provenance together, `matched_numbers` counts number
matches alone, and a statement about a null field is not killed for having no numbers.

Field references accept only leaf names that exist, full paths, or real suffix paths, matched on word
boundaries: `hrv_msx`, `fake_hrv_ms`, and `wrong.hrv_ms` never impersonate `hrv_ms`. The common names
HRV, resting heart rate, breathing rate, blood oxygen, sleep duration, and steps act as synonyms only
when the corresponding field is present. The specific sentences, number-match counts, cited fields,
and missed numbers are all recorded. This check verifies provenance; it does not establish that a
statement about missing data, a natural-language inference, or a causal claim is correct.

Prompt v4 requires at most three facts in the phenomena section, causes that state their basis or the
specific missing data, advice that opens with an action, and Chinese metric names. Two or three
numbers per sentence and 300 characters overall are writing goals, not new validation thresholds; the
grading rules do not enforce those writing targets. The repair prompt also requires non-null causes;
rules v2 mark missing causes with at least a warning without rewriting historical verdicts.

New results record `rules_version` and `validated_at` — the grading rules' version and the final
verdict time. They are stored, cached, and exported with the result; old rows are neither backfilled
nor recomputed. The rules version is independent of the prompt version. A new prompt applies to
manual generation only; older results stay in the database and exports and are never rewritten or
re-sent automatically.

A failure triggers exactly one automatic repair: the original message is followed by the full previous
model output and the repair prompt, so the second request contains **the aggregate metrics plus the
previous reply text**. There is no loop and no background call. If both attempts fail the reply is
still saved, clearly marked as failing validation with the specific issues; on malformed output the
human-readable reason is shown first and the raw reply is collapsed behind "View raw output", so a
wall of JSON never hits the page. Regeneration is always user-triggered. A network error during the
repair keeps the first reply and reports that the repair did not complete.

## Storage, usage, and keys

- `ai_analyses` — one row per analysis: model, prompt version, raw reply, displayed content,
  validation state, total usage, and timestamps.
- `ai_egress` — one row per actual request, `attempt` starting at 1 and at most 2. Stores the
  structured field paths, a SHA-256 of the sorted paths, a SHA-256 of the payload content,
  type/length shapes, a hash of the full message body, timestamps, and that attempt's usage; the
  payload content itself is never stored. Migration v5 adds `session_id` (null for non-OpenCode) and
  `user_agent`; historical rows keep null and no identifier is invented. The second attempt
  additionally records the origin marker, character count, and hash of `assistant_echo`, treated as
  neither a data field nor a copy of the model's text.
- The page shows the combined token usage of both requests; when the service reports none, it says so
  rather than showing zero.
- The API key is encrypted through the existing `SecretStore`; the settings API only returns whether a
  key is saved, and never returns an upstream error body. Leaving the key empty keeps the old value;
  deletion has its own button. Model output is never interpreted as HTML.
- Export includes AI results and egress records; clearing health data also clears both tables, keeping
  settings and encrypted credentials.

## Endpoints

- `POST /api/v1/settings/ai` — `ai_enabled`, `ai_base_url`, `ai_model`, `api_key` (optional);
  `clear_api_key=true` deletes the key on its own.
- `POST /api/v1/ai/models` — lists models using the saved configuration; returns
  `{ok, status, detail, models: [{id, name}]}`. Does not require AI to be enabled or a model to be
  selected, and changes no settings.
- `POST /api/v1/ai/test` — sends the minimal test with the saved configuration; returns
  `{ok, status, detail, model, latency_ms}`. `status` is the actual HTTP status, or a string such as
  `timeout` / `dns_error` / `connection_error` for network failures. Empty content still yields
  `ok=true` plus a `warning`; the page keeps the success state and shows the warning. It generates no
  analysis and writes no analysis or egress rows.
- `POST /api/v1/ai/analyze` — `{"start":"YYYY-MM-DD","end":"YYYY-MM-DD"}`; the legacy single `date` is
  accepted but the two cannot be mixed. `force=true` regenerates.
- `GET /api/v1/ai/analysis?start=YYYY-MM-DD&end=YYYY-MM-DD` — reads the window cache and non-secret
  settings; accepts the legacy `date` parameter. The result carries `start_date` and `end_date`; the
  legacy `date` field remains the end date.

Protocol reference: [OpenAI Chat Completions](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create).

## Single-day dual time basis and cache versions

The single-day protocol is `daily-v2` with input-note revision `v4-input-2`; the window protocol
remains `window-v1` with `v4-window-1`. Single-day measurement leaves are suffixed `*_today`; sleep
duration, resting heart rate, HRV, breathing rate, blood oxygen, and skin temperature deviation also
carry `*_7d`, computed and rounded by the same calendar-7-day path the pages use. Behaviour metrics
get no 7-day value. `rolling_window_days=7` states the window; "today" means the selected day, and
analysing a past date never substitutes the server's today. `statistics.comparisons` provides
`current_today` / `current_7d` with matching difference and percentage suffixes; steps provide today
only. Every difference is against the same baseline, so a same-day difference is never attached to a
mean.

`daily-v2` has a constant key set: missing values are `null`, never omitted, and legitimate zeroes
are kept. Correlations use 12 fixed pair slots, and only statistics that pass correction are
non-null. An empty slot means only that there is no citable outgoing conclusion — it must not be read
as "not computed", "missing", "not significant", or "no relationship". Local insights and charts are
unaffected by the egress slots.

Migration v7 adds `payload_schema_version` to `ai_analyses` and to every `ai_egress` row: old
single-day rows become `daily-v1`, old window rows `window-v1`, with replies, verdicts, hashes, and
timestamps unchanged. New rows keep a human-readable version name, a machine-readable
`payload_keys_json`, and the digest. The key list proves structure and the content hash proves whether
content is identical; neither alone can reconstruct the values.

The cache matches start date, end date, model, service address, `prompt_version`, and
`payload_schema_version`. Old single-day rows never hit the new protocol cache but remain in the
database and exports; GET never calls the model, and only a user click creates a new-protocol result.
Window caches are unaffected by a single-day protocol upgrade. Both actual requests record the same
input protocol and payload digest, and repair remains limited to one.

The model should say "today", "7-day average", or "last 7 days average" explicitly, avoiding vague
windows. This is a semantic requirement checked by manual sampling, not a regex gate, and historical
content is never re-judged.
