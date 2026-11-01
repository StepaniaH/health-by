import { t, language } from "./i18n.js";
// 极简 API 封装：非 2xx 抛 Error（优先使用服务端 detail 文案）
export async function api(path, { method = "GET", body } = {}) {
  const response = await fetch(`/api/v1${path}`, {
    method,
    headers: { "accept-language": language, ...(body !== undefined ? { "content-type": "application/json" } : {}) },
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  if (!response.ok) {
    let detail = t("web.api.api.98653317c", {p1: response.status});
    try {
      const payload = await response.json();
      if (payload && payload.detail) detail = payload.detail;
    } catch {
      /* 保留默认文案 */
    }
    if (response.status >= 500) detail = t("web.api.api.1a64c2d5b", {p1: response.status});
    const error = new Error(detail);
    error.status = response.status;
    throw error;
  }
  return response.json();
}

export const get = (path) => api(path);
export const post = (path, body = {}) => api(path, { method: "POST", body });

let toastTimer = null;
export function toast(message, isError = false, isWarn = false) {
  const node = document.getElementById("toast");
  if (!node) return;
  node.textContent = message;
  node.classList.toggle("error", isError);
  node.classList.toggle("warn", isWarn);
  node.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => {
    node.hidden = true;
  }, 4200);
}
