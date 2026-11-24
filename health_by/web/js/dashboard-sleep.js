import { t } from "./i18n.js";
import { DASH, num, minutes, clockTime } from "./format.js";
import { stageBar, sleepTimingChart, SLEEP_STAGE_LABELS, SLEEP_STAGE_COLORS } from "./charts.js";
import {
  $,
  achievementsBlock,
  clear,
  clockFromDecimal,
  h,
  line,
  linesBlock,
  METRIC_DOMAIN,
  numSpan,
  signalFor,
  stateClass,
  trendBlock,
  windowOf,
} from "./dashboard-dom.js";

export function renderSleepPage(day, insights, trends, goals) {
  void trends;
  const section = $("card-sleep");
  const sleep = day.sleep || {};
  const recovery = (insights && insights.recovery) || {};
  const rhythm = (insights && insights.rhythm) || {};
  const timing = (insights && insights.timing) || [];
  const hasNight = sleep.total_minutes !== null && sleep.total_minutes !== undefined;

  const nightBody = $("sleep-body");
  clear(nightBody);
  if (hasNight) {
    const stages = sleep.stages || {};
    const stageText = ["deep", "light", "rem", "wake"].filter((key) => stages[key])
      .map((key) => t("web.dashboard.renderSleepPage.f29f9494a", {p1: SLEEP_STAGE_LABELS[key], p2: stages[key]})).join(t("separator.list"));
    const chart = stageBar({ stages, ariaLabel: t("web.dashboard.renderSleepPage.77e6a5478", {stageText: stageText}) });
    if (chart) nightBody.appendChild(chart);
    const legend = h("div", { class: "legend" });
    for (const key of ["deep", "light", "rem", "wake"]) {
      if (!stages[key]) continue;
      legend.appendChild(h("span", {}, [h("i", { style: `background:${SLEEP_STAGE_COLORS[key]}` }), t("web.dashboard.renderSleepPage.f29f9494a", {p1: SLEEP_STAGE_LABELS[key], p2: stages[key]})]));
    }
    nightBody.appendChild(legend);
    const signal = signalFor(insights, "sleep");
    const parts = [minutes(sleep.total_minutes)];
    if (goals.sleep_minutes) parts.push(t("web.dashboard.renderSleepPage.6b0e287ba", {p1: minutes(goals.sleep_minutes)}));
    if (signal && signal.state_label) parts.push(signal.state_label);
    nightBody.appendChild(h("p", { class: "sleep-headline", text: parts.join(" · ") }));
  } else {
    nightBody.appendChild(h("p", { class: "section-empty", text: t("web.dashboard.renderSleepPage.55b00013a") }));
  }
  $("sleep-night-note").textContent = sleep.start_time ? `${clockTime(sleep.start_time)} – ${clockTime(sleep.end_time)}` : "";

  const signalHost = $("sleep-signals");
  clear(signalHost);
  const signals = (recovery.signals || []).filter((signal) => signal.label && signal.state && signal.state !== "unknown");
  if (signals.length) {
    signalHost.appendChild(h("p", { class: "signal-line" }, signals.map((signal, index) => h("span", {}, [
      index ? h("span", { class: "signal-sep", text: " · " }) : null,
      `${signal.label} `,
      h("span", { class: stateClass(signal) || "", text: signal.state_label }),
    ]))));
  }
  $("sleep-note").textContent = t("web.dashboard.renderSleepPage.6b758fc66");

  const trendBody = $("sleep-trend-body");
  clear(trendBody);
  const trendChart = trendBlock("sleep_minutes", {
    title: t("web.dashboard.renderSleepPage.98439c55d"), unit: t("web.dashboard.renderSleepPage.3b6fefc50"), color: "var(--m-sleep)", step: 120,
    format: (value) => t("web.dashboard.renderSleepPage.57727ff02", {p1: (value / 60).toFixed(1)}),
    goal: goals.sleep_minutes || null,
  });
  if (trendChart) trendBody.appendChild(trendChart);
  else trendBody.appendChild(h("p", { class: "section-empty", text: t("web.dashboard.renderSleepPage.23ce9adc6") }));
  const sleepStrip = achievementsBlock("sleep_ratio", goals.sleep_minutes ? minutes(goals.sleep_minutes) : t("web.dashboard.renderSleepPage.9573e4875"), t("web.dashboard.renderSleepPage.9573e4875"));
  const stripHost = $("sleep-achievements");
  clear(stripHost);
  if (sleepStrip) stripHost.appendChild(sleepStrip);
  const debt = recovery.sleep_debt || {};
  $("sleep-trend-note").textContent = debt.minutes
    ? t("web.dashboard.renderSleepPage.2a69ca5ac", {p1: debt.window || 7, p2: (debt.minutes / 60).toFixed(1), p3: debt.nights || 0})
    : "";

  const rhythmBody = $("sleep-rhythm-body");
  clear(rhythmBody);
  const strip = h("div", { class: "stat-strip" });
  const addStat = (label, value, unit, note) => strip.appendChild(h("div", { class: "stat" }, [
    h("div", { class: "stat-label", text: label }),
    h("div", { class: "stat-value" }, [value, unit ? h("span", { class: "u", text: unit }) : null]),
    h("div", { class: "stat-note", text: note || "" }),
  ]));
  addStat(t("web.dashboard.renderSleepPage.0daa929be"), rhythm.sri !== null && rhythm.sri !== undefined ? num(rhythm.sri) : DASH, "", t("web.dashboard.renderSleepPage.32c7803cb", {p1: rhythm.sri_pairs || 0}));
  addStat(t("web.dashboard.renderSleepPage.84ced41f5"), rhythm.social_jetlag_min !== null && rhythm.social_jetlag_min !== undefined ? num(rhythm.social_jetlag_min, 0) : DASH, t("web.dashboard.renderSleepPage.bd957bc49"), t("web.dashboard.renderSleepPage.4d995e356"));
  addStat(t("web.dashboard.renderSleepPage.6dde8ab37"), rhythm.midpoint_sd_min !== null && rhythm.midpoint_sd_min !== undefined ? `±${num(rhythm.midpoint_sd_min, 0)}` : DASH, t("web.dashboard.renderSleepPage.bd957bc49"), t("web.dashboard.renderSleepPage.f6d80879e", {p1: rhythm.n_nights || 0}));
  addStat(t("web.dashboard.renderSleepPage.13ab1d1fd"), rhythm.bed_sd_min !== null && rhythm.bed_sd_min !== undefined ? `±${num(rhythm.bed_sd_min, 0)}` : DASH, t("web.dashboard.renderSleepPage.bd957bc49"), t("web.dashboard.renderSleepPage.f6d80879e", {p1: rhythm.n_nights || 0}));
  rhythmBody.appendChild(strip);

  const timingByDate = new Map(timing.map((entry) => [entry.date, entry]));
  const entries = [];
  if (timing.length) {
    const lastDate = timing[timing.length - 1].date;
    const end = new Date(`${lastDate}T00:00:00Z`);
    const cursor = new Date(end);
    cursor.setUTCDate(cursor.getUTCDate() - (windowOf("baseline", 28) - 1));
    while (cursor <= end) {
      const key = cursor.toISOString().slice(0, 10);
      entries.push(timingByDate.get(key) || { date: key, bed: null, wake: null });
      cursor.setUTCDate(cursor.getUTCDate() + 1);
    }
  }
  const recorded = entries.filter((entry) => entry.bed !== null && entry.bed !== undefined).length;
  const timingChart = sleepTimingChart({
    entries,
    ariaLabel: t("web.dashboard.renderSleepPage.861a20aa1", {p1: entries.length, recorded: recorded, p3: clockFromDecimal(rhythm.midpoint_mean)}),
  });
  if (timingChart) {
    rhythmBody.appendChild(h("div", { class: "chart-head" }, [
      h("span", { class: "chart-title", text: t("web.dashboard.renderSleepPage.9dcd33efd") }),
      h("span", { class: "chart-note", text: t("web.dashboard.renderSleepPage.6b416bd2b", {p1: entries.length, recorded: recorded, p3: clockFromDecimal(rhythm.midpoint_mean)}) }),
    ]));
    rhythmBody.appendChild(timingChart);
  }

  const detailBody = $("sleep-detail-body");
  clear(detailBody);
  const detailRows = [];
  if (sleep.time_in_bed_minutes !== null && sleep.time_in_bed_minutes !== undefined) detailRows.push(line(t("web.dashboard.renderSleepPage.1e289aa70"), [minutes(sleep.time_in_bed_minutes)]));
  if (sleep.efficiency !== null && sleep.efficiency !== undefined) detailRows.push(line(t("web.dashboard.renderSleepPage.dc4a0545d"), [numSpan(num(sleep.efficiency)), " %"]));
  if (sleep.minutes_awake !== null && sleep.minutes_awake !== undefined) detailRows.push(line(t("web.dashboard.renderSleepPage.f7185055a"), [numSpan(num(sleep.minutes_awake)), t("web.dashboard.renderSleepPage.40de54344")]));
  if (sleep.minutes_to_fall_asleep !== null && sleep.minutes_to_fall_asleep !== undefined) detailRows.push(line(t("web.dashboard.renderSleepPage.553097b26"), [numSpan(num(sleep.minutes_to_fall_asleep)), t("web.dashboard.renderSleepPage.40de54344")]));
  if (sleep.minutes_after_wake_up !== null && sleep.minutes_after_wake_up !== undefined) detailRows.push(line(t("web.dashboard.renderSleepPage.3b09cd56d"), [numSpan(num(sleep.minutes_after_wake_up)), t("web.dashboard.renderSleepPage.40de54344")]));
  if (day.naps_count) {
    detailRows.push(line(t("web.dashboard.renderSleepPage.8ffb0e1ac"), [numSpan(String(day.naps_count)), t("web.dashboard.renderSleepPage.03b7163f7")], t("web.dashboard.renderSleepPage.447c464ec", {p1: minutes(day.naps_minutes || 0)})));
  }
  if (detailRows.length) detailBody.appendChild(linesBlock(detailRows));
  $("sleep-detail-note").textContent = detailRows.length ? t("web.dashboard.renderSleepPage.7cb07a80e", {p1: detailRows.length}) : "";
  $("sleep-detail").hidden = detailRows.length === 0;

  const pointer = $("sleep-pointer");
  const sleepRelated = (insights && insights.correlations || []).filter((c) => METRIC_DOMAIN[c.x] === "sleep" || METRIC_DOMAIN[c.y] === "sleep");
  if (sleepRelated.length) {
    pointer.textContent = t("web.dashboard.renderSleepPage.c3de46372", {});
    pointer.hidden = false;
  } else {
    pointer.hidden = true;
  }

  section.hidden = !hasNight && !trendChart && !timingChart;
}
