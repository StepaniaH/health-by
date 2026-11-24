export const THEME_STORAGE_KEY = "health-by.theme";
const VALID_THEMES = new Set(["auto", "light", "dark"]);

export function resolveTheme(value) {
  const normalized = String(value || "").trim().toLowerCase();
  return VALID_THEMES.has(normalized) ? normalized : "auto";
}

export function readStoredTheme() {
  try {
    return resolveTheme(globalThis.localStorage?.getItem(THEME_STORAGE_KEY));
  } catch {
    return "auto";
  }
}

export function applyTheme(preference, doc = globalThis.document) {
  const resolved = resolveTheme(preference);
  const root = doc?.documentElement;
  if (root && typeof root.setAttribute === "function") {
    if (resolved === "auto") {
      if (typeof root.removeAttribute === "function") root.removeAttribute("data-theme");
      else root.setAttribute("data-theme", "auto");
    } else {
      root.setAttribute("data-theme", resolved);
    }
  }
  return resolved;
}

export function setTheme(value, doc = globalThis.document) {
  const resolved = applyTheme(value, doc);
  try {
    globalThis.localStorage?.setItem(THEME_STORAGE_KEY, resolved);
  } catch {
    /* Storage may be blocked in restricted browsing modes. */
  }
  return resolved;
}

export function initTheme(doc = globalThis.document) {
  const current = applyTheme(readStoredTheme(), doc);
  const select = doc?.querySelector?.("[data-theme-select]");
  if (select) {
    select.value = current;
    if (typeof select.addEventListener === "function" && !select.dataset?.themeBound) {
      if (select.dataset) select.dataset.themeBound = "1";
      select.addEventListener("change", () => setTheme(select.value, doc));
    }
  }
  return current;
}

if (typeof globalThis.document !== "undefined") {
  applyTheme(readStoredTheme(), globalThis.document);
}
