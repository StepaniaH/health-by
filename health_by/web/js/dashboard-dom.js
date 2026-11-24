import { t } from "./i18n.js";
import { DASH, num, shortDate } from "./format.js";
import { bandChart, scatterChart } from "./charts.js";

export const WINDOW_DAYS = 90;   // 只用于发起请求；页面上的窗口文字一律来自 windowText()
export const state = { date: null, today: null, summary: null, insights: null, status: null, extraMeta: [], syncTimer: null };
export const $ = (id) => document.getElementById(id);

export const KEY_LABELS = {
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
export function h(tag, attrs = {}, children = []) {
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

export function clear(node) { while (node.firstChild) node.removeChild(node.firstChild); }
export function setHidden(id, hidden) { const node = $(id); if (node) node.hidden = hidden; }
export function numSpan(text) { return h("span", { class: "num", text }); }

export function clockFromDecimal(value) {
  if (value === null || value === undefined) return DASH;
  const hours = Math.floor(value) % 24;
  const mins = Math.min(59, Math.round((value - Math.floor(value)) * 60));
  return `${String(hours).padStart(2, "0")}:${String(mins).padStart(2, "0")}`;
}

export function isoShort(value) { return value ? String(value).slice(0, 10) : DASH; }

export function shiftIsoDate(iso, offsetDays) {
  const [year, month, day] = String(iso).split("-").map(Number);
  const base = new Date(Date.UTC(year, month - 1, day));
  if (base.getUTCFullYear() !== year || base.getUTCMonth() !== month - 1 || base.getUTCDate() !== day) {
    return null;
  }
  return new Date(Date.UTC(year, month - 1, day + offsetDays)).toISOString().slice(0, 10);
}

export function agoText(iso) {
  if (!iso) return "";
  const time = new Date(iso).getTime();
  if (Number.isNaN(time)) return "";
  const mins = Math.max(0, Math.round((Date.now() - time) / 60000));
  if (mins < 60) return t("web.dashboard.agoText.4ba0128b1", {mins: mins});
  const hours = Math.round(mins / 60);
  if (hours < 48) return t("web.dashboard.agoText.c430f0983", {hours: hours});
  return t("web.dashboard.agoText.ace2d01fc", {p1: Math.round(hours / 24)});
}

export function line(label, valueNodes, note = null, noteClass = "", spark = null, metric = null) {
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

export function linesBlock(children) {
  const box = h("div", { class: "lines" });
  for (const child of children) if (child) box.appendChild(child);
  return box;
}

// ---------- 表格 ----------
export function table(headers, rows, className = "") {
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

export const METRIC_DOMAIN = {
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
export const DOMAIN_LABELS = { sleep: t("web.dashboard.labels.01324e540"), activity: t("web.dashboard.labels.9e9bead81"), heart: t("web.dashboard.labels.015564cd0"), body: t("web.dashboard.labels.35b081b16") };

export function renderSectionNotice(sectionId, bodyIds, text) {
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

export function hideSectionNotice(sectionId, bodyIds) {
  const section = $(sectionId);
  if (!section) return;
  for (const id of bodyIds) {
    const node = $(id);
    if (node) node.hidden = false;
  }
  const note = section.querySelector(".section-empty");
  if (note) note.hidden = true;
}

export function signalFor(insights, key) {
  const signals = (insights && insights.recovery && insights.recovery.signals) || [];
  return signals.find((signal) => signal.key === key) || null;
}

export function stateLabel(signal) {
  return signal && signal.state_label ? signal.state_label : "";
}

export function stateClass(signal) {
  if (!signal) return "";
  if (signal.state === "low") return "state-warn";
  if (signal.state === "good") return "state-ok";
  return "";
}

export function baselineWindowText(days) {
  return Number.isFinite(days) ? t("web.dashboard.baselineWindowText.f6429626a", {days: days}) : "";
}

export function hrvDeltaText(pct, prefix = "HRV", days = null) {
  if (pct === null || pct === undefined || Number.isNaN(pct)) return "";
  const win = baselineWindowText(days);
  if (Math.abs(pct) < 1) return t("web.dashboard.hrvDeltaText.f5866e0cc", {prefix: prefix, win: win});
  return (pct > 0 ? t("web.dashboard.hrvDeltaText.6575d7a8a", {prefix: prefix, win: win, p4: Math.abs(Math.round(pct))}) : t("web.dashboard.hrvDeltaText.2f9d16702", {prefix: prefix, win: win, p4: Math.abs(Math.round(pct))}));
}

export function rhrDeltaText(delta, days = null) {
  if (delta === null || delta === undefined || Number.isNaN(delta)) return "";
  const win = baselineWindowText(days);
  if (Math.abs(delta) < 1) return t("web.dashboard.rhrDeltaText.ae8a4db25", {win: win});
  return (delta > 0 ? t("web.dashboard.rhrDeltaText.ee699df67", {win: win, p3: Math.abs(Math.round(delta))}) : t("web.dashboard.rhrDeltaText.610240067", {win: win, p3: Math.abs(Math.round(delta))}));
}

export function windowText() {
  const insights = state.insights || {};
  const days = Number.isFinite(insights.window_days) ? insights.window_days
    : (Number.isFinite(insights.n_days) ? insights.n_days : null);
  return days ? t("web.dashboard.windowText.f6429626a", {days: days}) : t("web.dashboard.windowText.474da3018");
}

export function windowOf(kind, fallback = null) {
  const insights = state.insights || {};
  const windows = insights.windows || {};
  return Number.isFinite(windows[kind]) ? windows[kind] : fallback;
}

export function cardGrid(cards, columns = 4) {
  const grid = h("div", { class: `today-grid cols-${columns}` });
  for (const item of cards) grid.appendChild(todayCard(item));
  return grid;
}

export function metricDisplay(key, daily) {
  const analysis = state.summary?.analysis || {};
  const rolling = analysis.display_mode?.[key] === "rolling7";
  const hasDisplay = Boolean(analysis.display_values && Object.prototype.hasOwnProperty.call(analysis.display_values, key));
  return {rolling, value: hasDisplay ? (analysis.display_values[key] ?? null) : daily};
}

export function metricBadge(key) {
  return key && metricDisplay(key, null).rolling ? h("span", {class: "tag metric-mode", text: t("web.dashboard.metricBadge.8f9021461")}) : null;
}

export function todayCard(item) {
  const attrs = { class: "today-cell today-card" };
  if (item.href) {
    attrs.href = item.href;
    attrs["aria-label"] = item.aria || (item.label ? `${item.label}${t("separator.colon")}${item.sentence || item.value}` : undefined);
  }
  return h(item.href ? "a" : "div", attrs, [
    h("div", { class: "today-card-label" }, [item.label, metricBadge(item.metric)]),
    h("div", { class: "today-card-value" }, [
      h("span", { class: "ring-value", text: item.value }),
      item.unit ? h("span", { class: "ring-unit", text: item.unit }) : null,
    ]),
    item.compare ? h("div", { class: "today-card-note", text: item.compare }) : null,
  ]);
}

export function trendBlock(key, { title, unit, color, format, goal = null, step = null, width = 960, height = 126 }) {
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
    width,
    height,
    yFormat: format || ((value) => num(Math.round(value))),
    tickStep: step,
    ariaLabel: t("web.dashboard.trendBlock.690ae5c4c", {title: title, windowLabel: windowLabel, bandDays: bandDays}),
  });
  if (chart) wrap.appendChild(chart);
  const outside = ((state.insights && state.insights.bands_outside) || {})[key] || null;
  const countSentence = outside
    ? t("web.dashboard.trendBlock.c85c9f79a", {windowLabel: windowLabel, p2: outside.points, p3: outside.outside, p4: outside.percent, bandDays: bandDays})
    : t("web.dashboard.trendBlock.42caf8551", {bandDays: bandDays});
  wrap.appendChild(h("p", {
    class: "hintline",
    text: t("web.dashboard.trendBlock.0fc6994e3", {countSentence: countSentence}),
  }));
  return wrap;
}

export function achievementsBlock(key, goalText, title) {
  const insights = state.insights || {};
  const summary = (insights.achievements_summary || {})[key] || null;
  if (!summary || !Number.isFinite(summary.window_days) || summary.window_days < 7) return null;
  const rawAll = insights.achievements || [];
  const anchor = insights.to || (rawAll.length ? rawAll[rawAll.length - 1].date : null);
  const cutoff = anchor ? shiftIsoDate(anchor, -(summary.window_days - 1)) : null;
  const all = cutoff
    ? rawAll.filter((entry) => entry?.date && entry.date >= cutoff && entry.date <= anchor)
    : rawAll.slice(-summary.window_days);
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

export function stripFor(label, achievements, key, goalText) {
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

export const METRIC_AXIS = {
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

export function relationsBlock(domain, title = t("web.dashboard.relationsBlock.e135989fc")) {
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

  const series = insights.series || [];
  const byDate = new Map(series.filter((row) => row?.date).map((row) => [row.date, row]));
  const scatterGrid = h("div", { class: "scatter-grid" });
  for (const correlation of candidates.slice(0, 2)) {
    const pairs = [];
    const offset = correlation.offset_days || 0;
    for (const row of series) {
      if (!row?.date) continue;
      const targetRow = offset === 0 ? row : byDate.get(shiftIsoDate(row.date, offset));
      if (!targetRow) continue;
      const x = row[correlation.x];
      const y = targetRow[correlation.y];
      if (x === null || x === undefined || y === null || y === undefined) continue;
      pairs.push({ x, y, label: `${row.date} · ${num(x)} → ${num(y, 1)}` });
    }
    if (pairs.length < 3) continue;
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
