/* global window, performance */
(function () {
  window.OpsConsole = window.OpsConsole || {};
  const OC = window.OpsConsole;

  const MAX_SAMPLES = 80;
  OC._perfLog = OC._perfLog || [];
  OC._perfEnabled = true;

  OC.perfMark = function perfMark(name) {
    if (!OC._perfEnabled || typeof performance === "undefined") return;
    try {
      performance.mark(`ops:${name}`);
    } catch {
      /* ignore */
    }
  };

  OC.perfMeasure = function perfMeasure(name, startMark, endMark) {
    if (!OC._perfEnabled || typeof performance === "undefined") return null;
    try {
      const end = endMark || `${startMark}:end`;
      const start = startMark || `${name}:start`;
      performance.mark(end);
      const entries = performance.measure(`ops:${name}`, start, end);
      const ms = entries?.duration ?? performance.getEntriesByName(`ops:${name}`).pop()?.duration;
      return typeof ms === "number" ? Math.round(ms * 10) / 10 : null;
    } catch {
      return null;
    }
  };

  OC.perfRecord = function perfRecord(entry) {
    if (!OC._perfEnabled) return;
    const row = {
      at: new Date().toISOString(),
      ...entry,
    };
    OC._perfLog.push(row);
    if (OC._perfLog.length > MAX_SAMPLES) OC._perfLog.shift();
    if (typeof console !== "undefined" && console.debug) {
      console.debug("[ops-perf]", row);
    }
  };

  OC.perfSummary = function perfSummary() {
    const by = {};
    (OC._perfLog || []).forEach((r) => {
      const key = r.name || "unknown";
      if (!by[key]) by[key] = { count: 0, total: 0, max: 0, samples: [] };
      const ms = Number(r.ms) || 0;
      by[key].count += 1;
      by[key].total += ms;
      by[key].max = Math.max(by[key].max, ms);
      by[key].samples.push(ms);
    });
    Object.values(by).forEach((b) => {
      const sorted = [...b.samples].sort((a, c) => a - c);
      const p95 = sorted[Math.min(sorted.length - 1, Math.floor(sorted.length * 0.95))] || 0;
      b.avg = b.count ? Math.round((b.total / b.count) * 10) / 10 : 0;
      b.p95 = p95;
      delete b.samples;
    });
    return by;
  };

  /** Timed fetch wrapper used by monitoring refresh. */
  OC.timedFetchJson = async function timedFetchJson(url, options = {}) {
    const label = options.perfLabel || url.split("?")[0];
    const t0 = typeof performance !== "undefined" ? performance.now() : Date.now();
    try {
      const data = await OC.fetchJson(url, options);
      const ms = (typeof performance !== "undefined" ? performance.now() : Date.now()) - t0;
      OC.perfRecord({ name: `net:${label}`, ms: Math.round(ms * 10) / 10, url });
      return data;
    } catch (err) {
      const ms = (typeof performance !== "undefined" ? performance.now() : Date.now()) - t0;
      OC.perfRecord({
        name: `net:${label}:error`,
        ms: Math.round(ms * 10) / 10,
        url,
        error: err.message,
      });
      throw err;
    }
  };

  OC.updateAlertCountInPlace = function updateAlertCountInPlace(count) {
    const value = String(count ?? 0);
    document
      .querySelectorAll(
        '#view-monitoring [data-ops-stat-action="alerts"] .ops-hero-stat-value, #view-monitoring [data-ops-kpi-action="alerts"] .ops-kpi-value, #view-deploy [data-ops-stat-action="alerts"] .ops-hero-stat-value, #view-deploy [data-ops-kpi-action="alerts"] .ops-kpi-value'
      )
      .forEach((el) => {
        el.textContent = value;
      });
  };
})();
