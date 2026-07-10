/* global window, document */
(function () {
  window.OpsConsole = window.OpsConsole || {};
  const OC = window.OpsConsole;

  OC.openMonitorIncidentDrawer = async function openMonitorIncidentDrawer(env, eventId) {
    const overlay = document.getElementById("monitor-drawer-overlay");
    const drawer = document.getElementById("monitor-drawer");
    const title = document.getElementById("monitor-drawer-title");
    const body = document.getElementById("monitor-drawer-body");
    if (!drawer || !body) return;

    title.textContent = `Incidente — ${env}`;
    body.innerHTML = `<p class="drawer-placeholder">Carregando…</p>`;
    overlay?.classList.remove("hidden");
    overlay?.setAttribute("aria-hidden", "false");
    drawer.classList.remove("hidden");
    drawer.classList.add("open");
    drawer.setAttribute("aria-hidden", "false");
    document.body.classList.add("drawer-open");

    try {
      const data = await OC.fetchJson(`/api/v1/monitoring/${env}/events/${eventId}`);
      if (data.error) {
        body.innerHTML = `<p class="global-error">${OC.escapeHtml(data.error)}</p>`;
        return;
      }
      OC.renderMonitorIncidentDrawer(data);
    } catch (err) {
      body.innerHTML = `<p class="global-error">${OC.escapeHtml(err.message)}</p>`;
    }
  };

  OC.closeMonitorIncidentDrawer = function closeMonitorIncidentDrawer() {
    const overlay = document.getElementById("monitor-drawer-overlay");
    const drawer = document.getElementById("monitor-drawer");
    overlay?.classList.add("hidden");
    overlay?.setAttribute("aria-hidden", "true");
    drawer?.classList.remove("open");
    drawer?.classList.add("hidden");
    drawer?.setAttribute("aria-hidden", "true");
    document.body.classList.remove("drawer-open");
  };

  OC.renderMonitorIncidentDrawer = function renderMonitorIncidentDrawer(data) {
    const body = document.getElementById("monitor-drawer-body");
    if (!body) return;
    const event = data.event || {};
    const series = data.series || {};
    const related = data.relatedEvents || [];
    const correlations = event.correlations || [];

    const corrHtml = correlations.length
      ? `<ul class="monitor-correlation-list">${correlations.map((c) => `<li class="monitor-correlation-${OC.escapeHtml(c.type || "")}">${OC.escapeHtml(c.label || "")}</li>`).join("")}</ul>`
      : "";

    const relatedHtml = related.length
      ? `<ul class="monitor-related-list">${related
          .slice(0, 8)
          .map(
            (r) => `<li><span class="monitor-sev monitor-sev-${OC.escapeHtml(r.severity || "info")}">${OC.escapeHtml(r.severity || "")}</span> ${OC.escapeHtml(r.title || "")} <span class="monitor-meta-muted">${OC.escapeHtml(OC.formatRelativeTime(r.recorded_at))}</span></li>`
          )
          .join("")}</ul>`
      : `<p class="monitor-empty">Nenhum evento correlacionado.</p>`;

    const points = series.points || [];
    let chartHtml = `<p class="monitor-empty monitor-empty-neutral">Sem série temporal para este incidente.</p>`;
    if (points.length && OC.buildMonitorMiniChart) {
      chartHtml = OC.buildMonitorMiniChart(points, event);
    } else if (points.length) {
      const vals = points.map((p) => Number(p.v)).filter((v) => !Number.isNaN(v));
      const max = vals.length ? Math.max(...vals) : 0;
      const min = vals.length ? Math.min(...vals) : 0;
      chartHtml = `<p class="monitor-chart-summary">${points.length} amostras · min ${Math.round(min)} ms · max ${Math.round(max)} ms</p>`;
    }

    body.innerHTML = `<div class="monitor-incident-detail">
      <div class="monitor-incident-head">
        <span class="monitor-sev monitor-sev-${OC.escapeHtml(event.severity || "info")}">${OC.escapeHtml((event.severity || "").toUpperCase())}</span>
        <span class="monitor-alert-env">${OC.escapeHtml(event.environment || "")}</span>
        <span class="monitor-meta-muted">${OC.escapeHtml(OC.formatDate(event.recorded_at))}</span>
      </div>
      <h3 class="monitor-incident-title">${OC.escapeHtml(event.title || "")}</h3>
      <p class="monitor-incident-category">${OC.escapeHtml(event.category || "")}</p>
      <pre class="monitor-incident-detail-text">${OC.escapeHtml(event.detail || "—")}</pre>
      ${corrHtml}
      <div class="monitor-incident-actions">
        ${event.investigationLink ? `<a class="btn btn-secondary btn-sm" href="${OC.escapeHtml(event.investigationLink)}">${OC.escapeHtml(event.recommendedAction || "Investigar")}</a>` : ""}
      </div>
      <h4 class="monitor-drawer-subtitle">Contexto temporal (±30 min)</h4>
      ${chartHtml}
      <h4 class="monitor-drawer-subtitle">Eventos correlacionados (±15 min)</h4>
      ${relatedHtml}
    </div>`;
  };

  OC.buildMonitorMiniChart = function buildMonitorMiniChart(points, event) {
    const width = 480;
    const height = 120;
    const pad = 12;
    const vals = points.map((p) => ({ t: new Date(p.t).getTime(), v: Number(p.v) })).filter((p) => !Number.isNaN(p.t) && !Number.isNaN(p.v));
    if (!vals.length) return "";
    const tMin = Math.min(...vals.map((p) => p.t));
    const tMax = Math.max(...vals.map((p) => p.t));
    const vMin = Math.min(...vals.map((p) => p.v), 0);
    const vMax = Math.max(...vals.map((p) => p.v), 1);
    const tSpan = tMax - tMin || 1;
    const vSpan = vMax - vMin || 1;
    const x = (t) => pad + ((t - tMin) / tSpan) * (width - pad * 2);
    const y = (v) => height - pad - ((v - vMin) / vSpan) * (height - pad * 2);
    const d = vals.map((p, i) => `${i === 0 ? "M" : "L"}${x(p.t).toFixed(1)},${y(p.v).toFixed(1)}`).join(" ");
    const eventT = event.recorded_at ? new Date(event.recorded_at).getTime() : null;
    const marker =
      eventT && eventT >= tMin && eventT <= tMax
        ? `<line x1="${x(eventT).toFixed(1)}" y1="${pad}" x2="${x(eventT).toFixed(1)}" y2="${height - pad}" stroke="currentColor" stroke-dasharray="4 2" opacity="0.5" />`
        : "";
    return `<div class="monitor-mini-chart-wrap"><svg class="monitor-mini-chart" viewBox="0 0 ${width} ${height}" role="img"><path d="${d}" fill="none" stroke="var(--color-primary, #2a5595)" stroke-width="2" />${marker}</svg></div>`;
  };

  OC.bindMonitorDrawer = function bindMonitorDrawer() {
    document.getElementById("monitor-drawer-close")?.addEventListener("click", OC.closeMonitorIncidentDrawer);
    document.getElementById("monitor-drawer-overlay")?.addEventListener("click", OC.closeMonitorIncidentDrawer);
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape") OC.closeMonitorIncidentDrawer?.();
    });
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", OC.bindMonitorDrawer);
  } else {
    OC.bindMonitorDrawer();
  }
})();
