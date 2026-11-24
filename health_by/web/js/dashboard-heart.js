import { t } from "./i18n.js";
import { num } from "./format.js";
import { hrDayChart } from "./charts.js";
import {
  $,
  cardGrid,
  clear,
  h,
  hrvDeltaText,
  line,
  linesBlock,
  metricDisplay,
  numSpan,
  relationsBlock,
  rhrDeltaText,
  trendBlock,
  windowOf,
} from "./dashboard-dom.js";

export function renderHeartPage(day, insights, trends, goals) {
  void trends;
  void goals;
  const section = $("card-heart");
  const rawHeart = day.heart || {};
  const rhrMode = metricDisplay("resting_hr", rawHeart.resting_hr);
  const hrvMode = metricDisplay("hrv_ms", rawHeart.hrv_ms);
  const heart = {...rawHeart, resting_hr: rhrMode.value, hrv_ms: hrvMode.value};
  const recovery = (insights && insights.recovery) || {};
  const baselineDays = windowOf("baseline", 28);

  const todayHost = $("heart-today-body");
  clear(todayHost);
  const heartCards = [];
  if (rhrMode.rolling || (heart.resting_hr !== null && heart.resting_hr !== undefined)) {
    heartCards.push({
      label: t("web.dashboard.renderHeartPage.e9a48bd3e"), metric: "resting_hr", value: num(heart.resting_hr, rhrMode.rolling ? 1 : 0), unit: "bpm",
      compare: rhrMode.rolling ? (heart.resting_hr == null ? t("web.dashboard.renderHeartPage.26f705334") : null) : rhrDeltaText(recovery.rhr_delta, baselineDays) || null,
    });
  }
  if (hrvMode.rolling || (heart.hrv_ms !== null && heart.hrv_ms !== undefined)) {
    heartCards.push({
      label: "HRV", metric: "hrv_ms", value: num(heart.hrv_ms, 1), unit: "ms",
      compare: hrvMode.rolling ? (heart.hrv_ms == null ? t("web.dashboard.renderHeartPage.26f705334") : null) : hrvDeltaText(recovery.hrv_delta_pct, "", baselineDays) || null,
    });
  }
  if (heartCards.length) todayHost.appendChild(cardGrid(heartCards, Math.min(4, heartCards.length)));
  else todayHost.appendChild(h("p", { class: "section-empty", text: t("web.dashboard.renderHeartPage.4cac2f022") }));

  const trendBody = $("heart-trend-body");
  clear(trendBody);
  const rhrChart = trendBlock("resting_hr", { title: t("web.dashboard.renderHeartPage.e9a48bd3e"), unit: "bpm", color: "var(--m-heart)", width: 460, height: 150 });
  const hrvChart = trendBlock("hrv_ms", { title: "HRV", unit: "ms", color: "var(--m-sleep-deep)", width: 460, height: 150 });
  const split = h("div", { class: "chart-split" });
  if (rhrChart) split.appendChild(rhrChart);
  if (hrvChart) split.appendChild(hrvChart);
  if (split.childElementCount) trendBody.appendChild(split);
  if (!rhrChart && !hrvChart) trendBody.appendChild(h("p", { class: "section-empty", text: t("web.dashboard.renderHeartPage.5e6a574fc") }));
  $("heart-note").textContent = t("web.dashboard.renderHeartPage.6b758fc66");

  const nightlyHost = $("nightly-body");
  clear(nightlyHost);
  const detailRows = [];
  for (const [key, raw, label, unit, precision] of [
    ["spo2", rawHeart.spo2, t("web.dashboard.renderHeartPage.3a773e960"), "%", 1],
    ["breathing_rate", rawHeart.breathing_rate, t("web.dashboard.renderHeartPage.f3261e58d"), t("web.dashboard.renderHeartPage.644993752"), 1],
    ["skin_temp_delta", rawHeart.skin_temp_delta_c, t("web.dashboard.renderHeartPage.fde263b56"), " °C", 2],
  ]) {
    const view = metricDisplay(key, raw);
    if (view.value !== null && view.value !== undefined) detailRows.push(line(label, [numSpan(num(view.value, precision)), unit], null, "", null, key));
    else if (view.rolling) detailRows.push(line(label, t("web.dashboard.renderHeartPage.26f705334"), null, "", null, key));
  }
  if (heart.core_temp_c !== null && heart.core_temp_c !== undefined) detailRows.push(line(t("web.dashboard.renderHeartPage.480a98dd5"), [numSpan(num(heart.core_temp_c, 1)), " °C"]));
  if (heart.vo2max !== null && heart.vo2max !== undefined) detailRows.push(line(t("web.dashboard.renderHeartPage.7adb1394f"), [numSpan(num(heart.vo2max, 1)), " ml/kg/min"]));
  if ((heart.hr_min !== null && heart.hr_min !== undefined) || (heart.hr_max !== null && heart.hr_max !== undefined)) detailRows.push(line(t("web.dashboard.renderHeartPage.825e9b7ae"), [numSpan(`${num(heart.hr_min)} – ${num(heart.hr_max)}`), " bpm"]));
  if (detailRows.length) nightlyHost.appendChild(linesBlock(detailRows));

  const curveHost = $("day-hr-body");
  clear(curveHost);
  const points = heart.hr_intraday || [];
  const curve = points.length >= 4 ? hrDayChart({
    points,
    resting: rawHeart.resting_hr,
    workouts: day.workouts || [],
    ariaLabel: t("web.dashboard.renderHeartPage.9f44372b6", {p1: points.length}),
  }) : null;
  if (curve) {
    curveHost.appendChild(curve);
    $("day-hr-note").textContent = rawHeart.resting_hr !== null && rawHeart.resting_hr !== undefined ? t("web.dashboard.renderHeartPage.74f11262d", {p1: num(rawHeart.resting_hr)}) : "";
  }
  $("heart-detail").hidden = !detailRows.length && !curve;
  $("heart-detail-note").textContent = [detailRows.length ? t("web.dashboard.renderHeartPage.7cb07a80e", {p1: detailRows.length}) : null, curve ? t("web.dashboard.renderHeartPage.937dc7bf3") : null].filter(Boolean).join(" · ");

  const relHost = $("heart-relations");
  clear(relHost);
  relHost.appendChild(relationsBlock("heart"));

  section.hidden = !heartCards.length && !rhrChart && !hrvChart && !detailRows.length && !curve;
}
