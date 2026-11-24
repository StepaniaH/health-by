import { t, formatMonthTitle, weekdayLabels } from "./i18n.js";

const SVG_NS = "http://www.w3.org/2000/svg";
const ISO_RE = /^(\d{4})-(\d{2})-(\d{2})$/;
const RELOAD_KEY = "health-by.doc-reload";

/** Evict a stale pre-upgrade cached HTML shell once so scripts match the active DOM. */
export function ensureFreshDocument(markerSelector) {
  if (document.querySelector?.(markerSelector)) {
    try { globalThis.sessionStorage?.removeItem(RELOAD_KEY); } catch { /* ignore */ }
    return true;
  }
  const href = globalThis.location?.href || "";
  try {
    if (globalThis.sessionStorage?.getItem(RELOAD_KEY) === href) return true;
    globalThis.sessionStorage?.setItem(RELOAD_KEY, href);
  } catch {
    return true;
  }
  if (typeof globalThis.location?.reload === "function") {
    globalThis.location.reload();
    return false;
  }
  return true;
}

function svgEl(tag, attrs = {}) {
  const node = document.createElementNS(SVG_NS, tag);
  for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, String(v));
  return node;
}

function makeIcon(paths, className = "") {
  const svg = svgEl("svg", {
    viewBox: "0 0 16 16",
    fill: "none",
    stroke: "currentColor",
    "stroke-width": "1.35",
    "stroke-linecap": "round",
    "stroke-linejoin": "round",
    "aria-hidden": "true",
    class: className,
  });
  for (const item of paths) {
    if (item.rect) svg.appendChild(svgEl("rect", item.rect));
    else svg.appendChild(svgEl("path", { d: item.d }));
  }
  return svg;
}

const chevronIcon = () => makeIcon([{ d: "M4 6.25 L8 10.25 L12 6.25" }], "ui-select-chevron");
const checkIcon = () => makeIcon([{ d: "M3.5 8.25 L6.5 11.25 L12.5 4.75" }], "ui-select-check");
const calendarIcon = () => makeIcon([
  { rect: { x: "2.25", y: "3.25", width: "11.5", height: "10.5", rx: "1.75" } },
  { d: "M5.25 1.75v2.5M10.75 1.75v2.5M2.25 6.75h11.5" },
], "inline-icon");
const prevMonthIcon = () => makeIcon([{ d: "M10 3.5 L5.5 8 L10 12.5" }], "inline-icon");
const nextMonthIcon = () => makeIcon([{ d: "M6 3.5 L10.5 8 L6 12.5" }], "inline-icon");

let activePopover = null;

function positionPopover(panel, trigger, { alignRight = false, minWidth = 0 } = {}) {
  if (!panel || !trigger) return;
  const rect = trigger.getBoundingClientRect();
  panel.style.position = "fixed";
  panel.style.left = "0px";
  panel.style.top = "0px";
  panel.style.visibility = "hidden";
  if (minWidth > 0) panel.style.minWidth = `${Math.round(minWidth)}px`;
  const pop = panel.getBoundingClientRect();
  const vw = window.innerWidth;
  const vh = window.innerHeight;
  const spaceBelow = vh - rect.bottom - 10;
  const spaceAbove = rect.top - 10;
  const top = (pop.height <= spaceBelow || spaceBelow >= spaceAbove)
    ? Math.min(rect.bottom + 6, Math.max(8, vh - pop.height - 8))
    : Math.max(8, rect.top - pop.height - 6);
  const preferRight = alignRight || rect.left > vw * 0.55;
  const rawLeft = preferRight ? (rect.right - pop.width) : rect.left;
  const left = Math.min(Math.max(8, rawLeft), Math.max(8, vw - pop.width - 8));
  panel.style.top = `${Math.round(top)}px`;
  panel.style.left = `${Math.round(left)}px`;
  panel.style.visibility = "visible";
}

export function closeActivePopover(restoreFocus = false) {
  if (!activePopover) return;
  const current = activePopover;
  activePopover = null;
  document.removeEventListener("pointerdown", current.onPointerDown, true);
  document.removeEventListener("keydown", current.onKeyDown, true);
  window.removeEventListener("resize", current.onReposition);
  window.removeEventListener("scroll", current.onScroll, true);
  current.panel.remove();
  current.trigger.setAttribute("aria-expanded", "false");
  if (restoreFocus) current.trigger.focus?.();
}

function openPopover(panel, trigger, options = {}) {
  const wasSame = activePopover && activePopover.trigger === trigger;
  closeActivePopover(false);
  if (wasSame) return false;
  const host = trigger.closest?.("dialog") || document.body;
  host.appendChild(panel);
  trigger.setAttribute("aria-expanded", "true");
  positionPopover(panel, trigger, options);

  const onPointerDown = (event) => {
    if (panel.contains(event.target) || trigger.contains(event.target)) return;
    closeActivePopover(false);
  };
  const onKeyDown = (event) => {
    if (event.key === "Escape") {
      event.preventDefault();
      event.stopPropagation();
      closeActivePopover(true);
      return;
    }
    options.onKeyDown?.(event);
  };
  const onReposition = () => positionPopover(panel, trigger, options);
  const onScroll = (event) => {
    if (panel.contains(event.target)) return;
    const rect = trigger.getBoundingClientRect();
    if (rect.bottom < 0 || rect.top > window.innerHeight) {
      closeActivePopover(false);
      return;
    }
    positionPopover(panel, trigger, options);
  };

  document.addEventListener("pointerdown", onPointerDown, true);
  document.addEventListener("keydown", onKeyDown, true);
  window.addEventListener("resize", onReposition, { passive: true });
  window.addEventListener("scroll", onScroll, { passive: true, capture: true });
  activePopover = { panel, trigger, onPointerDown, onKeyDown, onReposition, onScroll };
  return true;
}

function syncSelectWrapper(selectEl, wrapper, labelEl) {
  wrapper.hidden = Boolean(selectEl.hidden);
  const selected = selectEl.options?.[selectEl.selectedIndex] || selectEl.options?.[0];
  labelEl.textContent = selected ? selected.textContent : "";
}

export function enhanceSelect(selectEl, { variant = "field" } = {}) {
  if (!selectEl || selectEl.dataset?.uiEnhanced === "1") return null;
  if (selectEl.dataset) selectEl.dataset.uiEnhanced = "1";

  const wrapper = document.createElement("div");
  const variantClass = variant === "chip" ? "ui-select--chip" : "ui-select--field";
  wrapper.className = `ui-select ${variantClass}`;

  const trigger = document.createElement("button");
  trigger.type = "button";
  trigger.className = "ui-select-trigger";
  trigger.setAttribute("aria-haspopup", "listbox");
  trigger.setAttribute("aria-expanded", "false");
  const ariaLabel = selectEl.getAttribute("aria-label");
  if (ariaLabel) trigger.setAttribute("aria-label", ariaLabel);
  if (selectEl.hasAttribute("data-theme-select")) trigger.setAttribute("data-ui-select", "theme");
  else if (selectEl.hasAttribute("data-language-select")) trigger.setAttribute("data-ui-select", "language");
  else if (selectEl.id) trigger.setAttribute("data-ui-select", selectEl.id);

  const existingIcon = selectEl.parentElement?.querySelector?.(".select-chip-icon");
  if (existingIcon) trigger.appendChild(existingIcon);

  const labelEl = document.createElement("span");
  labelEl.className = "ui-select-label";
  trigger.appendChild(labelEl);
  trigger.appendChild(chevronIcon());
  wrapper.appendChild(trigger);

  selectEl.classList.add("sr-only");
  selectEl.tabIndex = -1;
  selectEl.setAttribute("aria-hidden", "true");
  selectEl.insertAdjacentElement("beforebegin", wrapper);

  const parentLabel = selectEl.closest?.("label");
  if (parentLabel && !parentLabel.dataset.uiSelectLabelBound) {
    parentLabel.dataset.uiSelectLabelBound = "1";
    parentLabel.addEventListener("click", (event) => {
      if (wrapper.contains(event.target)) return;
      event.preventDefault();
      trigger.focus();
      openSelectMenu();
    });
  }

  function sync() {
    syncSelectWrapper(selectEl, wrapper, labelEl);
  }

  function openSelectMenu(focusOffset = 0) {
    const menu = document.createElement("div");
    menu.className = "ui-select-menu";
    menu.setAttribute("role", "listbox");
    if (ariaLabel) menu.setAttribute("aria-label", ariaLabel);

    const options = [...(selectEl.options || [])];
    const buttons = [];
    let activeIndex = Math.max(0, options.findIndex((opt) => opt.value === selectEl.value));

    const selectOption = (value) => {
      const changed = selectEl.value !== value;
      selectEl.value = value;
      sync();
      closeActivePopover(true);
      if (changed) selectEl.dispatchEvent(new Event("change", { bubbles: true }));
    };

    for (let i = 0; i < options.length; i += 1) {
      const opt = options[i];
      if (!opt.value && opt.disabled) continue;
      const item = document.createElement("button");
      item.type = "button";
      item.className = "ui-select-option";
      item.setAttribute("role", "option");
      item.dataset.value = opt.value;
      const isSelected = opt.value === selectEl.value;
      item.setAttribute("aria-selected", isSelected ? "true" : "false");
      if (isSelected) item.classList.add("is-selected");
      const textSpan = document.createElement("span");
      textSpan.textContent = opt.textContent;
      item.appendChild(textSpan);
      if (isSelected) item.appendChild(checkIcon());
      item.addEventListener("click", (event) => {
        event.stopPropagation();
        selectOption(opt.value);
      });
      buttons.push(item);
      menu.appendChild(item);
    }

    if (!buttons.length) return;
    if (activeIndex < 0 || activeIndex >= buttons.length) activeIndex = 0;

    const focusAt = (nextIdx) => {
      const clamped = (nextIdx + buttons.length) % buttons.length;
      activeIndex = clamped;
      buttons[clamped]?.focus();
    };

    const opened = openPopover(menu, trigger, {
      alignRight: variant === "chip",
      minWidth: Math.max(trigger.getBoundingClientRect().width, variant === "chip" ? 136 : 180),
      onKeyDown(event) {
        if (event.key === "ArrowDown") {
          event.preventDefault();
          focusAt(activeIndex + 1);
        } else if (event.key === "ArrowUp") {
          event.preventDefault();
          focusAt(activeIndex - 1);
        } else if (event.key === "Home") {
          event.preventDefault();
          focusAt(0);
        } else if (event.key === "End") {
          event.preventDefault();
          focusAt(buttons.length - 1);
        }
      },
    });
    if (opened) focusAt(activeIndex + focusOffset);
  }

  trigger.addEventListener("click", (event) => {
    event.preventDefault();
    event.stopPropagation();
    openSelectMenu(0);
  });
  trigger.addEventListener("keydown", (event) => {
    if (event.key === "ArrowDown") {
      event.preventDefault();
      openSelectMenu(1);
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      openSelectMenu(-1);
    }
  });

  selectEl.addEventListener("change", sync);
  selectEl.addEventListener("ui:sync", sync);
  if (typeof MutationObserver !== "undefined") {
    const observer = new MutationObserver(sync);
    observer.observe(selectEl, {
      attributes: true,
      attributeFilter: ["hidden"],
      childList: true,
      subtree: true,
      characterData: true,
    });
  }
  sync();
  return wrapper;
}

export function initSelects(root = document) {
  for (const el of (root.querySelectorAll?.("[data-theme-select], [data-language-select]") || [])) {
    enhanceSelect(el, { variant: "chip" });
  }
  for (const id of ["sync-interval", "ai-model-list"]) {
    const el = root.getElementById?.(id) || document.getElementById(id);
    if (el) enhanceSelect(el, { variant: "field" });
  }
}

function parseIsoDate(raw) {
  const match = ISO_RE.exec(String(raw || "").trim());
  if (!match) return null;
  const year = Number(match[1]);
  const month = Number(match[2]);
  const day = Number(match[3]);
  const dt = new Date(Date.UTC(year, month - 1, day));
  if (dt.getUTCFullYear() !== year || dt.getUTCMonth() !== month - 1 || dt.getUTCDate() !== day) return null;
  return { year, month, day, iso: `${match[1]}-${match[2]}-${match[3]}` };
}

function formatIsoParts(year, month, day) {
  return `${String(year).padStart(4, "0")}-${String(month).padStart(2, "0")}-${String(day).padStart(2, "0")}`;
}

function localTodayIso() {
  const now = new Date();
  return formatIsoParts(now.getFullYear(), now.getMonth() + 1, now.getDate());
}

function openCalendarPopover(trigger, targetInput, { getAvailableDates = null, getToday = null } = {}) {
  const todayIso = parseIsoDate(getToday?.())?.iso || localTodayIso();
  const initialSelected = parseIsoDate(targetInput?.value) || parseIsoDate(todayIso);
  let viewYear = initialSelected.year;
  let viewMonth = initialSelected.month;

  const panel = document.createElement("div");
  panel.className = "ui-cal";
  panel.setAttribute("role", "dialog");
  panel.setAttribute("aria-modal", "false");
  panel.setAttribute("aria-label", t("ui.calendar.pick"));

  const pickIso = (iso) => {
    if (targetInput) {
      targetInput.value = iso;
      targetInput.dispatchEvent(new Event("change", { bubbles: true }));
    }
    closeActivePopover(true);
  };

  function shiftMonth(delta) {
    const next = new Date(Date.UTC(viewYear, viewMonth - 1 + delta, 1));
    viewYear = next.getUTCFullYear();
    viewMonth = next.getUTCMonth() + 1;
    renderGrid();
  }

  function renderGrid(focusIso = null) {
    panel.replaceChildren();
    const available = getAvailableDates?.();
    const selectedIso = parseIsoDate(targetInput?.value)?.iso || null;

    const head = document.createElement("div");
    head.className = "ui-cal-head";
    const prevBtn = document.createElement("button");
    prevBtn.type = "button";
    prevBtn.className = "ui-cal-nav";
    prevBtn.setAttribute("aria-label", t("ui.calendar.prev_month"));
    prevBtn.appendChild(prevMonthIcon());
    prevBtn.addEventListener("click", (e) => { e.stopPropagation(); shiftMonth(-1); });

    const title = document.createElement("span");
    title.className = "ui-cal-title";
    title.textContent = formatMonthTitle(viewYear, viewMonth);

    const nextBtn = document.createElement("button");
    nextBtn.type = "button";
    nextBtn.className = "ui-cal-nav";
    nextBtn.setAttribute("aria-label", t("ui.calendar.next_month"));
    nextBtn.appendChild(nextMonthIcon());
    nextBtn.addEventListener("click", (e) => { e.stopPropagation(); shiftMonth(1); });

    head.appendChild(prevBtn);
    head.appendChild(title);
    head.appendChild(nextBtn);
    panel.appendChild(head);

    const weekdays = document.createElement("div");
    weekdays.className = "ui-cal-weekdays";
    for (const label of weekdayLabels()) {
      const cell = document.createElement("span");
      cell.textContent = label;
      weekdays.appendChild(cell);
    }
    panel.appendChild(weekdays);

    const grid = document.createElement("div");
    grid.className = "ui-cal-grid";
    const firstWeekday = new Date(Date.UTC(viewYear, viewMonth - 1, 1)).getUTCDay();
    const startUtc = Date.UTC(viewYear, viewMonth - 1, 1 - firstWeekday);
    let focusTarget = null;

    for (let i = 0; i < 42; i += 1) {
      const cellDate = new Date(startUtc + i * 86_400_000);
      const cy = cellDate.getUTCFullYear();
      const cm = cellDate.getUTCMonth() + 1;
      const cd = cellDate.getUTCDate();
      const iso = formatIsoParts(cy, cm, cd);
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "ui-cal-day";
      btn.dataset.date = iso;
      btn.textContent = String(cd);
      if (cm !== viewMonth) btn.classList.add("is-outside");
      if (iso === todayIso) btn.classList.add("is-today");
      if (iso === selectedIso) {
        btn.classList.add("is-selected");
        btn.setAttribute("aria-selected", "true");
      }
      if (available && typeof available.has === "function" && available.has(iso)) {
        btn.classList.add("has-data");
      }
      btn.addEventListener("click", (e) => {
        e.stopPropagation();
        pickIso(iso);
      });
      if ((focusIso && iso === focusIso) || (!focusIso && iso === (selectedIso || todayIso))) {
        focusTarget = btn;
      }
      grid.appendChild(btn);
    }
    panel.appendChild(grid);

    const foot = document.createElement("div");
    foot.className = "ui-cal-foot";
    if (available && available.size > 0) {
      const legend = document.createElement("span");
      legend.className = "ui-cal-legend";
      const dot = document.createElement("span");
      dot.className = "ui-cal-dot";
      dot.setAttribute("aria-hidden", "true");
      legend.appendChild(dot);
      legend.appendChild(document.createTextNode(t("ui.calendar.has_data")));
      foot.appendChild(legend);
    } else {
      foot.appendChild(document.createElement("span"));
    }
    const todayBtn = document.createElement("button");
    todayBtn.type = "button";
    todayBtn.className = "link-btn";
    todayBtn.textContent = t("ui.calendar.today");
    todayBtn.addEventListener("click", (e) => {
      e.stopPropagation();
      pickIso(todayIso);
    });
    foot.appendChild(todayBtn);
    panel.appendChild(foot);

    if (focusTarget) focusTarget.focus?.();
  }

  const opened = openPopover(panel, trigger, {
    onKeyDown(event) {
      const focused = document.activeElement?.dataset?.date;
      if (!focused) return;
      const delta = { ArrowLeft: -1, ArrowRight: 1, ArrowUp: -7, ArrowDown: 7 }[event.key];
      if (!delta) {
        if (event.key === "PageUp") { event.preventDefault(); shiftMonth(-1); }
        else if (event.key === "PageDown") { event.preventDefault(); shiftMonth(1); }
        return;
      }
      event.preventDefault();
      const parsed = parseIsoDate(focused);
      if (!parsed) return;
      const next = new Date(Date.UTC(parsed.year, parsed.month - 1, parsed.day + delta));
      const ny = next.getUTCFullYear();
      const nm = next.getUTCMonth() + 1;
      const nd = next.getUTCDate();
      const nextIso = formatIsoParts(ny, nm, nd);
      if (ny !== viewYear || nm !== viewMonth) {
        viewYear = ny;
        viewMonth = nm;
        renderGrid(nextIso);
      } else {
        panel.querySelector?.(`[data-date="${nextIso}"]`)?.focus?.();
      }
    },
  });
  if (opened) renderGrid();
}

export function bindDateButton(buttonEl, targetInput, options = {}) {
  if (!buttonEl || !targetInput || buttonEl.dataset?.uiDateBound === "1") return;
  if (buttonEl.dataset) buttonEl.dataset.uiDateBound = "1";
  buttonEl.addEventListener("click", (event) => {
    event.preventDefault();
    event.stopPropagation();
    openCalendarPopover(buttonEl, targetInput, options);
  });
}

export function enhanceDateInput(inputEl, options = {}) {
  if (!inputEl || inputEl.dataset?.uiEnhanced === "1") return null;
  if (inputEl.dataset) inputEl.dataset.uiEnhanced = "1";

  const wrap = document.createElement("span");
  wrap.className = "date-input-wrap";
  inputEl.insertAdjacentElement("beforebegin", wrap);
  wrap.appendChild(inputEl);

  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = "date-input-trigger";
  btn.setAttribute("aria-label", t("ui.calendar.pick"));
  btn.setAttribute("aria-haspopup", "dialog");
  btn.setAttribute("aria-expanded", "false");
  btn.appendChild(calendarIcon());
  btn.addEventListener("click", (event) => {
    event.preventDefault();
    event.stopPropagation();
    openCalendarPopover(btn, inputEl, options);
  });
  inputEl.addEventListener("keydown", (event) => {
    if (event.key === "ArrowDown" && (event.altKey || !activePopover)) {
      event.preventDefault();
      openCalendarPopover(btn, inputEl, options);
    }
  });
  wrap.appendChild(btn);
  return wrap;
}
