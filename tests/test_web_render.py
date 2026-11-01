"""用 Node 执行实际渲染函数；DOM 替身只记录卡片，不复制渲染逻辑。"""

from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parent.parent


@pytest.mark.skipif(shutil.which("node") is None, reason="需要 Node.js 检查基线起点显示")
def test_truncated_baseline_start_and_sample_count_are_visible():
    script = r"""
const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict');
const source = fs.readFileSync('health_by/web/js/dashboard.js', 'utf8');
const nodes = new Map();
const sandbox = {$: id => {if (!nodes.has(id)) nodes.set(id, {children: [], appendChild(child) {this.children.push(child);}}); return nodes.get(id);},
  clear: node => {node.children = [];}, timelineRibbon: () => null, h: (tag, attrs) => ({tag, ...attrs})};
sandbox.URLSearchParams = URLSearchParams;
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync('health_by/web/js/i18n-messages.js', 'utf8').replace(/^import .*;$/gm, '').replace(/^export \{ duration as minutes \} from .*;$/gm, 'const minutes = duration;').replace(/^export /gm, ''), sandbox);
vm.runInContext(fs.readFileSync('health_by/web/js/i18n.js', 'utf8').replace(/^import .*;$/gm, '').replace(/^export \{ duration as minutes \} from .*;$/gm, 'const minutes = duration;').replace(/^export /gm, ''), sandbox);
vm.runInContext(source.slice(source.indexOf('function renderDataPage('), source.indexOf('function buildExtraMeta(')), sandbox);
sandbox.renderDataPage({coverage: {}}, null, {}, {comparisons: {
  hrv_ms: {field_appearance: "2024-02-01", break: {date: '2024-02-01'}, valid_from: '2024-02-01', sample_count: 14, insufficient_reason: null}
}});
assert(nodes.get('coverage-body').children.some(child => child.text === 'HRV基线自 2024-02-01 起，14 个有效日。'));
assert.equal(nodes.get('card-data').hidden, false);
"""
    result = subprocess.run(["node", "-e", script], cwd=ROOT, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr


@pytest.mark.skipif(shutil.which("node") is None, reason="需要 Node.js 检查滚动显示")
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
vm.runInContext(fs.readFileSync('health_by/web/js/dashboard.js', 'utf8')
  .replace(/^import [\s\S]*?from ".*?";$/gm, '').replace(/^import .*;$/gm, '').replace(/^export \{ duration as minutes \} from .*;$/gm, 'const minutes = duration;').replace(/^export /gm, '').replace(/^initLanguage\(\);$/gm, "").replace(/^init\(\);$/gm, ''), sandbox);
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
vm.runInContext(`state.summary = {analysis: {display_mode: {hrv_ms: 'daily'}, display_values: {hrv_ms: 42.7}}};`, sandbox);
sandbox.renderHeartPage({heart: {resting_hr: 64, hrv_ms: 90, hr_min: null, hr_max: null}}, null, [], {});
assert.match(nodes.get('heart-today-body').textContent, /HRV90ms/);
assert(!nodes.get('heart-today-body').textContent.includes('7 日均值'));
vm.runInContext(`state.summary = {analysis: {display_mode: {hrv_ms: 'rolling7'}, display_values: {hrv_ms: null}}};`, sandbox);
sandbox.renderHeartPage({heart: {resting_hr: 64, hrv_ms: null, hr_min: null, hr_max: null}}, null, [], {});
assert.match(nodes.get('heart-today-body').textContent, /HRV7 日均值.*近 7 天没有数据/);
"""
    result = subprocess.run(["node", "-e", script], cwd=ROOT, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr


@pytest.mark.skipif(shutil.which("node") is None, reason="渲染回归测试需要 Node.js")
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
const source = fs.readFileSync('health_by/web/js/dashboard.js', 'utf8');
const start = source.indexOf('function renderBodyPage(');
const end = source.indexOf('function renderDataPage(', start);
if (start < 0 || end < 0) throw new Error('找不到身体页渲染函数');
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
  linesBlock: () => ({}),
  windowOf: () => 14,
};
sandbox.URLSearchParams = URLSearchParams;
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync('health_by/web/js/i18n-messages.js', 'utf8').replace(/^import .*;$/gm, '').replace(/^export \{ duration as minutes \} from .*;$/gm, 'const minutes = duration;').replace(/^export /gm, ''), sandbox);
vm.runInContext(fs.readFileSync('health_by/web/js/i18n.js', 'utf8').replace(/^import .*;$/gm, '').replace(/^export \{ duration as minutes \} from .*;$/gm, 'const minutes = duration;').replace(/^export /gm, ''), sandbox);
vm.runInContext(fs.readFileSync('health_by/web/js/format.js', 'utf8')
  .replace(/^import .*;$/gm, '').replace(/^export \{ duration as minutes \} from .*;$/gm, 'const minutes = duration;').replace(/^export /gm, ''), sandbox);
vm.runInContext(source.slice(start, end), sandbox);
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
