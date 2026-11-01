import { t, initLanguage, formatTimestampISO } from "./i18n.js";
// 设置页：数据源 / Google Health 连接 / 目标 / 同步 / 数据管理
import { get, post, toast } from "./api.js";

const $ = (id) => document.getElementById(id);
let aiConfigRevision = 0;
let aiSavedRevision = 0;
let aiSaving = false;
let aiTesting = false;
let aiHasKey = false;
let aiModelsLoading = false;

function updateAIButtons() {
  const busy = aiSaving || aiTesting || aiModelsLoading;
  $("ai-save").disabled = busy;
  $("ai-clear-key").disabled = busy || !aiHasKey;
  $("ai-test").disabled = busy || aiConfigRevision !== aiSavedRevision;
  $("ai-models").disabled = busy || aiConfigRevision !== aiSavedRevision;
}

function clearAIModels() {
  $("ai-model-picker").hidden = true;
  $("ai-model-list").replaceChildren();
  $("ai-model-list").hidden = true;
  $("ai-models-note").textContent = t("web.settings.clearAIModels.0b2051ff8");
}

async function loadAIModels() {
  if (aiSaving || aiTesting || aiModelsLoading || aiConfigRevision !== aiSavedRevision) return;
  const revision = aiConfigRevision;
  aiModelsLoading = true;
  clearAIModels();
  $("ai-models-note").textContent = t("web.settings.loadAIModels.da19bfa8a");
  updateAIButtons();
  try {
    const result = await post("/ai/models", {});
    if (revision !== aiConfigRevision) return;
    $("ai-models-note").textContent = result.detail;
    if (!result.ok || !result.models.length) return;
    const list = $("ai-model-list");
    const placeholder = document.createElement("option");
    placeholder.value = "";
    placeholder.textContent = t("web.settings.loadAIModels.2e8ff0603");
    list.appendChild(placeholder);
    for (const model of result.models) {
      const option = document.createElement("option");
      option.value = model.id;
      option.textContent = model.name || model.id;
      list.appendChild(option);
    }
    list.value = "";
    list.hidden = false;
    $("ai-model-picker").hidden = false;
  } catch (error) {
    if (revision === aiConfigRevision) $("ai-models-note").textContent = t("web.settings.loadAIModels.122ec64da", {p1: error.message});
  } finally {
    aiModelsLoading = false;
    updateAIButtons();
  }
}

function selectAIModel() {
  const value = $("ai-model-list").value;
  if (!value) return;
  $("ai-model").value = value;
  markAIConfigDirty();
}

function aiTestNote(text, ok = null) {
  $("ai-test-result").textContent = text;
  $("ai-test-result").dataset.state = ok === null ? "" : (ok ? "ok" : "error");
}

function markAIConfigDirty() {
  aiConfigRevision += 1;
  aiTestNote(t("web.settings.markAIConfigDirty.9543ac646"));
  updateAIDisclosure();
  updateAIButtons();
}

function chip(text, klass = "") {
  const span = document.createElement("span");
  span.className = `chip ${klass}`.trim();
  span.textContent = text;
  return span;
}

function selectedProvider() {
  const checked = document.querySelector('input[name="provider"]:checked');
  return checked ? checked.value : "demo";
}

function updateSleepHours() {
  const value = Number($("goal-sleep").value || 0);
  $("goal-sleep-hours").textContent = value > 0 ? t("web.settings.updateSleepHours.1e2023b22", {p1: Math.floor(value / 60), p2: value % 60}) : "—";
}

// ---------- 加载 ----------

async function refresh() {
  const settings = await get("/settings");
  document.querySelectorAll('input[name="provider"]').forEach((radio) => {
    radio.checked = radio.value === settings.provider;
  });

  const gh = settings.google_health;
  $("gh-client-id").value = gh.client_id || "";
  $("gh-client-secret").value = "";
  $("gh-client-secret").placeholder = gh.has_client_secret
    ? t("web.settings.refresh.9ec71a349")
    : t("web.settings.refresh.3e9ba4222");
  $("gh-redirect-uri").value = gh.redirect_uri || settings.default_redirect_uri;

  const statusRow = $("gh-status");
  statusRow.replaceChildren(
    chip(gh.configured ? t("web.settings.refresh.99381639f") : t("web.settings.refresh.bbcaaa9e8"), gh.configured ? "ok" : ""),
    chip(gh.connected ? t("web.settings.refresh.5be0323e8") : t("web.settings.refresh.3d52574ce"), gh.connected ? "ok" : "warn"),
  );
  if (gh.needs_reconnect) statusRow.appendChild(chip(t("web.settings.refresh.cf234f87d"), "warn"));

  $("goal-steps").value = settings.goals.steps ?? 8000;
  $("goal-sleep").value = settings.goals.sleep_minutes ?? 480;
  updateSleepHours();

  $("sync-interval").value = String(settings.sync_interval_minutes ?? 360);
  const ai = settings.ai;
  $("ai-enabled").checked = ai.enabled;
  $("ai-base-url").value = ai.base_url;
  $("ai-model").value = ai.model;
  $("ai-key").value = "";
  $("ai-key").placeholder = ai.has_api_key ? t("web.settings.refresh.5de5dc433") : t("web.settings.refresh.f4803be34");
  aiHasKey = ai.has_api_key;
  clearAIModels();
  aiSavedRevision = ++aiConfigRevision;
  aiTestNote(t("web.settings.refresh.20de0829b"));
  updateAIButtons();
  $("ai-settings-note").textContent = (ai.enabled ? (ai.has_api_key ? t("web.settings.refresh.a990f2327", {}) : t("web.settings.refresh.bf90960d4", {})) : (ai.has_api_key ? t("web.settings.refresh.c2147e5dc", {}) : t("web.settings.refresh.151ddca74", {})));
  updateAIDisclosure();

  $("demo-note").hidden = !settings.demo.active;
  $("demo-note").textContent = settings.demo.active
    ? t("web.settings.refresh.a7c4551b0", {p1: settings.demo.days_stored})
    : t("web.settings.refresh.677c1db9e");

  $("about").textContent = `Health By v${settings.version || ""}`;

  await loadRuns();
}

async function loadRuns() {
  try {
    const payload = await get("/sync/runs?limit=6");
    const runs = payload.runs || [];
    const container = $("sync-runs");
    if (!runs.length) { container.replaceChildren(); return; }
    const table = document.createElement("table");
    table.className = "runs-table";
    const head = document.createElement("thead");
    const headings = document.createElement("tr");
    for (const key of ["sync.column.time", "sync.column.date", "sync.column.status", "sync.column.coverage"]) {
      const cell = document.createElement("th"); cell.textContent = t(key); headings.appendChild(cell);
    }
    head.appendChild(headings); table.appendChild(head);
    const body = document.createElement("tbody");
    for (const run of runs) {
      const row = document.createElement("tr");
      const statusText = run.status === "ok" ? t("web.settings.loadRuns.053461ce8") : run.status === "partial" ? t("web.settings.loadRuns.553dc98d8") : t("web.settings.loadRuns.28384d7af");
      // 窄屏没有表头（见 app.css 的 .runs-table 断点规则），所以状态和覆盖带上自己的标签，
      // 否则「成功」「31 / 31」脱离语境看不出是什么。
      for (const text of [formatTimestampISO(run.finished_at || run.started_at), run.date || "—",
        t("sync.row.status", { date: run.date || "—", status: statusText }),
        t("sync.row.coverage", { ok: run.succeeded ?? "—", total: run.total ?? "—" })]) {
        const cell = document.createElement("td"); cell.textContent = text; row.appendChild(cell);
      }
      body.appendChild(row);
    }
    table.appendChild(body);
    container.replaceChildren(table);
  } catch {
    /* 忽略：同步历史不是关键路径 */
  }
}

// ---------- 操作 ----------

function updateAIDisclosure() {
  const note = $("ai-disclosure");
  note.hidden = !$("ai-enabled").checked;
  let provider = t("web.settings.updateAIDisclosure.316bc4861");
  try { provider = new URL($("ai-base-url").value).host; } catch { /* 尚未填写完整 */ }
  note.textContent = t("web.settings.updateAIDisclosure.ca288d4e3", {provider: provider, p2: $("ai-model").value || t("web.settings.updateAIDisclosure.d07487a28")});
}

async function saveAI(clearKey = false) {
  if (aiSaving || aiTesting || aiModelsLoading) return;
  aiSaving = true;
  updateAIButtons();
  try {
    await post("/settings/ai", clearKey ? { clear_api_key: true } : {
      ai_enabled: $("ai-enabled").checked, ai_base_url: $("ai-base-url").value,
      ai_model: $("ai-model").value, api_key: $("ai-key").value,
    });
    $("ai-key").value = "";
    await refresh();
    toast(clearKey ? t("web.settings.saveAI.ae67bec42") : t("web.settings.saveAI.d38b33495"));
  } catch (error) { toast(error.message, true); }
  finally { aiSaving = false; updateAIButtons(); }
}

async function testAIConnection() {
  if (aiSaving || aiTesting || aiModelsLoading || aiConfigRevision !== aiSavedRevision) return;
  const revision = aiConfigRevision;
  aiTesting = true;
  updateAIButtons();
  aiTestNote(t("web.settings.testAIConnection.9c826c641"));
  try {
    const result = await post("/ai/test", {});
    if (revision !== aiConfigRevision) return;
    const elapsed = Number.isFinite(result.latency_ms) ? `${(result.latency_ms / 1000).toFixed(1)}s` : t("web.settings.testAIConnection.b953bdf9a");
    const status = !result.ok && typeof result.status === "number" ? `（HTTP ${result.status}）` : "";
    const warning = result.warning ? `。${result.warning}` : "";
    aiTestNote(`${result.detail}${status} · ${elapsed} · ${result.model}${warning}`, result.ok);
  } catch (error) {
    if (revision === aiConfigRevision) aiTestNote(error.message, false);
  } finally {
    aiTesting = false;
    updateAIButtons();
  }
}

async function save(patch, message) {
  try {
    await post("/settings", patch);
    await refresh();
    toast(message);
  } catch (error) {
    toast(error.message, true);
  }
}

async function saveCredentials() {
  const body = {
    client_id: $("gh-client-id").value.trim(),
    redirect_uri: $("gh-redirect-uri").value.trim(),
  };
  const secret = $("gh-client-secret").value.trim();
  if (secret) body.client_secret = secret;
  try {
    await post("/settings/credentials", body);
    await refresh();
    toast(t("web.settings.saveCredentials.99ae58228"));
  } catch (error) {
    toast(error.message, true);
  }
}

async function disconnect() {
  if (!window.confirm(t("web.settings.disconnect.07a1a94dc"))) return;
  try {
    await post("/disconnect");
    await refresh();
    toast(t("web.settings.disconnect.d928c2c62"));
  } catch (error) {
    toast(error.message, true);
  }
}

function pollSync() {
  const timer = setInterval(async () => {
    try {
      const status = await get("/sync/status");
      const progress = status.progress || {};
      const note = $("sync-note");
      if (progress.running) {
        note.textContent = t("web.settings.pollSync.96fd6ed0e", {p1: progress.completed, p2: progress.total});
        return;
      }
      clearInterval(timer);
      note.textContent = status.last_result && status.last_result.status === "ok" ? t("web.settings.pollSync.17a06ecc2") : t("web.settings.pollSync.668d1f64e");
      await loadRuns();
    } catch {
      clearInterval(timer);
    }
  }, 1000);
}

async function syncNow() {
  try {
    await post("/sync", { days: 2 });
    toast(t("web.settings.syncNow.e4fe33414"));
    pollSync();
  } catch (error) {
    toast(error.message, true);
  }
}

async function seedDemo() {
  try {
    await post("/demo/seed");
    toast(t("web.settings.seedDemo.24882472f"));
    pollSync();
  } catch (error) {
    toast(error.message, true);
  }
}

async function clearData() {
  const confirmText = $("clear-confirm").value.trim();
  if (confirmText !== "DELETE ALL DATA") {
    toast(t("web.settings.clearData.5b931319d"), true);
    return;
  }
  try {
    await post("/data/clear", { confirm: confirmText });
    $("clear-confirm").value = "";
    toast(t("web.settings.clearData.2b0476ef2"));
    await loadRuns();
  } catch (error) {
    toast(error.message, true);
  }
}

function handleOauthBanner() {
  const params = new URLSearchParams(window.location.search);
  const oauth = params.get("oauth");
  if (!oauth) return;
  const banner = $("oauth-banner");
  if (oauth === "ok") {
    banner.textContent = t("web.settings.handleOauthBanner.3b2f7c816");
    banner.className = "banner ok";
  } else {
    banner.textContent = t("web.settings.handleOauthBanner.57b7153aa", {p1: params.get("message") || t("web.settings.handleOauthBanner.13a46616e")});
    banner.className = "banner error";
  }
  banner.hidden = false;
  window.history.replaceState({}, "", "/settings");
}

function bind() {
  $("ai-enabled").addEventListener("change", markAIConfigDirty);
  $("ai-base-url").addEventListener("input", () => { clearAIModels(); markAIConfigDirty(); });
  $("ai-model").addEventListener("input", markAIConfigDirty);
  $("ai-key").addEventListener("input", () => { clearAIModels(); markAIConfigDirty(); });
  $("ai-models").addEventListener("click", loadAIModels);
  $("ai-model-list").addEventListener("change", selectAIModel);
  $("ai-save").addEventListener("click", () => saveAI());
  $("ai-test").addEventListener("click", testAIConnection);
  $("ai-clear-key").addEventListener("click", () => saveAI(true));
  $("save-provider").addEventListener("click", () => save({ provider: selectedProvider() }, t("web.settings.bind.9f8daadb2")));
  $("save-credentials").addEventListener("click", saveCredentials);
  $("connect-gh").addEventListener("click", () => { window.location.href = "/oauth/start"; });
  $("disconnect-gh").addEventListener("click", disconnect);
  $("save-goals").addEventListener("click", () => save({
    goals: { steps: Number($("goal-steps").value), sleep_minutes: Number($("goal-sleep").value) },
  }, t("web.settings.bind.248101058")));
  $("goal-sleep").addEventListener("input", updateSleepHours);
  $("save-sync").addEventListener("click", () => save({ sync_interval_minutes: Number($("sync-interval").value) }, t("web.settings.bind.9a0e4c03c")));
  $("sync-now").addEventListener("click", syncNow);
  $("seed-demo").addEventListener("click", seedDemo);
  $("clear-data").addEventListener("click", clearData);
}

async function init() {
  bind();
  handleOauthBanner();
  try {
    await refresh();
  } catch (error) {
    toast(error.message, true);
  }
}

initLanguage();
init();
