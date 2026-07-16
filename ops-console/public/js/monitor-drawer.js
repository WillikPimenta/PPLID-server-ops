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

  /**
   * Alerta ainda ativo = impacto em curso (ex.: offlineOngoing).
   * Eventos warn/critical das últimas 24h que já se resolveram NÃO contam.
   */
  OC.isAlertGroupActive = function isAlertGroupActive(g) {
    if (!g) return false;
    const sev = String(g.severity || "").toLowerCase();
    if (sev !== "critical" && sev !== "warn") return false;
    if (g.offlineOngoing === true) return true;
    return false;
  };

  OC.isAlertGroupOccurred = function isAlertGroupOccurred(g) {
    if (!g) return false;
    const sev = String(g.severity || "").toLowerCase();
    if (sev !== "critical" && sev !== "warn") return false;
    return !OC.isAlertGroupActive(g);
  };

  OC.countActiveAlerts = function countActiveAlerts(groups) {
    return (groups || []).filter((g) => OC.isAlertGroupActive(g)).length;
  };

  OC.countOccurredProblems = function countOccurredProblems(groups) {
    return (groups || []).filter((g) => OC.isAlertGroupOccurred(g)).length;
  };

  OC.filterActiveAlertGroups = function filterActiveAlertGroups(groups) {
    return (groups || []).filter((g) => OC.isAlertGroupActive(g));
  };

  OC.filterOccurredAlertGroups = function filterOccurredAlertGroups(groups) {
    return (groups || []).filter((g) => OC.isAlertGroupOccurred(g));
  };

  /** Cache de grupos de eventos (24h) — usados para ativos (ongoing) e ocorridos. */
  const ALERT_CACHE_TTL_MS = 30000;

  OC.fetchActiveAlertGroups = async function fetchActiveAlertGroups(hours = 24, options = {}) {
    const force = options.force === true;
    const now = Date.now();
    if (
      !force &&
      OC._alertGroupsFetchedAt &&
      now - OC._alertGroupsFetchedAt < ALERT_CACHE_TTL_MS &&
      Array.isArray(OC.lastAlertGroups)
    ) {
      return OC.lastAlertGroups;
    }
    try {
      const fetchFn = OC.timedFetchJson || OC.fetchJson;
      const data = await fetchFn(
        `/api/v1/monitoring/events/grouped?hours=${encodeURIComponent(String(hours))}`,
        { perfLabel: "grouped:alerts" }
      );
      const groups = data.groups || data.groupedEvents || [];
      OC.lastAlertGroups = groups;
      OC._alertGroupsFetchedAt = now;
      return groups;
    } catch {
      return OC.lastAlertGroups || [];
    }
  };

  function renderAlertGroupCards(alerts) {
    return alerts
      .map((g) => {
        const env = OC.escapeHtml(g.environment || "");
        const sev = OC.escapeHtml(String(g.severity || "info").toLowerCase());
        const cat = OC.escapeHtml(g.category || "");
        const count = g.count != null ? Number(g.count) : (g.events || []).length || 1;
        const lastAt = g.lastAt || g.last_at || "";
        const eventId =
          g.sampleEventId || g.latestEventId || g.id || (g.events && g.events[0] && g.events[0].id);
        const openBtn = eventId
          ? `<button type="button" class="btn btn-ghost btn-sm monitor-open-event" data-event-id="${OC.escapeHtml(String(eventId))}" data-event-env="${env}">Detalhes</button>`
          : "";
        const logsBtn =
          String(g.category || "").toLowerCase() === "logs"
            ? `<button type="button" class="btn btn-secondary btn-sm" data-open-logs-env="${env}" data-open-logs-since="${OC.escapeHtml(lastAt)}">Ver logs</button>`
            : "";
        const ongoing =
          g.offlineOngoing === true
            ? `<span class="monitor-meta-muted"> · em andamento</span>`
            : "";
        return `<li class="monitor-alerts-card">
          <div class="monitor-alerts-card-head">
            <span class="monitor-sev monitor-sev-${sev}">${sev}</span>
            <p class="monitor-alerts-card-title">${OC.escapeHtml(g.title || "Alerta")}</p>
          </div>
          <div class="monitor-alerts-card-meta">
            <span>${env}</span>
            ${cat ? `<span>· ${cat}</span>` : ""}
            <span>· ${count}×</span>
            ${ongoing}
            ${lastAt ? `<span class="monitor-meta-muted">${OC.escapeHtml(OC.formatRelativeTime(lastAt))}</span>` : ""}
          </div>
          ${
            g.detail
              ? `<p class="monitor-alerts-card-detail">${OC.escapeHtml(String(g.detail).slice(0, 220))}</p>`
              : ""
          }
          <div class="monitor-alerts-card-actions">${openBtn}${logsBtn}</div>
        </li>`;
      })
      .join("");
  }

  function paintAlertsDrawerBody(body, allGroups) {
    const active = OC.filterActiveAlertGroups(allGroups);
    const occurred = OC.filterOccurredAlertGroups(allGroups).slice(0, 30);

    const activeBlock = active.length
      ? `<ul class="monitor-alerts-list">${renderAlertGroupCards(active)}</ul>`
      : `<p class="monitor-empty monitor-empty-ok">Nenhum alerta ativo no momento.</p>
         <p class="drawer-hint">Só entram offline em curso, lentidão atual ou deploy ainda quebrado.</p>`;

    const occurredBlock = occurred.length
      ? `<ul class="monitor-alerts-list">${renderAlertGroupCards(occurred)}</ul>`
      : `<p class="monitor-empty monitor-empty-ok">Nenhum problema nas últimas 24h.</p>`;

    body.innerHTML = `
      <section class="monitor-drawer-section">
        <h4 class="monitor-drawer-subtitle">Alertas ativos (${active.length})</h4>
        ${activeBlock}
      </section>
      <section class="monitor-drawer-section">
        <h4 class="monitor-drawer-subtitle">Problemas ocorridos — 24h (${occurred.length})</h4>
        <p class="drawer-hint">Histórico warn/critical já resolvido — não indica impacto agora.</p>
        ${occurredBlock}
      </section>
      <div class="monitor-spike-context-actions">
        <button type="button" class="btn btn-secondary btn-sm" id="monitor-alerts-refresh">Atualizar</button>
        <a class="btn btn-secondary btn-sm" href="/monitoring/incidents" data-monitor-nav="incidents">Ver todos os incidentes</a>
      </div>`;

    body.querySelectorAll(".monitor-open-event").forEach((el) => {
      el.addEventListener("click", () => {
        const id = el.getAttribute("data-event-id");
        const env = el.getAttribute("data-event-env");
        if (id && env) OC.openMonitorIncidentDrawer(env, id);
      });
    });
    body.querySelectorAll("[data-open-logs-env]").forEach((el) => {
      el.addEventListener("click", () => {
        const env = el.getAttribute("data-open-logs-env");
        const since = el.getAttribute("data-open-logs-since") || "";
        openLogsTab(env, since);
      });
    });
    body.querySelector("#monitor-alerts-refresh")?.addEventListener("click", () => {
      OC.openMonitorAlertsDrawer([], { refresh: true, force: true });
    });
    bindDrawerNav(body);
  }

  OC.openMonitorAlertsDrawer = async function openMonitorAlertsDrawer(groups, options = {}) {
    const overlay = document.getElementById("monitor-drawer-overlay");
    const drawer = document.getElementById("monitor-drawer");
    const title = document.getElementById("monitor-drawer-title");
    const body = document.getElementById("monitor-drawer-body");
    if (!drawer || !body) return;
    const t0 = typeof performance !== "undefined" ? performance.now() : Date.now();

    title.textContent = options.title || "Alertas ativos";
    overlay?.classList.remove("hidden");
    overlay?.setAttribute("aria-hidden", "false");
    drawer.classList.remove("hidden");
    drawer.classList.add("open");
    drawer.setAttribute("aria-hidden", "false");
    document.body.classList.add("drawer-open");

    const cached =
      Array.isArray(groups) && groups.length
        ? groups
        : Array.isArray(OC.lastAlertGroups)
          ? OC.lastAlertGroups
          : null;

    if (cached) {
      paintAlertsDrawerBody(body, cached);
      OC.perfRecord?.({
        name: "drawer:alerts:paint-cache",
        ms: Math.round(((typeof performance !== "undefined" ? performance.now() : Date.now()) - t0) * 10) / 10,
      });
    } else {
      body.innerHTML = `<p class="drawer-placeholder">Carregando alertas…</p>`;
    }

    const now = Date.now();
    const stale =
      !OC._alertGroupsFetchedAt || now - OC._alertGroupsFetchedAt > ALERT_CACHE_TTL_MS;
    const shouldRefresh = options.force === true || options.refresh === true || !cached || stale;
    if (!shouldRefresh) return;

    try {
      const source = await OC.fetchActiveAlertGroups(24, {
        force: options.force === true || options.refresh === true || stale,
      });
      if (!document.getElementById("monitor-drawer")?.classList.contains("open")) return;
      paintAlertsDrawerBody(body, source);
      OC.perfRecord?.({
        name: "drawer:alerts:total",
        ms: Math.round(((typeof performance !== "undefined" ? performance.now() : Date.now()) - t0) * 10) / 10,
      });
    } catch (err) {
      if (!cached) {
        body.innerHTML = `<p class="global-error">${OC.escapeHtml(err.message || String(err))}</p>`;
      }
    }
  };

  function openLogsTab(env, since) {
    OC.closeMonitorIncidentDrawer?.();
    const query = {};
    // Linhas que geraram o alerta costumam ser ANTERIORES ao recorded_at do evento
    if (since) {
      const t = new Date(since).getTime();
      query.since = !Number.isNaN(t)
        ? new Date(t - 30 * 60 * 1000).toISOString()
        : since;
    }
    if (OC.monitorState) {
      OC.monitorState.logsPattern = "";
      saveLogsFocusEnv(env);
    }
    OC.navigate("monitoring", null, {
      tab: "logs",
      focusEnv: env || undefined,
      query,
    });
  }

  function saveLogsFocusEnv(env) {
    if (!env || !OC.monitorState || !OC.ENV_ORDER.includes(env)) return;
    if (!OC.monitorState.selectedEnvs.includes(env)) {
      OC.monitorState.selectedEnvs = [...OC.monitorState.selectedEnvs, env];
    }
  }

  function bindDrawerNav(root) {
    root.querySelectorAll("[data-monitor-nav]").forEach((el) => {
      el.addEventListener("click", (e) => {
        e.preventDefault();
        const tab = el.getAttribute("data-monitor-nav") || "incidents";
        OC.closeMonitorIncidentDrawer?.();
        OC.navigate("monitoring", null, { tab });
      });
    });
    root.querySelectorAll("a[href^='/monitoring'], a[href^='/database']").forEach((el) => {
      el.addEventListener("click", (e) => {
        const href = el.getAttribute("href") || "";
        if (!href.startsWith("/")) return;
        e.preventDefault();
        OC.closeMonitorIncidentDrawer?.();
        const route = OC.parseRoute(href);
        if (route.view === "monitoring") {
          OC.navigate("monitoring", null, {
            tab: route.tab || "summary",
            focusEnv: route.env || undefined,
            query: route.query || {},
          });
        } else if (route.view === "database") {
          OC.navigate("database", route.env);
        } else {
          window.history.pushState({}, "", href);
          OC.renderRoute?.();
        }
      });
    });
  }

  OC.renderMonitorIncidentDrawer = function renderMonitorIncidentDrawer(data) {
    const body = document.getElementById("monitor-drawer-body");
    if (!body) return;
    const event = data.event || {};
    const series = data.series || {};
    const related = data.relatedEvents || [];
    const correlations = event.correlations || [];
    const offline = data.offline || {};
    const nearbyLogs = data.nearbyLogs || [];

    const durationSec = offline.offlineDurationSec ?? event.offlineDurationSec;
    const durationLabel =
      offline.offlineDurationLabel ||
      event.offlineDurationLabel ||
      (durationSec != null ? OC.formatDuration(durationSec) : "");
    const ongoing = offline.ongoing ?? event.offlineOngoing;
    let offlineHtml = "";
    if (durationLabel || offline.offlineFrom || event.offlineFrom) {
      const from = offline.offlineFrom || event.offlineFrom;
      const until = offline.offlineUntil || event.offlineUntil;
      offlineHtml = `<div class="monitor-offline-box ${ongoing ? "is-ongoing" : ""}">
        <p class="monitor-offline-duration"><strong>Tempo offline:</strong> ${OC.escapeHtml(durationLabel || "—")}${ongoing ? " (ainda offline)" : ""}</p>
        <p class="monitor-meta-muted">${from ? `De ${OC.escapeHtml(OC.formatDate(from))}` : ""}${until ? ` até ${OC.escapeHtml(OC.formatDate(until))}` : ongoing ? " — em andamento" : ""}</p>
      </div>`;
    }

    const corrHtml = correlations.length
      ? `<ul class="monitor-correlation-list">${correlations
          .map(
            (c) =>
              `<li class="monitor-correlation-${OC.escapeHtml(c.type || "")}">${OC.escapeHtml(c.label || "")}</li>`
          )
          .join("")}</ul>`
      : "";

    const relatedHtml = related.length
      ? `<ul class="monitor-related-list">${related
          .slice(0, 8)
          .map(
            (r) =>
              `<li><span class="monitor-sev monitor-sev-${OC.escapeHtml(r.severity || "info")}">${OC.escapeHtml(r.severity || "")}</span> ${OC.escapeHtml(r.title || "")} <span class="monitor-meta-muted">${OC.escapeHtml(OC.formatRelativeTime(r.recorded_at))}</span></li>`
          )
          .join("")}</ul>`
      : `<p class="monitor-empty">Nenhum evento correlacionado.</p>`;

    const points = series.points || [];
    let chartHtml = `<p class="monitor-empty monitor-empty-neutral">Sem série temporal para este incidente.</p>`;
    const envName = event.environment || "MAIN";
    if (points.length && OC.buildSvgLineChart) {
      const slos = OC.getMonitoringSlos
        ? OC.getMonitoringSlos(OC.monitorState?.config)
        : { healthP95WarnMs: 2000 };
      const sloMs = slos.healthP95WarnMs ?? 2000;
      const lastPt = points[points.length - 1];
      const built = OC.buildSvgLineChart(
        {
          [envName]: {
            points,
            lastSampleAt: lastPt?.t || series.lastSampleAt || null,
            windowFrom: series.windowFrom || series.since || null,
            windowTo: series.windowTo || null,
          },
        },
        "Health latência",
        sloMs,
        { chartId: `incident-${event.id || envName}-${String(event.recorded_at || "").slice(0, 19)}` }
      );
      chartHtml = typeof built === "string" ? built : built.html;
    } else if (points.length && OC.buildMonitorMiniChart) {
      chartHtml = OC.buildMonitorMiniChart(points, event);
    } else if (points.length) {
      const vals = points.map((p) => Number(p.v)).filter((v) => !Number.isNaN(v));
      const max = vals.length ? Math.max(...vals) : 0;
      const min = vals.length ? Math.min(...vals) : 0;
      chartHtml = `<p class="monitor-chart-summary">${points.length} amostras · min ${Math.round(min)} ms · max ${Math.round(max)} ms</p>`;
    }

    const logsHtml = (() => {
      if (!nearbyLogs.length) {
        return `<div class="detail-log-view monitor-nearby-log-view"><span class="deploy-log-empty">Nenhum log próximo ao incidente nesta janela. Use o botão abaixo para abrir a aba Logs.</span></div>`;
      }
      const parsed = nearbyLogs.map((ln) => {
        const raw = String(ln.line || "");
        const classified = OC.classifyPlainLogLine
          ? OC.classifyPlainLogLine(raw)
          : { level: "INFO", text: raw };
        const time =
          (OC.formatTimeShort && ln.logged_at ? OC.formatTimeShort(ln.logged_at) : "") ||
          classified.time ||
          "";
        const svc = String(ln.service || "").trim();
        let text = String(classified.text || raw);
        if (svc && !text.toLowerCase().includes(svc.toLowerCase())) {
          text = `${svc} ${text}`;
        }
        return {
          time,
          level: classified.level || "INFO",
          text: text.slice(0, 400),
          count: 1,
          lastTime: time,
        };
      });
      const body =
        typeof OC.renderLogLinesHtml === "function"
          ? OC.renderLogLinesHtml(parsed, 80)
          : parsed
              .map(
                (row) =>
                  `<div class="log-line log-${OC.escapeHtml(row.level)}"><span class="log-time">${OC.escapeHtml(row.time)}</span> <span class="log-level">[${OC.escapeHtml(row.level)}]</span> <span class="log-text">${OC.escapeHtml(row.text)}</span></div>`
              )
              .join("");
      return `<div class="detail-log-view monitor-nearby-log-view" data-log-scroll-key="incident:nearby">${body}</div>`;
    })();

    const env = event.environment || "";
    const since = event.recorded_at || "";
    const investigateHref = event.investigationLink || "";
    const investigateLabel = event.recommendedAction || "Investigar";
    const isLogsAction =
      String(event.category || "").toLowerCase() === "logs" ||
      /logs/i.test(investigateHref) ||
      /logs/i.test(investigateLabel);

    const actionsHtml = isLogsAction
      ? `<button type="button" class="btn btn-secondary btn-sm" data-open-logs-env="${OC.escapeHtml(env)}" data-open-logs-since="${OC.escapeHtml(since)}">${OC.escapeHtml(investigateLabel || "Verificar logs do serviço")}</button>`
      : investigateHref
        ? `<a class="btn btn-secondary btn-sm" href="${OC.escapeHtml(investigateHref)}">${OC.escapeHtml(investigateLabel)}</a>`
        : "";

    body.innerHTML = `<div class="monitor-incident-detail">
      <div class="monitor-incident-head">
        <span class="monitor-sev monitor-sev-${OC.escapeHtml(event.severity || "info")}">${OC.escapeHtml((event.severity || "").toUpperCase())}</span>
        <span class="monitor-alert-env">${OC.escapeHtml(env)}</span>
        <span class="monitor-meta-muted">${OC.escapeHtml(OC.formatDate(event.recorded_at))}</span>
      </div>
      <h3 class="monitor-incident-title">${OC.escapeHtml(event.title || "")}</h3>
      <p class="monitor-incident-category">${OC.escapeHtml(event.category || "")}</p>
      ${offlineHtml}
      <pre class="monitor-incident-detail-text">${OC.escapeHtml(event.detail || "—")}</pre>
      ${corrHtml}
      <div class="monitor-incident-actions">${actionsHtml}</div>
      <h4 class="monitor-drawer-subtitle">Contexto temporal (±30 min)</h4>
      ${chartHtml}
      <h4 class="monitor-drawer-subtitle">Logs próximos (±30 min)</h4>
      ${logsHtml}
      <h4 class="monitor-drawer-subtitle">Eventos correlacionados (±15 min)</h4>
      ${relatedHtml}
    </div>`;

    body.querySelectorAll("[data-open-logs-env]").forEach((el) => {
      el.addEventListener("click", () => {
        openLogsTab(el.getAttribute("data-open-logs-env"), el.getAttribute("data-open-logs-since") || "");
      });
    });
    bindDrawerNav(body);
    OC.bindChartInteractions?.(body);
  };

  OC.buildMonitorMiniChart = function buildMonitorMiniChart(points, event) {
    // Fallback: mesmo gráfico de latência (eixo X + hover) quando possível.
    if (OC.buildSvgLineChart) {
      const env = event?.environment || "MAIN";
      const slos = OC.getMonitoringSlos
        ? OC.getMonitoringSlos(OC.monitorState?.config)
        : { healthP95WarnMs: 2000 };
      const built = OC.buildSvgLineChart(
        { [env]: { points } },
        "Health latência",
        slos.healthP95WarnMs ?? 2000,
        { chartId: `incident-mini-${env}-${Date.now()}` }
      );
      return typeof built === "string" ? built : built.html;
    }
    const width = 480;
    const height = 120;
    const pad = 12;
    const vals = points
      .map((p) => {
        const parsed = OC.parseDate ? OC.parseDate(p.t) : new Date(p.t);
        return { t: parsed?.getTime?.() ?? NaN, v: Number(p.v) };
      })
      .filter((p) => !Number.isNaN(p.t) && !Number.isNaN(p.v));
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
    const eventT = event.recorded_at
      ? (OC.parseDate ? OC.parseDate(event.recorded_at)?.getTime() : new Date(event.recorded_at).getTime())
      : null;
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
