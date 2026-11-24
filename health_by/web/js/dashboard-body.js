import { t } from "./i18n.js";
import { DASH, num, clockTime, shortDate } from "./format.js";
import { lineChart } from "./charts.js";
import { $, cardGrid, clear, h, line, linesBlock, numSpan, windowOf } from "./dashboard-dom.js";

export function renderBodyPage(day, insights, trends) {
  void insights;
  const section = $("card-body");
  const bodyData = day.body || {};
  const workouts = day.workouts || [];

  const todayHost = $("body-today");
  clear(todayHost);
  const bodyCards = [];
  if (bodyData.weight_kg !== null && bodyData.weight_kg !== undefined) {
    bodyCards.push({ label: t("web.dashboard.renderBodyPage.4729deedd"), value: num(bodyData.weight_kg, 1), unit: "kg", compare: bodyData.body_fat_pct !== null && bodyData.body_fat_pct !== undefined ? t("web.dashboard.renderBodyPage.7067e5404", {p1: num(bodyData.body_fat_pct, 1)}) : null });
  }
  const byName = new Map();
  for (const workout of workouts) {
    const entry = byName.get(workout.name) || { count: 0, minutes: 0 };
    entry.count += 1;
    entry.minutes += workout.duration_minutes || 0;
    byName.set(workout.name, entry);
  }
  const described = [...byName.entries()].map(([name, entry]) => (
    entry.count > 1 ? `${name} ×${entry.count}` : name
  ));
  if (workouts.length) {
    bodyCards.push({ label: t("web.dashboard.renderBodyPage.a29c0de14"), value: String(workouts.length), unit: t("web.dashboard.renderBodyPage.172fb7e67"), compare: described.join(" · ") || null });
  }
  if (bodyCards.length) todayHost.appendChild(cardGrid(bodyCards, Math.min(4, bodyCards.length)));
  else todayHost.appendChild(h("p", { class: "section-empty", text: t("web.dashboard.renderBodyPage.89688706d") }));

  const trendHost = $("body-trend");
  clear(trendHost);
  const days = Number.isFinite(windowOf("summary_trend")) ? windowOf("summary_trend") : 14;
  const points = (trends || []).map((entry) => ({ date: entry.date, label: shortDate(entry.date), value: entry.weight_kg ?? null }));
  const valid = points.filter((point) => point.value !== null && point.value !== undefined);
  let weightChart = null;
  if (valid.length >= 2) {
    weightChart = lineChart({ points, color: "var(--m-body)", unit: " kg", width: 640, height: 120, ariaLabel: t("web.dashboard.renderBodyPage.5a8a11d92", {days: days}) });
  }
  if (weightChart) {
    trendHost.appendChild(h("div", { class: "chart-head" }, [
      h("span", { class: "chart-title", text: t("web.dashboard.renderBodyPage.38f7c7e22") }),
      h("span", { class: "chart-note", text: t("web.dashboard.renderBodyPage.951e6a5fe", {days: days, p2: valid.length}) }),
    ]));
    trendHost.appendChild(weightChart);
    $("body-trend-block").hidden = false;
  } else {
    $("body-trend-block").hidden = true;
  }

  const bodyHost = $("body-body");
  clear(bodyHost);
  const rows = [];
  if (bodyData.water_ml !== null && bodyData.water_ml !== undefined) rows.push(line(t("web.dashboard.renderBodyPage.6edae041a"), [numSpan(num(bodyData.water_ml)), " ml"]));
  if (bodyData.calories_in !== null && bodyData.calories_in !== undefined) rows.push(line(t("web.dashboard.renderBodyPage.cc8fbb837"), [numSpan(num(bodyData.calories_in)), t("web.dashboard.renderBodyPage.1ad5aaa2c")]));
  if (rows.length) bodyHost.appendChild(linesBlock(rows));

  const workoutHost = $("workouts-body");
  clear(workoutHost);
  if (workouts.length) {
    workoutHost.appendChild(linesBlock(workouts.map((workout) => {
      const parts = [];
      if (workout.duration_minutes !== null && workout.duration_minutes !== undefined) parts.push(numSpan(String(Math.round(workout.duration_minutes))), t("web.dashboard.renderBodyPage.40de54344"));
      if (workout.distance_km !== null && workout.distance_km !== undefined) {
        if (parts.length) parts.push(" · ");
        parts.push(numSpan(num(workout.distance_km, 2)), " km");
      }
      if (workout.calories !== null && workout.calories !== undefined) {
        if (parts.length) parts.push(" · ");
        parts.push(numSpan(num(workout.calories)), t("web.dashboard.renderBodyPage.1ad5aaa2c"));
      }
      const note = workout.average_hr !== null && workout.average_hr !== undefined ? t("web.dashboard.renderBodyPage.aa4ea912c", {p1: num(workout.average_hr)}) : null;
      return line(`${clockTime(workout.start_time)} ${workout.name}`, parts.length ? parts : [DASH], note);
    })));
    $("workouts-note").textContent = t("web.dashboard.renderBodyPage.e311c654d", {p1: workouts.length});
  } else {
    workoutHost.appendChild(h("p", { class: "section-empty", text: t("web.dashboard.renderBodyPage.1afa92807") }));
    $("workouts-note").textContent = "";
  }
  $("body-detail").hidden = !rows.length && !workouts.length;
  $("body-note").textContent = (bodyData.weight_kg !== null && bodyData.weight_kg !== undefined) ? t("web.dashboard.renderBodyPage.c7ca79caf") : "";

  section.hidden = !bodyCards.length && !weightChart && !rows.length;
}
