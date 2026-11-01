import { t, initLanguage } from "./i18n.js";
// 看板：按领域分层 —— 今天（摘要）→ 睡眠 / 活动 / 心血管 / 身体与训练 / 数据
import { get, post, toast } from "./api.js";
import { bindAI, loadAI, renderAI } from "./ai.js";
import { DASH, num, minutes, delta, clockTime, dateTitle, shortDate, percent } from "./format.js";
import {
  barChart, lineChart, stageBar, timelineRibbon,
  scatterChart, bandChart, sleepTimingChart, heatmapChart, hrDayChart, cosinorChart,
  SLEEP_STAGE_LABELS, SLEEP_STAGE_COLORS,
} from "./charts.js";

const WINDOW_DAYS = 90;   // 只用于发起请求；页面上的窗口文字一律来自 windowText()
const state = { date: null, today: null, summary: null, insights: null, status: null, extraMeta: [], syncTimer: null };
const $ = (id) => document.getElementById(id);

const KEY_LABELS = {
  stepsDaily: t("web.dashboard.labels.2986c7ee9"), caloriesDaily: t("web.dashboard.labels.93ddcd048"), distanceDaily: t("web.dashboard.labels.3865483de"), floorsDaily: t("web.dashboard.labels.2748d6abc"),
  activeMinutesDaily: t("web.dashboard.labels.301220d3d"), zoneMinutesDaily: t("web.dashboard.labels.9de8f1ef7"), sedentaryDaily: t("web.dashboard.labels.4c734a93f"),
  weightDaily: t("web.dashboard.labels.38f7c7e22"), fatDaily: t("web.dashboard.labels.a852a5db0"), waterDaily: t("web.dashboard.labels.6edae041a"), nutritionDaily: t("web.dashboard.labels.cc8fbb837"),
  coreTemperatureDaily: t("web.dashboard.labels.480a98dd5"), restingHeartRaw: t("web.dashboard.labels.e9a48bd3e"), hrvRaw: t("web.dashboard.labels.303122175"),
  spo2Raw: t("web.dashboard.labels.2836400cc"), breathingRaw: t("web.dashboard.labels.f3261e58d"), skinTemperatureRaw: t("web.dashboard.labels.a3bc489c3"), cardioRaw: t("web.dashboard.labels.01d2cc5a9"),
  sleepRaw: t("web.dashboard.labels.01324e540"), activitiesRaw: t("web.dashboard.labels.ae4d58abf"), ecgRaw: t("web.dashboard.labels.44f9d177f"),
  irnAlertsRaw: t("web.dashboard.labels.d303d342b"), glucoseRaw: t("web.dashboard.labels.55c7f0f25"), stepsIntradayRaw: t("web.dashboard.labels.98a28007d"), heartIntradayRaw: t("web.dashboard.labels.ccb692b22"),
  devicesRaw: t("web.dashboard.labels.6c07bb6fd"), identity: t("web.dashboard.labels.34e5636c6"), profileRaw: t("web.dashboard.labels.d6568d8f5"), settingsRaw: t("web.dashboard.labels.a720b4000"),
  userInfo: t("web.dashboard.labels.426c4548f"), irnProfileRaw: t("web.dashboard.labels.9e7061fe1"),
};

// ---------- 基础 ----------
function h(tag, attrs = {}, children = []) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value === null || value === undefined || value === false) continue;
    if (key === "text") node.textContent = value;
    else if (key === "class") node.className = value;
    else node.setAttribute(key, value);
  }
  for (const child of children) {
    if (child === null || child === undefined || child === false) continue;
    node.appendChild(typeof child === "string" ? document.createTextNode(child) : child);
  }
  return node;
}
function clear(node) { while (node.firstChild) node.removeChild(node.firstChild); }
function setHidden(id, hidden) { const node = $(id); if (node) node.hidden = hidden; }
function numSpan(text) { return h("span", { class: "num", text }); }
function clockFromDecimal(value) {
  if (value === null || value === undefined) return DASH;
  const hours = Math.floor(value) % 24;
  const mins = Math.min(59, Math.round((value - Math.floor(value)) * 60));
  return `${String(hours).padStart(2, "0")}:${String(mins).padStart(2, "0")}`;
}
function isoShort(value) { return value ? String(value).slice(0, 10) : DASH; }
function agoText(iso) {
  if (!iso) return "";
  const time = new Date(iso).getTime();
  if (Number.isNaN(time)) return "";
  const mins = Math.max(0, Math.round((Date.now() - time) / 60000));
  if (mins < 60) return t("web.dashboard.agoText.4ba0128b1", {mins: mins});
  const hours = Math.round(mins / 60);
  if (hours < 48) return t("web.dashboard.agoText.c430f0983", {hours: hours});
  return t("web.dashboard.agoText.ace2d01fc", {p1: Math.round(hours / 24)});
}
function line(label, valueNodes, note = null, noteClass = "", spark = null, metric = null) {
  return h("div", { class: "line" }, [
    h("span", { class: "line-label" }, [label, metricBadge(metric)]),
    h("span", { class: "line-value" }, Array.isArray(valueNodes) ? valueNodes : [valueNodes]),
    (spark || note)
      ? h("span", { class: "line-tail" }, [
          spark || null,
          note ? h("span", { class: `line-note ${noteClass}`.trim(), text: note }) : null,
        ])
      : null,
  ]);
}
function linesBlock(children) {
  const box = h("div", { class: "lines" });
  for (const child of children) if (child) box.appendChild(child);
  return box;
}

// ---------- 表格 ----------
function table(headers, rows, className = "") {
  const tableNode = h("table", { class: `data ${className}`.trim() });
  const thead = h("thead");
  const headRow = h("tr");
  for (const header of headers) {
    headRow.appendChild(h("th", { class: header.num ? "num" : "", text: header.label }));
  }
  thead.appendChild(headRow);
  tableNode.appendChild(thead);
  const tbody = h("tbody");
  for (const row of rows) {
    const tr = h("tr", { class: row.strong ? "row-strong" : "" });
    for (const cell of row.cells) {
      const td = h("td", {
        class: [cell.num ? "num" : "", cell.class || ""].filter(Boolean).join(" "),
        // 窄屏会隐藏表头并把行拆成两行，单元格自带标签才读得懂
        "data-label": cell.label || null,
      });
      if (cell.node) td.appendChild(cell.node);
      else td.textContent = cell.text ?? DASH;
      tr.appendChild(td);
    }
    tbody.appendChild(tr);
  }
  tableNode.appendChild(tbody);
  return h("div", { class: "table-wrap" }, [tableNode]);
}

const METRIC_DOMAIN = {
  sleep_minutes: "sleep",
  deep_minutes: "sleep",
  sleep_efficiency: "sleep",
  sleep_mid: "sleep",
  steps: "activity",
  active_minutes: "activity",
  sedentary_minutes: "activity",
  resting_hr: "heart",
  hrv_ms: "heart",
};
const DOMAIN_LABELS = { sleep: t("web.dashboard.labels.01324e540"), activity: t("web.dashboard.labels.9e9bead81"), heart: t("web.dashboard.labels.015564cd0"), body: t("web.dashboard.labels.35b081b16") };

// 数据不足时保留区块编号与标题，正文换成一句事实（不整块消失，避免编号跳号）
function renderSectionNotice(sectionId, bodyIds, text) {
  const section = $(sectionId);
  if (!section) return;
  for (const id of bodyIds) {
    const node = $(id);
    if (node) { clear(node); node.hidden = true; }
  }
  let note = section.querySelector(".section-empty");
  if (!note) {
    note = h("p", { class: "section-empty" });
    section.appendChild(note);
  }
  note.textContent = text;
  note.hidden = false;
  section.hidden = false;
}

function hideSectionNotice(sectionId, bodyIds) {
  const section = $(sectionId);
  if (!section) return;
  for (const id of bodyIds) {
    const node = $(id);
    if (node) node.hidden = false;
  }
  const note = section.querySelector(".section-empty");
  if (note) note.hidden = true;
}

function signalFor(insights, key) {
  const signals = (insights && insights.recovery && insights.recovery.signals) || [];
  return signals.find((signal) => signal.key === key) || null;
}

function stateLabel(signal) {
  return signal && signal.state_label ? signal.state_label : "";
}

function stateClass(signal) {
  if (!signal) return "";
  if (signal.state === "low") return "state-warn";
  if (signal.state === "good") return "state-ok";
  return "";
}

// 与基线的差只有这一处措辞：0 附近写「持平」，且窗口必须带上（否则读者不知道跟哪个基线比）。
// days 传 null 时才退化为裸「基线」，正常情况下调用方都要给窗口。
function baselineWindowText(days) {
  return Number.isFinite(days) ? t("web.dashboard.baselineWindowText.f6429626a", {days: days}) : "";
}

function hrvDeltaText(pct, prefix = "HRV", days = null) {
  if (pct === null || pct === undefined || Number.isNaN(pct)) return "";
  const win = baselineWindowText(days);
  if (Math.abs(pct) < 1) return t("web.dashboard.hrvDeltaText.f5866e0cc", {prefix: prefix, win: win});
  return (pct > 0 ? t("web.dashboard.hrvDeltaText.6575d7a8a", {prefix: prefix, win: win, p4: Math.abs(Math.round(pct))}) : t("web.dashboard.hrvDeltaText.2f9d16702", {prefix: prefix, win: win, p4: Math.abs(Math.round(pct))}));
}

function rhrDeltaText(delta, days = null) {
  if (delta === null || delta === undefined || Number.isNaN(delta)) return "";
  const win = baselineWindowText(days);
  if (Math.abs(delta) < 1) return t("web.dashboard.rhrDeltaText.ae8a4db25", {win: win});
  return (delta > 0 ? t("web.dashboard.rhrDeltaText.ee699df67", {win: win, p3: Math.abs(Math.round(delta))}) : t("web.dashboard.rhrDeltaText.610240067", {win: win, p3: Math.abs(Math.round(delta))}));
}

// 标签必须跟着后端实际分析的窗口走，否则同一屏会出现「近 30 天」和「73 个点」互相矛盾。
function windowText() {
  const insights = state.insights || {};
  // 用后端回显的「请求窗口」而不是「有数据的天数」：两者不是一回事，
  // 混用会让同一张图旁边出现「近 85 天」和「73 个点」两个数打架。
  const days = Number.isFinite(insights.window_days) ? insights.window_days
    : (Number.isFinite(insights.n_days) ? insights.n_days : null);
  return days ? t("web.dashboard.windowText.f6429626a", {days: days}) : t("web.dashboard.windowText.474da3018");
}

// 各区块的内部窗口也由后端回显（insights.windows.<用途>），前端不维护任何窗口字面量。
function windowOf(kind, fallback = null) {
  const insights = state.insights || {};
  const windows = insights.windows || {};
  return Number.isFinite(windows[kind]) ? windows[kind] : fallback;
}

// ---------- 01 今天 ----------
// 「今天」用卡片：四领域各一张，无边框，用背景色差 + 圆角分隔
// 各领域「今天」共用同一形态：一排卡片（同一个概念不在不同区块换写法）
function cardGrid(cards, columns = 4) {
  const grid = h("div", { class: `today-grid cols-${columns}` });
  for (const item of cards) grid.appendChild(todayCard(item));
  return grid;
}

function metricDisplay(key, daily) {
  const analysis = state.summary?.analysis || {};
  const rolling = analysis.display_mode?.[key] === "rolling7";
  return {rolling, value: rolling ? (analysis.display_values?.[key] ?? null) : daily};
}

function metricBadge(key) {
  return key && metricDisplay(key, null).rolling ? h("span", {class: "tag metric-mode", text: t("web.dashboard.metricBadge.8f9021461")}) : null;
}

function todayCard(item) {
  return h("a", { class: "today-cell today-card", href: item.href, "aria-label": item.aria || `${item.label}：${item.sentence}` }, [
    h("div", { class: "today-card-label" }, [item.label, metricBadge(item.metric)]),
    h("div", { class: "today-card-value" }, [
      h("span", { class: "ring-value", text: item.value }),
      item.unit ? h("span", { class: "ring-unit", text: item.unit }) : null,
    ]),
    item.compare ? h("div", { class: "today-card-note", text: item.compare }) : null,
  ]);
}

function renderToday(day, insights, goals, analysis) {
  const section = $("card-today");
  const rows = $("today-rows");
  clear(rows);
  const recovery = (insights && insights.recovery) || {};
  const progress = (analysis && analysis.goal_progress) || {};
  const baselineDays = windowOf("baseline", 28);

  // 顶部一句话：只保留恢复判定，不再复述各领域数字
  $("today-summary").textContent = recovery.level_label ? t("web.dashboard.renderToday.e632d5cb9", {p1: recovery.level_label}) : "";
  $("today-criteria").textContent = t("web.dashboard.renderToday.60b7b7f33");

  const items = [];
  // 每个领域既有一句完整的话（给读屏与无障碍），也有一个"大字 + 一行对比"的短形态（给视觉）。
  // 约束：卡片取值一律复用已有的合并/派生结果（如 described、hrvDeltaText），
  // 不直接访问原始数组、不手拼句子——这两处绕过已经各引发过一次回归。
  const addRow = (item) => {
    if (!item || !item.value) return;
    items.push({
      aria: item.label ? `${item.label}：${item.sentence}` : undefined,
      ...item,
    });
  };

  // ① 睡眠：主语是「昨夜」，先给数值再给比较
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
    // 大数字给精确时长（不截断），说明里只放"目标 + 差额"——
    // 否则同一个数会在一张卡里出现两次、精度还不同，读者也无法从差额反推总量。
    // 不依赖"total_minutes 一定是整数"这个只写在后端类型上的前提：
    // 先按分钟取整，再由分钟数进位到小时，任何输入都不会印出「小时 60 分」。
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

  // ③ 心血管：主指标是静息心率；HRV 只在显著偏离（≥10%）时补一句
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
    // 显示阈值（只在显著偏离时补一句）保留在首屏，但措辞与持平判定复用同一个函数
    if (!hrvMode.rolling && recovery.hrv_delta_pct !== null && recovery.hrv_delta_pct !== undefined && Math.abs(recovery.hrv_delta_pct) >= 10) {
      sentence += t("separator.clauses") + hrvDeltaText(recovery.hrv_delta_pct, "HRV ", baselineDays);
    }
    // 措辞与持平判定必须复用同一个函数：手拼会同时丢掉窗口、并把「低于基线 0%」放回来
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

  // ④ 身体与训练：一句话里两件事，用分号分隔
  const bodyData = day.body || {};
  const workouts = day.workouts || [];
  const parts = [];
  // 同名训练先合并成「步行 ×2」，句子和卡片说明共用这一份结果
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
  if (described.length) parts.push(described.join(t('separator.comma')));
  const bodyValue = bodyData.weight_kg !== null && bodyData.weight_kg !== undefined
    ? { value: num(bodyData.weight_kg, 1), unit: "kg" }
    : (workouts.length ? { value: String(workouts.length), unit: t("web.dashboard.renderToday.688799852") } : { value: "—", unit: "" });
  addRow({
    domain: "body", label: t("web.dashboard.renderToday.35b081b16"), href: "#card-body",
    sentence: parts.length ? parts.join(t('separator.clauses')) : t("web.dashboard.renderToday.e96953bfc"),
    value: bodyValue.value, unit: bodyValue.unit,
    // 用上面的合并结果（同名训练已折成「步行 ×2」），构成写全：几项就写几项
    compare: described.length ? described.join(" · ") : null,
    color: "var(--m-body)",
  });

  if (!items.length) {
    renderSectionNotice("card-today", ["today-rows"], t("web.dashboard.renderToday.f0211c5bc"));
    return;
  }
  hideSectionNotice("card-today", ["today-rows"]);

  rows.appendChild(h("div", { class: "today-grid" }, items.map((item) => todayCard(item))));
  rows.appendChild(h("p", { class: "today-hint", text: t("web.dashboard.renderToday.4e576115e") }));
  // 这一块不需要右侧标签：标题已经是「今天」，再写「今日」是同义重复；
  // 其它块的标签是有信息量的（昨夜/今天/近 14 天/当日），不一样。
  section.hidden = false;
}

// ---------- 共用：单指标趋势（值 + 7 日均值 ±1σ，一张图） ----------
function trendBlock(key, { title, unit, color, format, goal = null, step = null }) {
  const windowLabel = windowText();
  const insights = state.insights || {};
  const bands = insights.bands || {};
  const points = (bands[key] || []).map((entry) => ({ ...entry, label: shortDate(entry.date) }));
  if (points.filter((p) => p.value !== null && p.value !== undefined).length < 7) return null;
  const wrap = h("div");
  wrap.appendChild(h("div", { class: "trend-head" }, [
    h("span", { class: "trend-title", text: title }),
    h("span", { class: "trend-value", text: unit }),
  ]));
  const bandDays = windowOf("band", 7);
  const chart = bandChart({
    points,
    color,
    goal,
    yFormat: format || ((value) => num(Math.round(value))),
    tickStep: step,
    ariaLabel: t("web.dashboard.trendBlock.690ae5c4c", {title: title, windowLabel: windowLabel, bandDays: bandDays}),
  });
  if (chart) wrap.appendChild(chart);
  // 带外统计由后端按指标下发（bands_outside.<key>），前端只渲染、不统计
  const outside = ((state.insights && state.insights.bands_outside) || {})[key] || null;
  // 把「窗口」「有数据的天数」「带外天数」三件事在一句话里说清，避免看起来互相矛盾
  // 三种天数不是一回事：窗口（windowLabel）、这条统计的分母（outside.points＝可评估天数）、
  // 带外天数（outside.outside）。按「这句话在描述什么」选词，别混用。
  const countSentence = outside
    ? t("web.dashboard.trendBlock.c85c9f79a", {windowLabel: windowLabel, p2: outside.points, p3: outside.outside, p4: outside.percent, bandDays: bandDays})
    : t("web.dashboard.trendBlock.42caf8551", {bandDays: bandDays});
  wrap.appendChild(h("p", {
    class: "hintline",
    text: t("web.dashboard.trendBlock.0fc6994e3", {countSentence: countSentence}),
  }));
  return wrap;
}

function achievementsBlock(key, goalText, title) {
  const insights = state.insights || {};
  // 窗口、总天数、达成天数都来自后端 achievements_summary；前端只负责画格子。
  const summary = (insights.achievements_summary || {})[key] || null;
  if (!summary || !Number.isFinite(summary.window_days) || summary.window_days < 7) return null;
  const all = (insights.achievements || []).slice(-summary.window_days);
  if (!all.length) return null;
  const wrap = h("div", { class: "achievements" });
  wrap.appendChild(h("div", { class: "mini-head" }, [
    h("h3", { text: t("web.dashboard.achievementsBlock.4bf6e2db3", {p1: title || t("web.dashboard.achievementsBlock.57060c88a"), p2: summary.window_days}) }),
    h("span", { text: t("web.dashboard.achievementsBlock.ce57c8e77", {p1: summary.achieved, p2: summary.total}) }),
  ]));
  wrap.appendChild(stripFor("", all, key, goalText));
  wrap.appendChild(h("div", { class: "strip-legend" }, [
    h("span", {}, [h("i", { class: "sw done" }), t("web.dashboard.achievementsBlock.09dfac88c")]),
    h("span", {}, [h("i", { class: "sw partial" }), t("web.dashboard.achievementsBlock.fa6104f7c")]),
    h("span", {}, [h("i", { class: "sw" }), t("web.dashboard.achievementsBlock.3b34e0416")]),
    h("span", { class: "dates", text: `${all[0].date} → ${all[all.length - 1].date}` }),
  ]));
  return wrap;
}

function stripFor(label, achievements, key, goalText) {
  const wrap = h("div", { class: "strip" });
  if (label) wrap.appendChild(h("span", { class: "strip-label", text: label }));
  const cells = h("span", { class: "strip-cells" });
  for (const entry of achievements) {
    const ratio = entry[key];
    let klass = "strip-cell";
    if (ratio !== null && ratio !== undefined) {
      if (ratio >= 1) klass += " done";
      else if (ratio >= 0.85) klass += " partial";
    }
    const cell = h("span", { class: klass });
    cell.title = ratio === null || ratio === undefined
      ? t("web.dashboard.stripFor.12652ca4c", {p1: entry.date})
      : (goalText ? t("achievement.goal", { date: entry.date, percent: Math.round(ratio * 100), goal: goalText })
        : t("achievement.percent", { date: entry.date, percent: Math.round(ratio * 100) }));
    cells.appendChild(cell);
  }
  wrap.appendChild(cells);
  return wrap;
}

// 散点坐标轴的单位写法（重构时随旧渲染器一起被删掉，恢复散点时补回）
const METRIC_AXIS = {
  sleep_minutes: (value) => t("web.dashboard.labels.57727ff02", {p1: (value / 60).toFixed(1)}),
  deep_minutes: (value) => t("web.dashboard.labels.57727ff02", {p1: (value / 60).toFixed(1)}),
  sleep_mid: (value) => clockFromDecimal(value),
  steps: (value) => t("web.dashboard.labels.b944468eb", {p1: num(Math.round(value / 100) / 10)}),
  active_minutes: (value) => t("web.dashboard.labels.bdf6eec02", {p1: Math.round(value)}),
  sedentary_minutes: (value) => t("web.dashboard.labels.da5fc9dcf", {p1: Math.round(value / 60)}),
  resting_hr: (value) => `${Math.round(value)} bpm`,
  hrv_ms: (value) => `${Math.round(value)} ms`,
  sleep_efficiency: (value) => `${Math.round(value)}%`,
};

// ---------- 关系（挂在结果指标所在领域页） ----------
function relationsBlock(domain, title = t("web.dashboard.relationsBlock.e135989fc")) {
  const insights = state.insights || {};
  const inDomain = (correlation) => METRIC_DOMAIN[correlation.y] === domain;
  const passing = (insights.correlations || []).filter(inDomain);
  const candidates = (insights.correlations_candidates || []).filter(inDomain);
  const block = h("div", { class: "figure-block" });
  const tableOnly = t("web.dashboard.relationsBlock.3b2f3e717");
  block.appendChild(h("div", { class: "chart-head" }, [
    h("span", { class: "chart-title", text: title }),
    h("span", { class: "chart-note", text: passing.length
      ? t("web.dashboard.relationsBlock.c6c53ca8c", {p1: insights.n_days || 0, tableOnly: tableOnly})
      : t("web.dashboard.relationsBlock.15af8c94d", {p1: insights.n_days || 0}) }),
  ]));

  if (!candidates.length && !passing.length) {
    block.appendChild(h("p", { class: "section-empty", text: t("web.dashboard.relationsBlock.930a49bd5", {p1: DOMAIN_LABELS[domain] || t("web.dashboard.relationsBlock.a7a1ae76e")}) }));
    return block;
  }

  // 散点始终画；只有通过校正的那条才带 r / 斜率等解读
  const series = insights.series || [];
  const scatterGrid = h("div", { class: "scatter-grid" });
  for (const correlation of candidates.slice(0, 2)) {
    const pairs = [];
    for (let index = 0; index < series.length; index += 1) {
      const target = index + (correlation.offset_days || 0);
      if (target >= series.length) break;
      const x = series[index][correlation.x];
      const y = series[target][correlation.y];
      if (x === null || x === undefined || y === null || y === undefined) continue;
      pairs.push({ x, y, label: `${series[index].date} · ${num(x)} → ${num(y, 1)}` });
    }
    if (pairs.length < 3) continue;
    // 能不能画线由数据形状决定：后端只给通过校正的配对下发 slope，未通过的是 null。
    // 于是"是否带解读"只需判一次 slope，不必再判 survives_correction —— 判断只有一个来源。
    const slope = correlation.slope ?? null;
    let intercept = null;
    if (slope !== null) {
      const meanX = pairs.reduce((sum, pair) => sum + pair.x, 0) / pairs.length;
      const meanY = pairs.reduce((sum, pair) => sum + pair.y, 0) / pairs.length;
      intercept = meanY - slope * meanX;
    }
    const chart = scatterChart({
      points: pairs,
      xFormat: METRIC_AXIS[correlation.x] || ((value) => num(value)),
      yFormat: METRIC_AXIS[correlation.y] || ((value) => num(value)),
      slope,
      intercept,
      annotation: slope !== null ? `r = ${num(correlation.r, 2)} · n = ${correlation.n}` : null,
      ariaLabel: slope !== null
        ? t("web.dashboard.relationsBlock.d9c5f2e97", {p1: correlation.x_label, p2: correlation.y_label, p3: num(correlation.r, 2), p4: correlation.n})
        : t("web.dashboard.relationsBlock.1ac706ef6", {p1: correlation.x_label, p2: correlation.y_label, p3: correlation.n}),
    });
    if (!chart) continue;
    scatterGrid.appendChild(h("div", { class: "insight-item" }, [
      h("div", { class: "insight-title", text: `${correlation.x_label} × ${correlation.y_label}` }),
      chart,
    ]));
  }
  if (scatterGrid.childElementCount) block.appendChild(scatterGrid);

  if (!passing.length) return block;

  block.appendChild(table(
    [{ label: t("web.dashboard.relationsBlock.253aaff19") }, { label: "r", num: true }, { label: "95% CI", num: true }, { label: "n", num: true }],
    passing.map((correlation) => ({
      cells: [
        { text: correlation.relation
          ? `${correlation.x_label} ${correlation.relation} ${correlation.y_label}`
          : t("web.dashboard.relationsBlock.38e7b0e26", {p1: correlation.x_label, p2: correlation.y_label}) },
        { text: (correlation.r > 0 ? `${"+"}${Number(correlation.r).toFixed(2).replace("-", "−")}` : `${""}${Number(correlation.r).toFixed(2).replace("-", "−")}`), num: true },
        { text: correlation.ci_low !== null ? t("web.dashboard.relationsBlock.5517b36d0", {p1: Number(correlation.ci_low).toFixed(2).replace("-", "−"), p2: Number(correlation.ci_high).toFixed(2).replace("-", "−")}) : DASH, num: true, label: "95% CI" },
        { text: num(correlation.n), num: true, label: "n" },
      ],
    })),
    "corr",
  ));
  return block;
}

// ---------- 02 睡眠 ----------
function renderSleepPage(day, insights, trends, goals) {
  const section = $("card-sleep");
  const sleep = day.sleep || {};
  const recovery = (insights && insights.recovery) || {};
  const rhythm = (insights && insights.rhythm) || {};
  const timing = (insights && insights.timing) || [];
  const hasNight = sleep.total_minutes !== null && sleep.total_minutes !== undefined;

  // ① 昨晚
  const nightBody = $("sleep-body");
  clear(nightBody);
  if (hasNight) {
    const stages = sleep.stages || {};
    const stageText = ["deep", "light", "rem", "wake"].filter((key) => stages[key])
      .map((key) => t("web.dashboard.renderSleepPage.f29f9494a", {p1: SLEEP_STAGE_LABELS[key], p2: stages[key]})).join(t('separator.list'));
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
  // state === "unknown" 时后端仍会给一个 "—" 占位标签；按"缺失就省略那一项"的规则过滤掉，
  // 不能印破折号。
  const signals = (recovery.signals || []).filter((signal) => signal.label && signal.state && signal.state !== "unknown");
  if (signals.length) {
    signalHost.appendChild(h("p", { class: "signal-line" }, signals.map((signal, index) => h("span", {}, [
      index ? h("span", { class: "signal-sep", text: " · " }) : null,
      `${signal.label} `,
      h("span", { class: stateClass(signal) || "", text: signal.state_label }),
    ]))));
  }
  // 睡眠块首个数是"昨夜睡了多久"→ 标签写昨夜；聚合窗口由块内各自数字自带
  $("sleep-note").textContent = t("web.dashboard.renderSleepPage.6b758fc66");

  // ② 趋势层：一张图（值 + 7 日均值 ±1σ + 目标线）+ 达标天数
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

  // ③ 作息规律
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

  // ④ 当晚明细（可折叠）
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

  // 指针：睡眠相关的关系挂在心血管页（结果是心率/HRV）
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

// ---------- 03 活动 ----------
function renderActivityPage(day, insights, trends, goals) {
  const section = $("card-activity");
  const activity = day.activity || {};
  const load = (insights && insights.load) || {};
  const rhythm = (insights && insights.rhythm) || {};
  const cosinor = rhythm.cosinor || {};
  const shortDays = Number.isFinite(load.window_days) ? load.window_days : windowOf("short", 7);

  // ① 今天：与 01 同一形态（一排卡片），不再用"标题 + 三句文字"
  const todayHost = $("activity-body");
  clear(todayHost);
  const mvpa = activity.active_minutes;
  const actCards = [];
  if (activity.steps !== null && activity.steps !== undefined) {
    actCards.push({
      label: t("web.dashboard.renderActivityPage.2986c7ee9"), value: num(activity.steps), unit: t("web.dashboard.renderActivityPage.a621b7cd7"),
      compare: goals.steps ? t("web.dashboard.renderActivityPage.a3bb0b826", {p1: num(goals.steps)}) : null, href: "#card-activity",
    });
  }
  if (activity.total_active_minutes !== null && activity.total_active_minutes !== undefined && activity.total_active_minutes > mvpa) {
    actCards.push({ label: t("web.dashboard.renderActivityPage.e85e590b6"), value: num(activity.total_active_minutes), unit: t("web.dashboard.renderActivityPage.bd957bc49"), compare: mvpa !== null ? t("web.dashboard.renderActivityPage.ffb94be1a", {p1: num(mvpa)}) : null, href: "#card-activity" });
  } else if (mvpa !== null && mvpa !== undefined) {
    actCards.push({ label: t("web.dashboard.renderActivityPage.2c1af931a"), value: num(mvpa), unit: t("web.dashboard.renderActivityPage.bd957bc49"), compare: null, href: "#card-activity" });
  }
  if (activity.sedentary_minutes !== null && activity.sedentary_minutes !== undefined) {
    actCards.push({ label: t("web.dashboard.renderActivityPage.aa1b957b8"), value: num(Math.round(activity.sedentary_minutes)), unit: t("web.dashboard.renderActivityPage.bd957bc49"), compare: null, href: "#card-activity" });
  }
  if (activity.distance_km !== null && activity.distance_km !== undefined) {
    actCards.push({ label: t("web.dashboard.renderActivityPage.3865483de"), value: num(activity.distance_km, 2), unit: "km", compare: null, href: "#card-activity" });
  }
  if (actCards.length) todayHost.appendChild(cardGrid(actCards, Math.min(4, actCards.length)));
  else todayHost.appendChild(h("p", { class: "section-empty", text: t("web.dashboard.renderActivityPage.a3e40865b") }));
  // 凡卡片给"今日值"的指标，都补一句它的时间聚合（否则读者会以为它没有日均这一说）
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
  // 标题行只放窗口标签
  $("activity-note").textContent = t("web.dashboard.renderActivityPage.d5f5a7a01");

  // ② 趋势：一张图 + 达成天数
  const trendBody = $("activity-trend-body");
  clear(trendBody);
  const stepsTrend = trendBlock("steps", { title: t("web.dashboard.renderActivityPage.2986c7ee9"), unit: t("web.dashboard.renderActivityPage.24363e22b"), color: "var(--m-steps)", goal: goals.steps || null });
  const activeTrend = trendBlock("active_minutes", { title: t("web.dashboard.renderActivityPage.301220d3d"), unit: t("web.dashboard.renderActivityPage.6ff10e192"), color: "var(--m-steps)" });
  const trendSplit = h("div", { class: "chart-split" });
  if (stepsTrend) trendSplit.appendChild(stepsTrend);
  if (activeTrend) trendSplit.appendChild(activeTrend);
  if (trendSplit.childElementCount) trendBody.appendChild(trendSplit);
  else trendBody.appendChild(h("p", { class: "section-empty", text: t("web.dashboard.renderActivityPage.5e6a574fc") }));
  const trendChart = stepsTrend || activeTrend;
  const stepsStrip = achievementsBlock("steps_ratio", goals.steps ? t("web.dashboard.renderActivityPage.e82f2306a", {p1: num(goals.steps)}) : t("web.dashboard.renderActivityPage.065cbd7d1"), t("web.dashboard.renderActivityPage.065cbd7d1"));
  if (stepsStrip) trendBody.appendChild(stepsStrip);
  $("activity-trend-note").textContent = "";

  // ③ 规律：工作日/周末 + 节律峰值（拟合不可用就整句不出现）+ WHO 折算
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
      // who_weighted_minutes 是滚动近 7 天（不是自然周），标签必须跟着走
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

  // ④ 明细：距离/爬楼/消耗 + 周内步数 + 热图
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

  // 关系指针：活动相关的关系挂在结果指标所属页
  const pointer = $("activity-pointer");
  const related = (insights && insights.correlations || []).filter((c) => METRIC_DOMAIN[c.y] === "activity");
  pointer.textContent = related.length ? t("web.dashboard.renderActivityPage.d8bc93a82") : "";
  pointer.hidden = !related.length;

  section.hidden = !actCards.length && !trendChart;
}

// ---------- 04 心血管 ----------
function renderHeartPage(day, insights, trends, goals) {
  const section = $("card-heart");
  const rawHeart = day.heart || {};
  const rhrMode = metricDisplay("resting_hr", rawHeart.resting_hr);
  const hrvMode = metricDisplay("hrv_ms", rawHeart.hrv_ms);
  const heart = {...rawHeart, resting_hr: rhrMode.value, hrv_ms: hrvMode.value};
  const recovery = (insights && insights.recovery) || {};
  const baselineDays = windowOf("baseline", 28);

  // ① 今天：与其它区块同一形态（卡片）
  const todayHost = $("heart-today-body");
  clear(todayHost);
  const heartCards = [];
  if (rhrMode.rolling || (heart.resting_hr !== null && heart.resting_hr !== undefined)) {
    heartCards.push({
      label: t("web.dashboard.renderHeartPage.e9a48bd3e"), metric: "resting_hr", value: num(heart.resting_hr, rhrMode.rolling ? 1 : 0), unit: "bpm",
      compare: rhrMode.rolling ? (heart.resting_hr == null ? t("web.dashboard.renderHeartPage.26f705334") : null) : rhrDeltaText(recovery.rhr_delta, baselineDays) || null, href: "#card-heart",
    });
  }
  if (hrvMode.rolling || (heart.hrv_ms !== null && heart.hrv_ms !== undefined)) {
    heartCards.push({
      label: "HRV", metric: "hrv_ms", value: num(heart.hrv_ms, 1), unit: "ms",
      compare: hrvMode.rolling ? (heart.hrv_ms == null ? t("web.dashboard.renderHeartPage.26f705334") : null) : hrvDeltaText(recovery.hrv_delta_pct, "", baselineDays) || null, href: "#card-heart",
    });
  }
  if (heartCards.length) todayHost.appendChild(cardGrid(heartCards, Math.min(4, heartCards.length)));
  else todayHost.appendChild(h("p", { class: "section-empty", text: t("web.dashboard.renderHeartPage.4cac2f022") }));

  // ② 趋势：两张图（静息心率 / HRV），各自带自己的带外统计
  const trendBody = $("heart-trend-body");
  clear(trendBody);
  const rhrChart = trendBlock("resting_hr", { title: t("web.dashboard.renderHeartPage.e9a48bd3e"), unit: "bpm", color: "var(--m-heart)" });
  const hrvChart = trendBlock("hrv_ms", { title: "HRV", unit: "ms", color: "var(--m-sleep-deep)" });
  const split = h("div", { class: "chart-split" });
  if (rhrChart) split.appendChild(rhrChart);
  if (hrvChart) split.appendChild(hrvChart);
  if (split.childElementCount) trendBody.appendChild(split);
  if (!rhrChart && !hrvChart) trendBody.appendChild(h("p", { class: "section-empty", text: t("web.dashboard.renderHeartPage.5e6a574fc") }));
  $("heart-note").textContent = t("web.dashboard.renderHeartPage.6b758fc66");

  // ④ 明细：夜间信号 + 当日心率曲线
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
  if (heart.hr_min !== null || heart.hr_max !== null) detailRows.push(line(t("web.dashboard.renderHeartPage.825e9b7ae"), [numSpan(`${num(heart.hr_min)} – ${num(heart.hr_max)}`), " bpm"]));
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

  // 关系（结果指标属于心血管的全部放这里）
  const relHost = $("heart-relations");
  clear(relHost);
  relHost.appendChild(relationsBlock("heart"));

  section.hidden = !heartCards.length && !rhrChart && !hrvChart;
}

// ---------- 05 身体与训练 ----------
function renderBodyPage(day, insights, trends) {
  const section = $("card-body");
  const bodyData = day.body || {};
  const workouts = day.workouts || [];

  // ① 今天：与其它区块同一形态（卡片），身体与训练各一张
  const todayHost = $("body-today");
  clear(todayHost);
  const bodyCards = [];
  if (bodyData.weight_kg !== null && bodyData.weight_kg !== undefined) {
    bodyCards.push({ label: t("web.dashboard.renderBodyPage.4729deedd"), value: num(bodyData.weight_kg, 1), unit: "kg", compare: bodyData.body_fat_pct !== null && bodyData.body_fat_pct !== undefined ? t("web.dashboard.renderBodyPage.7067e5404", {p1: num(bodyData.body_fat_pct, 1)}) : null, href: "#card-body" });
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
    bodyCards.push({ label: t("web.dashboard.renderBodyPage.a29c0de14"), value: String(workouts.length), unit: t("web.dashboard.renderBodyPage.172fb7e67"), compare: described.join(" · ") || null, href: "#card-body" });
  }
  if (bodyCards.length) todayHost.appendChild(cardGrid(bodyCards, Math.min(4, bodyCards.length)));
  else todayHost.appendChild(h("p", { class: "section-empty", text: t("web.dashboard.renderBodyPage.89688706d") }));

  const trendHost = $("body-trend");
  clear(trendHost);
  const days = Number.isFinite(windowOf("summary_trend")) ? windowOf("summary_trend") : 14;
  const points = (trends || []).map((entry) => ({ label: shortDate(entry.date), value: entry.weight_kg ?? null }));
  const valid = points.filter((point) => point.value !== null && point.value !== undefined);
  let weightChart = null;
  if (valid.length >= 2) {
    weightChart = lineChart({ points, color: "var(--m-body)", unit: " kg", width: 640, height: 120, ariaLabel: t("web.dashboard.renderBodyPage.5a8a11d92", {days: days}) });
  }
  if (weightChart) {
    // 窗口跟着它描述的那个容器走：趋势块自己声明「近 N 天」，不占用标题行
    trendHost.appendChild(h("div", { class: "chart-head" }, [
      h("span", { class: "chart-title", text: t("web.dashboard.renderBodyPage.38f7c7e22") }),
      h("span", { class: "chart-note", text: t("web.dashboard.renderBodyPage.951e6a5fe", {days: days, p2: valid.length}) }),
    ]));
    trendHost.appendChild(weightChart);
    $("body-trend-block").hidden = false;
  } else {
    $("body-trend-block").hidden = true;
  }

  // ④ 明细：饮水/摄入 + 训练列表
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
  // 标题行标签与"今天"这个容器同口径：今日体重是最近一次记录
  $("body-note").textContent = (bodyData.weight_kg !== null && bodyData.weight_kg !== undefined) ? t("web.dashboard.renderBodyPage.c7ca79caf") : "";

  section.hidden = !bodyCards.length && !weightChart;
}

// ---------- 06 数据 ----------
function renderDataPage(day, insights, goals, analysis) {
  const section = $("card-data");
  let visible = 0;

  // 时间轴
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

  // 周汇总：这是"每周"数值的唯一出处（各领域不再重复一句"本周…"）
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

  // 覆盖度
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
        text: (comparison.insufficient_reason ? t("web.dashboard.renderDataPage.d2d96ec04", {p1: baselineLabels[key] || key, p2: comparison.valid_from, p3: comparison.sample_count, p4: `；${comparison.insufficient_reason}`}) : t("web.dashboard.renderDataPage.e493d478a", {p1: baselineLabels[key] || key, p2: comparison.valid_from, p3: comparison.sample_count})) }));
      visible += 1;
    }

  }

  $("data-note-label").textContent = t("web.dashboard.renderDataPage.96ad1e31a");
  section.hidden = visible === 0;
}

// ---------- 元信息 ----------
function buildExtraMeta(day, analysis) {
  const items = [];
  const device = day.device;
  const sync = (analysis && analysis.sync) || {};
  if (sync.is_stale) {
    const when = device && device.last_sync_time ? agoText(device.last_sync_time) : null;
    items.push({ text: when ? t("web.dashboard.buildExtraMeta.a04e97c78", {when: when}) : t("web.dashboard.buildExtraMeta.7308973f3"), klass: "state-warn" });
  }
  // 存档本身不完整（某天归档时就有接口没取到），比「上次拉取失败」更要紧：
  // 这意味着这一天看到的数字是残的，而原来这个信息只藏在收起的「明细 → 数据覆盖」里。
  const coverage = day.coverage || {};
  const endpoints = coverage.endpoint_count || 0;
  const archived = coverage.success_count === null || coverage.success_count === undefined
    ? endpoints : coverage.success_count;
  const missing = Math.max(0, endpoints - archived);
  if (endpoints && missing > 0) {
    const names = (coverage.errors || []).map((error) => KEY_LABELS[error.key] || error.key).filter(Boolean);
    const shown = names.slice(0, 2);
    const rest = Math.max(0, missing - shown.length);
    let text = t("web.dashboard.buildExtraMeta.ded9886e9", { missing, detail: "" });
    if (shown.length) text = rest
      ? t("coverage.missing.detail_more", { count: missing, items: shown.join(t("separator.list")), remaining: rest })
      : t("coverage.missing.detail", { count: missing, items: shown.join(t("separator.list")) });
    items.push({ text, klass: "state-warn" });
  } else if (sync.total && sync.is_partial) {
    // 存档完整时，部分失败只说明上次拉取没拿全，措辞要和「这一天缺数据」区分开
    items.push({ text: t("web.dashboard.buildExtraMeta.1ae6fe4d2", {p1: sync.succeeded, p2: sync.total}), klass: "state-warn" });
  }
  const alerts = day.alerts && day.alerts.irregular_rhythm_alerts;
  if (alerts) items.push({ text: t("web.dashboard.buildExtraMeta.abb67daf7", {alerts: alerts}), klass: "state-bad" });
  state.extraMeta = items;
}

function updateMeta() {
  const node = $("masthead-meta");
  clear(node);
  const items = [];
  const provider = (state.status || {}).provider || {};
  if (provider.provider === "demo") items.push({ text: t("web.dashboard.updateMeta.a646e009a") });
  else if (provider.provider === "google-health") {
    if (provider.needs_reconnect) items.push({ text: t("web.dashboard.updateMeta.d9f0827f3"), klass: "state-warn" });
    else if (!provider.connected) items.push({ text: t("web.dashboard.updateMeta.ba4e26f3b"), klass: "state-warn" });
    else items.push({ text: "Google Health" });
  }
  for (const item of state.extraMeta) items.push(item);
  const run = (state.status || {}).last_sync_run;
  if (run && run.finished_at) items.push({ text: t("web.dashboard.updateMeta.69206da39", {p1: agoText(run.finished_at)}) });
  items.forEach((item) => {
    node.appendChild(h("span", { class: item.klass || "", text: item.text }));
  });
}

// ---------- 流程 ----------
// 表格在窄屏放不下时会横向滚动，但没有任何提示 —— 被裁掉的列看起来像不存在。
// 这里给真正溢出的表格加右侧渐隐和一行文字提示，滑到末尾后渐隐消失。
function watchTableScroll() {
  document.querySelectorAll(".table-wrap").forEach((wrap) => {
    const currentHint = () => {
      const next = wrap.nextElementSibling;
      return next && next.classList.contains("scroll-hint") ? next : null;
    };
    const update = () => {
      const overflowing = wrap.scrollWidth - wrap.clientWidth > 2;
      const atEnd = wrap.scrollLeft + wrap.clientWidth >= wrap.scrollWidth - 2;
      wrap.classList.toggle("is-scrollable", overflowing && !atEnd);
      // 每次重新查一次，避免 scroll 回调里反复插入同一个提示
      if (overflowing && !currentHint()) {
        wrap.insertAdjacentElement("afterend", h("p", { class: "scroll-hint", text: t("web.dashboard.update.409ff3d8e") }));
      } else if (!overflowing && currentHint()) {
        currentHint().remove();
      }
    };
    if (!wrap.dataset.scrollWatched) {
      wrap.dataset.scrollWatched = "1";
      wrap.addEventListener("scroll", update, { passive: true });
    }
    update();
  });
}

let scrollResizeTimer = null;
window.addEventListener("resize", () => {
  clearTimeout(scrollResizeTimer);
  scrollResizeTimer = setTimeout(watchTableScroll, 150);
}, { passive: true });

// 图表按视口宽度出图（见 charts.js 的 fitWidth），所以断点变化时必须用已取到的数据重渲染，
// 否则手机上会停留在桌面版那张被缩小的图。不重新请求，只重画。
function renderAll() {
  const summary = state.summary;
  if (!summary) return;
  const insights = state.insights;
  const { day, analysis, trends, goals } = summary;
  buildExtraMeta(day, analysis);
  renderToday(day, insights, goals || {}, analysis);
  setHidden("card-ai", false);
  renderAI();
  renderSleepPage(day, insights, trends, goals || {});
  renderActivityPage(day, insights, trends, goals || {});
  renderHeartPage(day, insights, trends, goals || {});
  renderBodyPage(day, insights, trends);
  renderDataPage(day, insights, goals || {}, analysis);
  updateMeta();
  watchTableScroll();
  const colophon = $("colophon-window");
  if (colophon && insights && Number.isFinite(insights.window_days)) colophon.textContent = String(insights.window_days);
  renderDataNote(insights);
  applyPendingHash();
  if (navUpdate) requestAnimationFrame(navUpdate);
}

// 分区被隐藏时必须说明原因，否则「没数据」和「页面出错」看起来一样。

function renderDataNote(insights) {
  const note = $("data-note");
  if (!note) return;
  // 各分区现在会自己说明"为什么没有内容"，这里只处理整体拿不到分析数据的情况
  if (insights) { note.hidden = true; return; }
  note.textContent = t("web.dashboard.renderDataNote.702470580");
  note.hidden = false;
}

function watchWidth() {
  const query = window.matchMedia("(max-width: 640px)");
  let narrow = query.matches;
  const onChange = (event) => {
    if (event.matches === narrow) return;
    narrow = event.matches;
    renderAll();
  };
  if (query.addEventListener) query.addEventListener("change", onChange);
  else if (query.addListener) query.addListener(onChange);
}

function setLoading(active) {
  const bar = $("load-bar");
  if (bar) bar.hidden = !active;
  const page = document.querySelector("main.page");
  if (page) {
    page.classList.toggle("is-loading", active);
    page.setAttribute("aria-busy", active ? "true" : "false");
  }
}

async function load() {
  // 连续点箭头会产生多个并行的请求，先回来的不一定是最后要的那天。
  // 用序号标记，只有最新一次请求的结果才允许写进页面。
  state.loadToken = (state.loadToken || 0) + 1;
  const token = state.loadToken;
  setLoading(true);
  $("date-label").textContent = state.date ? dateTitle(state.date) : t("web.dashboard.load.4927a53bc");
  $("date-picker").value = state.date || "";
  const todayBtn = $("date-today");
  if (todayBtn) todayBtn.hidden = Boolean(state.today && state.date === state.today);
  try {
    const [summary, insights] = await Promise.all([
      get(`/summary?date=${state.date}`),
      get(`/insights?date=${state.date}&days=${WINDOW_DAYS}`).catch(() => null),
      loadAI(state.date),
    ]);
    if (token !== state.loadToken) return;
    state.summary = summary;
    state.insights = insights;
    renderAll();
    setHidden("empty-state", true);
    setHidden("section-nav", false);
  } catch (error) {
    if (token !== state.loadToken) return;
    if (error.status === 404) showEmpty();
    else toast(error.message, true);
  } finally {
    if (token === state.loadToken) setLoading(false);
  }
}

function showEmpty() {
  state.extraMeta = [];
  updateMeta();
  // 只说「还没有数据」等于复述标题；给出范围与下一步才有用
  const range = state.range;
  $("empty-hint").textContent = range
    ? t("web.dashboard.showEmpty.eff81bd92", {p1: range.from, p2: range.to, p3: range.count})
    : (state.status?.data_dir ? t("web.dashboard.showEmpty.41fdb5fe1", {p1: t("web.dashboard.showEmpty.d57d43e60", {p1: state.status.data_dir})}) : t("web.dashboard.showEmpty.d3c786ce4", {}));
  for (const id of ["card-today", "card-sleep", "card-activity", "card-heart", "card-body", "card-data", "today-summary"]) {
    setHidden(id, true);
  }
  setHidden("data-note", true);
  setHidden("section-nav", true);
  setHidden("card-ai", false); // 单日没存档时仍可选择包含已有数据的分析窗口。
  setHidden("empty-state", false);
}

async function refreshStatus() {
  state.status = await get("/status");
  updateMeta();
}

// ---------- 日期 ↔ URL ----------
// 之前日期只存在内存里：后退会离开整站而不是回到前一天，也没法把某一天发给别人。
const DATE_PATTERN = /^\d{4}-\d{2}-\d{2}$/;

function dateFromUrl() {
  const value = new URLSearchParams(window.location.search).get("date");
  return value && DATE_PATTERN.test(value) ? value : null;
}

function rememberDate(mode = "push") {
  const url = new URL(window.location.href);
  if (state.date) url.searchParams.set("date", state.date);
  else url.searchParams.delete("date");
  // 保留 #section：否则从分享链接进来时，首屏这次 replaceState 会把锚点抹掉
  const next = `${url.pathname}${url.search}${url.hash}`;
  if (next === `${window.location.pathname}${window.location.search}${window.location.hash}`) return;
  const record = { date: state.date };
  if (mode === "replace") window.history.replaceState(record, "", next);
  else window.history.pushState(record, "", next);
}

function goToDate(date, mode = "push") {
  if (!date) return;
  state.date = date;
  rememberDate(mode);
  load();
}

function navigate(offset) {
  const [year, month, day] = state.date.split("-").map(Number);
  const next = new Date(year, month - 1, day + offset);
  goToDate(`${next.getFullYear()}-${String(next.getMonth() + 1).padStart(2, "0")}-${String(next.getDate()).padStart(2, "0")}`);
}

async function doSync() {
  try {
    await post("/sync", { date: state.date });
    toast(t("web.dashboard.doSync.52b0486ad"));
    watchSync();
  } catch (error) {
    toast(error.message, true);
  }
}

function syncOutcome(last) {
  if (!last) return { text: t("web.dashboard.syncOutcome.678b02618"), bad: false, warn: false };
  if (last.status === "auth_error") {
    return { text: last.error || t("web.dashboard.syncOutcome.864f1dd72"), bad: true, warn: false };
  }
  const results = last.results || [];
  const errored = results.filter((item) => item.status === "error");
  const partial = results.filter((item) => item.status === "partial");
  const empty = results.filter((item) => item.status === "empty");
  const dateList = (items) => t("web.dashboard.dateList.8d13781d4", {p1: items.slice(0, 2).map((item) => String(item.date || "")).join(t('separator.list')), p2: items.length > 2 ? t("web.dashboard.dateList.8ab274ae2") : ""});
  if (errored.length) {
    return { text: t("web.dashboard.syncOutcome.bede02c65", {p1: errored.length, p2: dateList(errored)}), bad: true, warn: false };
  }
  if (partial.length) {
    const jobs = [...new Set(partial.flatMap((item) => item.failed_jobs || []))];
    const detail = jobs.length ? t("web.dashboard.syncOutcome.7b45860ad", {p1: jobs.slice(0, 2).map((key) => KEY_LABELS[key] || key).join(t('separator.list'))}) : "";
    return { text: t("web.dashboard.syncOutcome.71e5f2aaa", {p1: partial.length, p2: dateList(partial), detail: detail}), bad: false, warn: true };
  }
  if (empty.length) {
    return { text: t("web.dashboard.syncOutcome.75c2e0806", {p1: empty.length}), bad: false, warn: true };
  }
  return { text: t("web.dashboard.syncOutcome.678b02618"), bad: false, warn: false };
}

function watchSync() {
  const button = $("sync-btn");
  const batchButton = $("batch-btn");
  button.disabled = true;
  batchButton.disabled = true;
  clearInterval(state.syncTimer);
  state.syncTimer = setInterval(async () => {
    try {
      const status = await get("/sync/status");
      const progress = status.progress || {};
      if (progress.running) {
        const total = progress.days_total || 0;
        const done = progress.days_completed || 0;
        const active = (progress.active || []).length;
        $("sync-note").textContent = total ? (active > 1 ? t("web.dashboard.watchSync.7192ab40d", {done: done, total: total, p3: t("web.dashboard.watchSync.da391e1c6", {active: active})}) : t("web.dashboard.watchSync.185efb75e", {done: done, total: total})) : t("web.dashboard.watchSync.4e2238b74");
        return;
      }
      clearInterval(state.syncTimer);
      button.disabled = false;
      batchButton.disabled = false;
      $("sync-note").textContent = "";
      const outcome = syncOutcome(status.last_result);
      toast(outcome.text, outcome.bad, outcome.warn);
      await refreshStatus();
      await load();
    } catch (error) {
      clearInterval(state.syncTimer);
      button.disabled = false;
      batchButton.disabled = false;
      toast(error.message, true);
    }
  }, 1000);
}

let navUpdate = null;

// 顶部 tab 原来依赖浏览器的原生锚点跳转。目标区块是懒渲染的（数据没回来时是 hidden），
// 锚点指向一个 hidden 节点时浏览器不会滚动，某些移动端浏览器还会退化成整页重新请求 ——
// 表现就是"点了刷新、停在顶部"。所以改成自己接管：先确认有内容，再滚动，最后写 URL。
const prefersReducedMotion = () => typeof window.matchMedia === "function"
  && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

function scrollToSection(id, { updateUrl = true } = {}) {
  const node = document.getElementById(id);
  if (!node || node.hidden) return false;
  node.scrollIntoView({ behavior: prefersReducedMotion() ? "auto" : "smooth", block: "start" });
  if (updateUrl) {
    const url = new URL(window.location.href);
    url.hash = id;
    window.history.pushState({ ...(window.history.state || {}), section: id }, "", `${url.pathname}${url.search}${url.hash}`);
  }
  return true;
}

// 分享出去的 ...#card-weekly 这类链接，要等区块渲染出来才跳得过去
let appliedHash = null;
function applyPendingHash() {
  const id = (window.location.hash || "").replace(/^#/, "");
  if (!id || id === appliedHash) return;
  if (scrollToSection(id, { updateUrl: false })) appliedHash = id;
}

function bindSectionNav() {
  const links = [...document.querySelectorAll(".section-nav a")];
  const targets = links
    .map((link) => ({ link, node: document.querySelector(link.getAttribute("href")) }))
    .filter((entry) => entry.node);
  if (!targets.length) return;
  for (const { link } of targets) {
    link.addEventListener("click", (event) => {
      event.preventDefault();
      const id = link.getAttribute("href").slice(1);
      if (!scrollToSection(id)) {
        // 目标没有内容时不跳也不改 URL，否则会留下一个指向空处的锚点
        toast(t("web.dashboard.bindSectionNav.35c2e6ee8"), false, true);
      }
    });
  }
  const update = () => {
    let current = targets[0];
    for (const entry of targets) {
      if (entry.node.hidden) continue;
      if (entry.node.getBoundingClientRect().top <= 140) current = entry;
    }
    for (const entry of targets) {
      entry.link.classList.toggle("active", entry === current);
      if (entry === current) entry.link.setAttribute("aria-current", "true");
      else entry.link.removeAttribute("aria-current");
    }
  };
  navUpdate = update;
  update();
  window.addEventListener("scroll", update, { passive: true });
  window.addEventListener("resize", update, { passive: true });
}

function bindControls() {
  bindAI();
  $("date-prev").addEventListener("click", () => navigate(-1));
  $("date-next").addEventListener("click", () => navigate(1));
  $("date-today").addEventListener("click", () => goToDate(state.today));
  const picker = $("date-picker");
  picker.addEventListener("click", () => {
    if (typeof picker.showPicker === "function") {
      try { picker.showPicker(); } catch { /* 已打开 */ }
    }
  });
  picker.addEventListener("change", (event) => {
    if (event.target.value) goToDate(event.target.value);
  });
  window.addEventListener("popstate", () => {
    const date = dateFromUrl() || state.today;
    if (!date || date === state.date) return;
    state.date = date;
    load();
  });
  $("sync-btn").addEventListener("click", doSync);
  $("empty-sync").addEventListener("click", doSync);

  const dialog = $("batch-dialog");
  $("batch-btn").addEventListener("click", () => {
    const today = state.today || new Date().toISOString().slice(0, 10);
    const start = new Date();
    start.setDate(start.getDate() - 29);
    if (!$("batch-start").value) $("batch-start").value = start.toISOString().slice(0, 10);
    if (!$("batch-end").value) $("batch-end").value = today;
    dialog.showModal();
  });
  $("batch-cancel").addEventListener("click", () => dialog.close());
  $("batch-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const start = $("batch-start").value;
    const end = $("batch-end").value;
    if (!start || !end) return;
    try {
      const result = await post("/sync", { start, end });
      dialog.close();
      toast(t("web.dashboard.bindControls.f08f99eb0", {p1: result.dates.length}));
      watchSync();
    } catch (error) {
      toast(error.message, true);
    }
  });
}

async function init() {
  bindControls();
  watchWidth();
  bindSectionNav();
  try {
    const dates = await get("/dates");
    state.today = dates.today;
    state.range = dates.dates && dates.dates.length
      ? { from: dates.dates[0], to: dates.dates[dates.dates.length - 1], count: dates.dates.length }
      : null;
    state.date = dateFromUrl() || (dates.dates || []).at(-1) || dates.today;
    await refreshStatus();
  } catch (error) {
    toast(error.message, true);
    state.date = dateFromUrl() || new Date().toISOString().slice(0, 10);
  }
  rememberDate("replace");
  await load();
}

initLanguage();
init();
