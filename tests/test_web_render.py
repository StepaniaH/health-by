"""用 Node 执行实际渲染函数；DOM 替身只记录卡片，不复制渲染逻辑。"""

from __future__ import annotations

import json
from pathlib import Path
import subprocess

import pytest

ROOT = Path(__file__).resolve().parent.parent


def test_truncated_baseline_start_and_sample_count_are_visible():
    script = r"""
const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict');
const source = fs.readFileSync('health_by/web/js/dashboard-data.js', 'utf8');
const nodes = new Map();
const sandbox = {$: id => {if (!nodes.has(id)) nodes.set(id, {children: [], appendChild(child) {this.children.push(child);}}); return nodes.get(id);},
  clear: node => {node.children = [];}, timelineRibbon: () => null, h: (tag, attrs) => ({tag, ...attrs}),
  isoShort: v => (v ? String(v).slice(0, 10) : '—'), KEY_LABELS: {}, line: (l, v) => ({l, v}),
  linesBlock: rows => ({rows}), table: () => ({}), numSpan: x => ({x}), DASH: '—', num: x => String(x), minutes: x => String(x)};
sandbox.URLSearchParams = URLSearchParams;
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync('health_by/web/js/i18n-messages.js', 'utf8').replace(/^import .*;$/gm, '').replace(/^export \{ duration as minutes \} from .*;$/gm, 'const minutes = duration;').replace(/^export /gm, ''), sandbox);
vm.runInContext(fs.readFileSync('health_by/web/js/i18n.js', 'utf8').replace(/^import .*;$/gm, '').replace(/^export \{ duration as minutes \} from .*;$/gm, 'const minutes = duration;').replace(/^export /gm, ''), sandbox);
vm.runInContext(source.replace(/^import [\s\S]*?from ".*?";$/gm, '').replace(/^import .*;$/gm, '').replace(/^export /gm, ''), sandbox);
sandbox.renderDataPage({coverage: {}}, null, {}, {comparisons: {
  hrv_ms: {field_appearance: "2024-02-01", break: {date: '2024-02-01'}, valid_from: '2024-02-01', sample_count: 14, insufficient_reason: null}
}});
assert(nodes.get('coverage-body').children.some(child => child.text === 'HRV基线自 2024-02-01 起，14 个有效日。'));
assert.equal(nodes.get('card-data').hidden, false);
"""
    result = subprocess.run(["node", "-e", script], cwd=ROOT, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr


def test_heart_cards_use_server_rolling_values_and_badges_without_recomputing():
    script = r"""
const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict');
const nodes = new Map();
function node(tag = 'div') {
  let text = '';
  return {tag, children: [], hidden: false,
    get textContent() {return text + this.children.map(child => child.textContent).join('');},
    set textContent(value) {text = value; this.children = [];},
    get firstChild() {return this.children[0];}, setAttribute(key, value) {this[key] = value;},
    appendChild(child) {this.children.push(child);}, removeChild(child) {this.children.splice(this.children.indexOf(child), 1);},
    querySelector: () => null};
}
const document = {getElementById(id) {if (!nodes.has(id)) nodes.set(id, node()); return nodes.get(id);},
  createElement: node, createTextNode(text) {const item = node('#text'); item.textContent = text; return item;}};
const sandbox = {document, window: {addEventListener() {}}};
sandbox.URLSearchParams = URLSearchParams;
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync('health_by/web/js/i18n-messages.js', 'utf8').replace(/^import .*;$/gm, '').replace(/^export \{ duration as minutes \} from .*;$/gm, 'const minutes = duration;').replace(/^export /gm, ''), sandbox);
vm.runInContext(fs.readFileSync('health_by/web/js/i18n.js', 'utf8').replace(/^import .*;$/gm, '').replace(/^export \{ duration as minutes \} from .*;$/gm, 'const minutes = duration;').replace(/^export /gm, ''), sandbox);
vm.runInContext(fs.readFileSync('health_by/web/js/format.js', 'utf8').replace(/^import .*;$/gm, '').replace(/^export \{ duration as minutes \} from .*;$/gm, 'const minutes = duration;').replace(/^export /gm, ''), sandbox);
for (const file of ['dashboard-dom.js', 'dashboard-heart.js']) {
  vm.runInContext(fs.readFileSync('health_by/web/js/' + file, 'utf8')
    .replace(/^import [\s\S]*?from ".*?";$/gm, '').replace(/^import .*;$/gm, '').replace(/^export \{ duration as minutes \} from .*;$/gm, 'const minutes = duration;').replace(/^export /gm, ''), sandbox);
}
sandbox.trendBlock = () => null;
sandbox.relationsBlock = () => node();
vm.runInContext(`state.summary = {analysis: {display_mode: {hrv_ms: 'rolling7', resting_hr: 'daily'}, display_values: {hrv_ms: 42.7}}};`, sandbox);
sandbox.renderHeartPage({heart: {resting_hr: 64, hrv_ms: 90, hr_min: null, hr_max: null}}, null, [], {});
const cards = nodes.get('heart-today-body').children[0].children;
assert.match(cards[0].textContent, /静息心率64bpm/);
assert(!cards[0].textContent.includes('7 日均值'));
assert.match(cards[1].textContent, /HRV7 日均值42.7ms/);
assert(!cards[1].textContent.includes('90'));
const descendants = host => [host, ...host.children.flatMap(descendants)];
assert(descendants(cards[1]).some(child => child.className === 'tag metric-mode'));
vm.runInContext(`state.summary = {analysis: {display_mode: {hrv_ms: 'daily', resting_hr: 'daily'}, display_values: {hrv_ms: 90, resting_hr: null}}};`, sandbox);
sandbox.renderHeartPage({heart: {resting_hr: 0, hrv_ms: 90, hr_min: null, hr_max: null}}, null, [], {});
assert(!nodes.get('heart-today-body').textContent.includes('静息心率'));
assert(!nodes.get('heart-today-body').textContent.includes('0bpm'));
assert.match(nodes.get('heart-today-body').textContent, /HRV90ms/);
assert(!nodes.get('heart-today-body').textContent.includes('7 日均值'));
vm.runInContext(`state.summary = {analysis: {display_mode: {hrv_ms: 'rolling7'}, display_values: {hrv_ms: null}}};`, sandbox);
sandbox.renderHeartPage({heart: {resting_hr: 64, hrv_ms: null, hr_min: null, hr_max: null}}, null, [], {});
assert.match(nodes.get('heart-today-body').textContent, /HRV7 日均值.*近 7 天没有数据/);
"""
    result = subprocess.run(["node", "-e", script], cwd=ROOT, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    "body,workouts,expected_labels",
    [
        ({"body_fat_pct": 20, "weight_kg": None}, [], []),
        ({"body_fat_pct": 20}, [], []),
        ({"weight_kg": 72.3, "body_fat_pct": 20}, [], ["身体"]),
        ({"weight_kg": None, "body_fat_pct": 20}, [{"name": "步行"}], ["训练"]),
    ],
)
def test_body_cards_require_a_weight_reading(body, workouts, expected_labels):
    script = r"""
const fs = require('node:fs');
const vm = require('node:vm');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const source = fs.readFileSync('health_by/web/js/dashboard-body.js', 'utf8');
if (!source.includes('function renderBodyPage(')) throw new Error('找不到身体页渲染函数');
const nodes = new Map();
let cards = [];
const sandbox = {
  $: (id) => {
    if (!nodes.has(id)) nodes.set(id, {appendChild() {}});
    return nodes.get(id);
  },
  clear() {},
  cardGrid: (items) => { cards = items; return {}; },
  h: () => ({}),
  numSpan: () => ({}),
  line: () => ({}),
  lineChart: () => null,
  linesBlock: () => ({}),
  windowOf: () => 14,
};
sandbox.URLSearchParams = URLSearchParams;
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync('health_by/web/js/i18n-messages.js', 'utf8').replace(/^import .*;$/gm, '').replace(/^export \{ duration as minutes \} from .*;$/gm, 'const minutes = duration;').replace(/^export /gm, ''), sandbox);
vm.runInContext(fs.readFileSync('health_by/web/js/i18n.js', 'utf8').replace(/^import .*;$/gm, '').replace(/^export \{ duration as minutes \} from .*;$/gm, 'const minutes = duration;').replace(/^export /gm, ''), sandbox);
vm.runInContext(fs.readFileSync('health_by/web/js/format.js', 'utf8')
  .replace(/^import .*;$/gm, '').replace(/^export \{ duration as minutes \} from .*;$/gm, 'const minutes = duration;').replace(/^export /gm, ''), sandbox);
vm.runInContext(source.replace(/^import [\s\S]*?from ".*?";$/gm, '').replace(/^import .*;$/gm, '').replace(/^export /gm, ''), sandbox);
sandbox.renderBodyPage({body: input.body, workouts: input.workouts}, null, []);
process.stdout.write(JSON.stringify(cards));
"""
    result = subprocess.run(
        ["node", "-e", script],
        cwd=ROOT,
        input=json.dumps({"body": body, "workouts": workouts}),
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    cards = json.loads(result.stdout)
    assert [card["label"] for card in cards] == expected_labels
    for card in cards:
        if card["label"] == "身体":
            assert card["value"] == "72.3"
            assert card["compare"] == "体脂 20%"


def test_empty_state_renders_without_unknown_i18n_keys():
    script = r"""
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
for (const lang of ['zh', 'en']) {
  const nodes = new Map();
  function node(tag = 'div') {
    return {
      tag, children: [], hidden: true, textContent: '',
      classList: { toggle() {} },
      setAttribute() {}, appendChild(c) { this.children.push(c); },
      removeChild(c) { const i = this.children.indexOf(c); if (i >= 0) this.children.splice(i, 1); },
      get firstChild() { return this.children[0]; }, querySelector: () => null,
    };
  }
  const document = {
    documentElement: { setAttribute() {} },
    getElementById(id) { if (!nodes.has(id)) nodes.set(id, node()); return nodes.get(id); },
    createElement: node,
    createTextNode(text) { return { textContent: text }; },
    querySelector: () => null,
    querySelectorAll: () => [],
  };
  const sandbox = {
    document,
    window: { addEventListener() {}, location: { search: `?lang=${lang}`, href: `http://127.0.0.1/?lang=${lang}` }, history: { replaceState() {}, pushState() {} } },
    location: { search: `?lang=${lang}`, href: `http://127.0.0.1/?lang=${lang}` },
    navigator: { language: lang },
    URLSearchParams, URL,
  };
  vm.createContext(sandbox);
  for (const file of ['i18n-messages.js', 'i18n.js', 'format.js', 'dashboard-dom.js', 'dashboard-today.js', 'dashboard-sleep.js', 'dashboard-activity.js', 'dashboard-heart.js', 'dashboard-body.js', 'dashboard-data.js', 'dashboard.js']) {
    let code = fs.readFileSync('health_by/web/js/' + file, 'utf8')
      .replace(/^import [\s\S]*?from ".*?";$/gm, '')
      .replace(/^import .*;$/gm, '')
      .replace(/^export \{ duration as minutes \} from .*;$/gm, 'const minutes = duration;')
      .replace(/^export /gm, '')
      .replace(/^initTheme\(\);$/gm, '')
      .replace(/^initLanguage\(\);$/gm, '')
      .replace(/^init\(\);$/gm, '');
    vm.runInContext(code, sandbox);
  }
  vm.runInContext(`state.range = null; state.status = { data_dir: '/tmp/data' };`, sandbox);
  sandbox.showEmpty();
  assert.equal(nodes.get('empty-state').hidden, false);
  assert.equal(nodes.get('section-nav').hidden, true);
  assert.equal(nodes.get('card-ai').hidden, false);
  assert(nodes.get('empty-hint').textContent.includes('/tmp/data'));

  vm.runInContext(`state.status = null;`, sandbox);
  sandbox.showEmpty();
  assert(nodes.get('empty-hint').textContent.length > 0);
}
"""
    result = subprocess.run(["node", "-e", script], cwd=ROOT, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr


def test_relations_scatter_does_not_pair_across_date_gaps():
    script = r"""
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const nodes = new Map();
function node(tag = 'div') {
  return {
    tag, children: [], hidden: false, textContent: '',
    setAttribute() {}, appendChild(c) { this.children.push(c); },
    removeChild(c) { const i = this.children.indexOf(c); if (i >= 0) this.children.splice(i, 1); },
    get firstChild() { return this.children[0]; }, querySelector: () => null,
  };
}
const document = {
  getElementById(id) { if (!nodes.has(id)) nodes.set(id, node()); return nodes.get(id); },
  createElement: node,
  createTextNode(text) { return { textContent: text }; },
};
let scatterCalls = 0;
const sandbox = {
  document,
  window: { addEventListener() {} },
  URLSearchParams,
  scatterChart() { scatterCalls += 1; return node('svg'); },
};
vm.createContext(sandbox);
for (const file of ['i18n-messages.js', 'i18n.js', 'format.js', 'dashboard-dom.js']) {
  let code = fs.readFileSync('health_by/web/js/' + file, 'utf8')
    .replace(/^import [\s\S]*?from ".*?";$/gm, '')
    .replace(/^import .*;$/gm, '')
    .replace(/^export \{ duration as minutes \} from .*;$/gm, 'const minutes = duration;')
    .replace(/^export /gm, '');
  vm.runInContext(code, sandbox);
}
sandbox.scatterChart = () => { scatterCalls += 1; return node('svg'); };
const series = [
  { date: '2026-06-01', steps: 5000, sleep_minutes: 400 },
  { date: '2026-06-04', steps: 6000, sleep_minutes: 420 },
  { date: '2026-06-07', steps: 7000, sleep_minutes: 440 },
  { date: '2026-06-10', steps: 8000, sleep_minutes: 460 },
];
vm.runInContext(`state.insights = ${JSON.stringify({
  n_days: 4,
  series,
  correlations: [],
  correlations_candidates: [{ x: 'steps', y: 'sleep_minutes', x_label: '步数', y_label: '次日睡眠', offset_days: 1, r: 1, n: 3, survives_correction: false, slope: null }],
})};`, sandbox);
sandbox.relationsBlock('sleep');
assert.equal(scatterCalls, 0);
"""
    result = subprocess.run(["node", "-e", script], cwd=ROOT, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr



def test_today_summary_unhides_after_empty_and_subsection_cards_omit_self_links():
    script = r"""
const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict');
const nodes = new Map();
const make = (tag = 'div') => {
  const attrs = {};
  let text = '';
  const el = {
    tag, attrs, children: [], hidden: false,
    setAttribute(k, v) { attrs[k] = String(v); },
    getAttribute(k) { return attrs[k]; },
    appendChild(c) { this.children.push(c); },
    addEventListener() {},
    querySelector() { return null; },
    get firstChild() { return this.children[0] || null; },
    removeChild(c) { const i = this.children.indexOf(c); if (i >= 0) this.children.splice(i, 1); },
    get textContent() { return text + this.children.map(c => c.textContent).join(''); },
    set textContent(v) { text = String(v); this.children = []; },
  };
  return el;
};
const document = {
  getElementById(id) { if (!nodes.has(id)) nodes.set(id, make()); return nodes.get(id); },
  createElement: make,
  createTextNode(t) { const n = make('#text'); n.textContent = t; return n; },
  querySelector: () => null,
  querySelectorAll: () => [],
};
const sandbox = {
  document, URL, URLSearchParams,
  window: { matchMedia: () => ({ matches: false, addEventListener() {} }), addEventListener() {}, location: { search: '', href: 'http://127.0.0.1/' } },
  get: async () => ({}), post: async () => ({}), toast() {},
  bindAI() {}, loadAI: async () => {}, renderAI() {},
  barChart: () => make('svg'), lineChart: () => make('svg'), stageBar: () => make('svg'),
  timelineRibbon: () => make('svg'), scatterChart: () => make('svg'), bandChart: () => make('svg'),
  sleepTimingChart: () => make('svg'), heatmapChart: () => make('svg'), hrDayChart: () => make('svg'),
  cosinorChart: () => make('svg'), SLEEP_STAGE_LABELS: {}, SLEEP_STAGE_COLORS: {},
};
vm.createContext(sandbox);
for (const file of ['i18n-messages.js', 'i18n.js', 'format.js', 'dashboard-dom.js', 'dashboard-today.js', 'dashboard-sleep.js', 'dashboard-activity.js', 'dashboard-heart.js', 'dashboard-body.js', 'dashboard-data.js', 'dashboard.js']) {
  vm.runInContext(
    fs.readFileSync('health_by/web/js/' + file, 'utf8')
      .replace(/^import\s+[\s\S]*?from\s+['"][^'"]+['"];\s*$/gm, '')
      .replace(/^export \{ duration as minutes \} from .*;$/gm, 'const minutes = duration;')
      .replace(/^export /gm, '')
      .replace(/^initTheme\(\);$/gm, '')
      .replace(/^initLanguage\(\);$/gm, '')
      .replace(/^init\(\);$/gm, ''),
    sandbox
  );
}
sandbox.showEmpty();
assert.equal(nodes.get('today-summary').hidden, true);
assert.equal(nodes.get('today-criteria-details').hidden, true);
sandbox.renderToday(
  { date: '2026-10-01', activity: { steps: 8000 }, heart: { resting_hr: 60 }, sleep: { total_minutes: 450 } },
  { recovery: { level_label: '充足' } },
  { steps: 8000, sleep_minutes: 480 },
  { display_mode: {} }
);
assert.equal(nodes.get('today-summary').hidden, false);
assert.match(nodes.get('today-summary').textContent, /充足/);
assert.equal(nodes.get('today-criteria-details').hidden, false);

// sub-section cards without href must render <div>, never self-links
const nonLink = sandbox.todayCard({ label: '步数', value: '8,000', unit: '步' });
assert.equal(nonLink.tag, 'div');
assert.equal(nonLink.attrs.href, undefined);
assert.equal(nonLink.attrs['aria-label'], undefined);
"""
    result = subprocess.run(["node", "-e", script], cwd=ROOT, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr


def test_dashboard_error_404_isolation_visibility_and_calendar_band_chart():
    script = r"""
const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict');
const nodes = new Map();
const make = (tag = 'div') => {
  const attrs = {};
  let text = '';
  return {
    tag, attrs, children: [], hidden: false, value: '', disabled: false,
    setAttribute(k, v) { attrs[k] = String(v); },
    getAttribute(k) { return attrs[k]; },
    appendChild(c) { this.children.push(c); },
    addEventListener() {},
    querySelector(sel) {
      if (sel === '.section-empty') return this.children.find(c => c.className === 'section-empty') || null;
      return null;
    },
    get firstChild() { return this.children[0] || null; },
    removeChild(c) { const i = this.children.indexOf(c); if (i >= 0) this.children.splice(i, 1); },
    get textContent() { return text + this.children.map(c => c.textContent).join(''); },
    set textContent(v) { text = String(v); this.children = []; },
  };
};
const document = {
  getElementById(id) { if (!nodes.has(id)) nodes.set(id, make()); return nodes.get(id); },
  createElement: make,
  createElementNS(_ns, tag) { return make(tag); },
  createTextNode(t) { const n = make('#text'); n.textContent = t; return n; },
  querySelector: () => null,
  querySelectorAll: () => [],
};
let resizeHandlers = [];
let narrowMatches = false;
let mode = 'ok';
const sandbox = {
  document, URL, URLSearchParams,
  window: {
    matchMedia: (q) => ({
      get matches() { return q.includes('max-width') ? narrowMatches : !narrowMatches; },
      addEventListener(_e, fn) { resizeHandlers.push(fn); },
    }),
    addEventListener() {},
    history: { pushState() {}, replaceState() {} },
    location: { pathname: '/', search: '', hash: '', href: 'http://127.0.0.1/' },
  },
  get: async (path) => {
    if (path.startsWith('/summary')) {
      if (mode === '503') { const err = new Error('HTTP 503'); err.status = 503; throw err; }
      if (mode === '404') { const err = new Error('HTTP 404'); err.status = 404; throw err; }
      return {
        date: '2026-10-01',
        day: { date: '2026-10-01', activity: { steps: 8888 }, heart: { resting_hr: 60 }, sleep: { total_minutes: 450 }, body: {} },
        goals: { steps: 8000, sleep_minutes: 480 },
        analysis: { display_mode: {}, display_values: {}, comparisons: {} },
        trends: [],
      };
    }
    return {};
  },
  post: async () => ({}),
  toast() {},
  bindAI() {}, loadAI: async () => {}, renderAI() {},
};
vm.createContext(sandbox);
for (const file of ['i18n-messages.js', 'i18n.js', 'format.js', 'charts.js', 'dashboard-dom.js', 'dashboard-today.js', 'dashboard-sleep.js', 'dashboard-activity.js', 'dashboard-heart.js', 'dashboard-body.js', 'dashboard-data.js', 'dashboard.js']) {
  vm.runInContext(
    fs.readFileSync('health_by/web/js/' + file, 'utf8')
      .replace(/^import\s+[\s\S]*?from\s+['"][^'"]+['"];\s*$/gm, '')
      .replace(/^export \{ duration as minutes \} from .*;$/gm, 'const minutes = duration;')
      .replace(/^export /gm, '')
      .replace(/^initTheme\(\);$/gm, '')
      .replace(/^initLanguage\(\);$/gm, '')
      .replace(/^init\(\);$/gm, ''),
    sandbox
  );
}
const getState = () => vm.runInContext('state', sandbox);

(async () => {
  sandbox.watchWidth();
  // 1. 正常加载 2026-10-01
  vm.runInContext(`state.today = '2026-10-05'; state.date = '2026-10-01';`, sandbox);
  await sandbox.load();
  assert.equal(getState().date, '2026-10-01');
  assert.match(nodes.get('today-rows').textContent, /8,888/);

  // 2. 切到 2026-10-02 返回 503：旧日期卡片必须清空，头部显示新日期，展示错误提示
  mode = '503';
  vm.runInContext(`state.date = '2026-10-02';`, sandbox);
  await sandbox.load();
  assert.equal(nodes.get('date-picker').value, '2026-10-02');
  assert.equal(nodes.get('today-rows').children.length, 0);
  assert.equal(nodes.get('data-note').hidden, false);
  assert.equal(nodes.get('data-note').textContent, 'HTTP 503');
  assert.equal(getState().summary, null);

  // 3. 重新加载 2026-10-01 再切到 404 空日：state.summary 必须清为 null，窗口缩放不得复活旧日内容
  mode = 'ok';
  vm.runInContext(`state.date = '2026-10-01';`, sandbox);
  await sandbox.load();
  assert.notEqual(getState().summary, null);
  mode = '404';
  vm.runInContext(`state.date = '2026-10-03';`, sandbox);
  await sandbox.load();
  assert.equal(getState().summary, null);
  assert.equal(nodes.get('empty-state').hidden, false);
  narrowMatches = true;
  for (const fn of resizeHandlers) fn();
  assert.equal(nodes.get('today-rows').children.length, 0);

  // 4. 仅 SpO2 时心脏区可见；仅饮水时身体区可见
  sandbox.renderHeartPage({ heart: { spo2: 98.1 } }, null, [], {});
  assert.equal(nodes.get('card-heart').hidden, false);
  assert.match(nodes.get('nightly-body').textContent, /98\.1%/);

  sandbox.renderBodyPage({ body: { water_ml: 1800 }, workouts: [] }, null, []);
  assert.equal(nodes.get('card-body').hidden, false);
  assert.match(nodes.get('body-body').textContent, /1,800/);

  // 5. bandChart 按真实日历间距横轴排布并在缺测跳日处断线
  const svg = sandbox.bandChart({
    points: [
      { date: '2026-06-01', label: '06-01', value: 50, mean: 50, lo: 45, hi: 55 },
      { date: '2026-06-02', label: '06-02', value: 52, mean: 51, lo: 46, hi: 56 },
      { date: '2026-06-20', label: '06-20', value: 60, mean: 55, lo: 50, hi: 60 },
      { date: '2026-06-21', label: '06-21', value: 62, mean: 56, lo: 51, hi: 61 },
    ],
    color: '#2563eb',
    width: 600,
    height: 120,
  });
  const descendants = (el) => [el, ...el.children.flatMap(descendants)];
  const all = descendants(svg);
  const circles = all.filter(el => el.tag === 'circle');
  assert.equal(circles.length, 4);
  const xs = circles.map(c => Number(c.getAttribute('cx')));
  assert((xs[2] - xs[1]) > 10 * (xs[1] - xs[0]), `expected proportional gap, got ${xs}`);
  const lines = all.filter(el => el.tag === 'polyline');
  assert.equal(lines.length, 2, 'missing calendar gap must split polyline into two segments');

  // 6. 体重 lineChart 按真实日历间距排布（1、2、10 日非等距），相邻日实线、跨多日虚线
  const weightSvg = sandbox.lineChart({
    points: [
      { date: '2026-06-01', label: '06-01', value: 70.0 },
      { date: '2026-06-02', label: '06-02', value: 70.2 },
      { date: '2026-06-10', label: '06-10', value: 69.5 },
    ],
    color: '#0f766e',
    width: 640,
    height: 120,
  });
  const wAll = descendants(weightSvg);
  const wCircles = wAll.filter(el => el.tag === 'circle');
  assert.equal(wCircles.length, 3);
  const wxs = wCircles.map(c => Number(c.getAttribute('cx')));
  assert((wxs[2] - wxs[1]) > 5 * (wxs[1] - wxs[0]), `expected proportional weight x-axis, got ${wxs}`);
  assert.equal(wAll.filter(el => el.tag === 'polyline').length, 1);
  assert.equal(wAll.filter(el => el.tag === 'line' && el.getAttribute('stroke-dasharray') === '3 3').length, 1);
})().catch(e => { console.error(e); process.exitCode = 1; });
"""
    result = subprocess.run(["node", "-e", script], cwd=ROOT, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
