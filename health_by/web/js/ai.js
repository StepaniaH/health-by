import { t, formatDateTime } from "./i18n.js";
// 读取缓存不会调用模型；只有用户点击按钮才生成。
import { get, post } from "./api.js";

const $ = (id) => document.getElementById(id);
const ai = { date: null, analysis: null, settings: null, loading: false, busy: false, error: "", token: 0, start: null, end: null, windowDirty: false, validWindow: false };

function appendHighlightedText(host, text) {
  // 只创建文本节点；模型的标签、实体和 Markdown 都不能变成可执行 HTML。
  const tokens = /D[-+]\d+|\d{4}-\d{2}-\d{2}|\d{1,2}:\d{2}|[-+−]?\d+(?:,\d{3})*(?:\.\d+)?[%％]?/g;
  let offset = 0;
  for (const match of text.matchAll(tokens)) {
    host.appendChild(document.createTextNode(text.slice(offset, match.index)));
    const strong = document.createElement("strong");
    strong.textContent = match[0];
    host.appendChild(strong);
    offset = match.index + match[0].length;
  }
  host.appendChild(document.createTextNode(text.slice(offset)));
}

function renderAdvice(text) {
  // 只去掉分句开头的列表序号；小数、句中数量、标点和条件句保持原样。
  const sentences = (text.match(/[^。]+(?:。+|$)|。+/g) || [])
    .map(raw => raw.replace(/^\s*\d+\s*[.、)．](?!\d)\s*/, ""));
  const list = document.createElement("ol");
  list.className = "ai-advice";
  for (const sentence of sentences.slice(0, 3)) {
    const item = document.createElement("li");
    appendHighlightedText(item, sentence);
    list.appendChild(item);
  }
  if (sentences.length > 3) {
    // 主列表最多三条；其余原文仍可展开查看，避免丢失限制条件。
    const details = document.createElement("details");
    const summary = document.createElement("summary");
    summary.textContent = t("web.ai.renderAdvice.beb3daa2f");
    const rest = document.createElement("p");
    appendHighlightedText(rest, sentences.slice(3).join(""));
    details.appendChild(summary);
    details.appendChild(rest);
    return [list, details];
  }
  return [list];
}

function renderEvidence(validation) {
  const details = document.createElement("details");
  details.className = "ai-evidence";
  const summary = document.createElement("summary");
  summary.textContent = t("web.ai.renderEvidence.a1d6ff336");
  details.appendChild(summary);
  const fields = Array.isArray(validation?.referenced_fields)
    ? [...new Set(validation.referenced_fields.filter((field) => typeof field === "string"))] : [];
  if (fields.length) {
    const list = document.createElement("ul");
    for (const field of fields) {
      const item = document.createElement("li");
      item.textContent = field;
      list.appendChild(item);
    }
    details.appendChild(list);
  } else {
    const note = document.createElement("p");
    note.textContent = t("web.ai.renderEvidence.17d6fbde6");
    details.appendChild(note);
  }
  return details;
}

export function renderAI() {
  const button = $("ai-generate");
  button.disabled = ai.loading || ai.busy || ai.windowDirty || !ai.validWindow || !ai.settings?.enabled;
  $("ai-window-note").textContent = ai.windowDirty ? t("web.ai.renderAI.2c774e75b")
    : ai.start && ai.end ? t("web.ai.renderAI.ee98d4582", {p1: ai.start, p2: ai.end, p3: ai.settings?.max_window_days || 366}) : t("web.ai.renderAI.a1c44dc27");
  button.textContent = ai.busy ? t("web.ai.renderAI.43d2661b3") : (ai.analysis ? t("web.ai.renderAI.3221a042e") : t("web.ai.renderAI.b932e2eca"));
  $("ai-error").textContent = ai.error;
  $("ai-error").hidden = !ai.error;
  $("ai-status").textContent = ai.windowDirty ? t("web.ai.renderAI.4cf21e686") : ai.loading ? t("web.ai.renderAI.93caf76e1")
    : ai.busy ? t("web.ai.renderAI.723f49e18")
    : !ai.settings?.enabled ? t("web.ai.renderAI.fbce4977e")
    : ai.analysis ? "" : t("web.ai.renderAI.a3c8ef7f3");
  const output = $("ai-output");
  output.replaceChildren();
  const result = ai.analysis;
  const validationHost = $("ai-validation");
  validationHost.replaceChildren();
  const validation = result?.validation;
  const truncated = result?.truncated || validation?.truncated || result?.finish_reason === "length";
  const malformed = validation?.issues?.some((issue) => issue.section === "output" || issue.reason.includes(t("web.ai.renderAI.2e1bdc294")));
  validationHost.hidden = !validation || validation.state === "ok";
  if (validation && validation.state !== "ok") {
    const title = document.createElement("p");
    title.textContent = truncated
      ? t("web.ai.renderAI.d56c3f79c", {p1: validation.max_tokens ?? t("web.ai.renderAI.4d8c1c5b4")})
      : validation.state === "failed"
      ? t("web.ai.renderAI.5a720b390")
      : validation.unsupported_sentences
        ? t("web.ai.renderAI.491f42798", {p1: validation.unsupported_sentences})
        : t("web.ai.renderAI.f3aa87bd6");
    validationHost.appendChild(title);
    for (const issue of [...(validation.issues || []), ...(validation.warnings || [])]) {
      const note = document.createElement("p");
      note.textContent = (issue.text && !malformed ? `${issue.section}：${issue.reason}${` — ${issue.text}`}` : `${issue.section}：${issue.reason}${""}`);
      validationHost.appendChild(note);
    }
    if (validation.missed?.length) {
      const missed = document.createElement("p");
      missed.textContent = t("web.ai.renderAI.9232193b4", {p1: validation.missed.join(t('separator.list')), p2: validation.matched_numbers ?? validation.matched});
      validationHost.appendChild(missed);
    }
  }
  if (result) {
    if (malformed) {
      const explanation = document.createElement("p");
      explanation.textContent = truncated
        ? t("web.ai.renderAI.5911bd2fc")
        : t("web.ai.renderAI.1964a4a20");
      output.appendChild(explanation);
    }
    for (const [key, label] of (malformed ? [] : [["phenomenon", t("web.ai.renderAI.3aa469bab")], ["causes", t("web.ai.renderAI.4141e844b")], ["advice", t("web.ai.renderAI.abee49747")]])) {
      if (!result.content[key]) continue;
      const title = document.createElement("h3");
      title.textContent = label;
      output.appendChild(title);
      if (key === "advice") {
        for (const node of renderAdvice(result.content[key])) output.appendChild(node);
      } else {
        const text = document.createElement("p");
        appendHighlightedText(text, result.content[key]);
        output.appendChild(text);
      }
    }
    if (!malformed) output.appendChild(renderEvidence(validation));
    if (validation?.state === "failed") {
      const details = document.createElement("details");
      const summary = document.createElement("summary");
      summary.textContent = t("web.ai.renderAI.217681696");
      const raw = document.createElement("pre");
      raw.className = "ai-raw";
      raw.textContent = result.raw_content || "";
      details.appendChild(summary);
      details.appendChild(raw);
      output.appendChild(details);
    }
    const usage = result.usage || {};
    const tokens = usage.total_tokens === null || usage.total_tokens === undefined
      ? t("web.ai.renderAI.d31a696af") : t("web.ai.renderAI.14ff47e5b", {p1: usage.total_tokens, p2: usage.prompt_tokens ?? "—", p3: usage.completion_tokens ?? "—"});
    const created = formatDateTime(result.created_at);
    const meta = [
      t("ai.meta.model", { model: result.model }),
      t("ai.meta.created", { created }), tokens,
      t("ai.meta.attempts", { n: result.attempts || 1, count: result.attempts || 1 }),
      t("ai.meta.range", { start: result.start_date || result.date || ai.start, end: result.end_date || result.date || ai.end }),
      t("ai.meta.prompt", { version: result.prompt_version }),
    ];
    if (result.payload_schema_version) meta.push(t("ai.meta.protocol", { version: result.payload_schema_version }));
    meta.push(t("ai.meta.finish", { reason: result.finish_reason || t("web.ai.renderAI.4d8c1c5b4") }));
    $("ai-meta").textContent = meta.join(" · ");
  } else $("ai-meta").textContent = "";
  let host = t("web.ai.renderAI.97caec274");
  try { if (ai.settings?.base_url) host = new URL(ai.settings.base_url).host; } catch { /* 保留通用名称 */ }
  const scope = ai.start === ai.end ? t("web.ai.renderAI.4b4e02bde") : t("web.ai.renderAI.cfd52f66e");
  $("ai-boundary").textContent = t("web.ai.renderAI.453a84795", {host: host, scope: scope});
}

function offsetDate(date, days) {
  const value = new Date(`${date}T00:00:00Z`);
  value.setUTCDate(value.getUTCDate() + days);
  return value.toISOString().slice(0, 10);
}

function presetDates() {
  const choice = $("ai-window-preset").value || "1";
  if (choice === "custom") return;
  $("ai-window-preset").value = choice;
  $("ai-window-start").value = offsetDate(ai.date, -(Number(choice) - 1));
  $("ai-window-end").value = ai.date;
}

export function markAIWindowDirty() {
  $("ai-window-preset").value = "custom";
  ai.windowDirty = true;
  ai.validWindow = false;
  ai.analysis = null;
  ai.loading = false;
  ai.error = "";
  ai.token += 1;
  renderAI();
}

export async function applyAIWindow() {
  const start = $("ai-window-start").value;
  const end = $("ai-window-end").value;
  const token = ++ai.token;
  ai.start = start;
  ai.end = end;
  ai.windowDirty = false;
  ai.validWindow = false;
  ai.analysis = null;
  ai.loading = true;
  ai.error = "";
  renderAI();
  try {
    const result = await get(`/ai/analysis?start=${encodeURIComponent(start || "")}&end=${encodeURIComponent(end || "")}`);
    if (token !== ai.token) return;
    ai.analysis = result.analysis;
    ai.settings = result.settings;
    ai.validWindow = true;
  } catch (error) {
    if (token === ai.token) ai.error = error.message;
  } finally {
    if (token === ai.token) { ai.loading = false; renderAI(); }
  }
}

export async function loadAI(date) {
  ai.date = date;
  if (!$("ai-window-start").value || !$("ai-window-end").value) $("ai-window-preset").value = "1";
  presetDates();
  return applyAIWindow();
}

export async function generateAI() {
  if (ai.busy || ai.loading || ai.windowDirty || !ai.validWindow || !ai.settings?.enabled) return;
  const start = ai.start, end = ai.end;
  const token = ai.token;
  ai.busy = true;
  ai.error = "";
  renderAI();
  try {
    const result = await post("/ai/analyze", { start, end, force: Boolean(ai.analysis) });
    if (start === ai.start && end === ai.end && token === ai.token) ai.analysis = result;
  } catch (error) {
    if (start === ai.start && end === ai.end && token === ai.token) ai.error = error.message;
  } finally {
    ai.busy = false;
    renderAI();
  }
}

export function bindAI() {
  $("ai-generate").addEventListener("click", generateAI);
  $("ai-window-apply").addEventListener("click", applyAIWindow);
  $("ai-window-start").addEventListener("change", markAIWindowDirty);
  $("ai-window-end").addEventListener("change", markAIWindowDirty);
  $("ai-window-preset").addEventListener("change", () => {
    if ($("ai-window-preset").value === "custom") { markAIWindowDirty(); return; }
    presetDates();
    return applyAIWindow();
  });
}
