"""运行实际 AI 页面模块，验证手动触发、异步日期隔离和纯文本输出。"""

from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parent.parent


@pytest.mark.skipif(shutil.which("node") is None, reason="需要 Node.js 执行前端模块")
def test_ai_rendering_never_autogenerates_or_displays_a_reply_for_the_wrong_date():
    script = r"""
const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict');
const nodes = new Map();
function node(tag = 'div') {
  let text = '';
  const item = {tag, children: [], hidden: false,
    get textContent() {return text + this.children.map(child => child.textContent).join('');},
    set textContent(value) {text = value; this.children = [];},
    replaceChildren() { this.children = []; }, appendChild(child) { this.children.push(child); },
    addEventListener() {}};
  Object.defineProperty(item, 'innerHTML', {set() {throw new Error('模型内容不能写入 innerHTML');}});
  return item;
}
const document = {getElementById(id) {
  if (!nodes.has(id)) nodes.set(id, node());
  return nodes.get(id);
}, createElement: node, createTextNode(text) {const item = node('#text'); item.textContent = text; return item;}};
const settings = {enabled: true, base_url: 'http://127.0.0.1:11434/v1'};
const reply = (text) => ({model: 'test-model', prompt_version: 'v1', payload_schema_version: 'daily-v2', created_at: '2026-11-01T00:00:00Z',
  content: {phenomenon: text, causes: null, advice: null}, usage: {total_tokens: 0, prompt_tokens: 0, completion_tokens: 0}});
let posts = 0, resolvePost, readError = false;
const sandbox = {document, URL,
  get: async () => {
    if (readError) throw new Error('读取失败');
    return {settings, analysis: posts ? reply('新日期 200 步') : null};
  },
  post: async () => {posts++; return new Promise(resolve => {resolvePost = resolve;});},
};
sandbox.URLSearchParams = URLSearchParams;
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync('health_by/web/js/i18n-messages.js', 'utf8').replace(/^import .*;$/gm, '').replace(/^export \{ duration as minutes \} from .*;$/gm, 'const minutes = duration;').replace(/^export /gm, ''), sandbox);
vm.runInContext(fs.readFileSync('health_by/web/js/i18n.js', 'utf8').replace(/^import .*;$/gm, '').replace(/^export \{ duration as minutes \} from .*;$/gm, 'const minutes = duration;').replace(/^export /gm, ''), sandbox);
vm.runInContext(fs.readFileSync('health_by/web/js/ai.js', 'utf8')
  .replace(/^import .*;$/gm, '').replace(/^export \{ duration as minutes \} from .*;$/gm, 'const minutes = duration;').replace(/^export /gm, ''), sandbox);
(async () => {
  await sandbox.loadAI('2026-11-01');
  assert.equal(posts, 0);
  assert.equal(nodes.get('ai-generate').textContent, '生成解读');
  const pending = sandbox.generateAI();
  await sandbox.generateAI();
  assert.equal(posts, 1); // 连点也只有一次模型调用。
  assert.equal(nodes.get('ai-generate').disabled, true);
  await sandbox.loadAI('2026-11-02');
  resolvePost(reply('旧日期 100 步'));
  await pending;
  assert.equal(nodes.get('ai-output').children[1].textContent, '新日期 200 步');
  assert.match(nodes.get('ai-meta').textContent, /0 tokens/);
  assert.match(nodes.get('ai-meta').textContent, /协议 daily-v2/);
  assert.match(nodes.get('ai-boundary').textContent, /所选日的当日指标.*7 日均值/);
  readError = true;
  await sandbox.loadAI('2026-11-03');
  assert.equal(nodes.get('ai-error').textContent, '读取失败');
  assert.equal(nodes.get('ai-output').children.length, 0);
  readError = false;
  await sandbox.loadAI('2026-11-03');
  const next = sandbox.generateAI();
  const unsafe = '<img src=x onerror=alert(1)> 300 步';
  const flagged = reply(unsafe);
  flagged.validation = {state: 'failed', issues: [{section: 'advice', reason: '没有引用数字', text: '注意休息'}], missed: [], matched: 1};
  resolvePost(flagged);
  await next;
  assert.equal(nodes.get('ai-output').children[1].textContent, unsafe);
  assert.equal(nodes.get('ai-output').children[1].tag, 'p');
  assert.equal(nodes.get('ai-validation').hidden, false);
  assert.match(nodes.get('ai-validation').children[0].textContent, /未通过校验/);
  assert.match(nodes.get('ai-validation').children[1].textContent, /注意休息/);
  const warnedPending = sandbox.generateAI();
  const warned = reply('步数 1000 步。步数 1000 步。步数 1000 步。今天状态不错。');
  warned.validation = {state: 'warn', issues: [], warnings: [{section: 'phenomenon',
    reason: '没有出处', text: '今天状态不错。'}], unsupported_sentences: 1, missed: [], matched: 3};
  resolvePost(warned);
  await warnedPending;
  assert.match(nodes.get('ai-validation').children[0].textContent, /1 句缺少出处/);
  assert(!nodes.get('ai-validation').children[0].textContent.includes('未通过'));
  assert.match(nodes.get('ai-validation').children[1].textContent, /今天状态不错/);
  assert.equal(nodes.get('ai-output').children[1].textContent, warned.content.phenomenon);
  const malformedPending = sandbox.generateAI();
  const raw = '{"phenomenon":{"unexpected":"RAW-CANARY"}}';
  const malformed = reply(raw);
  malformed.raw_content = raw;
  malformed.validation = {state: 'failed', issues: [{section: 'output', reason: '输出不是要求的三字段 JSON 对象', text: raw}], missed: [], matched: 0};
  resolvePost(malformed);
  await malformedPending;
  const children = nodes.get('ai-output').children;
  assert.match(children[0].textContent, /本次输出不符合格式要求/);
  assert.equal(children[1].tag, 'details');
  assert.equal(children[1].open, undefined); // 默认折叠。
  assert.equal(children[1].children[0].textContent, '查看原始输出');
  assert.equal(children[1].children[1].textContent, raw);
  assert(!nodes.get('ai-validation').children.some(child => child.textContent.includes('RAW-CANARY')));
  const truncatedPending = sandbox.generateAI();
  const truncated = reply('{"advice":"unfinished');
  truncated.raw_content = truncated.content.phenomenon;
  truncated.finish_reason = 'length';
  truncated.truncated = true;
  truncated.validation = {state: 'failed', max_tokens: 2000, issues: [{section: 'output', reason: '输出被截断', text: ''}]};
  resolvePost(truncated);
  await truncatedPending;
  assert.match(nodes.get('ai-validation').children[0].textContent, /输出被截断.*2,000/);
  assert(!nodes.get('ai-output').children[0].textContent.includes('格式'));
  assert.match(nodes.get('ai-meta').textContent, /结束原因 length/);
})().catch(error => {console.error(error); process.exitCode = 1;});
"""
    result = subprocess.run(["node", "-e", script], cwd=ROOT, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr


@pytest.mark.skipif(shutil.which("node") is None, reason="需要 Node.js 检查实际排版脚本")
def test_ai_layout_preserves_text_and_conditions_with_safe_highlights_and_folded_evidence():
    script = r"""
const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict');
const nodes = new Map();
function node(tag = 'div') {
  let text = '';
  return {tag, children: [], hidden: false,
    get textContent() {return text + this.children.map(child => child.textContent).join('');},
    set textContent(value) {text = value; this.children = [];},
    set innerHTML(value) {throw new Error('禁止解析模型 HTML');},
    replaceChildren() {text = ''; this.children = [];},
    appendChild(child) {this.children.push(child);}, addEventListener() {}};
}
const document = {getElementById(id) {if (!nodes.has(id)) nodes.set(id, node()); return nodes.get(id);},
  createElement: node, createTextNode(text) {const item = node('#text'); item.textContent = text; return item;}};
const content = {
  phenomenon: 'D-12 步数 2,100；差值 −2.5%，2026-01-02 的记录仍待核对。',
  causes: '<img src=x onerror=alert(1)> &lt;script&gt; 不能据此判断。',
  advice: '若 HRV 低于 36.5，先回撤强度。今晚 22:15 前准备睡觉。先补水。然后补齐记录，若仍不完整就等明天。',
};
let reply = {content, validation: {state: 'ok', referenced_fields: ['metrics.activity.steps', '睡眠时长', '<img onerror=alert(9)>']},
  usage: {}, model: 'synthetic', prompt_version: 'v3', created_at: '2026-01-02T00:00:00Z'};
const sandbox = {document, URL, get: async () => ({settings: {enabled: true}, analysis: reply}),
  post: () => {throw new Error('排版不能触发模型');}};
sandbox.URLSearchParams = URLSearchParams;
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync('health_by/web/js/i18n-messages.js', 'utf8').replace(/^import .*;$/gm, '').replace(/^export \{ duration as minutes \} from .*;$/gm, 'const minutes = duration;').replace(/^export /gm, ''), sandbox);
vm.runInContext(fs.readFileSync('health_by/web/js/i18n.js', 'utf8').replace(/^import .*;$/gm, '').replace(/^export \{ duration as minutes \} from .*;$/gm, 'const minutes = duration;').replace(/^export /gm, ''), sandbox);
vm.runInContext(fs.readFileSync('health_by/web/js/ai.js', 'utf8')
  .replace(/^import .*;$/gm, '').replace(/^export \{ duration as minutes \} from .*;$/gm, 'const minutes = duration;').replace(/^export /gm, ''), sandbox);
const descendants = host => [host, ...host.children.flatMap(descendants)];
(async () => {
  await sandbox.loadAI('2026-01-02');
  const children = nodes.get('ai-output').children;
  assert.deepEqual(children.filter(child => child.tag === 'h3').map(child => child.textContent), ['现象', '可能原因', '建议']);
  assert.equal(children[1].textContent, content.phenomenon);
  assert.equal(children[3].textContent, content.causes);
  assert.deepEqual(children[1].children.filter(child => child.tag === 'strong').map(child => child.textContent),
    ['D-12', '2,100', '−2.5%', '2026-01-02']);
  const list = children.find(child => child.tag === 'ol');
  assert.equal(list.children.length, 3);
  assert(list.children.every(child => child.tag === 'li'));
  assert.equal(list.children[0].textContent, '若 HRV 低于 36.5，先回撤强度。'); // 小数不拆句，条件不删。
  const rest = children.find(child => child.tag === 'details' && child.children[0].textContent === '其余建议');
  assert(!rest.open);
  assert.equal(list.children.map(child => child.textContent).join('') + rest.children[1].textContent, content.advice);
  const evidence = children.find(child => child.className === 'ai-evidence');
  assert(!evidence.open);
  assert.equal(evidence.children[0].tag, 'summary');
  assert.equal(evidence.children[0].textContent, '依据（引用了哪些字段）');
  assert.deepEqual(evidence.children[1].children.map(child => child.textContent), reply.validation.referenced_fields);
  assert(!descendants(nodes.get('ai-output')).some(child => ['img', 'script'].includes(child.tag)));

  // 只去当前分句起始序号，不替换正文中的编号、换行或英文句点。
  reply = {...reply, content: {...content, advice: '1. 先补齐记录\n2. 若仍缺测，再等待。'}, validation: {state: 'ok'}};
  await sandbox.loadAI('2026-01-02');
  const single = nodes.get('ai-output').children.find(child => child.tag === 'ol');
  assert.equal(single.children.length, 1);
  assert.equal(single.textContent, '先补齐记录\n2. 若仍缺测，再等待。');
  const noFields = nodes.get('ai-output').children.find(child => child.className === 'ai-evidence');
  assert.match(noFields.textContent, /未记录引用字段/);
  assert(!noFields.open);

  for (const mark of ['.', '、', ')', '．']) {
    const first = '今晚补齐睡眠记录。', second = '明天走到 8,600 步。';
    reply = {...reply, content: {...content, advice: `1${mark} ${first}2${mark} ${second}`}};
    await sandbox.loadAI('2026-01-02');
    const numbered = nodes.get('ai-output').children.find(child => child.tag === 'ol');
    assert.deepEqual(numbered.children.map(child => child.textContent), [first, second]);
  }
  reply = {...reply, content: {...content, advice: '1.5 小时不是 5 小时。2.5 公里不是 5 公里。还差 1,234 步。4) 若仍缺测，再核对记录。5、保留 23:15 的时间。'}};
  await sandbox.loadAI('2026-01-02');
  const numberedChildren = nodes.get('ai-output').children;
  const quantities = numberedChildren.find(child => child.tag === 'ol');
  assert.deepEqual(quantities.children.map(child => child.textContent),
    ['1.5 小时不是 5 小时。', '2.5 公里不是 5 公里。', '还差 1,234 步。']);
  const numberedRest = numberedChildren.find(child => child.tag === 'details' && child.children[0].textContent === '其余建议');
  assert.equal(numberedRest.children[1].textContent, '若仍缺测，再核对记录。保留 23:15 的时间。');
  assert(!numberedRest.open);
})().catch(error => {console.error(error); process.exitCode = 1;});
"""
    result = subprocess.run(["node", "-e", script], cwd=ROOT, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr


@pytest.mark.skipif(shutil.which("node") is None, reason="需要 Node.js 执行设置页")
def test_connection_ui_requires_saved_configuration_and_keeps_success_and_errors_visible():
    script = r"""
const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict');
const nodes = new Map();
const node = () => ({value: '', checked: false, textContent: '', dataset: {}, children: [],
  replaceChildren(...items) {this.children = items;}, appendChild(item) {this.children.push(item);},
  addEventListener() {}});
const document = {getElementById(id) {if (!nodes.has(id)) nodes.set(id, node()); return nodes.get(id);},
  createElement: node, querySelectorAll: () => []};
let ai = {enabled: false, base_url: 'https://a.example/v1', model: 'model-a', has_api_key: true};
const probes = [];
let defer = false, finish;
let reply = {ok: true, status: 200, detail: '连接正常', model: 'model-b', latency_ms: 1200};
const sandbox = {document, URL, toast() {},
  get: async path => path === '/settings' ? {ai: {...ai}, goals: {steps: 8000, sleep_minutes: 480},
    google_health: {}, demo: {active: true, days_stored: 0}, sync_interval_minutes: 0} : {runs: []},
  post: async (path, body) => {
    if (path === '/settings/ai') {
      ai = {...ai, enabled: body.ai_enabled, base_url: body.ai_base_url, model: body.ai_model};
      return ai;
    }
    assert.equal(path, '/ai/test');
    assert.equal(Object.keys(body).length, 0); // 不把未保存的 key 或模型偷偷发到测试端点。
    probes.push(ai.model);
    return defer ? new Promise(resolve => {finish = resolve;}) : reply;
  },
};
sandbox.URLSearchParams = URLSearchParams;
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync('health_by/web/js/i18n-messages.js', 'utf8').replace(/^import .*;$/gm, '').replace(/^export \{ duration as minutes \} from .*;$/gm, 'const minutes = duration;').replace(/^export /gm, ''), sandbox);
vm.runInContext(fs.readFileSync('health_by/web/js/i18n.js', 'utf8').replace(/^import .*;$/gm, '').replace(/^export \{ duration as minutes \} from .*;$/gm, 'const minutes = duration;').replace(/^export /gm, ''), sandbox);
vm.runInContext(fs.readFileSync('health_by/web/js/settings.js', 'utf8')
  .replace(/^import .*;$/gm, '').replace(/^initLanguage\(\);$/gm, "").replace(/\ninit\(\);\s*$/, ''), sandbox);
(async () => {
  await sandbox.refresh();
  assert.equal(nodes.get('ai-test').disabled, false); // 关闭健康分析也能先测配置。
  nodes.get('ai-model').value = 'model-b';
  nodes.get('ai-base-url').value = 'https://b.example/v1';
  sandbox.markAIConfigDirty();
  assert.equal(nodes.get('ai-test').disabled, true);
  await sandbox.testAIConnection();
  assert.equal(probes.length, 0);
  assert.match(nodes.get('ai-test-result').textContent, /先保存/);
  await sandbox.saveAI();
  assert.equal(ai.model, 'model-b');
  assert.equal(nodes.get('ai-test').disabled, false);
  await sandbox.testAIConnection();
  assert.deepEqual(probes, ['model-b']);
  assert.equal(nodes.get('ai-test-result').textContent, '连接正常 · 1.2s · model-b');
  assert.equal(nodes.get('ai-test-result').dataset.state, 'ok');
  reply = {ok: true, status: 200, detail: '连接正常', model: 'model-b', latency_ms: 1599,
    warning: '连接正常，但模型没有返回内容；生成分析时若仍为空，考虑换模型。'};
  await sandbox.testAIConnection();
  assert.match(nodes.get('ai-test-result').textContent, /连接正常 · 1.6s · model-b/);
  assert.match(nodes.get('ai-test-result').textContent, /模型没有返回内容/);
  assert.equal(nodes.get('ai-test-result').dataset.state, 'ok');
  defer = true;
  const pending = sandbox.testAIConnection();
  nodes.get('ai-model').value = 'model-c';
  sandbox.markAIConfigDirty();
  finish(reply);
  await pending;
  assert.match(nodes.get('ai-test-result').textContent, /配置已修改/);
  assert.equal(nodes.get('ai-test').disabled, true);
  await sandbox.saveAI();
  defer = false;
  reply = {ok: false, status: 402, detail: '额度不足 —— 换 key 或补充额度。', model: 'model-c', latency_ms: 200};
  await sandbox.testAIConnection();
  assert.match(nodes.get('ai-test-result').textContent, /额度不足/);
  assert.match(nodes.get('ai-test-result').textContent, /HTTP 402/);
  assert.match(nodes.get('ai-test-result').textContent, /model-c/);
  assert.equal(nodes.get('ai-test-result').dataset.state, 'error');
})().catch(error => {console.error(error); process.exitCode = 1;});
"""
    result = subprocess.run(["node", "-e", script], cwd=ROOT, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr


@pytest.mark.skipif(shutil.which("node") is None, reason="需要 Node.js 执行设置页")
def test_model_picker_preserves_manual_entry_and_discards_stale_lists():
    script = r"""
const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict');
const nodes = new Map();
const node = () => ({value: '', checked: false, textContent: '', dataset: {}, children: [], events: {},
  replaceChildren(...items) {this.children = items;}, appendChild(item) {this.children.push(item);},
  addEventListener(name, handler) {this.events[name] = handler;}});
const document = {getElementById(id) {if (!nodes.has(id)) nodes.set(id, node()); return nodes.get(id);},
  createElement: node, querySelectorAll: () => []};
let ai = {enabled: false, base_url: 'https://model.example/v1', model: '', has_api_key: true};
let modelReply = {ok: true, status: 200, detail: '已获取模型列表', models: [
  {id: 'saved-id', name: 'Readable model'}, {id: 'only-id', name: 'only-id'}]};
let deferred = false, finish;
const calls = [];
const sandbox = {document, URL, toast() {}, window: {},
  get: async path => path === '/settings' ? {ai: {...ai}, goals: {}, google_health: {}, demo: {}, sync_interval_minutes: 0} : {runs: []},
  post: async (path, body) => {
    if (path === '/settings/ai') {
      ai = {...ai, enabled: body.ai_enabled, base_url: body.ai_base_url, model: body.ai_model};
      return ai;
    }
    assert.equal(path, '/ai/models');
    assert.equal(Object.keys(body).length, 0);
    calls.push(ai.base_url);
    return deferred ? new Promise(resolve => {finish = resolve;}) : modelReply;
  },
};
sandbox.URLSearchParams = URLSearchParams;
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync('health_by/web/js/i18n-messages.js', 'utf8').replace(/^import .*;$/gm, '').replace(/^export \{ duration as minutes \} from .*;$/gm, 'const minutes = duration;').replace(/^export /gm, ''), sandbox);
vm.runInContext(fs.readFileSync('health_by/web/js/i18n.js', 'utf8').replace(/^import .*;$/gm, '').replace(/^export \{ duration as minutes \} from .*;$/gm, 'const minutes = duration;').replace(/^export /gm, ''), sandbox);
vm.runInContext(fs.readFileSync('health_by/web/js/settings.js', 'utf8')
  .replace(/^import .*;$/gm, '').replace(/^initLanguage\(\);$/gm, "").replace(/\ninit\(\);\s*$/, ''), sandbox);
(async () => {
  sandbox.bind();
  await sandbox.refresh();
  assert.equal(calls.length, 0); // 打开设置页不自动发外部请求。
  await nodes.get('ai-models').events.click();
  const list = nodes.get('ai-model-list');
  assert.equal(list.hidden, false);
  assert.equal(list.children[1].textContent, 'Readable model');
  assert.equal(list.children[1].value, 'saved-id');
  assert.equal(nodes.get('ai-model').value, ''); // 不擅自选第一个。
  list.value = 'saved-id';
  list.events.change();
  assert.equal(nodes.get('ai-model').value, 'saved-id');
  assert.equal(nodes.get('ai-base-url').value, 'https://model.example/v1');
  assert.equal(nodes.get('ai-models').disabled, true); // 选择后仍需保存。
  await sandbox.saveAI();
  assert.equal(ai.model, 'saved-id');
  for (const detail of ['该地址没有模型列表接口，请手动填写模型名。', '密钥无效或已过期', '连不上该地址']) {
    modelReply = {ok: false, status: 404, detail, models: []};
    await sandbox.loadAIModels();
    assert.equal(nodes.get('ai-models-note').textContent, detail);
    assert.equal(list.hidden, true);
    assert.notEqual(nodes.get('ai-model').disabled, true);
    assert.equal(nodes.get('ai-save').disabled, false);
  }
  nodes.get('ai-model').value = 'manual-id';
  nodes.get('ai-model').events.input();
  await sandbox.saveAI();
  assert.equal(ai.model, 'manual-id');
  deferred = true;
  const pending = sandbox.loadAIModels();
  nodes.get('ai-base-url').value = 'https://changed.example/v1';
  nodes.get('ai-base-url').events.input();
  finish({ok: true, detail: 'old provider', models: [{id: 'stale-id', name: 'stale'}]});
  await pending;
  assert.equal(list.hidden, true);
  assert.equal(list.children.length, 0);
  assert.equal(nodes.get('ai-model').value, 'manual-id');
  assert.equal(nodes.get('ai-models').disabled, true);
})().catch(error => {console.error(error); process.exitCode = 1;});
"""
    result = subprocess.run(["node", "-e", script], cwd=ROOT, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr


@pytest.mark.skipif(shutil.which('node') is None, reason='需要 Node.js 执行窗口控件')
def test_window_selection_only_reads_cache_and_never_displays_an_old_window_reply():
    script = r"""
const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict');
const nodes = new Map();
const node = () => ({value: '', _text: '', children: [], events: {}, hidden: false,
  get textContent() {return this._text + this.children.map(child => child.textContent).join('');},
  set textContent(value) {this._text = value; this.children = [];},
  replaceChildren() {this.children = [];}, appendChild(item) {this.children.push(item);},
  addEventListener(name, handler) {this.events[name] = handler;}});
const document = {getElementById(id) {if (!nodes.has(id)) nodes.set(id, node()); return nodes.get(id);},
  createElement: node, createTextNode: text => ({textContent: text})};
const settings = {enabled: true, base_url: 'https://model.example/v1', max_window_days: 366};
const reads = [], posts = [];
let finish, rejectRead = false;
const sandbox = {document, URL, get: async path => {
  reads.push(path);
  if (rejectRead) throw new Error('开始日期不能晚于结束日期');
  return {settings, analysis: null};
}, post: async (path, body) => {posts.push({...body}); return new Promise(resolve => {finish = resolve;});}};
sandbox.URLSearchParams = URLSearchParams;
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync('health_by/web/js/i18n-messages.js', 'utf8').replace(/^import .*;$/gm, '').replace(/^export \{ duration as minutes \} from .*;$/gm, 'const minutes = duration;').replace(/^export /gm, ''), sandbox);
vm.runInContext(fs.readFileSync('health_by/web/js/i18n.js', 'utf8').replace(/^import .*;$/gm, '').replace(/^export \{ duration as minutes \} from .*;$/gm, 'const minutes = duration;').replace(/^export /gm, ''), sandbox);
vm.runInContext(fs.readFileSync('health_by/web/js/ai.js', 'utf8').replace(/^import .*;$/gm, '').replace(/^export \{ duration as minutes \} from .*;$/gm, 'const minutes = duration;').replace(/^export /gm, ''), sandbox);
(async () => {
  sandbox.bindAI();
  await sandbox.loadAI('2026-11-01');
  assert.match(reads.at(-1), /start=2026-11-01&end=2026-11-01/);
  nodes.get('ai-window-preset').value = '7';
  await nodes.get('ai-window-preset').events.change();
  assert.match(reads.at(-1), /start=2026-10-26&end=2026-11-01/);
  assert.equal(posts.length, 0); // 切区间不调用模型。
  const pending = sandbox.generateAI();
  assert.equal(posts[0].start, '2026-10-26');
  assert.equal(posts[0].end, '2026-11-01');
  nodes.get('ai-window-start').value = '2026-10-20';
  nodes.get('ai-window-end').value = '2026-10-25';
  nodes.get('ai-window-start').events.change();
  assert.equal(nodes.get('ai-generate').disabled, true);
  await sandbox.applyAIWindow();
  finish({content: {phenomenon: 'OLD-WINDOW', causes: null, advice: null}, usage: {total_tokens: 0}});
  await pending;
  assert.equal(nodes.get('ai-output').children.length, 0);
  const current = sandbox.generateAI();
  assert.equal(posts[1].start, '2026-10-20');
  assert.equal(posts[1].end, '2026-10-25');
  finish({content: {phenomenon: 'NEW-WINDOW', causes: null, advice: null}, usage: {total_tokens: 0},
    start_date: '2026-10-20', end_date: '2026-10-25'});
  await current;
  assert.equal(nodes.get('ai-output').children[1].textContent, 'NEW-WINDOW');
  assert.match(nodes.get('ai-meta').textContent, /2026-10-20 至 2026-10-25/);
  rejectRead = true;
  await sandbox.applyAIWindow();
  assert.equal(nodes.get('ai-generate').disabled, true);
  assert.match(nodes.get('ai-error').textContent, /开始日期不能/);
  await sandbox.generateAI();
  assert.equal(posts.length, 2);
})().catch(error => {console.error(error); process.exitCode = 1;});
"""
    result = subprocess.run(['node', '-e', script], cwd=ROOT, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
