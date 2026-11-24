import { initTheme } from "./theme.js";
import { t, initLanguage } from "./i18n.js";
// 看板协调器：报头元信息、请求编排、URL 同步、轮询、分区导航与初始化
import { get, post, toast } from "./api.js";
import { bindAI, loadAI, renderAI } from "./ai.js";
import { initSelects, enhanceDateInput, bindDateButton, ensureFreshDocument } from "./ui-controls.js";
import { dateTitle } from "./format.js";
import { $, agoText, clear, h, KEY_LABELS, setHidden, state, WINDOW_DAYS } from "./dashboard-dom.js";
import { renderToday } from "./dashboard-today.js";
import { renderSleepPage } from "./dashboard-sleep.js";
import { renderActivityPage } from "./dashboard-activity.js";
import { renderHeartPage } from "./dashboard-heart.js";
import { renderBodyPage } from "./dashboard-body.js";
import { renderDataPage } from "./dashboard-data.js";

// ---------- 元信息 ----------
function buildExtraMeta(day, analysis) {
  const items = [];
  const device = day.device;
  const sync = (analysis && analysis.sync) || {};
  if (sync.is_stale) {
    const when = device && device.last_sync_time ? agoText(device.last_sync_time) : null;
    items.push({ text: when ? t("web.dashboard.buildExtraMeta.a04e97c78", {when: when}) : t("web.dashboard.buildExtraMeta.7308973f3"), klass: "state-warn" });
  }
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
  if (provider.provider === "google-health") {
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

function renderDataNote(insights) {
  const note = $("data-note");
  if (!note) return;
  if (insights) { note.textContent = ""; note.hidden = true; return; }
  note.textContent = t("web.dashboard.renderDataNote.702470580");
  note.hidden = false;
}

const DAY_SECTION_IDS = [
  "card-today", "card-sleep", "card-activity", "card-heart",
  "card-body", "card-data", "today-summary", "today-criteria-details",
];

const DAY_CONTENT_IDS = [
  "today-rows", "sleep-body", "sleep-signals", "sleep-trend-body",
  "sleep-achievements", "sleep-rhythm-body", "sleep-detail-body",
  "activity-body", "activity-trend-body", "activity-rhythm-lines",
  "cosinor-body", "activity-detail-body", "activity-weekday",
  "heart-today-body", "heart-trend-body", "nightly-body",
  "day-hr-body", "heart-relations", "body-today", "body-trend",
  "body-body", "workouts-body", "timeline-body", "weekly-table",
  "coverage-body",
];

function commitDateHeader(date) {
  $("date-label").textContent = date ? dateTitle(date) : t("web.dashboard.load.4927a53bc");
  $("date-picker").value = date || "";
  const todayBtn = $("date-today");
  if (todayBtn) todayBtn.hidden = Boolean(state.today && date === state.today);
}

function clearDayView() {
  state.summary = null;
  state.insights = null;
  state.extraMeta = [];
  updateMeta();
  for (const id of DAY_SECTION_IDS) setHidden(id, true);
  for (const id of DAY_CONTENT_IDS) {
    const node = $(id);
    if (node) clear(node);
  }
  const summaryEl = $("today-summary");
  if (summaryEl) summaryEl.textContent = "";
  setHidden("section-nav", true);
}

function showLoadError(message) {
  clearDayView();
  setHidden("empty-state", true);
  setHidden("card-ai", false);
  const note = $("data-note");
  if (note) {
    note.textContent = message;
    note.hidden = false;
  }
  toast(message, true);
}

function watchWidth() {
  const narrowQuery = window.matchMedia("(max-width: 640px)");
  const wideQuery = window.matchMedia("(min-width: 1024px)");
  let narrow = narrowQuery.matches;
  let wide = wideQuery.matches;
  const onChange = () => {
    if (narrowQuery.matches === narrow && wideQuery.matches === wide) return;
    narrow = narrowQuery.matches;
    wide = wideQuery.matches;
    renderAll();
  };
  for (const query of [narrowQuery, wideQuery]) {
    if (query.addEventListener) query.addEventListener("change", onChange);
    else if (query.addListener) query.addListener(onChange);
  }
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
  state.loadToken = (state.loadToken || 0) + 1;
  const token = state.loadToken;
  const targetDate = state.date;
  setLoading(true);
  try {
    const [summary, insights] = await Promise.all([
      get(`/summary?date=${targetDate}`),
      get(`/insights?date=${targetDate}&days=${WINDOW_DAYS}`).catch(() => null),
      loadAI(targetDate),
    ]);
    if (token !== state.loadToken) return;
    commitDateHeader(targetDate);
    state.summary = summary;
    state.insights = insights;
    renderAll();
    setHidden("empty-state", true);
    setHidden("section-nav", false);
  } catch (error) {
    if (token !== state.loadToken) return;
    commitDateHeader(targetDate);
    if (error.status === 404) showEmpty();
    else showLoadError(error.message);
  } finally {
    if (token === state.loadToken) setLoading(false);
  }
}

function showEmpty() {
  clearDayView();
  const range = state.range;
  $("empty-hint").textContent = range
    ? t("web.dashboard.showEmpty.eff81bd92", {p1: range.from, p2: range.to, p3: range.count})
    : (state.status?.data_dir ? t("web.dashboard.showEmpty.41fdb5fe1", {p1: t("web.dashboard.showEmpty.d57d43e60", {p1: state.status.data_dir})}) : t("web.dashboard.showEmpty.d3c786ce4", {}));
  setHidden("data-note", true);
  setHidden("card-ai", false);
  setHidden("empty-state", false);
}

async function refreshStatus() {
  state.status = await get("/status");
  updateMeta();
}

// ---------- 日期 ↔ URL ----------
const DATE_PATTERN = /^\d{4}-\d{2}-\d{2}$/;

function dateFromUrl() {
  const value = new URLSearchParams(window.location.search).get("date");
  return value && DATE_PATTERN.test(value) ? value : null;
}

function rememberDate(mode = "push") {
  const url = new URL(window.location.href);
  if (state.date) url.searchParams.set("date", state.date);
  else url.searchParams.delete("date");
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
  const dateList = (items) => t("web.dashboard.dateList.8d13781d4", {p1: items.slice(0, 2).map((item) => String(item.date || "")).join(t("separator.list")), p2: items.length > 2 ? t("web.dashboard.dateList.8ab274ae2") : ""});
  if (errored.length) {
    return { text: t("web.dashboard.syncOutcome.bede02c65", {p1: errored.length, p2: dateList(errored)}), bad: true, warn: false };
  }
  if (partial.length) {
    const jobs = [...new Set(partial.flatMap((item) => item.failed_jobs || []))];
    const detail = jobs.length ? t("web.dashboard.syncOutcome.7b45860ad", {p1: jobs.slice(0, 2).map((key) => KEY_LABELS[key] || key).join(t("separator.list"))}) : "";
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
  const calOpts = { getAvailableDates: () => state.availableDates, getToday: () => state.today };
  bindDateButton($("date-pick-btn"), picker, calOpts);
  picker.addEventListener("change", (event) => {
    if (event.target.value) goToDate(event.target.value);
  });
  for (const id of ["ai-window-start", "ai-window-end", "batch-start", "batch-end"]) {
    enhanceDateInput($(id), calOpts);
  }
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
  if (!ensureFreshDocument("#date-pick-btn")) return;
  initTheme();
  initSelects();
  bindControls();
  watchWidth();
  bindSectionNav();
  try {
    const dates = await get("/dates");
    state.today = dates.today;
    state.availableDates = new Set(dates.dates || []);
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
