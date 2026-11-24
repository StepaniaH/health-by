import { t } from "./i18n.js";
import { DASH, num, minutes } from "./format.js";
import { timelineRibbon } from "./charts.js";
import { $, clear, h, isoShort, KEY_LABELS, line, linesBlock, table } from "./dashboard-dom.js";

export function renderDataPage(day, insights, goals, analysis) {
  void goals;
  const section = $("card-data");
  let visible = 0;

  const sleep = day.sleep || null;
  const stages = (sleep && sleep.stage_timeline) || [];
  const intraday = (day.activity && day.activity.steps_intraday) || [];
  const workouts = day.workouts || [];
  const ribbon = timelineRibbon({
    sleep, stages, intraday, workouts,
    ariaLabel: (sleep ? t("web.dashboard.renderDataPage.554e3a2ca", {p2: intraday.length, p3: workouts.length}) : t("web.dashboard.renderDataPage.a51c7f2a2", {p2: intraday.length, p3: workouts.length})),
  });
  const timelineHost = $("timeline-body");
  clear(timelineHost);
  if (ribbon) {
    timelineHost.appendChild(ribbon);
    const legend = h("div", { class: "legend" });
    if (intraday.length) legend.appendChild(h("span", {}, [h("i", { style: "background:var(--m-steps)" }), t("web.dashboard.renderDataPage.3b25a3b0f")]));
    if (sleep) legend.appendChild(h("span", {}, [h("i", { style: "background:var(--m-sleep)" }), t("web.dashboard.renderDataPage.01324e540")]));
    if (workouts.length) legend.appendChild(h("span", {}, [h("i", { style: "background:var(--ink)" }), t("web.dashboard.renderDataPage.a9d130d79")]));
    timelineHost.appendChild(legend);
    visible += 1;
  }
  $("timeline-note").textContent = ribbon ? t("web.dashboard.renderDataPage.33dc68db4") : "";

  const weekly = (insights && insights.weekly) || [];
  const weeklyHost = $("weekly-table");
  clear(weeklyHost);
  if (weekly.length) {
    weeklyHost.appendChild(table(
      [
        { label: t("web.dashboard.renderDataPage.6081ee084") }, { label: t("web.dashboard.renderDataPage.6fe633049"), num: true }, { label: t("web.dashboard.renderDataPage.01324e540") }, { label: t("web.dashboard.renderDataPage.2810c1ae7"), num: true },
        { label: t("web.dashboard.renderDataPage.f61d4e4f5"), num: true }, { label: t("web.dashboard.renderDataPage.6bfde7bb8"), num: true }, { label: t("web.dashboard.renderDataPage.cb614c817"), num: true }, { label: "HRV ms", num: true },
      ],
      weekly.map((week, index) => ({
        strong: index === weekly.length - 1,
        cells: [
          { text: isoShort(week.week_start) },
          { text: week.days < 7 ? `${week.days} / 7` : t("web.dashboard.renderDataPage.417b6fe32", {p1: week.days}), num: true },
          { text: week.sleep_avg !== null ? minutes(week.sleep_avg) : DASH },
          { text: week.efficiency_avg !== null ? `${num(week.efficiency_avg, 0)}%` : DASH, num: true },
          { text: week.steps_avg !== null ? num(week.steps_avg) : DASH, num: true },
          { text: week.active_sum !== null ? num(week.active_sum) : DASH, num: true },
          { text: week.rhr_avg !== null ? num(week.rhr_avg, 0) : DASH, num: true },
          { text: week.hrv_avg !== null ? Number(week.hrv_avg).toFixed(1) : DASH, num: true },
        ],
      })),
      "weekly",
    ));
    $("weekly-note").textContent = t("web.dashboard.renderDataPage.dabebd949", {p1: weekly[0].week_start});
    visible += 1;
  }

  const coverage = day.coverage || {};
  const errors = coverage.errors || [];
  const coverageHost = $("coverage-body");
  clear(coverageHost);
  if (errors.length) {
    coverageHost.appendChild(h("p", { class: "muted small", text: t("web.dashboard.renderDataPage.2fcb07ce8", {p1: coverage.success_count, p2: coverage.endpoint_count}) }));
    coverageHost.appendChild(linesBlock(errors.map((error) => line(KEY_LABELS[error.key] || error.key, error.message || t("web.dashboard.renderDataPage.460b3574e")))));
    $("coverage-note").textContent = t("web.dashboard.renderDataPage.6bde1e6d0", {p1: errors.length});
    visible += 1;
  } else if (coverage.endpoint_count) {
    coverageHost.appendChild(h("p", { class: "muted small", text: t("web.dashboard.renderDataPage.2fcb07ce8", {p1: coverage.success_count, p2: coverage.endpoint_count}) }));
    $("coverage-note").textContent = "";
    visible += 1;
  } else {
    coverageHost.appendChild(h("p", { class: "section-empty", text: t("web.dashboard.renderDataPage.6e019dd6b") }));
  }

  const baselineLabels = {steps: t("web.dashboard.renderDataPage.2986c7ee9"), sleep_minutes: t("web.dashboard.renderDataPage.98439c55d"), resting_hr: t("web.dashboard.renderDataPage.e9a48bd3e"), hrv_ms: "HRV",
    breathing_rate: t("web.dashboard.renderDataPage.f3261e58d"), spo2: t("web.dashboard.renderDataPage.2836400cc"), skin_temp_delta: t("web.dashboard.renderDataPage.a3bc489c3")};
  for (const [key, comparison] of Object.entries(analysis?.comparisons || {})) {
    if (comparison.field_appearance && comparison.valid_from) {
      coverageHost.appendChild(h("p", {class: "muted small",
        text: (comparison.insufficient_reason ? t("web.dashboard.renderDataPage.d2d96ec04", {p1: baselineLabels[key] || key, p2: comparison.valid_from, p3: comparison.sample_count, p4: `${t("separator.clauses")}${comparison.insufficient_reason}`}) : t("web.dashboard.renderDataPage.e493d478a", {p1: baselineLabels[key] || key, p2: comparison.valid_from, p3: comparison.sample_count})) }));
      visible += 1;
    }
  }

  $("data-note-label").textContent = t("web.dashboard.renderDataPage.96ad1e31a");
  section.hidden = visible === 0;
}
