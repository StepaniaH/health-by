// Only this module selects a locale. Catalogs own wording, units and order.
import { messages, pluralParameters } from "./i18n-messages.js";

export const DEFAULT_LANGUAGE = "zh";
export function supportedLanguage(value) {
  const code = String(value || "").toLowerCase().split(/[-_]/)[0];
  return Object.hasOwn(messages, code) ? code : null;
}
export function resolveLanguage({ search = "", stored = null, browser = "" } = {}) {
  return supportedLanguage(new URLSearchParams(search).get("lang"))
    || supportedLanguage(stored) || supportedLanguage(browser) || DEFAULT_LANGUAGE;
}
let stored = null;
try { stored = globalThis.localStorage?.getItem("health-by.language"); } catch { /* Storage may be blocked. */ }
export const language = resolveLanguage({ search: globalThis.location?.search, stored, browser: globalThis.navigator?.language });
export const locale = language === "en" ? "en-US" : "zh-CN";
export function t(key, params = {}) {
  const catalog = messages[language];
  const plural = params.count === 1 ? "one" : "other";
  let resolved = Object.hasOwn(params, "count") && Object.hasOwn(catalog, `${key}.${plural}`) ? `${key}.${plural}` : key;
  if (Object.hasOwn(pluralParameters, key)) {
    const forms = pluralParameters[key].map(name => Number(String(params[name]).replaceAll(",", "")) === 1 ? "one" : "other");
    resolved = [key, ...forms].join(".");
  }
  if (!Object.hasOwn(catalog, resolved)) throw new Error(`Unknown translation key: ${resolved}`);
  return catalog[resolved].replace(/\{([a-zA-Z_][a-zA-Z_0-9]*)\}/g, (_, name) => {
    if (!Object.hasOwn(params, name)) throw new Error(`Missing translation parameter: ${name}`);
    return typeof params[name] === "number" ? formatNumber(params[name], 20) : String(params[name]);
  });
}
export function formatNumber(value, digits = 0) {
  return new Intl.NumberFormat(locale, { maximumFractionDigits: digits }).format(value);
}
export function formatDateTime(value) {
  if (!value || Number.isNaN(new Date(value).getTime())) return "—";
  return new Intl.DateTimeFormat(locale, { year: "numeric", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", hour12: false }).format(new Date(value));
}
export function formatDateTitle(value) {
  const date = new Date(`${value}T12:00:00`);
  const parts = Object.fromEntries(new Intl.DateTimeFormat(locale, { year: "numeric", month: "short", day: "numeric" }).formatToParts(date).map(part => [part.type, part.value]));
  const weekday = new Intl.DateTimeFormat(locale, { weekday: language === "en" ? "long" : "short" }).format(date);
  return t("date.heading", { year: parts.year, month: parts.month, day: parts.day, weekday });
}
export function formatTimestampISO(value) {
  if (!value || Number.isNaN(new Date(value).getTime())) return "—";
  const parts = Object.fromEntries(new Intl.DateTimeFormat(locale, { year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hourCycle: "h23" }).formatToParts(new Date(value)).map(part => [part.type, part.value]));
  return `${parts.year}-${parts.month}-${parts.day} ${parts.hour}:${parts.minute}`;
}

export function duration(value) {
  if (value === null || value === undefined) return "—";
  const total = Math.round(value), hours = Math.floor(total / 60), rest = total % 60;
  if (!hours) return t("duration.minutes", { n: rest, count: rest });
  if (!rest) return t("duration.hours", { n: hours, count: hours });
  return t("duration.hours_minutes", { hours, minutes: rest });
}
export function applyTranslations(root = document) {
  root.documentElement?.setAttribute("lang", language);
  for (const node of root.querySelectorAll("[data-i18n]")) node.textContent = t(node.dataset.i18n);
  for (const attr of ["title", "placeholder", "aria-label"]) {
    for (const node of root.querySelectorAll(`[data-i18n-${attr}]`)) node.setAttribute(attr, t(node.getAttribute(`data-i18n-${attr}`)));
  }
}
export function setLanguage(value) {
  const code = supportedLanguage(value);
  if (!code) return;
  try { globalThis.localStorage?.setItem("health-by.language", code); } catch { /* URL remains authoritative. */ }
  const url = new URL(globalThis.location.href);
  url.searchParams.set("lang", code);
  globalThis.location.assign(url);
}
export function initLanguage() {
  const selectedInURL = supportedLanguage(new URLSearchParams(globalThis.location?.search).get("lang"));
  if (selectedInURL) {
    try { globalThis.localStorage?.setItem("health-by.language", selectedInURL); } catch { /* URL selection remains available. */ }
  }
  applyTranslations();
  for (const link of document.querySelectorAll('a[href="/"], a[href="/settings"]')) {
    const url = new URL(link.getAttribute("href"), globalThis.location.href);
    url.searchParams.set("lang", language); link.setAttribute("href", url.pathname + url.search);
  }
  const select = document.querySelector("[data-language-select]");
  if (select) { select.value = language; select.addEventListener("change", () => setLanguage(select.value)); }
}
