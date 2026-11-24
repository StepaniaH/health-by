"""End-to-end smoke suite: synthetic 35-day DB + FastAPI app + real ES-module dashboard render."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from click.testing import CliRunner
from fastapi.testclient import TestClient

from devtools import make_dev_data
from health_by.api.app import create_app
from health_by.cli import main
from health_by.config import load_config

ROOT = Path(__file__).resolve().parent.parent


def test_full_e2e_smoke_http_and_browser_es_modules(tmp_path, monkeypatch):
    data_dir = tmp_path / "e2e-data"
    assert make_dev_data.build(data_dir, days=35) == 0

    monkeypatch.setenv("HEALTH_BY_DATA", str(data_dir))
    cfg = load_config()
    app = create_app(cfg)

    with TestClient(app) as client:
        for page_path in ("/", "/settings"):
            res = client.get(page_path)
            assert res.status_code == 200
            assert "script-src 'self'" in res.headers["content-security-policy"]
            assert res.headers["x-frame-options"] == "DENY"
            assert "data-theme-select" in res.text
            assert "data-language-select" in res.text

        for asset in (
            "/static/css/app.css",
            "/static/js/theme.js",
            "/static/js/i18n.js",
            "/static/js/i18n-messages.js",
            "/static/js/ui-controls.js",
            "/static/js/dashboard-dom.js",
            "/static/js/dashboard-today.js",
            "/static/js/dashboard-sleep.js",
            "/static/js/dashboard-activity.js",
            "/static/js/dashboard-heart.js",
            "/static/js/dashboard-body.js",
            "/static/js/dashboard-data.js",
            "/static/js/dashboard.js",
            "/static/js/settings.js",
        ):
            asset_res = client.get(asset)
            assert asset_res.status_code == 200, asset

        health = client.get("/api/v1/health")
        assert health.status_code == 200 and health.json()["status"] == "ok"

        dates_res = client.get("/api/v1/dates")
        assert dates_res.status_code == 200
        dates = dates_res.json()["dates"]
        assert len(dates) == 35
        # 合成数据模拟约 6% 缺失睡眠的夜晚，挑选最近一个含睡眠的日期，避免跨自然日偶发波动。
        latest = next(
            d
            for d in reversed(dates)
            if (client.get("/api/v1/summary", params={"date": d}).json().get("day") or {}).get("sleep")
        )

        rendered_payloads: dict[str, dict] = {}
        for lang in ("zh", "en"):
            summary_res = client.get("/api/v1/summary", params={"date": latest, "lang": lang})
            assert summary_res.status_code == 200
            assert summary_res.headers["content-language"] == lang
            insights_res = client.get("/api/v1/insights", params={"date": latest, "days": 90, "lang": lang})
            assert insights_res.status_code == 200
            assert insights_res.headers["content-language"] == lang
            rendered_payloads[lang] = {
                "summary": summary_res.json(),
                "insights": insights_res.json(),
            }

        status_res = client.get("/api/v1/status")
        assert status_res.status_code == 200 and status_res.json()["days_stored"] == 35

        cov_res = client.get("/api/v1/coverage")
        assert cov_res.status_code == 200 and cov_res.json()["normalized_days"] == 35

        export_res = client.get("/api/v1/export")
        assert export_res.status_code == 200
        exported = json.loads(export_res.text)
        assert len(exported["days"]) == 35
        assert len(exported["raw_days"]) == 35

    # Feed real API payloads directly into native Node ES modules for both languages (`en` and `zh`).
    node_script = r"""
import assert from "node:assert/strict";
import fs from "node:fs";

const input = JSON.parse(fs.readFileSync(0, "utf8"));
const lang = input.lang;
const indexHtml = fs.readFileSync("health_by/web/index.html", "utf8");
const htmlIds = [...indexHtml.matchAll(/\bid="([^"]+)"/g)].map((m) => m[1]);

const storageMap = new Map();
const rootAttrs = new Map();
const makeEl = (tag = "div") => {
  const attrs = {};
  const classSet = new Set();
  let text = "";
  return {
    tagName: tag.toUpperCase(),
    attrs,
    children: [],
    hidden: false,
    dataset: {},
    value: "",
    classList: {
      toggle(k, force) {
        if (force === undefined) {
          if (classSet.has(k)) classSet.delete(k); else classSet.add(k);
        } else if (force) classSet.add(k);
        else classSet.delete(k);
      },
      contains(k) { return classSet.has(k); },
    },
    set className(v) { attrs.class = String(v); },
    get className() { return attrs.class || ""; },
    setAttribute(k, v) { attrs[k] = String(v); },
    removeAttribute(k) { delete attrs[k]; },
    getAttribute(k) { return attrs[k]; },
    appendChild(c) { this.children.push(c); return c; },
    removeChild(c) {
      const i = this.children.indexOf(c);
      if (i >= 0) this.children.splice(i, 1);
    },
    insertAdjacentElement() {},
    addEventListener() {},
    querySelector(sel) {
      if (sel === "[data-theme-select]") return themeSelectEl;
      return null;
    },
    querySelectorAll: () => [],
    get firstChild() { return this.children[0] || null; },
    get childElementCount() { return this.children.length; },
    get textContent() { return text + this.children.map((c) => c.textContent || "").join(""); },
    set textContent(v) { text = String(v); this.children = []; },
  };
};

const nodes = new Map(htmlIds.map((id) => [id, makeEl()]));
const themeSelectEl = makeEl("select");
const listeners = {};
themeSelectEl.addEventListener = (ev, fn) => { listeners[ev] = fn; };

globalThis.localStorage = {
  getItem: (k) => storageMap.get(k) ?? null,
  setItem: (k, v) => { storageMap.set(k, String(v)); },
};
globalThis.window = {
  matchMedia: () => ({ matches: false, addEventListener() {} }),
  addEventListener() {},
  location: { search: `?lang=${lang}`, href: `http://127.0.0.1/?lang=${lang}`, hash: "" },
  history: { replaceState() {}, pushState() {} },
};
Object.defineProperty(globalThis, "location", { value: globalThis.window.location, configurable: true });
Object.defineProperty(globalThis, "navigator", { value: { language: lang }, configurable: true });
globalThis.document = {
  documentElement: {
    setAttribute(k, v) { rootAttrs.set(k, String(v)); },
    removeAttribute(k) { rootAttrs.delete(k); },
  },
  getElementById(id) {
    if (!nodes.has(id)) nodes.set(id, makeEl());
    return nodes.get(id);
  },
  createElement: makeEl,
  createElementNS: (_ns, tag) => makeEl(tag),
  createTextNode: (t) => ({ textContent: String(t) }),
  querySelector: (sel) => (sel === "[data-theme-select]" ? themeSelectEl : null),
  querySelectorAll: () => [],
};

const themeMod = await import("./health_by/web/js/theme.js");
assert.equal(themeMod.initTheme(), "auto");
assert.equal(rootAttrs.has("data-theme"), false);
assert.equal(themeMod.setTheme("dark"), "dark");
assert.equal(rootAttrs.get("data-theme"), "dark");
assert.equal(storageMap.get(themeMod.THEME_STORAGE_KEY), "dark");
themeSelectEl.value = "light";
listeners.change();
assert.equal(rootAttrs.get("data-theme"), "light");
themeMod.setTheme("auto");
assert.equal(rootAttrs.has("data-theme"), false);

const domMod = await import("./health_by/web/js/dashboard-dom.js");
const { renderToday } = await import("./health_by/web/js/dashboard-today.js");
const { renderSleepPage } = await import("./health_by/web/js/dashboard-sleep.js");
const { renderActivityPage } = await import("./health_by/web/js/dashboard-activity.js");
const { renderHeartPage } = await import("./health_by/web/js/dashboard-heart.js");
const { renderBodyPage } = await import("./health_by/web/js/dashboard-body.js");
const { renderDataPage } = await import("./health_by/web/js/dashboard-data.js");

const { summary, insights } = input.payload;
domMod.state.summary = summary;
domMod.state.insights = insights;

renderToday(summary.day, insights, summary.goals, summary.analysis);
renderSleepPage(summary.day, insights, summary.trends, summary.goals);
renderActivityPage(summary.day, insights, summary.trends, summary.goals);
renderHeartPage(summary.day, insights, summary.trends, summary.goals);
renderBodyPage(summary.day, insights, summary.trends);
renderDataPage(summary.day, insights, summary.goals, summary.analysis);

for (const id of ["card-today", "card-sleep", "card-activity", "card-heart", "card-body", "card-data"]) {
  assert.equal(nodes.get(id).hidden, false, `${id} should be visible (${lang})`);
}
const countTags = (root, tag) => (root.tagName === tag ? 1 : 0)
  + (root.children || []).reduce((acc, ch) => acc + countTags(ch, tag), 0);
for (const hostId of ["sleep-body", "sleep-trend-body", "heart-trend-body", "activity-trend-body", "timeline-body"]) {
  assert(countTags(nodes.get(hostId), "SVG") >= 1, `${hostId} should render SVG chart (${lang})`);
}
assert(nodes.get("today-rows").textContent.length > 0, `today-rows should not be empty (${lang})`);
"""
    for lang in ("en", "zh"):
        proc = subprocess.run(
            ["node", "--input-type=module", "-e", node_script],
            cwd=ROOT,
            input=json.dumps({"lang": lang, "payload": rendered_payloads[lang]}),
            capture_output=True,
            text=True,
            timeout=15,
        )
        assert proc.returncode == 0, f"{lang}: {proc.stderr}"

    # Also exercise offline maintenance CLI against the generated DB in tmp_path (never touches data/).
    runner = CliRunner()
    comp_res = runner.invoke(
        main, ["--lang", "en", "--data", str(data_dir), "db", "compress-raw", "--no-backup"]
    )
    assert comp_res.exit_code == 0, comp_res.output
