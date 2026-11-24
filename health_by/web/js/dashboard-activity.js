import { t } from "./i18n.js";
import { num } from "./format.js";
import { barChart, heatmapChart, cosinorChart } from "./charts.js";
import {
  $,
  achievementsBlock,
  cardGrid,
  clear,
  clockFromDecimal,
  h,
  line,
  linesBlock,
  METRIC_DOMAIN,
  numSpan,
  trendBlock,
  windowOf,
  windowText,
} from "./dashboard-dom.js";

export function renderActivityPage(day, insights, trends, goals) {
  void trends;
  const section = $("card-activity");
  const activity = day.activity || {};
  const load = (insights && insights.load) || {};
  const rhythm = (insights && insights.rhythm) || {};
  const cosinor = rhythm.cosinor || {};
  const shortDays = Number.isFinite(load.window_days) ? load.window_days : windowOf("short", 7);

  const todayHost = $("activity-body");
  clear(todayHost);
  const mvpa = activity.active_minutes;
  const actCards = [];
  if (activity.steps !== null && activity.steps !== undefined) {
    actCards.push({
      label: t("web.dashboard.renderActivityPage.2986c7ee9"), value: num(activity.steps), unit: t("web.dashboard.renderActivityPage.a621b7cd7"),
      compare: goals.steps ? t("web.dashboard.renderActivityPage.a3bb0b826", {p1: num(goals.steps)}) : null,
    });
  }
  if (activity.total_active_minutes !== null && activity.total_active_minutes !== undefined && activity.total_active_minutes > mvpa) {
    actCards.push({ label: t("web.dashboard.renderActivityPage.e85e590b6"), value: num(activity.total_active_minutes), unit: t("web.dashboard.renderActivityPage.bd957bc49"), compare: mvpa !== null ? t("web.dashboard.renderActivityPage.ffb94be1a", {p1: num(mvpa)}) : null });
  } else if (mvpa !== null && mvpa !== undefined) {
    actCards.push({ label: t("web.dashboard.renderActivityPage.2c1af931a"), value: num(mvpa), unit: t("web.dashboard.renderActivityPage.bd957bc49"), compare: null });
  }
  if (activity.sedentary_minutes !== null && activity.sedentary_minutes !== undefined) {
    actCards.push({ label: t("web.dashboard.renderActivityPage.aa1b957b8"), value: num(Math.round(activity.sedentary_minutes)), unit: t("web.dashboard.renderActivityPage.bd957bc49"), compare: null });
  }
  if (activity.distance_km !== null && activity.distance_km !== undefined) {
    actCards.push({ label: t("web.dashboard.renderActivityPage.3865483de"), value: num(activity.distance_km, 2), unit: "km", compare: null });
  }
  if (actCards.length) todayHost.appendChild(cardGrid(actCards, Math.min(4, actCards.length)));
  else todayHost.appendChild(h("p", { class: "section-empty", text: t("web.dashboard.renderActivityPage.a3e40865b") }));

  const aggregates = [];
  if (load.steps_avg_7 !== null && load.steps_avg_7 !== undefined) {
    aggregates.push(load.steps_avg_28 !== null && load.steps_avg_28 !== undefined
      ? t("activity.steps_average_baseline", { value: num(load.steps_avg_7), baseline: num(load.steps_avg_28) })
      : t("activity.steps_average", { value: num(load.steps_avg_7) }));
  }
  if (load.active_avg_7 !== null && load.active_avg_7 !== undefined) {
    aggregates.push(load.active_avg_28 !== null && load.active_avg_28 !== undefined
      ? t("activity.active_average_baseline", { value: num(load.active_avg_7), baseline: num(load.active_avg_28) })
      : t("activity.active_average", { value: num(load.active_avg_7) }));
  }
  if (load.sedentary_avg_7 !== null && load.sedentary_avg_7 !== undefined) aggregates.push(t("web.dashboard.renderActivityPage.9001fd9a3", {p1: num(Math.round(load.sedentary_avg_7))}));
  if (load.workout_minutes_week !== null && load.workout_minutes_week !== undefined) aggregates.push(t("web.dashboard.renderActivityPage.9e2e05da2", {p1: num(load.workout_minutes_week)}));
  if (aggregates.length) {
    todayHost.appendChild(h("p", { class: "hintline", text: t("web.dashboard.renderActivityPage.469eb56ea", {shortDays: shortDays, p2: aggregates.join(" · ")}) }));
  }
  $("activity-note").textContent = t("web.dashboard.renderActivityPage.d5f5a7a01");

  const trendBody = $("activity-trend-body");
  clear(trendBody);
  const stepsTrend = trendBlock("steps", { title: t("web.dashboard.renderActivityPage.2986c7ee9"), unit: t("web.dashboard.renderActivityPage.24363e22b"), color: "var(--m-steps)", goal: goals.steps || null, width: 460, height: 150 });
  const activeTrend = trendBlock("active_minutes", { title: t("web.dashboard.renderActivityPage.301220d3d"), unit: t("web.dashboard.renderActivityPage.6ff10e192"), color: "var(--m-steps)", width: 460, height: 150 });
  const trendSplit = h("div", { class: "chart-split" });
  if (stepsTrend) trendSplit.appendChild(stepsTrend);
  if (activeTrend) trendSplit.appendChild(activeTrend);
  if (trendSplit.childElementCount) trendBody.appendChild(trendSplit);
  else trendBody.appendChild(h("p", { class: "section-empty", text: t("web.dashboard.renderActivityPage.5e6a574fc") }));
  const trendChart = stepsTrend || activeTrend;
  const stepsStrip = achievementsBlock("steps_ratio", goals.steps ? t("web.dashboard.renderActivityPage.e82f2306a", {p1: num(goals.steps)}) : t("web.dashboard.renderActivityPage.065cbd7d1"), t("web.dashboard.renderActivityPage.065cbd7d1"));
  if (stepsStrip) trendBody.appendChild(stepsStrip);
  $("activity-trend-note").textContent = "";

  const rhythmHost = $("activity-rhythm-lines");
  clear(rhythmHost);
  const summary = insights && insights.weekday_summary;
  if (summary && Number.isFinite(summary.workday_avg) && Number.isFinite(summary.weekend_avg)) {
    const diff = summary.weekend_diff_percent;
    const dir = diff === 0 ? t("web.dashboard.renderActivityPage.a9105fc05") : (diff > 0 ? t("web.dashboard.renderActivityPage.40e659316", {p2: Math.abs(diff)}) : t("web.dashboard.renderActivityPage.e266aa4cf", {p2: Math.abs(diff)}));
    rhythmHost.appendChild(h("p", {
      class: "lead-line",
      text: t("web.dashboard.renderActivityPage.e4df3f981", {p1: num(summary.workday_avg), p2: num(summary.weekend_avg), dir: dir, p4: windowText()}),
    }));
  }
  if (cosinor.usable === true && cosinor.acrophase_hour !== null && cosinor.acrophase_hour !== undefined) {
    rhythmHost.appendChild(h("p", {
      class: "lead-line",
      text: t("web.dashboard.renderActivityPage.479a488fd", {p1: clockFromDecimal(cosinor.acrophase_hour), p2: num(cosinor.r2, 2)}),
    }));
  }
  if (Number.isFinite(load.who_weighted_minutes) && Number.isFinite(load.who_weekly_minutes)) {
    const over = load.who_weighted_minutes >= load.who_weekly_minutes;
    rhythmHost.appendChild(h("p", {
      class: "lead-line",
      text: (over ? t("web.dashboard.renderActivityPage.e9ca3b649", {shortDays: shortDays, p2: num(load.who_weighted_minutes), p4: num(load.who_weekly_minutes)}) : t("web.dashboard.renderActivityPage.dcd7e04df", {shortDays: shortDays, p2: num(load.who_weighted_minutes), p4: num(load.who_weekly_minutes)})),
    }));
  }

  const cosHost = $("cosinor-body");
  clear(cosHost);
  const cosChart = cosinorChart({
    hourly: rhythm.hourly || [],
    cosinor,
    ariaLabel: cosinor.usable === true && cosinor.acrophase_hour !== null
      ? t("web.dashboard.renderActivityPage.5dfd58dbb", {p1: windowOf("rhythm", 14), p2: clockFromDecimal(cosinor.acrophase_hour), p3: num(cosinor.r2, 2)})
      : t("web.dashboard.renderActivityPage.57d2371c5"),
  });
  if (cosChart) {
    cosHost.appendChild(h("div", { class: "chart-head" }, [
      h("span", { class: "chart-title", text: t("web.dashboard.renderActivityPage.ac2d93b81") }),
      h("span", { class: "chart-note", text: t("web.dashboard.renderActivityPage.7c018c7d7", {p1: windowOf("rhythm", 14)}) }),
    ]));
    cosHost.appendChild(cosChart);
  }

  const detailHost = $("activity-detail-body");
  clear(detailHost);
  const detailRows = [];
  if (activity.distance_km !== null && activity.distance_km !== undefined) detailRows.push(line(t("web.dashboard.renderActivityPage.3865483de"), [numSpan(num(activity.distance_km, 2)), " km"]));
  if (activity.floors !== null && activity.floors !== undefined) detailRows.push(line(t("web.dashboard.renderActivityPage.2748d6abc"), [numSpan(num(activity.floors)), t("web.dashboard.renderActivityPage.396b6c05f")]));
  if (activity.calories_out !== null && activity.calories_out !== undefined) detailRows.push(line(t("web.dashboard.renderActivityPage.6841999fa"), [numSpan(num(activity.calories_out)), t("web.dashboard.renderActivityPage.1ad5aaa2c")]));
  if (activity.zone_minutes !== null && activity.zone_minutes !== undefined) detailRows.push(line(t("web.dashboard.renderActivityPage.9de8f1ef7"), [numSpan(num(activity.zone_minutes)), t("web.dashboard.renderActivityPage.40de54344")]));
  if (detailRows.length) detailHost.appendChild(linesBlock(detailRows));
  $("activity-detail").hidden = !detailRows.length;
  $("activity-detail-note").textContent = detailRows.length ? t("web.dashboard.renderActivityPage.7cb07a80e", {p1: detailRows.length}) : "";

  const weekdayHost = $("activity-weekday");
  clear(weekdayHost);
  const weekdayRows = (insights && insights.weekday) || [];
  if (weekdayRows.some((row) => row.steps_avg !== null && row.steps_avg !== undefined)) {
    const labels = [t("web.dashboard.renderActivityPage.c430f4c12"), t("web.dashboard.renderActivityPage.d1378a68e"), t("web.dashboard.renderActivityPage.296116896"), t("web.dashboard.renderActivityPage.e9d3b01a5"), t("web.dashboard.renderActivityPage.572388f3b"), t("web.dashboard.renderActivityPage.f9aa11dbb"), t("web.dashboard.renderActivityPage.ee239f394")];
    const points = weekdayRows.map((row) => ({ label: labels[row.weekday], value: row.steps_avg }));
    const chart = barChart({ points, color: "var(--m-steps)", unit: t("web.dashboard.renderActivityPage.e010e3f57"), width: 460, height: 150, ariaLabel: t("web.dashboard.renderActivityPage.bf4397bb1"), axisLabels: true, yAxis: true });
    weekdayHost.appendChild(h("div", { class: "chart-head" }, [
      h("span", { class: "chart-title", text: t("web.dashboard.renderActivityPage.29a3e0349") }),
      h("span", { class: "chart-note", text: t("web.dashboard.renderActivityPage.6c42518a4", {p1: windowText()}) }),
    ]));
    if (chart) weekdayHost.appendChild(chart);
    const peak = weekdayRows.reduce((best, row) => (row.steps_avg !== null && row.steps_avg > (best?.steps_avg ?? -1) ? row : best), null);
    if (peak) weekdayHost.appendChild(h("p", { class: "hintline", text: t("web.dashboard.renderActivityPage.18f49ab72", {p1: labels[peak.weekday], p2: num(Math.round(peak.steps_avg))}) }));
  }
  const heatmap = (insights && insights.heatmap) || [];
  if (heatmap.flat().some((value) => value !== null && value !== undefined)) {
    let peakRow = 0; let peakCol = 0; let peakValue = -1;
    heatmap.forEach((row, rowIndex) => row.forEach((value, colIndex) => {
      if (value !== null && value !== undefined && value > peakValue) { peakValue = value; peakRow = rowIndex; peakCol = colIndex; }
    }));
    const labels = [t("web.dashboard.renderActivityPage.c430f4c12"), t("web.dashboard.renderActivityPage.d1378a68e"), t("web.dashboard.renderActivityPage.296116896"), t("web.dashboard.renderActivityPage.e9d3b01a5"), t("web.dashboard.renderActivityPage.572388f3b"), t("web.dashboard.renderActivityPage.f9aa11dbb"), t("web.dashboard.renderActivityPage.ee239f394")];
    const chart = heatmapChart({
      matrix: heatmap,
      ariaLabel: t("web.dashboard.renderActivityPage.7f9d4a777", {p1: labels[peakRow], p2: String(peakCol).padStart(2, "0"), p3: num(peakValue)}),
    });
    weekdayHost.appendChild(h("div", { class: "chart-head" }, [
      h("span", { class: "chart-title", text: t("web.dashboard.renderActivityPage.c08646f5f") }),
      h("span", { class: "chart-note", text: t("web.dashboard.renderActivityPage.936e39ced", {p1: labels[peakRow], p2: String(peakCol).padStart(2, "0"), p3: num(peakValue)}) }),
    ]));
    if (chart) weekdayHost.appendChild(chart);
  }

  const pointer = $("activity-pointer");
  const related = (insights && insights.correlations || []).filter((c) => METRIC_DOMAIN[c.y] === "activity");
  pointer.textContent = related.length ? t("web.dashboard.renderActivityPage.d8bc93a82") : "";
  pointer.hidden = !related.length;

  section.hidden = !actCards.length && !trendChart;
}
