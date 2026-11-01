import { formatNumber, formatDateTitle } from "./i18n.js";
// Numeric helpers; wording and units are catalog resources.
export const DASH = "—";

export function num(value, digits = 0) {
  if (value === null || value === undefined || Number.isNaN(value)) return DASH;
  return formatNumber(Number(value), digits);
}

export { duration as minutes } from "./i18n.js";

export function delta(value, unit = "", digits = 0) {
  if (value === null || value === undefined) return "";
  const sign = value > 0 ? "+" : "−";
  const valueText = `${sign}${num(Math.abs(value), digits)}`;
  return unit ? `${valueText} ${unit}` : valueText;
}

export function deltaClass(value) {
  if (value === null || value === undefined || value === 0) return "";
  return value > 0 ? "up" : "down";
}

export function clockTime(iso) {
  if (!iso) return DASH;
  return String(iso).slice(11, 16);
}

export function dateTitle(iso) {
  return iso ? formatDateTitle(iso) : DASH;
}

export function shortDate(iso) {
  return iso || "";
}

export function percent(value, digits = 0) {
  if (value === null || value === undefined) return DASH;
  return `${num(value, digits)}%`;
}
