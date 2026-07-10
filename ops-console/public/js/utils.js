/* global window */
window.OpsConsole = window.OpsConsole || {};

window.OpsConsole.ENV_ORDER = ["MAIN", "DEV", "HOM"];
window.OpsConsole.REFRESH_MS = 2000;
window.OpsConsole.LITE_REFRESH_MS = 2000;
window.OpsConsole.FULL_REFRESH_MS = 30000;
window.OpsConsole.DEPLOY_REFRESH_MS = 2000;
window.OpsConsole.THEME_STORAGE_KEY = "pplid-theme";

window.OpsConsole.escapeHtml = function escapeHtml(text) {
  const div = document.createElement("div");
  div.textContent = String(text ?? "");
  return div.innerHTML;
};

window.OpsConsole.formatDate = function formatDate(iso) {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleString("pt-BR");
  } catch {
    return String(iso);
  }
};

window.OpsConsole.formatTimeShort = function formatTimeShort(iso) {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  } catch {
    return "—";
  }
};

window.OpsConsole.formatRelativeTime = function formatRelativeTime(iso) {
  if (!iso) return "—";
  try {
    const then = new Date(iso).getTime();
    const diffSec = Math.floor((Date.now() - then) / 1000);
    if (diffSec < 60) return "há menos de 1 min";
    if (diffSec < 3600) return `há ${Math.floor(diffSec / 60)} min`;
    if (diffSec < 86400) return `há ${Math.floor(diffSec / 3600)} h`;
    return `há ${Math.floor(diffSec / 86400)} dias`;
  } catch {
    return "—";
  }
};

window.OpsConsole.formatDuration = function formatDuration(seconds) {
  if (seconds == null || seconds === "") return "—";
  const s = Number(seconds);
  if (Number.isNaN(s)) return "—";
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  const r = s % 60;
  return r ? `${m}min ${r}s` : `${m}min`;
};

window.OpsConsole.formatLiveDuration = function formatLiveDuration(startedAt) {
  if (!startedAt) return "—";
  const start = new Date(startedAt).getTime();
  if (Number.isNaN(start)) return "—";
  const diffSec = Math.max(0, Math.floor((Date.now() - start) / 1000));
  return window.OpsConsole.formatDuration(diffSec);
};

window.OpsConsole.truncate = function truncate(text, maxLen) {
  const s = String(text || "");
  if (s.length <= maxLen) return s;
  return `${s.slice(0, maxLen - 1)}…`;
};

window.OpsConsole.plain = function plain(value, fallback) {
  if (value == null || value === "") return fallback || "Não informado";
  return String(value);
};

window.OpsConsole.fixMojibake = function fixMojibake(text) {
  const s = String(text ?? "");
  if (!s || (!s.includes("Ã") && !s.includes("â") && !s.includes("\uFFFD"))) return s;
  try {
    const bytes = new Uint8Array([...s].map((ch) => ch.charCodeAt(0) & 0xff));
    const decoded = new TextDecoder("utf-8").decode(bytes);
    return decoded && decoded !== s ? decoded : s;
  } catch {
    return s;
  }
};
