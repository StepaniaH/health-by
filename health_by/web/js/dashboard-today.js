import { t } from "./i18n.js";
import { num, minutes } from "./format.js";
import {
  $,
  clear,
  h,
  hideSectionNotice,
  hrvDeltaText,
  metricDisplay,
  renderSectionNotice,
  setHidden,
  todayCard,
  windowOf,
} from "./dashboard-dom.js";

export function renderToday(day, insights, goals, analysis) {
  const section = $("card-today");
  const rows = $("today-rows");
  clear(rows);
  const recovery = (insights && insights.recovery) || {};
  const baselineDays = windowOf("baseline", 28);
  void analysis;

  const items = [];
  const addRow = (item) => {
    if (!item || !item.value) return;
    items.push({
      aria: item.label ? `${item.label}${t("separator.colon")}${item.sentence}` : undefined,
      ...item,
    });
  };

  const sleepMode = metricDisplay("sleep_minutes", day.sleep?.total_minutes);
  const sleep = {...(day.sleep || {}), total_minutes: sleepMode.value};
  if (sleep.total_minutes !== null && sleep.total_minutes !== undefined) {
    let sentence = sleepMode.rolling
      ? t("sleep.sentence.rolling", { duration: minutes(sleep.total_minutes) })
      : t("sleep.sentence.today", { duration: minutes(sleep.total_minutes) });
    if (goals.sleep_minutes) {
      const diff = sleep.total_minutes - goals.sleep_minutes;
      const equal = Math.abs(diff) < 1;
      const parameters = { duration: minutes(sleep.total_minutes), difference: minutes(Math.abs(diff)) };
      sentence = sleepMode.rolling
        ? t(equal ? "sleep.sentence.rolling.equal" : diff < 0 ? "sleep.sentence.rolling.short" : "sleep.sentence.rolling.over", parameters)
        : t(equal ? "sleep.sentence.today.equal" : diff < 0 ? "sleep.sentence.today.short" : "sleep.sentence.today.over", parameters);
    }
    const ratio = goals.sleep_minutes ? sleep.total_minutes / goals.sleep_minutes : null;
    const totalRounded = Math.round(sleep.total_minutes);
    const hoursPart = Math.floor(totalRounded / 60);
    const minutesPart = totalRounded - hoursPart * 60;
    const short = sleep.total_minutes >= 60
      ? { value: String(hoursPart), unit: minutesPart ? t("web.dashboard.renderToday.ad94f6cb8", {minutesPart: minutesPart}) : t("web.dashboard.renderToday.3b6fefc50") }
      : { value: String(Math.round(sleep.total_minutes)), unit: t("web.dashboard.renderToday.bd957bc49") };
    const diffText = goals.sleep_minutes
      ? (Math.abs(sleep.total_minutes - goals.sleep_minutes) < 1
        ? t("web.dashboard.renderToday.0033d8a13", {p1: minutes(goals.sleep_minutes)})
        : (sleep.total_minutes < goals.sleep_minutes ? t("web.dashboard.renderToday.0ce12458b", {p1: minutes(goals.sleep_minutes), p3: minutes(Math.abs(sleep.total_minutes - goals.sleep_minutes))}) : t("web.dashboard.renderToday.68af90641", {p1: minutes(goals.sleep_minutes), p3: minutes(Math.abs(sleep.total_minutes - goals.sleep_minutes))})))
      : null;
    addRow({
      domain: "sleep", label: t("web.dashboard.renderToday.01324e540"), metric: "sleep_minutes", href: "#card-sleep", sentence,
      value: short.value, unit: short.unit, compare: diffText,
      ringRatio: ratio, color: "var(--m-sleep)",
    });
  } else {
    addRow({ domain: "sleep", label: t("web.dashboard.renderToday.01324e540"), href: "#card-sleep", metric: "sleep_minutes", sentence: sleepMode.rolling ? t("web.dashboard.renderToday.685cef9f5") : t("web.dashboard.renderToday.e45263e66"), value: "—", unit: "", compare: "" });
  }

  const activity = day.activity || {};
  if (activity.steps !== null && activity.steps !== undefined) {
    let sentence = t("web.dashboard.renderToday.a9c5663e6", {p1: num(activity.steps)});
    if (goals.steps) {
      const diff = activity.steps - goals.steps;
      sentence = t(Math.abs(diff) < 1 ? "activity.sentence.equal" : diff < 0 ? "activity.sentence.short" : "activity.sentence.over",
        { p1: num(activity.steps), p2: num(Math.abs(diff)) });
    }
    const stepDiff = goals.steps
      ? (Math.abs(activity.steps - goals.steps) < 1
        ? t("web.dashboard.renderToday.3f9ee5b8e", {p1: num(goals.steps)})
        : (activity.steps < goals.steps ? t("web.dashboard.renderToday.f3e4b5a1b", {p1: num(goals.steps), p3: num(Math.abs(activity.steps - goals.steps))}) : t("web.dashboard.renderToday.9c75be2da", {p1: num(goals.steps), p3: num(Math.abs(activity.steps - goals.steps))})))
      : null;
    addRow({
      domain: "activity", label: t("web.dashboard.renderToday.9e9bead81"), href: "#card-activity", sentence,
      value: num(activity.steps), unit: t("web.dashboard.renderToday.a621b7cd7"), compare: stepDiff,
      ringRatio: goals.steps ? activity.steps / goals.steps : null, color: "var(--m-steps)",
    });
  } else {
    addRow({ domain: "activity", label: t("web.dashboard.renderToday.9e9bead81"), href: "#card-activity", sentence: t("web.dashboard.renderToday.f213d5661"), value: "—", unit: "", compare: "" });
  }

  const rhrMode = metricDisplay("resting_hr", day.heart?.resting_hr);
  const hrvMode = metricDisplay("hrv_ms", day.heart?.hrv_ms);
  const heart = {...(day.heart || {}), resting_hr: rhrMode.value, hrv_ms: hrvMode.value};
  if (heart.resting_hr !== null && heart.resting_hr !== undefined) {
    let sentence = (rhrMode.rolling ? t("web.dashboard.renderToday.a553bcfca", {p2: num(heart.resting_hr, rhrMode.rolling ? 1 : 0)}) : t("web.dashboard.renderToday.8dc0ce8d0", {p2: num(heart.resting_hr, rhrMode.rolling ? 1 : 0)}));
    const delta = rhrMode.rolling ? null : recovery.rhr_delta;
    if (delta !== null && delta !== undefined && !Number.isNaN(delta)) {
      sentence = t(Math.abs(delta) < 1 ? "heart.sentence.equal" : delta > 0 ? "heart.sentence.higher" : "heart.sentence.lower",
        { p2: num(heart.resting_hr), baselineDays, p3: Math.abs(Math.round(delta)) });
    }
    if (!hrvMode.rolling && recovery.hrv_delta_pct !== null && recovery.hrv_delta_pct !== undefined && Math.abs(recovery.hrv_delta_pct) >= 10) {
      sentence += t("separator.clauses") + hrvDeltaText(recovery.hrv_delta_pct, "HRV ", baselineDays);
    }
    const hrvDelta = hrvMode.rolling ? "" : hrvDeltaText(recovery.hrv_delta_pct, "", baselineDays);
    const hrvNote = heart.hrv_ms !== null && heart.hrv_ms !== undefined
      ? (hrvMode.rolling ? t("hrv.note.rolling", { value: num(heart.hrv_ms, 1) })
        : (hrvDelta ? t("hrv.note.with_comparison", { value: num(heart.hrv_ms, 1), comparison: hrvDelta })
          : t("hrv.note.today", { value: num(heart.hrv_ms, 1) })))
      : (hrvMode.rolling ? t("web.dashboard.renderToday.b52d0ae53") : hrvDelta || null);
    addRow({
      domain: "heart", label: t("web.dashboard.renderToday.015564cd0"), metric: "resting_hr", href: "#card-heart", sentence,
      value: num(heart.resting_hr, rhrMode.rolling ? 1 : 0), unit: "bpm", compare: hrvNote, color: "var(--m-heart)",
    });
  } else {
    addRow({ domain: "heart", label: t("web.dashboard.renderToday.015564cd0"), href: "#card-heart", metric: "resting_hr", sentence: rhrMode.rolling ? t("web.dashboard.renderToday.9ff0c0d9e") : t("web.dashboard.renderToday.805788750"), value: "—", unit: "", compare: "" });
  }

  const bodyData = day.body || {};
  const workouts = day.workouts || [];
  const parts = [];
  const byName = new Map();
  for (const workout of workouts) {
    const entry = byName.get(workout.name) || { count: 0, minutes: 0 };
    entry.count += 1;
    entry.minutes += workout.duration_minutes || 0;
    byName.set(workout.name, entry);
  }
  const described = [...byName.entries()].map(([name, entry]) => (
    entry.count > 1 ? t("web.dashboard.renderToday.b0b558529", {name: name, p2: entry.count, p3: Math.round(entry.minutes)}) : t("web.dashboard.renderToday.5558c5202", {name: name, p2: Math.round(entry.minutes)})
  ));
  if (bodyData.weight_kg !== null && bodyData.weight_kg !== undefined) parts.push(t("web.dashboard.renderToday.eb0bc477e", {p1: num(bodyData.weight_kg, 1)}));
  if (described.length) parts.push(described.join(t("separator.comma")));
  const bodyValue = bodyData.weight_kg !== null && bodyData.weight_kg !== undefined
    ? { value: num(bodyData.weight_kg, 1), unit: "kg" }
    : (workouts.length ? { value: String(workouts.length), unit: t("web.dashboard.renderToday.688799852") } : { value: "—", unit: "" });
  addRow({
    domain: "body", label: t("web.dashboard.renderToday.35b081b16"), href: "#card-body",
    sentence: parts.length ? parts.join(t("separator.clauses")) : t("web.dashboard.renderToday.e96953bfc"),
    value: bodyValue.value, unit: bodyValue.unit,
    compare: described.length ? described.join(" · ") : null,
    color: "var(--m-body)",
  });

  const summaryParts = [];
  if (recovery.level_label) summaryParts.push(t("web.dashboard.renderToday.e632d5cb9", {p1: recovery.level_label}));
  const highlights = items.map((item) => item.sentence).filter(Boolean).slice(0, 3);
  if (highlights.length) summaryParts.push(highlights.join(t("separator.clauses")));
  const summaryText = summaryParts.join(t("separator.clauses"));
  const summaryEl = $("today-summary");
  if (summaryEl) {
    summaryEl.textContent = summaryText;
    summaryEl.hidden = !summaryText;
  }
  const criteriaEl = $("today-criteria");
  if (criteriaEl) criteriaEl.textContent = t("web.dashboard.renderToday.60b7b7f33");
  setHidden("today-criteria-details", false);

  if (!items.length) {
    renderSectionNotice("card-today", ["today-rows"], t("web.dashboard.renderToday.f0211c5bc"));
    return;
  }
  hideSectionNotice("card-today", ["today-rows"]);

  rows.appendChild(h("div", { class: "today-grid" }, items.map((item) => todayCard(item))));
  section.hidden = false;
}
