import { t } from "./i18n.js";
// 手写 SVG 图表（零依赖，印刷风）：柱状 / 折线 / 睡眠阶段条 / 一天时间轴缎带
const NS = "http://www.w3.org/2000/svg";

// 窄屏上固定 viewBox 会连刻度文字一起缩小：960 宽的图放进 390px 屏，缩放 0.37 倍，
// 10px 的轴标签实际只有 3.7px。所以窄屏直接按视口宽度出图，让文字保持真实像素大小，
// 而不是让整张图等比缩放。
const NARROW_QUERY = "(max-width: 640px)";
const NARROW_BASE_WIDTH = 360;

export function isNarrowViewport() {
  if (typeof window === "undefined" || !window.matchMedia) return false;
  return window.matchMedia(NARROW_QUERY).matches;
}

function fitWidth(base) {
  return isNarrowViewport() ? Math.min(base, NARROW_BASE_WIDTH) : base;
}

function el(name, attrs = {}) {
  const node = document.createElementNS(NS, name);
  for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, value);
  return node;
}

function withTitle(node, text) {
  const title = el("title");
  title.textContent = text;
  node.appendChild(title);
  return node;
}

export const SLEEP_STAGE_LABELS = { deep: t("web.charts.labels.ee3d90ef1"), light: t("web.charts.labels.7073c4c57"), rem: t("web.charts.labels.64e20b077"), wake: t("web.charts.labels.c19450348") };
export const SLEEP_STAGE_COLORS = {
  deep: "var(--m-sleep-deep)",
  light: "var(--m-sleep)",
  rem: "var(--m-sleep-rem)",
  wake: "var(--m-wake)",
};

export function barChart({ points, goal = null, color = "var(--m-steps)", unit = "", width = 640, height = 110, ariaLabel = t("web.charts.barChart.8aece403c"), axisLabels = false, yAxis = false }) {
  width = fitWidth(width);
  const values = points.map((p) => p.value).filter((v) => v !== null && v !== undefined);
  if (!values.length) return null;
  const max = Math.max(...values, goal || 0, 1);
  const left = yAxis ? 34 : 2;
  const right = 2;
  const labelSpace = axisLabels ? 15 : 0;
  const bottom = height - 2 - labelSpace;
  const gap = points.length > 40 ? 1 : 3;
  const plotWidth = width - left - right;
  const barWidth = Math.max(1.5, (plotWidth - gap * (points.length - 1)) / points.length);
  let scaleMax = max;
  let tickStep = 0;
  if (yAxis) {
    const magnitude = Math.pow(10, Math.floor(Math.log10(Math.max(max / 4, 1e-6))));
    const normalized = max / 4 / magnitude;
    tickStep = (normalized <= 1 ? 1 : normalized <= 2 ? 2 : normalized <= 4 ? 4 : normalized <= 5 ? 5 : 10) * magnitude;
    scaleMax = Math.ceil(max / tickStep) * tickStep;
  }
  const svg = el("svg", { viewBox: `0 0 ${width} ${height}`, class: "chart", role: "img", "aria-label": ariaLabel });
  if (yAxis) {
    const formatTick = (value) => (scaleMax >= 1000 ? `${(value / 1000).toFixed(value % 1000 ? 1 : 0)}k` : String(Math.round(value)));
    for (let value = 0; value <= scaleMax + 1e-9; value += tickStep) {
      const rowY = bottom - (value / scaleMax) * (bottom - 2 - 4);
      svg.appendChild(el("line", { x1: left, x2: width - right, y1: rowY, y2: rowY, stroke: value === 0 ? "var(--rule)" : "var(--rule-soft)", "stroke-width": 1 }));
      const tick = el("text", { x: left - 5, y: rowY + 3, "font-size": 9, fill: "var(--ink-3)", "text-anchor": "end" });
      tick.textContent = formatTick(value);
      svg.appendChild(tick);
    }
  } else {
    svg.appendChild(el("line", { x1: left, x2: width - right, y1: bottom, y2: bottom, stroke: "var(--rule)", "stroke-width": 1 }));
  }
  points.forEach((point, index) => {
    if (point.value === null || point.value === undefined) return;
    const barHeight = Math.max(1.5, (point.value / scaleMax) * (bottom - 2 - 4));
    const rect = el("rect", {
      x: left + index * (barWidth + gap),
      y: bottom - barHeight,
      width: barWidth,
      height: barHeight,
      fill: color,
      rx: 1,
    });
    withTitle(rect, `${point.label}：${point.value}${unit}`);
    svg.appendChild(rect);
  });
  if (goal) {
    const y = bottom - (goal / scaleMax) * (bottom - 2 - 4);
    svg.appendChild(el("line", {
      x1: left, x2: width - right, y1: y, y2: y,
      stroke: "var(--ink-3)", "stroke-dasharray": "3 4", "stroke-width": 1,
    }));
  }
  if (axisLabels) {
    points.forEach((point, index) => {
      const label = el("text", {
        x: left + index * (barWidth + gap) + barWidth / 2,
        y: height - 3,
        "font-size": 10, fill: "var(--ink-3)", "text-anchor": "middle",
      });
      label.textContent = point.label || "";
      svg.appendChild(label);
    });
  }
  return svg;
}

export function lineChart({ points, baseline = null, color = "var(--m-heart)", unit = "", width = 640, height = 110, ariaLabel = t("web.charts.lineChart.c24656699") }) {
  width = fitWidth(width);
  const validIndices = points.map((p, i) => (p.value !== null && p.value !== undefined ? i : -1)).filter((i) => i >= 0);
  if (!validIndices.length) return null;
  const values = validIndices.map((i) => points[i].value);
  let min = Math.min(...values, ...(baseline !== null && baseline !== undefined ? [baseline] : []));
  let max = Math.max(...values, ...(baseline !== null && baseline !== undefined ? [baseline] : []));
  if (min === max) { min -= 1; max += 1; }
  const pad = 4;
  const x = (i) => pad + (i * (width - pad * 2)) / Math.max(1, points.length - 1);
  const y = (v) => height - pad - ((v - min) / (max - min)) * (height - pad * 2);

  const svg = el("svg", { viewBox: `0 0 ${width} ${height}`, class: "chart", role: "img", "aria-label": ariaLabel });
  svg.appendChild(el("line", { x1: 2, x2: width - 2, y1: height - 2, y2: height - 2, stroke: "var(--rule)", "stroke-width": 1 }));
  svg.appendChild(el("polyline", {
    points: validIndices.map((i) => `${x(i)},${y(points[i].value)}`).join(" "),
    fill: "none", stroke: color, "stroke-width": 1.75, "stroke-linejoin": "round", "stroke-linecap": "round",
  }));
  validIndices.forEach((i, order) => {
    const isLast = order === validIndices.length - 1;
    const dot = el("circle", {
      cx: x(i), cy: y(points[i].value),
      r: isLast ? 2.8 : 1.8,
      fill: color, opacity: isLast ? 1 : 0.55,
    });
    withTitle(dot, `${points[i].label}：${points[i].value}${unit}`);
    svg.appendChild(dot);
  });
  if (baseline !== null && baseline !== undefined) {
    const by = y(baseline);
    svg.appendChild(el("line", {
      x1: pad, x2: width - pad, y1: by, y2: by,
      stroke: "var(--ink-3)", "stroke-dasharray": "3 4", "stroke-width": 1,
    }));
  }
  return svg;
}

export function stageBar({ stages, width = 640, height = 18, ariaLabel = t("web.charts.stageBar.ebfb0ca69") }) {
  width = fitWidth(width);
  const keys = ["deep", "light", "rem", "wake"].filter((key) => (stages[key] || 0) > 0);
  const total = keys.reduce((sum, key) => sum + (stages[key] || 0), 0);
  if (!total) return null;
  const svg = el("svg", { viewBox: `0 0 ${width} ${height}`, class: "chart", role: "img", "aria-label": ariaLabel });
  let offset = 0;
  keys.forEach((key) => {
    const segmentWidth = ((stages[key] || 0) / total) * width;
    const rect = el("rect", {
      x: offset, y: 0, width: Math.max(1, segmentWidth), height,
      fill: SLEEP_STAGE_COLORS[key], opacity: 0.9, rx: 1.5,
    });
    withTitle(rect, t("web.charts.stageBar.67d849bb4", {p1: SLEEP_STAGE_LABELS[key], p2: stages[key]}));
    svg.appendChild(rect);
    offset += segmentWidth;
  });
  return svg;
}

// 一天的时间轴缎带：睡眠阶段带（上）/ 运动段（中）/ 分时步数柱（下）/ 小时刻度
export function timelineRibbon({ sleep = null, stages = [], intraday = [], workouts = [], width = 960, height = 140, ariaLabel = t("web.charts.timelineRibbon.eab7fa269") }) {
  width = fitWidth(width);
  const hasSleep = sleep && sleep.start_time && sleep.end_time;
  const hasSteps = intraday.length > 0;
  if (!hasSleep && !hasSteps && !workouts.length) return null;

  const PAD = 6;
  const span = width - PAD * 2;
  const BASE = 96;            // 底轴
  const BAR_MAX = 48;         // 步数柱最高
  const SLEEP_Y = 10, SLEEP_H = 20;
  const WORKOUT_Y = 34, WORKOUT_H = 9;

  const x = (hours) => PAD + (Math.min(24, Math.max(0, hours)) / 24) * span;
  const fracOfIso = (value) => {
    const parsed = new Date(value);
    return Number.isNaN(parsed.getTime()) ? null : parsed.getHours() + parsed.getMinutes() / 60;
  };

  const svg = el("svg", { viewBox: `0 0 ${width} ${height}`, class: "chart", role: "img", "aria-label": ariaLabel });
  // 每 3 小时一条纵向参考线（给中段空白以结构感）
  for (const hour of [3, 6, 9, 12, 15, 18, 21]) {
    const gridX = x(hour);
    svg.appendChild(el("line", { x1: gridX, x2: gridX, y1: 6, y2: BASE, stroke: "var(--rule)", "stroke-width": 1 }));
  }
  svg.appendChild(el("line", { x1: PAD, x2: width - PAD, y1: BASE, y2: BASE, stroke: "var(--rule)", "stroke-width": 1 }));

  // 睡眠带（跨夜会话取当日可见段；有阶段明细则按阶段着色）
  if (hasSleep) {
    let start = fracOfIso(sleep.start_time);
    let end = fracOfIso(sleep.end_time);
    if (start !== null && end !== null) {
      if (end <= start) start = 0; // 跨夜：当日可见部分从零点起
      if (stages && stages.length) {
        for (const segment of stages) {
          let segStart = fracOfIso(segment.start);
          let segEnd = fracOfIso(segment.end);
          if (segStart === null || segEnd === null) continue;
          if (segEnd <= segStart) segStart = 0;
          const x0 = x(segStart), x1 = x(segEnd);
          if (x1 - x0 < 0.6) continue;
          const rect = el("rect", {
            x: x0, y: SLEEP_Y, width: x1 - x0, height: SLEEP_H,
            fill: SLEEP_STAGE_COLORS[segment.stage] || "var(--m-sleep)", rx: 1.5,
          });
          withTitle(rect, `${SLEEP_STAGE_LABELS[segment.stage] || segment.stage} ${String(segment.start).slice(11, 16)}–${String(segment.end).slice(11, 16)}`);
          svg.appendChild(rect);
        }
      } else {
        const rect = el("rect", {
          x: x(start), y: SLEEP_Y, width: Math.max(1, x(end) - x(start)), height: SLEEP_H,
          fill: "var(--m-sleep)", opacity: 0.42, rx: 1.5,
        });
        withTitle(rect, t("web.charts.timelineRibbon.398783a24", {p1: String(sleep.start_time).slice(11, 16), p2: String(sleep.end_time).slice(11, 16)}));
        svg.appendChild(rect);
      }
    }
  }

  // 运动段（整列淡染锚定底轴 + 顶部实块）
  for (const workout of workouts) {
    const start = fracOfIso(workout.start_time);
    if (start === null) continue;
    const duration = workout.duration_minutes || 0;
    const spanWidth = Math.max(2.5, x(start + duration / 60) - x(start));
    const column = el("rect", { x: x(start), y: 6, width: spanWidth, height: BASE - 6, fill: "var(--ink)", opacity: 0.05 });
    withTitle(column, t("web.charts.timelineRibbon.c8aba3c5c", {p1: workout.name}));
    svg.appendChild(column);
    const rect = el("rect", {
      x: x(start), y: WORKOUT_Y,
      width: spanWidth, height: WORKOUT_H,
      fill: "var(--ink)", opacity: 0.85, rx: 1.5,
    });
    withTitle(rect, t("web.charts.timelineRibbon.ccc806e8f", {p1: workout.name, p2: String(workout.start_time).slice(11, 16), p3: Math.round(duration)}));
    svg.appendChild(rect);
  }

  // 分时步数柱（每小时合计）
  if (hasSteps) {
    const byHour = new Map();
    for (const point of intraday) {
      const hour = parseInt(String(point.time).slice(0, 2), 10);
      if (Number.isNaN(hour)) continue;
      byHour.set(hour, (byHour.get(hour) || 0) + (point.value || 0));
    }
    const max = Math.max(1, ...byHour.values());
    const slot = span / 24;
    for (const [hour, value] of byHour) {
      const barHeight = Math.max(1.5, (value / max) * BAR_MAX);
      const barWidth = Math.min(slot * 0.62, 18);
      const rect = el("rect", {
        x: x(hour) + slot / 2 - barWidth / 2, y: BASE - barHeight,
        width: barWidth, height: barHeight, fill: "var(--m-steps)", rx: 1,
      });
      withTitle(rect, t("web.charts.timelineRibbon.23974cbad", {p1: String(hour).padStart(2, "0"), value: value}));
      svg.appendChild(rect);
    }
  }

  // 小时刻度
  for (const hour of [0, 3, 6, 9, 12, 15, 18, 21, 24]) {
    const tickX = x(hour);
    svg.appendChild(el("line", { x1: tickX, x2: tickX, y1: BASE, y2: BASE + 5, stroke: "var(--rule)" }));
    const label = el("text", {
      x: tickX, y: BASE + 20, "font-size": 11,
      "text-anchor": hour === 0 ? "start" : hour === 24 ? "end" : "middle",
      fill: "var(--ink-2)",
    });
    label.textContent = t("web.charts.timelineRibbon.82074298d", {hour: hour});
    svg.appendChild(label);
  }
  return svg;
}

// 散点 + 趋势线（交叉关联）
export function scatterChart({
  points, xFormat = (v) => String(Math.round(v)), yFormat = (v) => String(Math.round(v)),
  color = "var(--m-sleep)", slope = null, intercept = null, width = 460, height = 240,
  annotation = null, ariaLabel = t("web.charts.scatterChart.c062f5c0b"),
}) {
  width = fitWidth(width);
  if (!points.length) return null;
  const pad = { l: 44, r: 10, t: 10, b: 26 };
  let xmin = Math.min(...points.map((p) => p.x));
  let xmax = Math.max(...points.map((p) => p.x));
  let ymin = Math.min(...points.map((p) => p.y));
  let ymax = Math.max(...points.map((p) => p.y));
  if (xmin === xmax) { xmin -= 1; xmax += 1; }
  if (ymin === ymax) { ymin -= 1; ymax += 1; }
  const xPadRange = (xmax - xmin) * 0.06;
  const yPadRange = (ymax - ymin) * 0.1;
  xmin -= xPadRange; xmax += xPadRange; ymin -= yPadRange; ymax += yPadRange;
  const px = (v) => pad.l + ((v - xmin) / (xmax - xmin)) * (width - pad.l - pad.r);
  const py = (v) => height - pad.b - ((v - ymin) / (ymax - ymin)) * (height - pad.t - pad.b);

  const svg = el("svg", { viewBox: `0 0 ${width} ${height}`, class: "chart", role: "img", "aria-label": ariaLabel });
  svg.appendChild(el("line", { x1: pad.l, x2: pad.l, y1: pad.t, y2: height - pad.b, stroke: "var(--rule)" }));
  svg.appendChild(el("line", { x1: pad.l, x2: width - pad.r, y1: height - pad.b, y2: height - pad.b, stroke: "var(--rule)" }));
  for (const fraction of [0, 0.5, 1]) {
    const xv = xmin + fraction * (xmax - xmin);
    const xLabel = el("text", { x: px(xv), y: height - pad.b + 15, "font-size": 10, fill: "var(--ink-3)", "text-anchor": fraction === 0 ? "start" : fraction === 1 ? "end" : "middle" });
    xLabel.textContent = xFormat(xv);
    svg.appendChild(xLabel);
  }
  const ySpan = ymax - ymin;
  const yMagnitude = Math.pow(10, Math.floor(Math.log10(Math.max(ySpan / 4, 1e-6))));
  const yNormalized = ySpan / 4 / yMagnitude;
  const yStep = (yNormalized <= 1 ? 1 : yNormalized <= 2 ? 2 : yNormalized <= 5 ? 5 : 10) * yMagnitude;
  for (let yv = Math.ceil(ymin / yStep) * yStep; yv <= ymax + 1e-9; yv += yStep) {
    const rowY = py(yv);
    svg.appendChild(el("line", { x1: pad.l, x2: width - pad.r, y1: rowY, y2: rowY, stroke: "var(--rule-soft)", "stroke-width": 1 }));
    const yLabel = el("text", { x: pad.l - 5, y: rowY + 3, "font-size": 10, fill: "var(--ink-3)", "text-anchor": "end" });
    yLabel.textContent = yFormat(yv);
    svg.appendChild(yLabel);
  }
  if (slope !== null && slope !== undefined && intercept !== null && intercept !== undefined) {
    const yAt = (v) => intercept + slope * v;
    svg.appendChild(el("line", {
      x1: px(xmin), y1: py(yAt(xmin)), x2: px(xmax), y2: py(yAt(xmax)),
      stroke: "var(--ink-3)", "stroke-width": 1.25, "stroke-dasharray": "4 4",
    }));
  }
  if (annotation) {
    const note = el("text", { x: pad.l + 8, y: pad.t + 13, "font-size": 11, fill: "var(--ink-2)" });
    note.textContent = annotation;
    svg.appendChild(note);
  }
  for (const point of points) {
    const dot = el("circle", { cx: px(point.x), cy: py(point.y), r: 2.3, fill: color, opacity: 0.55 });
    withTitle(dot, point.label || "");
    svg.appendChild(dot);
  }
  return svg;
}

// 基线带折线：值 + 滚动均值 ± 1σ（阴影为带）
export function bandChart({
  points, color = "var(--m-heart)", width = 960, height = 126,
  yFormat = (v) => String(Math.round(v)), highlightColor = "var(--warn)", ariaLabel = t("web.charts.bandChart.324cc3a3a"), tickStep = null,
  goal = null,
}) {
  width = fitWidth(width);
  const validValues = points.map((p) => p.value).filter((v) => v !== null && v !== undefined);
  if (validValues.length < 2) return null;
  const bounds = [];
  for (const point of points) {
    for (const key of ["value", "lo", "hi"]) {
      if (point[key] !== null && point[key] !== undefined) bounds.push(point[key]);
    }
  }
  let min = Math.min(...bounds);
  let max = Math.max(...bounds);
  if (min === max) { min -= 1; max += 1; }
  const margin = (max - min) * 0.1;
  min -= margin; max += margin;
  const pad = { l: 52, r: 6, t: 8, b: 14 };
  const x = (index) => pad.l + (index * (width - pad.l - pad.r)) / Math.max(1, points.length - 1);
  const y = (value) => height - pad.b - ((value - min) / (max - min)) * (height - pad.t - pad.b);

  // 取整刻度，避免出现 2.1 / 6.7 / 11.2 这类跟随极值的刻度
  const rawStep = (max - min) / 3;
  const magnitude = Math.pow(10, Math.floor(Math.log10(Math.max(rawStep, 1e-6))));
  const normalized = rawStep / magnitude;
  const step = tickStep || (normalized <= 1 ? 1 : normalized <= 2 ? 2 : normalized <= 5 ? 5 : 10) * magnitude;
  const ticks = [];
  for (let value = Math.ceil(min / step) * step; value <= max + 1e-9; value += step) ticks.push(value);

  const svg = el("svg", { viewBox: `0 0 ${width} ${height}`, class: "chart", role: "img", "aria-label": ariaLabel });
  for (const levelValue of ticks) {
    const levelY = y(levelValue);
    svg.appendChild(el("line", { x1: pad.l, x2: width - pad.r, y1: levelY, y2: levelY, stroke: "var(--rule-soft)", "stroke-width": 1 }));
    const label = el("text", { x: pad.l - 5, y: levelY + 3, "font-size": 10, fill: "var(--ink-3)", "text-anchor": "end" });
    label.textContent = yFormat(levelValue);
    svg.appendChild(label);
  }

  // 目标线：与柱状图同样的虚线语义，便于在趋势图上直接看达标与否
  if (goal !== null && goal !== undefined) {
    const goalY = y(goal);
    svg.appendChild(el("line", {
      x1: pad.l, x2: width - pad.r, y1: goalY, y2: goalY,
      stroke: "var(--ink-3)", "stroke-width": 1, "stroke-dasharray": "3 3",
    }));
    const goalLabel = el("text", { x: width - pad.r, y: goalY - 4, "font-size": 10, fill: "var(--ink-3)", "text-anchor": "end" });
    goalLabel.textContent = t("web.charts.bandChart.6b0e287ba", {p1: yFormat(goal)});
    svg.appendChild(goalLabel);
  }

  const runs = [];
  let current = null;
  points.forEach((point, index) => {
    const hasBand = point.lo !== null && point.lo !== undefined && point.hi !== null && point.hi !== undefined;
    if (hasBand) {
      if (!current) { current = []; runs.push(current); }
      current.push(index);
    } else {
      current = null;
    }
  });
  for (const run of runs) {
    if (run.length < 2) continue;
    const top = run.map((index) => `${x(index)},${y(points[index].hi)}`).join(" ");
    const bottom = [...run].reverse().map((index) => `${x(index)},${y(points[index].lo)}`).join(" ");
    svg.appendChild(el("polygon", { points: `${top} ${bottom}`, fill: color, opacity: 0.1 }));
  }

  const valueIndices = points.map((p, index) => (p.value !== null && p.value !== undefined ? index : -1)).filter((index) => index >= 0);
  // 缺失值处断开折线，避免跨空白段拉出虚假直线
  let segment = [];
  const flushSegment = () => {
    if (segment.length >= 2) {
      svg.appendChild(el("polyline", {
        points: segment.map((index) => `${x(index)},${y(points[index].value)}`).join(" "),
        fill: "none", stroke: color, "stroke-width": 1.75, "stroke-linejoin": "round", "stroke-linecap": "round",
      }));
    }
    segment = [];
  };
  points.forEach((point, index) => {
    if (point.value !== null && point.value !== undefined) segment.push(index);
    else flushSegment();
  });
  flushSegment();
  valueIndices.forEach((index, order) => {
    const point = points[index];
    const isLast = order === valueIndices.length - 1;
    const outOfBand = (point.lo !== null && point.lo !== undefined && point.value < point.lo)
      || (point.hi !== null && point.hi !== undefined && point.value > point.hi);
    svg.appendChild(el("circle", {
      cx: x(index),
      cy: y(point.value),
      r: outOfBand ? 2.5 : isLast ? 2.6 : 1.7,
      fill: outOfBand ? highlightColor : color,
      opacity: outOfBand ? 0.95 : isLast ? 1 : 0.5,
    }));
  });
  if (points.length) {
    const first = el("text", { x: pad.l, y: height - 3, "font-size": 10, fill: "var(--ink-3)", "text-anchor": "start" });
    first.textContent = points[0].label || "";
    svg.appendChild(first);
    if (points.length > 10) {
      const midIndex = Math.floor((points.length - 1) / 2);
      const mid = el("text", { x: x(midIndex), y: height - 3, "font-size": 10, fill: "var(--ink-3)", "text-anchor": "middle" });
      mid.textContent = points[midIndex].label || "";
      svg.appendChild(mid);
    }
    const last = el("text", { x: width - pad.r, y: height - 3, "font-size": 10, fill: "var(--ink-3)", "text-anchor": "end" });
    last.textContent = points[points.length - 1].label || "";
    svg.appendChild(last);
  }
  return svg;
}

// 作息规律：入睡/起床时刻（纵轴为时刻，默认 20:00 → 次日 10:00）
export function sleepTimingChart({ entries, from = 20, to = 35, width = 960, height = 236, ariaLabel = t("web.charts.sleepTimingChart.138e00470") }) {
  width = fitWidth(width);
  if (!entries || entries.filter((entry) => entry.bed !== null && entry.bed !== undefined).length < 3) return null;
  const pad = { l: 44, r: 8, t: 8, b: 24 };
  const y = (t) => pad.t + ((Math.min(to, Math.max(from, t)) - from) / (to - from)) * (height - pad.t - pad.b);
  const slot = (width - pad.l - pad.r) / entries.length;
  const barWidth = Math.min(slot * 0.55, 20);
  const clock = (t) => {
    const hours = Math.floor(t) % 24;
    const minutes = Math.min(59, Math.round((t - Math.floor(t)) * 60));
    return `${String(hours).padStart(2, "0")}:${String(minutes).padStart(2, "0")}`;
  };
  const svg = el("svg", { viewBox: `0 0 ${width} ${height}`, class: "chart", role: "img", "aria-label": ariaLabel });
  for (const t of [20, 22, 24, 26, 28, 30, 32, 34]) {
    const gridY = y(t);
    svg.appendChild(el("line", { x1: pad.l, x2: width - pad.r, y1: gridY, y2: gridY, stroke: "var(--rule-soft)", "stroke-width": 1 }));
    const label = el("text", { x: pad.l - 6, y: gridY + 3, "font-size": 10, fill: "var(--ink-3)", "text-anchor": "end" });
    label.textContent = `${String(t % 24).padStart(2, "0")}:00`;
    svg.appendChild(label);
  }
  entries.forEach((entry, index) => {
    const cx = pad.l + slot * index + slot / 2;
    if (entry.bed === null || entry.bed === undefined || entry.wake === null || entry.wake === undefined) {
      svg.appendChild(el("line", {
        x1: cx, x2: cx, y1: height - pad.b - 7, y2: height - pad.b,
        stroke: "var(--rule)", "stroke-width": 2,
      }));
      return;
    }
    const yBed = y(entry.bed);
    const yWake = y(entry.wake);
    const rect = el("rect", {
      x: cx - barWidth / 2, y: Math.min(yBed, yWake),
      width: barWidth, height: Math.max(3, Math.abs(yWake - yBed)),
      fill: "var(--m-sleep)", opacity: 0.7, rx: 2.5,
    });
    withTitle(rect, t("web.charts.sleepTimingChart.8d34d05d2", {p1: entry.date, p2: clock(entry.bed), p3: clock(entry.wake)}));
    svg.appendChild(rect);
    if (index % 3 === 0 || index === entries.length - 1) {
      const label = el("text", { x: cx, y: height - pad.b + 14, "font-size": 10, fill: "var(--ink-3)", "text-anchor": "middle" });
      label.textContent = String(entry.date);
      svg.appendChild(label);
    }
  });
  return svg;
}

// 周内 × 小时 活动热图
export function heatmapChart({ matrix, width = 960, height = 232, ariaLabel = t("web.charts.heatmapChart.aa37e11d2") }) {
  width = fitWidth(width);
  const flat = matrix.flat().filter((v) => v !== null && v !== undefined);
  if (!flat.length) return null;
  const max = Math.max(...flat, 1);
  const pad = { l: 40, r: 6, t: 6, b: 24 };
  const cols = 24;
  const rows = matrix.length;
  const cellW = (width - pad.l - pad.r) / cols;
  const cellH = (height - pad.t - pad.b) / rows;
  const weekdays = [t("web.charts.heatmapChart.c430f4c12"), t("web.charts.heatmapChart.d1378a68e"), t("web.charts.heatmapChart.296116896"), t("web.charts.heatmapChart.e9d3b01a5"), t("web.charts.heatmapChart.572388f3b"), t("web.charts.heatmapChart.f9aa11dbb"), t("web.charts.heatmapChart.ee239f394")];
  const svg = el("svg", { viewBox: `0 0 ${width} ${height}`, class: "chart", role: "img", "aria-label": ariaLabel });
  for (let row = 0; row < rows; row++) {
    const label = el("text", { x: pad.l - 6, y: pad.t + cellH * row + cellH / 2 + 3, "font-size": 10, fill: "var(--ink-3)", "text-anchor": "end" });
    label.textContent = weekdays[row] || "";
    svg.appendChild(label);
    for (let col = 0; col < cols; col++) {
      const cellX = pad.l + col * cellW + 1;
      const cellY = pad.t + row * cellH + 1.5;
      const cellWidth = cellW - 2;
      const cellHeight = cellH - 3;
      const value = matrix[row][col];
      if (value === null || value === undefined) {
        svg.appendChild(el("rect", { x: cellX, y: cellY, width: cellWidth, height: cellHeight, fill: "var(--rule-soft)", opacity: 0.32, rx: 1.5 }));
        continue;
      }
      const opacity = 0.12 + 0.85 * Math.sqrt(value / max);
      const rect = el("rect", {
        x: cellX, y: cellY, width: cellWidth, height: cellHeight,
        fill: "var(--m-steps)", opacity: opacity.toFixed(2), rx: 1.5,
      });
      withTitle(rect, t("web.charts.heatmapChart.e488cfb06", {p1: weekdays[row], p2: String(col).padStart(2, "0"), value: value}));
      svg.appendChild(rect);
    }
  }
  // 每 6 小时一条分组分隔（纸色细缝，帮助定位）
  for (const col of [6, 12, 18]) {
    const lineX = pad.l + col * cellW;
    svg.appendChild(el("line", { x1: lineX, x2: lineX, y1: pad.t, y2: height - pad.b, stroke: "var(--paper)", "stroke-width": 2 }));
  }
  for (const col of [0, 3, 6, 9, 12, 15, 18, 21]) {
    const label = el("text", { x: pad.l + col * cellW + cellW / 2, y: height - pad.b + 15, "font-size": 10, fill: "var(--ink-3)", "text-anchor": "middle" });
    label.textContent = t("web.charts.heatmapChart.da5fc9dcf", {p1: String(col).padStart(2, "0")});
    svg.appendChild(label);
  }
  return svg;
}

// 迷你趋势线（行内 sparkline）
export function sparkline({ values, color = "var(--ink-3)", width = 84, height = 20 }) {
  const valid = values.map((value, index) => (value === null || value === undefined ? null : { index, value })).filter(Boolean);
  if (valid.length < 3) return null;
  const min = Math.min(...valid.map((p) => p.value));
  const max = Math.max(...valid.map((p) => p.value));
  const span = max - min || 1;
  const x = (index) => 2 + (index * (width - 4)) / Math.max(1, values.length - 1);
  const y = (value) => height - 2 - ((value - min) / span) * (height - 4);
  const svg = el("svg", { viewBox: `0 0 ${width} ${height}`, width, height, class: "spark-svg", "aria-hidden": "true" });
  svg.appendChild(el("polyline", {
    points: valid.map((p) => `${x(p.index)},${y(p.value)}`).join(" "),
    fill: "none", stroke: color, "stroke-width": 1.3, "stroke-linejoin": "round", "stroke-linecap": "round", opacity: 0.85,
  }));
  const last = valid[valid.length - 1];
  svg.appendChild(el("circle", { cx: x(last.index), cy: y(last.value), r: 1.8, fill: color }));
  return svg;
}

// 当日心率曲线（含静息参考线与运动时段淡染）
export function hrDayChart({ points, resting = null, workouts = [], color = "var(--m-heart)", width = 960, height = 132, ariaLabel = t("web.charts.hrDayChart.ef0d62e7b") }) {
  width = fitWidth(width);
  if (!points || points.length < 2) return null;
  const pad = { l: 6, r: 6, t: 12, b: 20 };
  const toFrac = (hhmm) => {
    const [h, m] = String(hhmm).split(":").map(Number);
    return (h + (m || 0) / 60) / 24;
  };
  const values = points.map((p) => p.value);
  let min = Math.min(...values);
  let max = Math.max(...values);
  if (resting !== null && resting !== undefined) {
    min = Math.min(min, resting);
    max = Math.max(max, resting);
  }
  const padRange = (max - min || 1) * 0.12;
  min -= padRange; max += padRange;
  const x = (frac) => pad.l + frac * (width - pad.l - pad.r);
  const y = (value) => height - pad.b - ((value - min) / (max - min)) * (height - pad.t - pad.b);

  const svg = el("svg", { viewBox: `0 0 ${width} ${height}`, class: "chart", role: "img", "aria-label": ariaLabel });
  for (const workout of workouts) {
    const start = toFrac(String(workout.start_time).slice(11, 16));
    const end = start + ((workout.duration_minutes || 0) / 60) / 24;
    svg.appendChild(el("rect", { x: x(start), y: pad.t, width: Math.max(2, x(end) - x(start)), height: height - pad.t - pad.b, fill: "var(--ink)", opacity: 0.05 }));
  }
  svg.appendChild(el("line", { x1: pad.l, x2: width - pad.r, y1: height - pad.b, y2: height - pad.b, stroke: "var(--rule)", "stroke-width": 1 }));
  if (resting !== null && resting !== undefined) {
    svg.appendChild(el("line", { x1: pad.l, x2: width - pad.r, y1: y(resting), y2: y(resting), stroke: "var(--ink-3)", "stroke-dasharray": "4 4", "stroke-width": 1 }));
  }
  const valid = points.map((point, index) => (point.value !== null && point.value !== undefined ? index : -1)).filter((index) => index >= 0);
  svg.appendChild(el("polyline", {
    points: valid.map((index) => `${x(toFrac(points[index].time))},${y(points[index].value)}`).join(" "),
    fill: "none", stroke: color, "stroke-width": 1.6, "stroke-linejoin": "round", "stroke-linecap": "round",
  }));
  valid.forEach((index) => {
    const dot = el("circle", { cx: x(toFrac(points[index].time)), cy: y(points[index].value), r: 1.6, fill: color, opacity: 0.5 });
    withTitle(dot, `${points[index].time} · ${points[index].value} bpm`);
    svg.appendChild(dot);
  });
  for (const hour of [0, 6, 12, 18, 24]) {
    const tickX = x(hour / 24);
    const label = el("text", { x: tickX, y: height - pad.b + 15, "font-size": 10, fill: "var(--ink-3)", "text-anchor": hour === 0 ? "start" : hour === 24 ? "end" : "middle" });
    label.textContent = t("web.charts.hrDayChart.efa48d32e", {hour: hour});
    svg.appendChild(label);
  }
  return svg;
}

// 日活动节律：每小时平均步数柱 + 24 小时余弦拟合曲线
export function cosinorChart({
  hourly, cosinor = null, color = "var(--m-steps)", width = 960, height = 208,
  ariaLabel = t("web.charts.cosinorChart.10b703938"),
}) {
  width = fitWidth(width);
  const values = (hourly || []).filter((value) => value !== null && value !== undefined);
  if (values.length < 6) return null;
  const rawMax = Math.max(...values, 1);
  const magnitude = Math.pow(10, Math.floor(Math.log10(rawMax)));
  const normalized = rawMax / magnitude;
  const max = (normalized <= 1 ? 1 : normalized <= 2 ? 2 : normalized <= 5 ? 5 : 10) * magnitude;
  const pad = { l: 38, r: 8, t: 12, b: 24 };
  const x = (hour) => pad.l + (hour / 24) * (width - pad.l - pad.r);
  const y = (value) => height - pad.b - (value / max) * (height - pad.t - pad.b);
  const svg = el("svg", { viewBox: `0 0 ${width} ${height}`, class: "chart", role: "img", "aria-label": ariaLabel });

  for (const fraction of [0, 0.5, 1]) {
    const levelValue = max * fraction;
    const levelY = y(levelValue);
    svg.appendChild(el("line", { x1: pad.l, x2: width - pad.r, y1: levelY, y2: levelY, stroke: "var(--rule-soft)", "stroke-width": 1 }));
    const label = el("text", { x: pad.l - 5, y: levelY + 3, "font-size": 10, fill: "var(--ink-3)", "text-anchor": "end" });
    label.textContent = String(Math.round(levelValue));
    svg.appendChild(label);
  }
  const slot = (width - pad.l - pad.r) / 24;
  hourly.forEach((value, hour) => {
    if (value === null || value === undefined) return;
    const barWidth = Math.min(slot * 0.55, 22);
    const rect = el("rect", {
      x: x(hour) + slot / 2 - barWidth / 2,
      y: y(value),
      width: barWidth,
      height: Math.max(1, height - pad.b - y(value)),
      fill: color,
      opacity: 0.35,
      rx: 1,
    });
    withTitle(rect, t("web.charts.cosinorChart.30a40051c", {p1: String(hour).padStart(2, "0"), value: value}));
    svg.appendChild(rect);
  });
  if (cosinor && cosinor.mesor !== null && cosinor.amplitude !== null && cosinor.acrophase_hour !== null) {
    const points = [];
    for (let hour = 0; hour <= 24; hour += 0.25) {
      const value = cosinor.mesor + cosinor.amplitude * Math.cos((2 * Math.PI * (hour - cosinor.acrophase_hour)) / 24);
      points.push(`${x(hour)},${y(Math.max(0, value))}`);
    }
    svg.appendChild(el("polyline", {
      points: points.join(" "), fill: "none", stroke: "var(--ink)", "stroke-width": 1.4,
      "stroke-dasharray": "5 4", "stroke-linejoin": "round", opacity: 0.75,
    }));
  }
  for (const hour of [0, 6, 12, 18, 24]) {
    const tickX = x(hour);
    svg.appendChild(el("line", { x1: tickX, x2: tickX, y1: height - pad.b, y2: height - pad.b + 4, stroke: "var(--rule)" }));
    const label = el("text", { x: tickX, y: height - pad.b + 15, "font-size": 10, fill: "var(--ink-3)", "text-anchor": hour === 0 ? "start" : hour === 24 ? "end" : "middle" });
    label.textContent = t("web.charts.cosinorChart.efa48d32e", {hour: hour});
    svg.appendChild(label);
  }
  return svg;
}
