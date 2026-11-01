# Computation limits for local statistics

[Chinese](zh/life-science-basis.md)

How each local statistic is computed, which parameters it uses, and where it stops being
applicable. These thresholds are display rules, not clinical criteria.

## Raw date coverage

`GET /api/v1/coverage` and `health-by status` compare the measurement dates inside the raw archive
with the normalized dates in `days`. `day.coverage` in `/summary` carries the same four fields; the
existing per-endpoint success fields are kept, and the source of truth is the same.

`raw_days` is the size of the union of the dates embedded in `hrvRaw` and `sleepRaw` — not the number
of rows in the raw table. HRV is dated by the record's own date and sleep by its local end day, parsed
by the same code the translator uses; repeated dates count once.

`normalized_days` is the number of rows in `days`. `missing_count` is the full number of raw dates
that still have no normalized row, and `missing` lists at most 30 of them in ascending order. This is
a date-level check: it does not guarantee every metric is complete on a listed day, and it does not
mean those dates have been backfilled. The query neither writes to the database nor calls upstream.
The raw archive is scanned in full once; the web app then reuses the result and invalidates it when
this connection writes or another connection commits, so switching dates does not re-read a large
archive every time.

The set is independently reproducible: take HRV record dates and sleep local end days, apply the same
date parsing, union them, and subtract the `days` date set. A payload row's date must not stand in for
a measurement date, and checking HRV alone would miss sleep. `missing` is capped at 30 entries; the
full count is `missing_count`.

## Level changes in the baseline segment

`regime.detect_break` enumerates split points over valid daily observations sorted by date, requiring
at least 14 samples on each side. It divides the absolute difference of the before/after means by the
pooled within-group standard deviation:

`pooled_sd² = (SS_before + SS_after) / (n_before + n_after - 2)`

The split point with the largest effect size is reported only at or above 0.8, with ties taking the
earliest date; the split day belongs to the later segment. When both segments are constant with
different means, the zero denominator is reported as the largest finite float, so the JSON stays
valid. This is an effect-size heuristic: the threshold is not a significance test, and it cannot
establish that the measurement method or a physiological cause changed.

The baseline still uses at most the 28 calendar days before the selected day, excluding it.
**Statistical break detection is disabled:** `analysis.comparisons.<metric>.break` keeps its field but
is always null; it raises no notice and does not change the baseline median, standard deviation,
sample count, or start. The pure function is kept for research and recomputation; it is not a
criterion the product currently applies. In a synthetic experiment with seed 20241101 (56 points per
series), pure noise triggered 14 times in 300 runs and a random walk 289 times in 300; in every case
the product's `break` stayed null and the baseline kept its window. Scanning split points over an
autocorrelated series amplifies chance differences, so this effect size cannot separate a real step
from a random walk. Re-enabling it needs a method that controls false positives, or independent
external event evidence, and fresh acceptance.

Only sustained field appearance from `field_appears` may shrink the baseline: seven consecutive
calendar days with an archive and no valid reading for the field, followed by three consecutive
calendar days with valid readings. Days without an archive are not evidence of absence, and a gap
that recovers after readings exist is not a first appearance. Zero readings count as no-reading
evidence for resting heart rate, HRV, breathing rate, and blood oxygen; for skin temperature
deviation, steps, sleep duration, and other fields a zero is a valid value, so "zero became non-zero"
must not shrink the baseline. Skin temperature moving from missing to a sustained zero does count as
a first appearance.

`valid_from` is the later of the window start and the sustained first-appearance date; only valid
readings from that date onward enter the baseline. `field_appearance` reports the first-appearance
date separately, and statistical candidates and appearance evidence never substitute for each other.
With fewer than 7 valid samples the baseline is empty, and `insufficient_reason` states why; data from
before the first appearance is never mixed back in. The interface shows the post-appearance start and
valid day count, not statistical break candidates. These are coverage boundaries of that field; they
do not establish an algorithm change for other metrics.

## Within-week / between-week variance ratio and display values

This is a heuristic for choosing a display, not a clinical validation of measurement reliability, and
it does not attribute day-to-day variation to noise. The function name `reliability.icc` is an
interface convention; the method is a **variance ratio**, not a standard ANOVA ICC.

The window is the 56 calendar days ending on the selected day, reading normalized `days` only.
Grouping by ISO year and ISO week, with at least 3 valid observations per week and at least 3
qualifying weeks:

```
bvar = population variance of the weekly means
wvar = equally weighted mean of the weekly population variances
reliability = bvar / (bvar + wvar)
```

Weeks are weighted equally and missing days are not zero-filled. A zero total variance, or too few
qualifying weeks, returns null; identical weekly means with within-week variation returns 0.
Reproducible expected values for the synthetic vectors are 0.99715099715, 937/6702, and 0 — see the
unit tests.

`analysis.reliability_basis` echoes the window, minimum weeks, threshold, method, and definition, and
reports `weeks` and `mean_days_per_week` per metric: metrics go missing on different days, so they
cannot share one sample size. The ratio is affected by how many observations each week holds and how
weeks are grouped; 0.4 is a display threshold for this product, not a calibrated boundary of health or
data quality.

`analysis.reliability / display_mode / display_values / insufficient` use exactly the same seven
metric keys as `comparisons`. When a state metric's ratio is below 0.4 the display defaults to a
right-aligned 7-day average; when data is insufficient or the ratio cannot be computed, the daily
value is kept and `insufficient: true` is echoed. Behaviour metrics always display daily values;
steps, one of the seven, gets no rolling display value even when its ratio is low.

The 7-day average uses the observations available in the last 7 **calendar days**, not the last 7
records; it is empty only when the whole window is missing. The `rolling` series prefix uses the days
available at that point. The backend computes and rounds `display_values` to each metric's precision;
the page only presents them with a "7-day average" label and never recomputes. Raw daily snapshots,
single-night sleep detail, and intraday heart-rate curves keep the day's own readings; rolling cards
carry no same-day baseline-difference copy. A fully missing rolling window shows "no data in the last
7 days" rather than a zero.

`comparisons.<metric>.current` equals `display_values.<metric>`; `current_daily` keeps the raw daily
value separately. `difference` and `percent_change` are computed from the former against the
baseline, and headings and interpretations that use the comparison value label each metric as "today"
or "7-day average". A daily fact and a rolling summary are different time bases and never borrow each
other's differences.

### How to read the ratio

This document records no health statistics from any deployment. For current results, read
`analysis.reliability` and `analysis.reliability_basis` in `/summary`, and quote them together with
the window, the number of qualifying weeks, and the mean observations per week.

A ratio nearer 1 means between-week differences in the weekly mean are larger than within-week
variation under this method; nearer 0 means the opposite. It cannot separate measurement error, real
day-to-day change, or life circumstances, and it cannot establish that a single day's reading
represents a whole period. State metrics and behaviour metrics follow different display rules, and the
ratio is not a basis for ranking them.

Observations per week, the pattern of gaps, and the grouping all affect the result. 0.4 is only the
current fixed display threshold; **no clinical calibration across populations or missing-data patterns
has been done.** The endpoint echoes the sample structure; this round does not adjust the threshold by
sparsity or compute a separate confidence level.

## Appendix A · Reproducible benchmarks

Three purely synthetic vectors check the formula; they are not used to calibrate display thresholds.
A and B are 5 weeks × 7 days, C is 3 weeks × 4 days; each inner list is one week of valid
observations:

```python
A = [[49,50,51,50,49,50,51],[59,60,61,60,59,60,61],[69,70,71,70,69,70,71],
     [79,80,81,80,79,80,81],[89,90,91,90,89,90,91]]      # large between-week gap → 0.99715099715
B = [[50,90,10,70,30,80,20],[60,20,100,30,90,10,80],[70,30,110,40,100,20,90],
     [40,80,0,60,20,70,10],[30,70,-10,50,10,60,0]]       # within-week varies more → 937/6702
C = [[60,61,59,60],[60,61,59,60],[60,61,59,60]]          # identical weeks       → 0
```

**C is expected to be 0**: weekly means are equal so the between-week variance is 0, while within-week
variance is not, giving a ratio of 0. If both variances were zero the denominator would be zero and
null should be returned. `null` means only "cannot be computed", and the two are handled differently
by the display rule.

## Appendix B · What this round did not establish

- No autonomic mechanism was validated; variance ratios or rolling means cannot support a mechanism
  such as "parasympathetic saturation".
- No head-to-head comparison of measurement reliability between metrics was done, so no claim is made
  that breathing rate, resting heart rate, or HRV is the more reliable.
- No cross-device or cross-algorithm calibration was done, so absolute values from different sources
  are not the same ruler.
- There is no population reference and no clinical threshold; personal history baselines support
  descriptive comparison only and never a normal/abnormal verdict.
- There is no causal identification; a change coinciding with an event supports a description of
  timing, not attribution.

## Dual basis: today and 7-day average

The single-day input `daily-v2` distinguishes the two time bases in the field names:

- `*_today` is the selected day's value, not necessarily the server's today.
- The six state metrics also carry `*_7d`, the mean of available observations across the 7 calendar
  days ending on the selected day.
- Behaviour metrics such as activity provide the daily value only; other existing daily fields are
  also suffixed `*_today`.
- Missing measurements keep the field and fill `null`; a legitimate zero stays zero, and zero must
  never stand in for missing. A `null` `*_7d` means the calendar window has no usable reading, which
  says more than "the selected day is missing".
- A `null` in a fixed correlation slot means something else: there is no citable outgoing conclusion
  this round. It must not be read as missing data, a failed correction, or zero correlation;
  statistics that fail correction are not sent.

### When the two values are equal

Display precision, data distribution, missing days, and the window all affect whether the two values
coincide. When they do there is no visible difference to explain, but **equal values are still
different time bases** and the text must still say which one it means. One field contract therefore
covers both the equal and unequal cases without a per-metric protocol. The extra fields and token cost
remain: equal numbers are not zero cost, and they do not remove the obligation to state the basis.

### Checking the time basis

The frequency of equal values depends on the sample, window, and display precision. It does not
establish measurement reliability and is not used to set the 0.4 display threshold.

Manual sampling is stratified by whether the two values differ: at most 10 samples per metric, of
which at least 7 have differing values, recording the actual count when fewer are available. When the
values are equal, or rounding makes several paths match, no unique attribution is made. Metric name,
number, and the sentence's own "today / 7-day average / last 7 days average" wording are cross-checked
together; a keyword appearing is not taken as proof the basis is right, and no automatic gate is
added.

### Window analysis stays at window-level aggregation

The dual-basis change applies to single-day analysis only. `window-v1` continues to send range
aggregates, adding no daily readings or arrays and so not widening the data egress; its field
structure and prompt protocol stay at their original version.
