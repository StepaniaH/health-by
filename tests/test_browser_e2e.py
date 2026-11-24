"""Real-browser Chromium E2E smoke test (runs in CI via Playwright; skipped locally if absent)."""

from __future__ import annotations

import socket
import threading
import time
from pathlib import Path

import pytest
import uvicorn

from devtools import make_dev_data
from health_by.api.app import create_app
from health_by.config import Config

pytestmark = pytest.mark.browser


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_ready(port: int, timeout: float = 10.0) -> None:
    import urllib.request

    deadline = time.monotonic() + timeout
    url = f"http://127.0.0.1:{port}/api/v1/health"
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1) as resp:
                if resp.status == 200:
                    return
        except OSError:
            time.sleep(0.1)
    raise RuntimeError(f"Server on port {port} did not become ready in {timeout}s")


def test_real_chromium_dashboard_and_settings(tmp_path: Path):
    playwright_sync = pytest.importorskip("playwright.sync_api")

    data_dir = tmp_path / "browser-data"
    assert make_dev_data.build(data_dir, days=35) == 0

    port = _free_port()
    cfg = Config(data_dir=data_dir, host="127.0.0.1", port=port)
    app = create_app(cfg)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    try:
        _wait_ready(port)
        base_url = f"http://127.0.0.1:{port}"
        with playwright_sync.sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(viewport={"width": 1280, "height": 900})
            page = context.new_page()
            console_errors: list[str] = []
            page_errors: list[str] = []
            page.on("console", lambda msg: console_errors.append(msg.text) if msg.type == "error" else None)
            page.on("pageerror", lambda exc: page_errors.append(str(exc)))

            # Dashboard loads cleanly and renders core sections & SVGs.
            # Pick the latest generated day that has a sleep session (synthetic data simulates ~6% missing sleep nights).
            from contextlib import closing
            from health_by import db as db_module, storage
            with closing(db_module.connect(cfg.db_path)) as check_conn:
                sleep_date = next(
                    d
                    for d in reversed(storage.available_dates(check_conn))
                    if (storage.get_day(check_conn, d) or {}).get("sleep")
                )
            resp = page.goto(f"{base_url}/?date={sleep_date}", wait_until="networkidle")
            assert resp is not None and resp.headers.get("cache-control") == "no-store"
            css_resp = page.request.get(f"{base_url}/static/css/app.css")
            assert css_resp.status == 200 and css_resp.headers.get("cache-control") == "no-cache"
            page.wait_for_selector("#card-today:not([hidden])", timeout=10_000)
            assert page.locator("#today-rows").inner_text().strip()
            assert page.locator("#sleep-body svg").count() >= 1
            assert page.locator("#empty-state").is_hidden()

            # Custom editorial calendar popover opens on click, shows archived dots, and closes cleanly on Escape.
            page.locator("#date-pick-btn").click()
            page.wait_for_selector(".ui-cal", timeout=5_000)
            assert page.locator(".ui-cal-day.has-data").count() >= 1
            page.keyboard.press("Escape")
            page.wait_for_function("() => !document.querySelector('.ui-cal')")

            # AI segmented window preset controls toggle custom date range cleanly.
            assert "is-active" in (page.locator('[data-ai-preset="1"]').get_attribute("class") or "")
            assert page.locator("#ai-custom-range").is_hidden()
            page.locator('[data-ai-preset="custom"]').click()
            assert not page.locator("#ai-custom-range").is_hidden()
            assert page.locator('[data-ai-preset="custom"]').get_attribute("aria-pressed") == "true"

            # Apply custom range, then re-clicking active Custom preserves applied range state.
            with page.expect_response(lambda res: "/api/v1/ai/analysis" in res.url and res.status == 200):
                page.locator("#ai-window-apply").click()
            page.wait_for_function(
                "d => (document.getElementById('ai-window-note').textContent || '').includes(d)",
                arg=sleep_date,
            )
            applied_note = page.locator("#ai-window-note").inner_text()
            assert sleep_date in applied_note
            page.locator('[data-ai-preset="custom"]').click()
            assert page.locator("#ai-window-note").inner_text() == applied_note
            assert not page.locator("#ai-custom-range").is_hidden()

            # Unapplied custom draft must NOT auto-apply when switching to another day.
            page.locator("#ai-window-start").fill("2026-01-01")
            page.locator("#ai-window-start").dispatch_event("change")
            dirty_note = page.locator("#ai-window-note").inner_text()
            ai_calls_on_nav: list[str] = []
            page.on("request", lambda req: ai_calls_on_nav.append(req.url) if "/api/v1/ai/analysis" in req.url else None)
            prev_title = page.locator("#date-label").inner_text()
            with page.expect_response(lambda res: "/api/v1/summary" in res.url and res.status == 200):
                page.locator("#date-prev").click()
            page.wait_for_function("prev => document.getElementById('date-label').textContent !== prev", arg=prev_title)
            assert ai_calls_on_nav == []
            assert not page.locator("#ai-custom-range").is_hidden()
            assert page.locator("#ai-window-note").inner_text() == dirty_note

            page.locator('[data-ai-preset="7"]').click()
            page.wait_for_function("() => document.getElementById('ai-custom-range').hidden === true")
            assert page.locator('[data-ai-preset="7"]').get_attribute("aria-pressed") == "true"

            # Settings page loads cleanly with custom editorial dropdowns & theme switch works.
            settings_resp = page.goto(f"{base_url}/settings", wait_until="networkidle")
            assert settings_resp is not None and settings_resp.headers.get("cache-control") == "no-store"
            page.wait_for_selector('[data-ui-select="theme"]', timeout=10_000)
            assert page.locator('[data-ui-select="language"]').count() >= 1
            assert page.locator('[data-ui-select="sync-interval"]').count() >= 1
            assert page.locator("#ai-enabled").is_visible()

            theme_trigger = page.locator('[data-ui-select="theme"]')
            theme_trigger.click()
            page.wait_for_selector('.ui-select-menu[role="listbox"]', timeout=5_000)
            tbox = theme_trigger.bounding_box()
            mbox = page.locator('.ui-select-menu[role="listbox"]').bounding_box()
            assert tbox is not None and mbox is not None and mbox["y"] >= tbox["y"] + tbox["height"]
            page.locator('.ui-select-option[data-value="dark"]').click()
            assert page.locator("html").get_attribute("data-theme") == "dark"

            theme_trigger.click()
            page.wait_for_selector('.ui-select-option[data-value="auto"]', timeout=5_000)
            page.locator('.ui-select-option[data-value="auto"]').click()
            assert page.locator("html").get_attribute("data-theme") is None

            page.locator('[data-ui-select="sync-interval"]').click()
            page.wait_for_selector('.ui-select-menu[role="listbox"]', timeout=5_000)
            page.keyboard.press("Escape")
            page.wait_for_function("() => !document.querySelector('.ui-select-menu')")

            # Forced-colors mode restores native checkbox appearance so checked/unchecked stay distinct.
            page.emulate_media(forced_colors="active")
            appearance = page.evaluate("() => getComputedStyle(document.getElementById('ai-enabled')).appearance")
            assert appearance != "none"
            page.emulate_media(forced_colors="none")

            # Clicking `← 返回看板` (`/?lang=...` via i18n.js) keeps full new Dashboard UI cleanly.
            with page.expect_response(
                lambda res: res.url.startswith(f"{base_url}/?lang=") and res.status == 200
            ) as back_info:
                page.locator('.masthead a.link-btn[href^="/"]').click()
            back_resp = back_info.value
            assert back_resp.headers.get("cache-control") == "no-store"
            page.wait_for_selector("#card-today:not([hidden])", timeout=10_000)
            page.locator("#date-pick-btn").click()
            page.wait_for_selector(".ui-cal", timeout=5_000)
            page.keyboard.press("Escape")
            page.wait_for_function("() => !document.querySelector('.ui-cal')")
            assert page.locator("#card-ai .seg-control").is_visible()
            assert "is-active" in (page.locator('[data-ai-preset="1"]').get_attribute("class") or "")
            assert page.locator("#ai-custom-range").is_hidden()
            assert page.locator('#card-ai input[type="date"]').count() == 0

            assert not page_errors, f"Uncaught page errors: {page_errors}"
            assert not console_errors, f"Console errors: {console_errors}"

            # Intentional 503 on preset must allow in-place retry by clicking the same preset again.
            page.goto(f"{base_url}/?date={sleep_date}", wait_until="networkidle")
            page.wait_for_selector("#card-ai:not([hidden])", timeout=10_000)
            fail_once = {"done": False}

            def handle_503(route):
                if not fail_once["done"]:
                    fail_once["done"] = True
                    route.fulfill(status=503, content_type="application/json", body='{"detail":"temporary 503"}')
                else:
                    route.continue_()

            page.route("**/api/v1/ai/analysis*", handle_503)
            page.locator('[data-ai-preset="7"]').click()
            page.wait_for_selector("#ai-error:not([hidden])", timeout=10_000)
            with page.expect_response(lambda res: "/api/v1/ai/analysis" in res.url and res.status == 200):
                page.locator('[data-ai-preset="7"]').click()
            page.wait_for_function("() => document.getElementById('ai-error').hidden === true")
            page.unroute("**/api/v1/ai/analysis*", handle_503)

            # Navigating to a date with no archive (expected 404) shows empty state and does NOT resurrect stale data on resize.
            page.goto(f"{base_url}/?date=2099-01-01", wait_until="networkidle")
            page.wait_for_selector("#empty-state:not([hidden])", timeout=10_000)
            assert page.locator("#card-today").is_hidden()
            page.set_viewport_size({"width": 480, "height": 844})
            page.wait_for_timeout(300)
            assert page.locator("#card-today").is_hidden()
            assert not page.locator("#empty-state").is_hidden()
            assert not page_errors, f"Uncaught page errors after 404/resize: {page_errors}"

            # Stale cached pre-upgrade HTML (missing #date-pick-btn) auto-reloads once to recover cleanly.
            console_before_stale = len(console_errors)
            stale_hits = {"count": 0}
            def is_zh_home(url):
                return url == f"{base_url}/?lang=zh"

            def handle_stale_html(route):
                if stale_hits["count"] == 0:
                    stale_hits["count"] += 1
                    route.fulfill(
                        status=200,
                        content_type="text/html; charset=utf-8",
                        body='<!doctype html><html lang="zh"><head></head><body><script type="module" src="/static/js/dashboard.js"></script></body></html>',
                    )
                else:
                    route.continue_()

            page.route(is_zh_home, handle_stale_html)
            with page.expect_response(
                lambda res: is_zh_home(res.url) and res.headers.get("cache-control") == "no-store"
            ):
                page.goto(f"{base_url}/?lang=zh", wait_until="commit")
            page.wait_for_selector("#date-pick-btn", timeout=10_000)
            page.wait_for_selector("#card-today:not([hidden])", timeout=10_000)
            assert stale_hits["count"] == 1
            page.locator("#date-pick-btn").click()
            page.wait_for_selector(".ui-cal", timeout=5_000)
            page.keyboard.press("Escape")
            page.wait_for_function("() => !document.querySelector('.ui-cal')")
            page.unroute(is_zh_home, handle_stale_html)
            assert not page_errors, f"Uncaught page errors after stale-HTML recovery: {page_errors}"
            assert not console_errors[console_before_stale:], f"Console errors after stale-HTML recovery: {console_errors[console_before_stale:]}"
            browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=5)
