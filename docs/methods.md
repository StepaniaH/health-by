# Metric definitions and sources

[Chinese](zh/methods.md)

Every number on the dashboard can be traced here: how it is computed, which parameters it uses,
which publication it comes from, and when it should not be trusted.

The README's analysis table is the short version. This page is the checkable one. Where the two
disagree, this page wins.

All constants are named in the code (`health_by/insights.py`, `health_by/metrics.py`); the values
quoted here map one-to-one onto them.

---

## 1. Three global conventions

### 1.1 Windows follow the calendar, not the row count

Day records exist only for dates that have data (`storage.get_day_window` does not pad gaps), so two
adjacent rows can be days apart. Every window calculation (baseline, rolling band, ACWR) opens on
**calendar days**, with missing days carried as `None` for alignment — never by taking "the last N
rows" as "the last N days".

### 1.2 A reported number carries its definition

The same metric over the same database can be counted several ways, each defensible. Whenever a
number could be recomputed by someone else, publish the query or the condition alongside it.

### 1.3 A single reading is not a conclusion

Night-to-night noise in HRV, resting heart rate, blood oxygen, and breathing rate routinely exceeds
any threshold for a meaningful change. Their **single-day values are descriptive only** ("currently
45 ms, −2 ms against the recent mean") and carry no on-target / low verdict. Verdicts apply to
multi-day trends only, and must name the baseline window they used.

---

## 2. Sleep

### 2.1 Sleep duration and sleep debt

| | |
|---|---|
| Definition | debt = Σ max(0, goal − that night's duration), counting **recorded nights only** |
| Parameters | 7-day window (`SHORT_WINDOW_DAYS`); goal from user settings, default 480 minutes |
| Source | conventional; the goal is the user's own setting, not a population recommendation |
| Implementation | `metrics.sleep_debt()` |

**Known limitation:** the debt is a **lower bound**. Nights with no record count as no debt, so the
true figure can only be larger. A report must state "N nights recorded" alongside it, or the reader
will take the debt as complete.

### 2.2 Baseline: the only definition in the product

Two different "baselines" on one screen make two conclusions impersonate each other. Window, centre,
and minimum sample size are therefore defined once, in `metrics.baseline_stats()`, and both
`insights.py` and `analysis.py` reference it.

| Parameter | Value | Notes |
|---|---|---|
| Window | **28 days** (calendar) | A 7-day average is too noisy for day-level verdicts |
| Centre | **median** | robust to outliers; one late night should not move the baseline |
| Minimum samples | **7** | a standard deviation from fewer than 7 points is too unstable for an SWC band |

**Calling convention: `offset_days`**

```
caller passes a series that INCLUDES the day     → offset_days = 1   (insights)
caller passes a series that EXCLUDES the day     → offset_days = 0   (analysis)
```

Getting this wrong produces no error — just a baseline that is one day off, and looks plausible.
`test_baseline_is_identical_across_analysis_and_insights` pins it: both modules must produce the same
number from the same data.

Below the minimum sample count no baseline is published. The interface must say **not enough samples**
rather than appearing broken.

### 2.3 Sleep efficiency

| | |
|---|---|
| Definition | the device's sleep efficiency (asleep ÷ time in bed), as a percentage |
| Judgement | compared with the 28-day baseline; **at least 5 percentage points below** counts as low |
| Parameters | `BASELINE_WINDOW_DAYS=28`, `BASELINE_MIN_SAMPLES=7`, `EFFICIENCY_LOW_DROP=5.0` |
| Source | baseline comparison follows Hopkins' smallest-worthwhile-change framing; the 5-point value is this project's choice, **not a published threshold** |

**Known limitation:** 5 points is empirical. Sleep efficiency depends heavily on how the device is
worn (watch versus band) and does not compare across devices.

### 2.4 Sleep Regularity Index (SRI)

| | |
|---|---|
| Definition | agreement between adjacent days at the same 15-minute slot, corrected for chance |
| Range | 0–100. 100 = identical, 0 ≈ random, negative = less consistent than random |
| Parameters | `bin_minutes=15`, at most 28 adjacent pairs, **no value published below 7 pairs** (`MIN_SRI_PAIRS=7`) |
| Source | approach after Phillips et al. 2017 (SLEEP) and Windred et al. 2024 |
| Implementation | `metrics.sri()` |

> **⚠️ Difference from the literature:** this implementation is a **chance-corrected kappa-style**
> ratio, `(p − chance) / (1 − chance)`, **not** Phillips' linear rescaling
> `−100 + 200 × (agree / total)`. The endpoints agree (fully consistent → 100, random → 0);
> **mid-range values differ, so they cannot be compared with, or subtracted from, published SRI.**
>
> Published bands (e.g. "≥87 is regular") come from population tertiles and **do not apply to a
> single person**, so the interface reports the number and its direction, never a regular/irregular
> grade.

**Known limitation:** 7 adjacent pairs means roughly two weeks of data. With less data the metric is
`null`, not a low score.

---

## 3. Rhythm

### 3.1 Social jetlag

| | |
|---|---|
| Definition | absolute difference between mean sleep **midpoint** on free days (Sat/Sun) and workdays |
| Parameters | at least 3 workday and 2 free-day nights; 28-day window |
| Source | Wittmann et al. 2006 |
| Implementation | `metrics.sleep_timing()` |

**Known limitation:** treating Saturday and Sunday as free days is an approximation and does not hold
for shift workers. Differences under 60 minutes are annotated as "usually no meaningful social
jetlag" — an order-of-magnitude note from the literature, not a hard threshold.

### 3.2 Daily activity rhythm (cosinor)

| | |
|---|---|
| Definition | mean intraday steps per hour over the last 14 days, fitted to a 24-hour cosine: `y = MESOR + A·cos(ωt − φ)` |
| Output | `mesor`, `amplitude`, `acrophase_hour`, `r2`, `hours_observed` |
| Parameters | at least 12 observed hours; **no peak time below `r2 = 0.3`** (`MIN_COSINOR_R2=0.3`) |
| Source | cosinor analysis is the standard method in circadian research |
| Implementation | `metrics.cosinor()` |

**Two implementation points:**

1. **Fit by least squares with an intercept, not the equal-interval shortcut.** The shortcut
   `b = 2/n Σ y·cos` assumes uniform sampling across 24 hours. Intraday steps may cover only some
   hours; the design matrix uses the actual observed times and is solved by least squares.
2. **`r2` uses the sample mean as its reference, not the fitted intercept.** `Σ(y−c)²` is minimised
   at `c = ȳ`, so `r2(ȳ) ≤ r2(mesor)` always: using the intercept as reference inflates `r2` and
   systematically loosens the threshold. The two agree only when every hour is observed.

**Known limitation:** one cosine assumes a single daily peak. A bimodal pattern (early rise plus an
evening session) fits a compromise peak with low `r2` and is rejected by the threshold — a correct
rejection, not a defect.

### 3.3 Baseline band

| | |
|---|---|
| Definition | 7-day rolling mean ± 1 standard deviation, calendar window |
| Parameters | `BAND_WINDOW_DAYS=7`, `BAND_MIN_SAMPLES=3` |
| Implementation | `insights._band()` |

**Known limitation:** ±1 SD covers about 68% of variation. It is a descriptive range, **not a
confidence interval**, and must not be used as evidence of significance.

---

## 4. Activity and load

### 4.1 Load ratio (ACWR)

| | |
|---|---|
| Definition | mean of the last 7 days ÷ mean of the last 28 days |
| Parameters | both windows aligned by calendar; returns `None` rather than computing when either has fewer than half its days |
| Source | acute:chronic framework after Gabbett 2016 (originally training load; applied here to active minutes and steps) |
| Implementation | `metrics.acwr()` |

> **⚠️ On the "optimal range":** the widely quoted 0.8–1.3 sweet spot has drawn methodological
> criticism (window coupling, non-comparable ratios). The interface therefore reports **direction and
> magnitude only** ("last 7 days ≈ your longer-term level") and **never a safe/dangerous colour.**

### 4.2 WHO weekly activity

| | |
|---|---|
| Definition | moderate minutes + 2 × vigorous minutes, compared with the 150 min/week reference |
| Parameters | `WHO_VIGOROUS_MULTIPLIER=2.0`, `WHO_WEEKLY_ACTIVE_MINUTES=150` |
| Source | WHO 2020 physical activity guidance: 150–300 moderate **or** 75–150 vigorous, as equivalents |
| Implementation | `insights._load()` |

**Known limitation:** summing moderate and vigorous 1:1 **understates** the weighted equivalent, so
they are weighted 1:2 and the text says "vigorous counted double". The factor is WHO's equivalence
convention, not a precise metabolic equivalent.

---

## 5. Cross-correlations

| | |
|---|---|
| Definition | Pearson r with a Fisher z 95% confidence interval |
| Parameters | at least 10 samples per pair (`MIN_CORRELATION_SAMPLES=10`); 12 predefined pairs; Bonferroni α = 0.05/12 ≈ 0.0042 |
| Source | Fisher (1921) z transform; Bonferroni for multiple comparisons |
| Implementation | `insights._correlations()`, `metrics.fisher_ci()`, `metrics.correlation_is_significant()` |

**How to read it:**

- Every reported correlation carries `r`, `n`, `ci_low`, `ci_high`, `excludes_zero`,
  `survives_correction`.
- A **strength word is published only when the CI excludes zero *and* the pair survives correction.**
  In the examples below, "not significant" means the pair fails the reporting criteria; it does
  not establish that a relationship is absent or small. `r = 0.6, n = 10` looks strong, but its 95%
  CI is about `[−0.05, 0.89]`, which includes zero.
- **The Bonferroni denominator is fixed at 12 (the number of predefined tests), not the number of
  pairs actually computed.** Skipped pairs would otherwise shrink the denominator on sparse data,
  loosening the correction exactly when evidence is weakest — and the gaps are not random (days
  without the device are themselves informative).

**On whether the strength word accounts for sample size:**

`strength_label` looks only at `|r|` (0.2 / 0.4 / 0.6, Cohen-style), **but it is never used alone**.
`insights._strength()` requires two sample-dependent gates first:

1. the Fisher 95% CI excludes zero — its width depends on n;
2. the pair survives Bonferroni — also depends on n.

Synthetic parameter examples: the same `r` at different `n` (α = 0.05/12):

| r | n | 95% CI | Survives | Output |
|---|---|---|---|---|
| 0.62 | 12 | [0.072, 0.881] | no | not significant (fails correction) |
| 0.62 | 20 | [0.245, 0.834] | yes | strong |
| 0.45 | 12 | [−0.167, 0.814] | no | not significant |
| 0.45 | 40 | [0.161, 0.668] | yes | moderate |

So whether a strength word appears depends on n, not on r alone. Once both gates pass, the word
depends only on `|r|`: `r=0.62, n=20` (CI width 0.589) and `r=0.62, n=80` (width 0.276) both read
"strong", although the second is far better estimated. Whether to vary the word with n is a product
choice, not a defect.

**Boundaries of the method:** Pearson r measures linear association. The `offset_days` next-day lag
is a stated assumption, not estimated from the data.

#### How high the bar is

Twelve pairs tested together give Bonferroni α = 0.05/12, two-tailed critical z = 2.865. In terms of
`|r|`:

| n | \|r\| needed with correction | \|r\| needed without (α=0.05) |
|---|---|---|
| 67 | 0.344 | 0.238 |
| 77 | 0.321 | 0.224 |
| 84 | 0.308 | 0.218 |

A single-pair 95% confidence interval excluding zero does not guarantee that the pair survives
multiple-comparison correction. A pair must meet both the corrected threshold and the minimum sample
count. If no pair passes, there is no reportable statistical conclusion; scatter plots can still
show the observations.

Changing any of these numbers requires changing this section in the same commit.

#### Which pair counts as "closest" (definition)

The interface reports "closest pair: X ~ Y (r=…, n=…) / about n≈… needed, roughly N more days".

```
required_n = ceil( (z_c / atanh|r|)² + 3 )
eta        = required_n − n
```

**Select by smallest `eta`**; break ties by `required_n`, then by a fixed label order.

The sentence promises "about N more days", so the pair it names must be the one with the smallest N,
or the text contradicts its own number. Note that `eta` is **not** monotonic in `|z|`: at equal
`|z|`, larger `|r|` means smaller `eta`. Selecting by largest `|z|` or largest `|r|` are both
approximations that pick different pairs.

The three-level ordering must be deterministic: when two pairs differ by 0.001 in `|r|`, an
undefined tie-break would change the sentence on every refresh.

#### Numbers shown must be reproducible

Any number in the interface or documentation must answer four questions, or it is only
*appearing* precise:

1. **Which window** (last 30 days / last 90 days / all)
2. **Which definition** (e.g. "closest" = smallest `eta` among failing pairs, not largest `|r|`)
3. **How ties are broken** (a deterministic order for equal or near-equal values)
4. **When it was computed** (anything that changes as data grows must be computed by the backend,
   never hard-coded)

If a number would change with another window, another ordering, or one more day of data, the
interface must state its conditions or recompute it live. Doing neither produces a number that
expires.

**Display rule:** pairs that fail correction are **not sent to the interface**; the UI states counts
via `correlations_tested` / `correlations_hidden` instead of showing a result with a disclaimer.
Changing this rule requires changing this page.

---

## 6. Data trust (attached to every conclusion)

Conclusions are not simply present-or-absent: there are two kinds of loss in between, and both must
be visible.

| Field | Meaning | Notes |
|---|---|---|
| `coverage.days_with_archive` | days with an archive inside the window | "a 30-day window" ≠ "based on 30 days" |
| `coverage.per_metric_days` | days each metric actually has readings | coverage differs per metric; the weakest one bounds the conclusion |
| `coverage.partial_days` | days where the archive exists but some endpoints failed | these look like data days and are missing several metrics |
| `coverage.excluded_out_of_range` | days dropped as out of range (per metric) | exclusion is silent otherwise, which is indistinguishable from not happening |
| `coverage.sparse_days` | days with notably fewer fields than others in the window | upstream parsing can fail while the sync log reports success |

**Valid ranges (`insights.BOUNDS`):** sleep duration 0–960 min, sleep efficiency 0–100%,
resting heart rate 25–220 bpm, HRV 5–250 ms, blood oxygen 50–100%, breathing rate 4–40 /min,
weight 20–300 kg, body fat 1–70%. Out-of-range values enter **no calculation**; they are counted and
reported.

> **Known blind spot of `sparse_days`:** it catches **single-day** outliers only. If an upstream
> rename removes the same field every day, the median moves with it and no day looks sparse. That
> uniform drift is caught on the write path against the previous parse
> (`engine._drift_warnings`); the two mechanisms have different blind spots and complement each other.

---

## 7. What this project does not do

Stated so that nobody adds them "while nearby":

- **No composite score.** There is no health score. The recovery panel counts four fixed rules,
  item by item; it is not a weighted sum and is not rescaled to 0–100.
- **No diagnosis.** No disease names, no medical advice.
- **No prediction.** Every number describes archived data; nothing is extrapolated.
- **No external statistics dependency.** Normal quantiles, least squares, and significance tests are
  written in `metrics.py` as pure functions, so they can be unit-tested and audited.
