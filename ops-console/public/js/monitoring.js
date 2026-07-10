/* global window, document */
(function () {
  window.OpsConsole = window.OpsConsole || {};
  const OC = window.OpsConsole;

  const STORAGE_ENVS = "pplid-monitor-envs";
  const STORAGE_CATS = "pplid-monitor-categories";
  const STORAGE_FILTERS = "pplid-monitor-filters";
  const MONITOR_REFRESH_MS = 30000;

  const ENV_COLORS = { MAIN: "#2a5595", DEV: "#0fac67", HOM: "#ff8a00" };
  const CATEGORY_KEYS = ["api", "availability", "postgres", "syncs", "deploy", "logs"];
  const CATEGORY_LABELS = {
    api: "APIs",
    availability: "Disponibilidade",
    postgres: "PostgreSQL",
    syncs: "Syncs",
    deploy: "Deploy",
    logs: "Logs",
  };
  const TAB_LABELS = {
    summary: "Resumo",
    incidents: "Incidentes",
    latency: "Latência",
    syncs: "Syncs",
    apis: "APIs",
    logs: "Logs",
  };
  const STATUS_LABELS = {
    ok: "Saudável",
    warn: "Atenção",
    critical: "Crítico",
    unknown: "Sem dados",
    neutral: "Neutro",
  };
  const DEFAULT_SLOS = {
    healthP95WarnMs: 400,
    healthP95CriticalMs: 600,
    uptimeWarnPct: 99.0,
    syncFailuresWarn24h: 1,
    deployFailureRateWarnPct: 30.0,
  };
  const SEV_ORDER = { critical: 0, warn: 1, info: 2, ok: 3, unknown: 4, neutral: 5 };

  OC.monitorTimer = null;
  OC._monitorRefreshInFlight = false;
  OC._monitorRefreshPending = false;
  OC._monitorRefreshGeneration = 0;
  OC._monitorAbortController = null;
  OC.monitorState = {
    config: null,
    activeTab: "summary",
    selectedEnvs: ["MAIN", "DEV", "HOM"],
    categories: {
      api: true,
      availability: true,
      postgres: true,
      syncs: true,
      deploy: true,
      logs: true,
    },
    eventFilters: { severity: "", category: "", hours: 24 },
    dataByEnv: {},
    lastRefreshedAt: null,
    payload: null,
  };

  function loadPrefs() {
    try {
      const envs = JSON.parse(localStorage.getItem(STORAGE_ENVS) || "null");
      if (Array.isArray(envs) && envs.length) OC.monitorState.selectedEnvs = envs;
    } catch {
      /* ignore */
    }
    try {
      const cats = JSON.parse(localStorage.getItem(STORAGE_CATS) || "null");
      if (cats && typeof cats === "object") {
        OC.monitorState.categories = { ...OC.monitorState.categories, ...cats };
      }
    } catch {
      /* ignore */
    }
    try {
      const filters = JSON.parse(localStorage.getItem(STORAGE_FILTERS) || "null");
      if (filters && typeof filters === "object") {
        OC.monitorState.eventFilters = { ...OC.monitorState.eventFilters, ...filters };
      }
    } catch {
      /* ignore */
    }
    if (OC.currentRoute?.tab) OC.monitorState.activeTab = OC.currentRoute.tab;
  }

  function savePrefs() {
    localStorage.setItem(STORAGE_ENVS, JSON.stringify(OC.monitorState.selectedEnvs));
    localStorage.setItem(STORAGE_CATS, JSON.stringify(OC.monitorState.categories));
    localStorage.setItem(STORAGE_FILTERS, JSON.stringify(OC.monitorState.eventFilters));
  }

  function getSlos(config) {
    return { ...DEFAULT_SLOS, ...(config?.slos || {}) };
  }

  function pctChange(current, baseline) {
    if (current == null || baseline == null || baseline === 0) return null;
    return Math.round(((current - baseline) / baseline) * 100);
  }

  function worstLevel(...levels) {
    const rank = { critical: 0, warn: 1, info: 2, ok: 3, neutral: 4, unknown: 5 };
    let best = "unknown";
    levels.forEach((lvl) => {
      if (rank[lvl] < rank[best]) best = lvl;
    });
    return best;
  }

  function evalHealthMetric(summary, slos, dataFresh) {
    const health = summary?.health || {};
    const count = health.count || 0;
    const latest = health.latest;
    const p95 = health.p95;
    const max = health.max;
    const avg = health.avg;

    if (!dataFresh) {
      return {
        level: "unknown",
        label: "Sem dados recentes",
        detail: "Coleta atrasada ou indisponível",
        value: "—",
        latest: null,
        p95: null,
        max: null,
      };
    }
    if (!count || latest == null) {
      return {
        level: "unknown",
        label: "Sem dados",
        detail: "Coleta indisponível ou sem amostras",
        value: "—",
        latest: null,
        p95: null,
        max: null,
      };
    }

    const sloMetric = p95 != null ? p95 : latest;
    let level = "ok";
    let label = "OK";
    if (sloMetric >= slos.healthP95CriticalMs) {
      level = "critical";
      label = "Violando SLO";
    } else if (sloMetric >= slos.healthP95WarnMs) {
      level = "warn";
      label = "Acima do esperado";
    }

    const delta = pctChange(latest, avg);
    return {
      level,
      label,
      value: `${Math.round(latest)} ms`,
      latest: `${Math.round(latest)} ms`,
      p95: p95 != null ? `${Math.round(p95)} ms` : "—",
      max: max != null ? `${Math.round(max)} ms` : "—",
      slo: `p95 <= ${slos.healthP95WarnMs} ms`,
      sub: avg != null ? `média 24h ${Math.round(avg)} ms` : "",
      delta: delta != null ? `${delta >= 0 ? "+" : ""}${delta}% vs média 24h` : "",
    };
  }

  function evalApi5xxMetric(summary) {
    const api = summary?.api || {};
    if (api.deferred) {
      return {
        level: "neutral",
        label: "Não incluído",
        detail: "Abra a aba APIs para métricas detalhadas",
        value: "—",
      };
    }
    if (api.error || api.reachable === false) {
      return {
        level: "unknown",
        label: "Coleta indisponível",
        detail: api.error || "Middleware ou backend inacessível",
        value: "—",
      };
    }
    const totals = api.totals || {};
    const requests = totals.requests || 0;
    const errors = totals.errors5xx || 0;
    if (requests === 0) {
      return { level: "neutral", label: "Sem tráfego", detail: "Nenhuma requisição nas últimas 24h", value: "0", sub: "reqs 0" };
    }
    if (errors === 0) {
      return { level: "ok", label: "Sem erros", detail: "Sem erros 5xx nas últimas 24h", value: "0", sub: `reqs ${requests}` };
    }
    const rate = Math.round((errors / requests) * 1000) / 10;
    return {
      level: errors >= 10 ? "critical" : "warn",
      label: `${errors} erros`,
      detail: `${rate}% do tráfego`,
      value: String(errors),
      sub: `reqs ${requests}`,
    };
  }

  function evalSyncMetric(summary, slos) {
    if (summary?.syncQueryError) {
      return {
        level: "unknown",
        label: "Consulta indisponível",
        detail: summary.syncQueryError,
        value: "—",
      };
    }
    const failures = summary?.syncFailures24h ?? 0;
    if (failures === 0) {
      return { level: "ok", label: "Sem falhas", value: "0", sub: "últimas 24h" };
    }
    return {
      level: failures >= slos.syncFailuresWarn24h ? "warn" : "ok",
      label: failures >= slos.syncFailuresWarn24h ? "Falhas detectadas" : "OK",
      value: String(failures),
      sub: "últimas 24h",
    };
  }

  function evalDeployMetric(aggregates, slos) {
    if (!aggregates || aggregates.total24h === 0) {
      return { level: "neutral", label: "Sem deploys", value: "—", sub: "últimas 24h" };
    }
    const rate = aggregates.successRate24h;
    const failed = aggregates.failed24h || 0;
    if (failed === 0) {
      return { level: "ok", label: "Pipeline estável", value: `${rate ?? 100}%`, sub: `${aggregates.total24h} deploy(s)` };
    }
    const level = rate != null && rate < 100 - slos.deployFailureRateWarnPct ? "critical" : "warn";
    return { level, label: `${failed} falha(s)`, value: rate != null ? `${rate}%` : String(failed), sub: `${aggregates.total24h} deploy(s)` };
  }

  function computeEnvOverview(env, summary, deployData, slos) {
    const dataFresh = summary?.dataFresh !== false;
    const health = evalHealthMetric(summary, slos, dataFresh);
    const api = evalApi5xxMetric(summary);
    const sync = evalSyncMetric(summary, slos);
    const deploy = evalDeployMetric(deployData?.aggregates24h, slos);

    let overall;
    if (!dataFresh) {
      overall = "unknown";
    } else if (!summary?.latestReachable) {
      overall = "critical";
    } else {
      overall = worstLevel(health.level, api.level, sync.level, deploy.level);
      if (overall === "info") overall = "ok";
    }

    const reasons = [];
    if (!dataFresh) reasons.push("coleta atrasada");
    else {
      if (health.level === "critical" || health.level === "warn") {
        reasons.push(`latência atual ${health.latest || health.value}`);
      }
      if (api.level === "warn" || api.level === "critical") reasons.push(api.label);
      if (deploy.level === "warn" || deploy.level === "critical") reasons.push(deploy.label);
      if (sync.level === "warn") reasons.push("falhas de sync");
      if (!summary?.latestReachable) reasons.push("backend offline");
    }

    return { env, overall, label: STATUS_LABELS[overall] || overall, reasons, health, api, sync, deploy, dataFresh, summary };
  }

  function seriesAvg(points) {
    const vals = (points || []).map((p) => Number(p.v)).filter((v) => !Number.isNaN(v));
    if (!vals.length) return null;
    return vals.reduce((a, b) => a + b, 0) / vals.length;
  }

  function buildSvgLineChart(seriesByEnv, metricLabel, sloMs) {
    const width = 720;
    const height = 200;
    const pad = { top: 16, right: 16, bottom: 36, left: 48 };
    const innerW = width - pad.left - pad.right;
    const innerH = height - pad.top - pad.bottom;

    const allPoints = [];
    Object.values(seriesByEnv).forEach((s) => {
      (s.points || []).forEach((p) => allPoints.push(p));
    });
    if (!allPoints.length) {
      return `<p class="monitor-empty monitor-empty-neutral">Sem dados de ${OC.escapeHtml(metricLabel)} no período.</p>`;
    }

    const times = allPoints.map((p) => new Date(p.t).getTime()).filter((t) => !Number.isNaN(t));
    const values = allPoints.map((p) => Number(p.v)).filter((v) => !Number.isNaN(v));
    const tMin = Math.min(...times);
    const tMax = Math.max(...times);
    const vMin = Math.min(...values, 0);
    const vMax = Math.max(...values, sloMs || 1, 1);
    const tSpan = tMax - tMin || 1;
    const vSpan = vMax - vMin || 1;

    const x = (t) => pad.left + ((t - tMin) / tSpan) * innerW;
    const y = (v) => pad.top + innerH - ((v - vMin) / vSpan) * innerH;

    let paths = "";
    let dots = "";
    let legend = "";
    const summaries = [];
    Object.entries(seriesByEnv).forEach(([env, series]) => {
      const pts = (series.points || [])
        .map((p) => ({ t: new Date(p.t).getTime(), v: Number(p.v) }))
        .filter((p) => !Number.isNaN(p.t) && !Number.isNaN(p.v))
        .sort((a, b) => a.t - b.t);
      if (!pts.length) return;
      const last = pts[pts.length - 1];
      const avg = seriesAvg(pts);
      summaries.push({ env, last: last.v, avg });
      const d = pts.map((p, i) => `${i === 0 ? "M" : "L"}${x(p.t).toFixed(1)},${y(p.v).toFixed(1)}`).join(" ");
      const color = ENV_COLORS[env] || "#666";
      paths += `<path d="${d}" fill="none" stroke="${color}" stroke-width="2" />`;
      pts.forEach((p) => {
        if (sloMs && p.v >= sloMs) {
          dots += `<circle cx="${x(p.t).toFixed(1)}" cy="${y(p.v).toFixed(1)}" r="3" fill="${color}" />`;
        }
      });
      legend += `<span class="monitor-legend-item"><span class="monitor-legend-swatch" style="background:${color}"></span>${env} · atual ${Math.round(last.v)} ms</span>`;
    });

    const gridY = [0, 0.5, 1].map((f) => {
      const val = vMin + vSpan * f;
      const yy = y(val);
      return `<line x1="${pad.left}" y1="${yy}" x2="${width - pad.right}" y2="${yy}" class="monitor-grid-line" />
        <text x="${pad.left - 6}" y="${yy + 4}" text-anchor="end" class="monitor-axis-label">${Math.round(val)}</text>`;
    });

    const xLabels = [0, 0.5, 1].map((f) => {
      const t = tMin + tSpan * f;
      const label = new Date(t).toLocaleDateString("pt-BR", { day: "2-digit", month: "2-digit" });
      return `<text x="${x(t).toFixed(1)}" y="${height - 8}" text-anchor="middle" class="monitor-axis-label">${label}</text>`;
    });

    const sloLine =
      sloMs && sloMs >= vMin && sloMs <= vMax
        ? `<line x1="${pad.left}" y1="${y(sloMs)}" x2="${width - pad.right}" y2="${y(sloMs)}" class="monitor-slo-line" />
           <text x="${width - pad.right - 4}" y="${y(sloMs) - 4}" text-anchor="end" class="monitor-slo-label">SLO ${sloMs} ms</text>`
        : "";

    const worst = summaries.sort((a, b) => b.last - a.last)[0];
    const trendNote = worst
      ? `${worst.env} atingiu ${Math.round(worst.last)} ms no período (média ${Math.round(worst.avg || 0)} ms).`
      : "";

    return `<p class="monitor-chart-summary">${OC.escapeHtml(trendNote)}</p>
    <div class="monitor-chart-wrap">
      <svg class="monitor-chart" viewBox="0 0 ${width} ${height}" role="img" aria-label="${OC.escapeHtml(metricLabel)}">
        ${gridY.join("")}
        ${sloLine}
        ${paths}
        ${dots}
        ${xLabels.join("")}
      </svg>
      <div class="monitor-legend">${legend}</div>
    </div>`;
  }

  function statusBadge(level, label) {
    return `<span class="monitor-status monitor-status-${OC.escapeHtml(level)}">${OC.escapeHtml(label)}</span>`;
  }

  function metricCard({ env, title, metric }) {
    const extra =
      metric.latest && metric.p95
        ? `<p class="monitor-metric-breakdown">Atual: ${OC.escapeHtml(metric.latest)} · p95 24h: ${OC.escapeHtml(metric.p95)} · Máx 24h: ${OC.escapeHtml(metric.max || "—")}</p>`
        : "";
    return `<article class="monitor-metric-card monitor-metric-${metric.level}">
      <header class="monitor-metric-head">
        <span class="monitor-metric-env">${OC.escapeHtml(env)}</span>
        <span class="monitor-metric-title">${OC.escapeHtml(title)}</span>
        ${statusBadge(metric.level, metric.label)}
      </header>
      <p class="monitor-metric-value">${OC.escapeHtml(metric.value ?? "—")}</p>
      ${extra}
      ${metric.slo ? `<p class="monitor-metric-slo">SLO: ${OC.escapeHtml(metric.slo)}</p>` : ""}
      ${metric.delta ? `<p class="monitor-metric-delta">${OC.escapeHtml(metric.delta)}</p>` : ""}
      ${metric.sub ? `<p class="monitor-metric-sub">${OC.escapeHtml(metric.sub)}</p>` : ""}
      ${metric.detail ? `<p class="monitor-metric-detail">${OC.escapeHtml(metric.detail)}</p>` : ""}
    </article>`;
  }

  function renderStaleBanner(config) {
    const collector = config?.collectorStatus || {};
    if (collector.status === "ok") return "";
    const label = collector.label || "Coleta atrasada";
    return `<div class="monitor-stale-banner" role="alert">
      <strong>${OC.escapeHtml(label)}</strong> — métricas podem estar desatualizadas.
      ${collector.lastSampleAt ? `<span class="monitor-meta-muted">Última amostra ${OC.formatRelativeTime(collector.lastSampleAt)}</span>` : ""}
    </div>`;
  }

  function renderMetaBar(config, warnings) {
    const refreshed = OC.monitorState.lastRefreshedAt ? OC.formatDate(OC.monitorState.lastRefreshedAt) : "—";
    const collector = config?.collectorStatus || {};
    const collectorClass = collector.status === "ok" ? "ok" : collector.status === "stale" ? "warn" : "unknown";
    const refreshSec = Math.round(MONITOR_REFRESH_MS / 1000);
    return `<div class="monitor-meta-bar">
      <div class="monitor-meta-items">
        <span>Última atualização: <strong>${OC.escapeHtml(refreshed)}</strong></span>
        <span>Auto-refresh: <strong>${refreshSec}s</strong></span>
        <span>Coleta: <strong class="monitor-collector-${collectorClass}">${OC.escapeHtml(collector.label || "—")}</strong></span>
        ${collector.lastSampleAt ? `<span class="monitor-meta-muted">última amostra ${OC.formatRelativeTime(collector.lastSampleAt)}</span>` : ""}
      </div>
      <button type="button" class="btn btn-secondary btn-sm" id="monitor-refresh-now">Atualizar agora</button>
    </div>
    ${renderStaleBanner(config)}
    ${warnings.length ? `<div class="global-error" role="alert">${warnings.map((w) => OC.escapeHtml(w)).join("<br>")}</div>` : ""}`;
  }

  function activeFilterCount() {
    const envDiff = OC.ENV_ORDER.length - OC.monitorState.selectedEnvs.length;
    const catOff = CATEGORY_KEYS.filter((k) => !OC.monitorState.categories[k]).length;
    return envDiff + catOff;
  }

  OC.renderMonitoringFilters = function renderMonitoringFilters() {
    const envPills = OC.ENV_ORDER.map((env) => {
      const active = OC.monitorState.selectedEnvs.includes(env);
      return `<button type="button" class="monitor-pill ${active ? "is-active" : ""}" data-monitor-env="${env}">${env}</button>`;
    }).join("");
    const catPills = CATEGORY_KEYS.map((key) => {
      const active = OC.monitorState.categories[key];
      return `<button type="button" class="monitor-pill ${active ? "is-active" : ""}" data-monitor-cat="${key}">${CATEGORY_LABELS[key]}</button>`;
    }).join("");
    const count = activeFilterCount();
    return `<div class="monitor-filters">
      <div class="monitor-filter-group">
        <span class="monitor-filter-label">Ambientes</span>
        <div class="monitor-pill-row">${envPills}</div>
      </div>
      <div class="monitor-filter-group">
        <span class="monitor-filter-label">Categorias</span>
        <div class="monitor-pill-row">${catPills}</div>
      </div>
      <div class="monitor-filter-actions">
        ${count ? `<span class="monitor-filter-count">${count} filtro(s) ativo(s)</span>` : ""}
        <button type="button" class="btn btn-ghost btn-sm" id="monitor-clear-filters">Limpar filtros</button>
      </div>
    </div>`;
  };

  function renderTabBar(activeTab) {
    const tabs = Object.entries(TAB_LABELS)
      .map(
        ([key, label]) =>
          `<button type="button" class="monitor-tab ${key === activeTab ? "is-active" : ""}" data-monitor-tab="${key}">${OC.escapeHtml(label)}</button>`
      )
      .join("");
    return `<nav class="monitor-tabs" aria-label="Abas de monitoramento">${tabs}</nav>`;
  }

  function renderStatusOverview(overviews) {
    const sorted = [...overviews].sort((a, b) => SEV_ORDER[a.overall] - SEV_ORDER[b.overall]);
    const cards = sorted
      .map(
        (o) => `<div class="monitor-env-status monitor-env-status-${o.overall}">
          <span class="monitor-env-status-name">${OC.escapeHtml(o.env)}</span>
          ${statusBadge(o.overall, o.label)}
          <p class="monitor-env-status-reason">${OC.escapeHtml(o.reasons.join(" · ") || "Sem alertas ativos")}</p>
          <button type="button" class="btn btn-ghost btn-sm monitor-env-drill" data-monitor-env-focus="${OC.escapeHtml(o.env)}">Investigar</button>
        </div>`
      )
      .join("");
    const worst = sorted[0];
    return `<section class="monitor-section monitor-overview">
      <h3 class="monitor-section-title">Status geral</h3>
      <p class="monitor-overview-note">${worst && worst.overall !== "ok" && worst.overall !== "unknown" ? `Ambiente mais crítico: <strong>${OC.escapeHtml(worst.env)}</strong> (${OC.escapeHtml(worst.label)})` : worst?.overall === "unknown" ? "Coleta indisponível — status pode estar desatualizado." : "Todos os ambientes selecionados estão dentro do esperado."}</p>
      <div class="monitor-env-status-grid">${cards}</div>
    </section>`;
  }

  function renderGroupedIncidents(groups, limit) {
    if (!groups?.length) {
      return `<p class="monitor-empty monitor-empty-ok">Nenhum incidente recente.</p>`;
    }
    const items = groups.slice(0, limit || 5).map((g) => {
      const countLabel = g.count > 1 ? ` · ${g.count} ocorrências` : "";
      const range =
        g.firstAt && g.lastAt && g.firstAt !== g.lastAt
          ? ` (${OC.formatDate(g.firstAt)} – ${OC.formatDate(g.lastAt)})`
          : g.lastAt
            ? ` (${OC.formatRelativeTime(g.lastAt)})`
            : "";
      return `<li class="monitor-group-item">
        <span class="monitor-sev monitor-sev-${OC.escapeHtml(g.severity || "info")}">${OC.escapeHtml(g.severity || "")}</span>
        <span class="monitor-alert-env">${OC.escapeHtml(g.environment || "")}</span>
        <span class="monitor-alert-title">${OC.escapeHtml(g.title || "")}${countLabel}${range}</span>
        <button type="button" class="btn btn-ghost btn-sm monitor-open-event" data-event-id="${g.sampleEventId}" data-event-env="${OC.escapeHtml(g.environment || "")}">Detalhes</button>
      </li>`;
    });
    return `<ul class="monitor-alert-list">${items.join("")}</ul>`;
  }

  function renderIncidentFilters() {
    const f = OC.monitorState.eventFilters;
    const sevOpts = ["", "critical", "warn", "info"]
      .map((s) => `<option value="${s}" ${f.severity === s ? "selected" : ""}>${s || "Todas severidades"}</option>`)
      .join("");
    const catOpts = ["", ...CATEGORY_KEYS]
      .map((c) => `<option value="${c}" ${f.category === c ? "selected" : ""}>${c ? CATEGORY_LABELS[c] : "Todas categorias"}</option>`)
      .join("");
    const hourOpts = [1, 6, 24, 168]
      .map((h) => `<option value="${h}" ${Number(f.hours) === h ? "selected" : ""}>Últimas ${h}h</option>`)
      .join("");
    return `<div class="monitor-incident-filters">
      <label>Severidade <select id="monitor-filter-severity">${sevOpts}</select></label>
      <label>Categoria <select id="monitor-filter-category">${catOpts}</select></label>
      <label>Período <select id="monitor-filter-hours">${hourOpts}</select></label>
    </div>`;
  }

  function renderEventsTable(events, grouped) {
    if (grouped?.length) {
      const rows = grouped.map((g) => {
        const countLabel = g.count > 1 ? ` (${g.count}×)` : "";
        return `<tr class="monitor-group-row" data-event-id="${g.sampleEventId}" data-event-env="${OC.escapeHtml(g.environment || "")}">
          <td title="${OC.escapeHtml(g.lastAt || "")}">${OC.escapeHtml(OC.formatDate(g.lastAt))}</td>
          <td><span class="monitor-sev monitor-sev-${OC.escapeHtml(g.severity || "info")}">${OC.escapeHtml(g.severity || "")}</span></td>
          <td>${OC.escapeHtml(g.environment || "")}</td>
          <td>${OC.escapeHtml(g.category || "")}</td>
          <td>${OC.escapeHtml(g.title || "")}${countLabel}</td>
          <td class="monitor-detail-col">${OC.escapeHtml(g.sampleEvent?.detail || "")}</td>
          <td><button type="button" class="btn btn-ghost btn-sm monitor-open-event" data-event-id="${g.sampleEventId}" data-event-env="${OC.escapeHtml(g.environment || "")}">Detalhes</button></td>
        </tr>`;
      });
      return `<div class="monitor-table-wrap"><table class="monitor-table">
        <thead><tr><th>Quando</th><th>Severidade</th><th>Ambiente</th><th>Categoria</th><th>Título</th><th>Detalhe</th><th>Ação</th></tr></thead>
        <tbody>${rows.join("")}</tbody>
      </table></div>`;
    }
    if (!events?.length) return `<p class="monitor-empty monitor-empty-neutral">Nenhum evento recente.</p>`;
    const rows = events
      .sort((a, b) => {
        const sev = SEV_ORDER[String(a.severity).toLowerCase()] - SEV_ORDER[String(b.severity).toLowerCase()];
        if (sev !== 0) return sev;
        return String(b.recorded_at).localeCompare(String(a.recorded_at));
      })
      .map(
        (e) => `<tr data-event-id="${e.id}" data-event-env="${OC.escapeHtml(e.environment || "")}">
          <td title="${OC.escapeHtml(e.recorded_at || "")}">${OC.escapeHtml(OC.formatDate(e.recorded_at))}</td>
          <td><span class="monitor-sev monitor-sev-${OC.escapeHtml(e.severity || "info")}">${OC.escapeHtml(e.severity || "")}</span></td>
          <td>${OC.escapeHtml(e.environment || "")}</td>
          <td>${OC.escapeHtml(e.category || "")}</td>
          <td>${OC.escapeHtml(e.title || "")}</td>
          <td class="monitor-detail-col">${OC.escapeHtml(e.detail || "")}</td>
          <td><button type="button" class="btn btn-ghost btn-sm monitor-open-event" data-event-id="${e.id}" data-event-env="${OC.escapeHtml(e.environment || "")}">Detalhes</button></td>
        </tr>`
      )
      .join("");
    return `<div class="monitor-table-wrap"><table class="monitor-table">
      <thead><tr><th>Quando</th><th>Severidade</th><th>Ambiente</th><th>Categoria</th><th>Título</th><th>Detalhe</th><th>Ação</th></tr></thead>
      <tbody>${rows}</tbody>
    </table></div>`;
  }

  function renderHealthCards(overviews) {
    const cards = [];
    const sorted = [...overviews].sort((a, b) => SEV_ORDER[a.overall] - SEV_ORDER[b.overall]);
    sorted.forEach((o) => {
      cards.push(metricCard({ env: o.env, title: "Health latência", metric: o.health }));
      if (OC.monitorState.categories.api) cards.push(metricCard({ env: o.env, title: "Erros 5xx", metric: o.api }));
      if (OC.monitorState.categories.syncs) cards.push(metricCard({ env: o.env, title: "Falhas de sync", metric: o.sync }));
      if (OC.monitorState.categories.deploy) cards.push(metricCard({ env: o.env, title: "Pipeline deploy", metric: o.deploy }));
      if (OC.monitorState.categories.postgres) {
        const pg = o.summary?.postgres?.connections || {};
        const pgMetric = {
          level: pg.max != null ? "ok" : "unknown",
          label: pg.max != null ? "OK" : "Sem dados",
          value: pg.max != null ? String(Math.round(pg.max)) : "—",
          sub: pg.avg != null ? `média ${Math.round(pg.avg)}` : "",
        };
        cards.push(metricCard({ env: o.env, title: "Conexões PostgreSQL", metric: pgMetric }));
      }
    });
    return `<div class="monitor-metric-grid">${cards.join("")}</div>`;
  }

  function renderApiRoutesTable(routesByEnv) {
    const rows = [];
    const instrumentationNotes = [];
    Object.entries(routesByEnv).forEach(([env, data]) => {
      const instr = data.instrumentation || "unavailable";
      if (data.error && instr === "unavailable") {
        instrumentationNotes.push(`${env}: ${data.error}`);
        return;
      }
      if (instr === "unavailable") {
        instrumentationNotes.push(`${env}: middleware de coleta indisponível`);
        return;
      }
      if (instr === "no_traffic") {
        instrumentationNotes.push(`${env}: sem tráfego detectado nas últimas 24h`);
        return;
      }
      (data.slowRoutes || []).forEach((r) => {
        rows.push(`<tr>
          <td>${OC.escapeHtml(env)}</td>
          <td><code>${OC.escapeHtml(r.method || "")} ${OC.escapeHtml(r.route || "")}</code></td>
          <td>${r.avgMs ?? "—"}</td>
          <td>${r.maxMs ?? "—"}</td>
          <td>${r.errors5xx ?? 0}</td>
          <td>${r.count ?? 0}</td>
        </tr>`);
      });
    });
    if (instrumentationNotes.length && !rows.length) {
      return `<div class="monitor-empty-states">${instrumentationNotes.map((n) => `<p class="monitor-empty monitor-empty-neutral">${OC.escapeHtml(n)}</p>`).join("")}</div>`;
    }
    if (!rows.length) return `<p class="monitor-empty monitor-empty-ok">Nenhuma rota lenta encontrada nas últimas 24h.</p>`;
    return `<div class="monitor-table-wrap"><table class="monitor-table">
      <thead><tr><th>Ambiente</th><th>Rota</th><th>Média ms</th><th>Max ms</th><th>5xx</th><th>Amostras</th></tr></thead>
      <tbody>${rows.join("")}</tbody>
    </table></div>`;
  }

  function renderSyncTimeline(syncsByEnv, highlight) {
    const items = [];
    let total = 0;
    let hasError = false;
    Object.entries(syncsByEnv).forEach(([env, data]) => {
      if (data.error) {
        hasError = true;
        items.push(`<li class="monitor-sync-item monitor-sync-fail"><span class="monitor-sync-env">${OC.escapeHtml(env)}</span><span class="monitor-sync-status">Erro: ${OC.escapeHtml(data.error)}</span></li>`);
        return;
      }
      (data.syncs || []).slice(0, 50).forEach((s) => {
        total += 1;
        const key = `${s.source || ""}/${s.kind || ""}`;
        const isHighlight = highlight && key.includes(highlight);
        const ok = s.success ? "ok" : "fail";
        items.push(`<li class="monitor-sync-item monitor-sync-${ok} ${isHighlight ? "is-highlight" : ""}">
          <span class="monitor-sync-env">${OC.escapeHtml(env)}</span>
          <span class="monitor-sync-src">${OC.escapeHtml(s.source || "")}/${OC.escapeHtml(s.kind || "")}</span>
          <span class="monitor-sync-time" title="${OC.escapeHtml(s.startedAt || "")}">${OC.escapeHtml(OC.formatDate(s.startedAt))}</span>
          <span class="monitor-sync-dur">${s.duration_seconds != null ? `${Number(s.duration_seconds).toFixed(1)}s` : "—"}</span>
          <span class="monitor-sync-status">${s.success ? "OK" : "FALHA"}</span>
          ${s.message ? `<span class="monitor-sync-msg">${OC.escapeHtml(String(s.message).slice(0, 120))}</span>` : ""}
        </li>`);
      });
    });
    if (!items.length && !hasError) {
      return `<p class="monitor-empty monitor-empty-neutral">Nenhum sync nos últimos 7 dias. Verifique se há agenda configurada ou se a coleta está ativa.</p>`;
    }
    return `<p class="monitor-section-hint">${total} execução(ões) recentes</p><ul class="monitor-sync-list">${items.join("")}</ul>`;
  }

  function renderLogsViewer(logsByEnv) {
    const blocks = [];
    Object.entries(logsByEnv).forEach(([env, data]) => {
      const lines = data.lines || [];
      if (!lines.length) {
        blocks.push(`<p class="monitor-empty monitor-empty-neutral">${OC.escapeHtml(env)}: nenhuma linha encontrada.</p>`);
        return;
      }
      const rows = lines
        .map(
          (l) => `<tr>
            <td>${OC.escapeHtml(OC.formatDate(l.logged_at))}</td>
            <td>${OC.escapeHtml(l.service || "")}</td>
            <td>${OC.escapeHtml(l.stream || "")}</td>
            <td class="monitor-log-line"><code>${OC.escapeHtml(l.line || "")}</code></td>
          </tr>`
        )
        .join("");
      blocks.push(`<h4 class="monitor-log-env">${OC.escapeHtml(env)}</h4>
        <div class="monitor-table-wrap"><table class="monitor-table monitor-log-table">
          <thead><tr><th>Quando</th><th>Serviço</th><th>Stream</th><th>Linha</th></tr></thead>
          <tbody>${rows}</tbody>
        </table></div>`);
    });
    return blocks.join("") || `<p class="monitor-empty monitor-empty-neutral">Nenhum log encontrado.</p>`;
  }

  function renderDeploySection(deployByEnv) {
    const summaries = [];
    const details = [];
    Object.entries(deployByEnv).forEach(([env, data]) => {
      const agg = data.aggregates24h || {};
      const agg7 = data.aggregates7d || {};
      const failSteps = Object.entries(agg.failuresByStep || {})
        .sort((a, b) => b[1] - a[1])
        .slice(0, 4)
        .map(([step, count]) => `${step}: ${count}`)
        .join(" · ");
      summaries.push(`<div class="monitor-deploy-summary-card">
        <span class="monitor-sync-env">${OC.escapeHtml(env)}</span>
        <span>${agg.total24h ?? 0} deploy(s) / 24h</span>
        <span>Taxa sucesso: ${agg.successRate24h != null ? `${agg.successRate24h}%` : "—"}</span>
        <span>Falhas: ${agg.failed24h ?? 0}</span>
        ${failSteps ? `<span class="monitor-deploy-fail-steps">${OC.escapeHtml(failSteps)}</span>` : ""}
      </div>`);
      const runs = (data.runs || []).slice(0, 12);
      const runItems = runs
        .map(
          (r) => `<li class="monitor-deploy-item">
          <code>${OC.escapeHtml(r.run_id || "")}</code>
          <span>${OC.escapeHtml(r.result || r.status || "")}</span>
          <span class="monitor-sync-time">${OC.escapeHtml(OC.formatDate(r.started_at))}</span>
          ${r.failed_step ? `<span class="monitor-sync-status">falhou: ${OC.escapeHtml(r.failed_step)}</span>` : ""}
        </li>`
        )
        .join("");
      details.push(`<details class="monitor-deploy-details">
        <summary>${OC.escapeHtml(env)} — ${runs.length} deploy(s) recentes (7d: ${agg7.failed24h ?? 0} falhas)</summary>
        ${runItems ? `<ul class="monitor-sync-list">${runItems}</ul>` : `<p class="monitor-empty">Sem deploys.</p>`}
      </details>`);
    });
    if (!summaries.length) return `<p class="monitor-empty monitor-empty-neutral">Nenhum deploy registrado.</p>`;
    return `<div class="monitor-deploy-summary-grid">${summaries.join("")}</div>${details.join("")}`;
  }

  function renderLoadingSection(title) {
    return `<section class="monitor-section"><h3 class="monitor-section-title">${OC.escapeHtml(title)}</h3><div class="loading-inline"><div class="loading-spinner loading-spinner-sm"></div> Carregando…</div></section>`;
  }

  function monitoringFetchPlan(tab, categories) {
    const plan = {
      config: true,
      summaries: false,
      healthSeries: false,
      events: false,
      grouped: false,
      apiRoutes: false,
      syncs: false,
      deploys: false,
      logs: false,
    };
    switch (tab) {
      case "summary":
        plan.summaries = true;
        plan.events = true;
        plan.grouped = true;
        if (categories.availability) plan.healthSeries = true;
        if (categories.deploy) plan.deploys = true;
        break;
      case "incidents":
        plan.events = true;
        plan.grouped = true;
        break;
      case "latency":
        plan.summaries = true;
        plan.healthSeries = true;
        break;
      case "syncs":
        plan.syncs = true;
        break;
      case "apis":
        plan.apiRoutes = true;
        break;
      case "logs":
        plan.logs = true;
        break;
      default:
        plan.summaries = true;
        plan.events = true;
        plan.grouped = true;
        if (categories.availability) plan.healthSeries = true;
        if (categories.deploy) plan.deploys = true;
    }
    return plan;
  }

  function emptyPayloadExtras() {
    return {
      healthSeries: {},
      events: [],
      groupedEvents: [],
      apiRoutes: {},
      syncs: {},
      deploys: {},
      logs: {},
    };
  }

  function renderTabContent(tab, payload, overviews, slos) {
    const { healthSeries, groupedEvents, events, apiRoutes, syncs, deploys, logs, loading } = payload;
    const highlight = OC.currentRoute?.query?.highlight || "";
    const since = OC.currentRoute?.query?.since || "";

    switch (tab) {
      case "summary":
        return `${renderStatusOverview(overviews)}
          ${loading?.grouped ? renderLoadingSection("Incidentes abertos") : `<section class="monitor-section"><h3 class="monitor-section-title">Incidentes abertos</h3>${renderGroupedIncidents(groupedEvents, 5)}</section>`}
          <section class="monitor-section"><h3 class="monitor-section-title">Saúde por ambiente</h3>${renderHealthCards(overviews)}</section>
          ${OC.monitorState.categories.availability ? (loading?.healthSeries ? renderLoadingSection("Tendência de latência (7 dias)") : `<section class="monitor-section"><h3 class="monitor-section-title">Tendência de latência (7 dias)</h3>${buildSvgLineChart(healthSeries, "latência health", slos.healthP95WarnMs)}</section>`) : ""}
          ${OC.monitorState.categories.deploy ? (loading?.deploys ? renderLoadingSection("Pipeline de deploy") : `<section class="monitor-section"><h3 class="monitor-section-title">Pipeline de deploy</h3>${renderDeploySection(deploys)}</section>`) : ""}`;
      case "incidents":
        return loading?.events
          ? renderLoadingSection("Incidentes")
          : `<section class="monitor-section"><h3 class="monitor-section-title">Incidentes</h3>${renderIncidentFilters()}${renderEventsTable(events, groupedEvents)}</section>`;
      case "latency":
        return loading?.healthSeries
          ? renderLoadingSection("Tendência de latência (7 dias)")
          : `<section class="monitor-section"><h3 class="monitor-section-title">Tendência de latência (7 dias)</h3>${buildSvgLineChart(healthSeries, "latência health", slos.healthP95WarnMs)}</section>`;
      case "syncs":
        return loading?.syncs
          ? renderLoadingSection("Processamento de dados (syncs)")
          : `<section class="monitor-section"><h3 class="monitor-section-title">Processamento de dados (syncs)</h3>${renderSyncTimeline(syncs, highlight)}</section>`;
      case "apis":
        return loading?.apiRoutes
          ? renderLoadingSection("Rotas mais lentas")
          : `<section class="monitor-section"><h3 class="monitor-section-title">Rotas mais lentas</h3>${renderApiRoutesTable(apiRoutes)}</section>`;
      case "logs":
        return loading?.logs
          ? renderLoadingSection("Logs de serviço")
          : `<section class="monitor-section"><h3 class="monitor-section-title">Logs de serviço${since ? ` desde ${OC.escapeHtml(OC.formatDate(since))}` : ""}</h3>${renderLogsViewer(logs)}</section>`;
      default:
        return renderTabContent("summary", payload, overviews, slos);
    }
  }

  function bindMonitoringInteractions(root) {
    OC.bindBackNavigation(root);
    root.querySelector("#monitor-refresh-now")?.addEventListener("click", () => OC.refreshMonitoring({ force: true }));
    root.querySelector("#monitor-clear-filters")?.addEventListener("click", () => {
      OC.monitorState.selectedEnvs = [...OC.ENV_ORDER];
      CATEGORY_KEYS.forEach((k) => {
        OC.monitorState.categories[k] = true;
      });
      OC.monitorState.eventFilters = { severity: "", category: "", hours: 24 };
      savePrefs();
      OC.refreshMonitoring();
    });
    root.querySelectorAll("[data-monitor-env]").forEach((el) => {
      el.addEventListener("click", () => {
        const env = el.getAttribute("data-monitor-env");
        if (OC.monitorState.selectedEnvs.includes(env)) {
          OC.monitorState.selectedEnvs = OC.monitorState.selectedEnvs.filter((e) => e !== env);
        } else {
          OC.monitorState.selectedEnvs.push(env);
        }
        if (!OC.monitorState.selectedEnvs.length) OC.monitorState.selectedEnvs = [env];
        savePrefs();
        OC.refreshMonitoring();
      });
    });
    root.querySelectorAll("[data-monitor-cat]").forEach((el) => {
      el.addEventListener("click", () => {
        const key = el.getAttribute("data-monitor-cat");
        OC.monitorState.categories[key] = !OC.monitorState.categories[key];
        savePrefs();
        OC.refreshMonitoring();
      });
    });
    root.querySelectorAll("[data-monitor-tab]").forEach((el) => {
      el.addEventListener("click", () => {
        const tab = el.getAttribute("data-monitor-tab");
        OC.navigate("monitoring", OC.currentRoute.env, { tab, query: OC.currentRoute.query || {} });
      });
    });
    root.querySelectorAll(".monitor-env-drill").forEach((el) => {
      el.addEventListener("click", () => {
        const env = el.getAttribute("data-monitor-env-focus");
        OC.navigate("monitoring", env, { tab: "incidents", focusEnv: env });
      });
    });
    root.querySelectorAll(".monitor-open-event").forEach((el) => {
      el.addEventListener("click", () => {
        const id = el.getAttribute("data-event-id");
        const env = el.getAttribute("data-event-env");
        if (id && env && OC.openMonitorIncidentDrawer) OC.openMonitorIncidentDrawer(env, id);
      });
    });
    root.querySelectorAll(".monitor-group-row").forEach((el) => {
      el.addEventListener("click", (e) => {
        if (e.target.closest(".monitor-open-event")) return;
        const id = el.getAttribute("data-event-id");
        const env = el.getAttribute("data-event-env");
        if (id && env && OC.openMonitorIncidentDrawer) OC.openMonitorIncidentDrawer(env, id);
      });
    });
    ["monitor-filter-severity", "monitor-filter-category", "monitor-filter-hours"].forEach((id) => {
      root.querySelector(`#${id}`)?.addEventListener("change", (e) => {
        const key = id.replace("monitor-filter-", "");
        OC.monitorState.eventFilters[key] = e.target.value;
        savePrefs();
        OC.refreshMonitoring();
      });
    });
  }

  OC.showMonitoringLoading = function showMonitoringLoading() {
    const root = document.getElementById("view-monitoring");
    if (!root) return;
    loadPrefs();
    const activeTab = OC.monitorState.activeTab || "summary";
    root.innerHTML = `${OC.renderInternalPageHeader({
      title: "Monitoramento",
      subtitle: `<span class="monitor-retention-badge">Carregando dados…</span>`,
    })}
    ${OC.renderMonitoringFilters()}
    ${renderTabBar(activeTab)}
    <div class="monitor-tab-panel"><div class="loading-inline"><div class="loading-spinner loading-spinner-sm"></div> Carregando monitoramento…</div></div>`;
    bindMonitoringInteractions(root);
  };

  OC.renderMonitoringView = function renderMonitoringView(payload) {
    const root = document.getElementById("view-monitoring");
    if (!root) return;

    const retention = payload?.config?.retentionDays ?? 7;
    const warnings = payload?.warnings || [];
    const slos = getSlos(payload?.config);
    const overviews = (payload?.envSummaries || []).map(({ env, summary }) =>
      computeEnvOverview(env, summary, payload?.deploys?.[env], slos)
    );
    const activeTab = OC.monitorState.activeTab || "summary";

    root.innerHTML = `${OC.renderInternalPageHeader({
      title: "Monitoramento",
      subtitle: `<span class="monitor-retention-badge">Retenção automática: ${retention} dias</span>`,
    })}
    ${renderMetaBar(payload?.config, warnings)}
    ${OC.renderMonitoringFilters()}
    ${renderTabBar(activeTab)}
    <div class="monitor-tab-panel">${renderTabContent(activeTab, payload, overviews, slos)}</div>`;

    bindMonitoringInteractions(root);
  };

  OC.fetchMonitoringJson = async function fetchMonitoringJson(url, fallback = null, options = {}) {
    try {
      return await OC.fetchJson(url, options);
    } catch (err) {
      if (err.name === "AbortError") throw err;
      return { error: err.message || String(err), ...(fallback || {}) };
    }
  };

  OC.refreshMonitoring = async function refreshMonitoring(options = {}) {
    if (OC.currentRoute?.view !== "monitoring") return;

    const force = options.force === true;
    if (OC._monitorRefreshInFlight) {
      if (!force) {
        OC._monitorRefreshPending = true;
        return;
      }
      OC._monitorAbortController?.abort();
    }

    OC._monitorRefreshInFlight = true;
    OC._monitorRefreshGeneration += 1;
    const generation = OC._monitorRefreshGeneration;
    const abortController = new AbortController();
    OC._monitorAbortController = abortController;
    const fetchOpts = () => ({ signal: abortController.signal });

    loadPrefs();
    const warnings = [];
    const tab = OC.monitorState.activeTab || "summary";
    const plan = monitoringFetchPlan(tab, OC.monitorState.categories);
    const prev = OC.monitorState.payload || {};

    if (options.showLoading || !prev.envSummaries?.length) {
      OC.showMonitoringLoading();
    }

    const envs = OC.monitorState.selectedEnvs.filter((e) => OC.ENV_ORDER.includes(e));
    if (!envs.length) envs.push("DEV");

    const loading = {};
    if (plan.grouped) loading.grouped = true;
    if (plan.healthSeries) loading.healthSeries = true;
    if (plan.events) loading.events = true;
    if (plan.apiRoutes) loading.apiRoutes = true;
    if (plan.syncs) loading.syncs = true;
    if (plan.deploys) loading.deploys = true;
    if (plan.logs) loading.logs = true;

    let config = prev.config;
    let envSummaries = prev.envSummaries || [];
    let healthSeries = { ...emptyPayloadExtras().healthSeries, ...(prev.healthSeries || {}) };
    let events = prev.events || [];
    let groupedEvents = prev.groupedEvents || [];
    let apiRoutes = { ...emptyPayloadExtras().apiRoutes, ...(prev.apiRoutes || {}) };
    let syncs = { ...emptyPayloadExtras().syncs, ...(prev.syncs || {}) };
    let deploys = { ...emptyPayloadExtras().deploys, ...(prev.deploys || {}) };
    let logs = { ...emptyPayloadExtras().logs, ...(prev.logs || {}) };

    const publish = (partial = {}) => {
      if (generation !== OC._monitorRefreshGeneration) return;
      const payload = {
        config,
        envSummaries,
        healthSeries,
        events,
        groupedEvents,
        apiRoutes,
        syncs,
        deploys,
        logs,
        warnings,
        loading: { ...loading, ...partial.loading },
      };
      OC.monitorState.payload = payload;
      OC.renderMonitoringView(payload);
    };

    const finishMonitoringRefresh = (payload) => {
      if (generation !== OC._monitorRefreshGeneration) return;
      OC.monitorState.lastRefreshedAt = new Date().toISOString();
      OC.monitorState.payload = payload;
      OC.renderMonitoringView(payload);
      const openEventId = OC.currentRoute?.query?.event;
      const openEnv = OC.currentRoute?.query?.env;
      if (openEventId && openEnv && OC.openMonitorIncidentDrawer) {
        OC.openMonitorIncidentDrawer(openEnv.toUpperCase(), openEventId);
      }
    };

    const DASHBOARD_TABS = new Set(["summary", "incidents", "latency"]);

    try {
      if (DASHBOARD_TABS.has(tab)) {
        const ef = OC.monitorState.eventFilters;
        const dashParams = new URLSearchParams({
          envs: envs.join(","),
          tab,
          eventHours: String(ef.hours || 24),
          limit: "100",
        });
        if (plan.healthSeries) dashParams.set("healthSeries", "1");
        if (plan.deploys) dashParams.set("deploy", "1");
        if (OC.monitorState.categories.api) dashParams.set("api", "1");
        if (ef.severity) dashParams.set("severity", ef.severity);
        if (ef.category) dashParams.set("category", ef.category);

        const dash = await OC.fetchMonitoringJson(
          `/api/v1/monitoring/dashboard?${dashParams}`,
          {},
          fetchOpts()
        );
        if (dash.error) warnings.push(dash.error);

        config = dash.config || config;
        if (config?.error) warnings.push(config.error);
        OC.monitorState.config = config;
        envSummaries = dash.envSummaries || envSummaries;
        healthSeries = { ...healthSeries, ...(dash.healthSeries || {}) };
        deploys = { ...deploys, ...(dash.deploys || {}) };
        groupedEvents = dash.groupedEvents || [];
        events = dash.events || [];

        finishMonitoringRefresh({
          config,
          envSummaries,
          healthSeries,
          events,
          groupedEvents,
          apiRoutes,
          syncs,
          deploys,
          logs,
          warnings,
          loading: {},
        });
        return;
      }

      const configPromise = plan.config
        ? OC.fetchMonitoringJson(
            "/api/v1/monitoring/config",
            { retentionDays: 7, enabledCategories: OC.monitorState.categories },
            fetchOpts()
          )
        : Promise.resolve(config);

      const summariesPromise = plan.summaries
        ? Promise.all(
            envs.map(async (env) => {
              const summary = await OC.fetchMonitoringJson(
                `/api/v1/monitoring/${env}/summary`,
                { environment: env },
                fetchOpts()
              );
              if (summary.error) warnings.push(`${env} summary: ${summary.error}`);
              return { env, summary };
            })
          )
        : Promise.resolve(envSummaries);

      [config, envSummaries] = await Promise.all([configPromise, summariesPromise]);
      if (config?.error) warnings.push(config.error);
      OC.monitorState.config = config;

      if (generation === OC._monitorRefreshGeneration) {
        publish();
      }

      const ef = OC.monitorState.eventFilters;
      const eventParams = new URLSearchParams({ limit: "100" });
      if (ef.severity) eventParams.set("severity", ef.severity);
      if (ef.category) eventParams.set("category", ef.category);
      if (ef.hours) eventParams.set("hours", String(ef.hours));

      const phase2 = [];

      if (plan.healthSeries) {
        phase2.push(
          Promise.all(
            envs.map(async (env) => {
              healthSeries[env] = await OC.fetchMonitoringJson(
                `/api/v1/monitoring/${env}/series?metric=health_latency_ms&hours=168`,
                { environment: env, points: [] },
                fetchOpts()
              );
            })
          ).then(() => {
            delete loading.healthSeries;
          })
        );
      }

      if (plan.events) {
        phase2.push(
          Promise.all(
            envs.map((env) =>
              OC.fetchMonitoringJson(
                `/api/v1/monitoring/${env}/events?${eventParams}`,
                { events: [] },
                fetchOpts()
              )
            )
          ).then((eventsLists) => {
            events = eventsLists
              .flatMap((r) => r.events || [])
              .sort((a, b) => String(b.recorded_at).localeCompare(String(a.recorded_at)))
              .slice(0, 200);
            delete loading.events;
          })
        );
      }

      if (plan.grouped) {
        phase2.push(
          OC.fetchMonitoringJson(
            `/api/v1/monitoring/events/grouped?hours=${ef.hours || 24}`,
            { groups: [] },
            fetchOpts()
          ).then((groupedResp) => {
            groupedEvents = (groupedResp.groups || []).filter((g) => envs.includes(g.environment));
            delete loading.grouped;
          })
        );
      }

      if (plan.apiRoutes) {
        phase2.push(
          Promise.all(
            envs.map(async (env) => {
              apiRoutes[env] = await OC.fetchMonitoringJson(
                `/api/v1/monitoring/${env}/api-routes?window=24h`,
                { environment: env, slowRoutes: [] },
                fetchOpts()
              );
            })
          ).then(() => {
            delete loading.apiRoutes;
          })
        );
      }

      if (plan.syncs) {
        phase2.push(
          Promise.all(
            envs.map(async (env) => {
              syncs[env] = await OC.fetchMonitoringJson(
                `/api/v1/monitoring/${env}/syncs`,
                { environment: env, syncs: [] },
                fetchOpts()
              );
            })
          ).then(() => {
            delete loading.syncs;
          })
        );
      }

      if (plan.deploys) {
        phase2.push(
          Promise.all(
            envs.map(async (env) => {
              deploys[env] = await OC.fetchMonitoringJson(
                `/api/v1/monitoring/${env}/deploy`,
                { environment: env, runs: [], steps: [] },
                fetchOpts()
              );
            })
          ).then(() => {
            delete loading.deploys;
          })
        );
      }

      if (plan.logs) {
        const since = OC.currentRoute?.query?.since || "";
        phase2.push(
          Promise.all(
            envs.map(async (env) => {
              const qs = since
                ? `?since=${encodeURIComponent(since)}&pattern=ERROR&limit=200`
                : "?pattern=ERROR&limit=200";
              logs[env] = await OC.fetchMonitoringJson(
                `/api/v1/monitoring/${env}/logs${qs}`,
                { environment: env, lines: [] },
                fetchOpts()
              );
            })
          ).then(() => {
            delete loading.logs;
          })
        );
      }

      await Promise.all(phase2);

      finishMonitoringRefresh({
        config,
        envSummaries,
        healthSeries,
        events,
        groupedEvents,
        apiRoutes,
        syncs,
        deploys,
        logs,
        warnings,
        loading: {},
      });
    } catch (err) {
      if (err.name === "AbortError") return;
      const root = document.getElementById("view-monitoring");
      if (root) {
        root.innerHTML = `${OC.renderBackToEnvironments("DEV")}
          <p class="global-error" role="alert">Erro ao carregar monitoramento: ${OC.escapeHtml(err.message)}</p>`;
        OC.bindBackNavigation(root);
      }
    } finally {
      OC._monitorRefreshInFlight = false;
      if (OC._monitorRefreshPending) {
        OC._monitorRefreshPending = false;
        OC.refreshMonitoring();
      }
    }
  };

  OC.startMonitoringRefresh = function startMonitoringRefresh() {
    OC.stopMonitoringRefresh();
    OC.monitorTimer = setInterval(() => {
      if (!OC._monitorRefreshInFlight) OC.refreshMonitoring();
    }, MONITOR_REFRESH_MS);
  };

  OC.stopMonitoringRefresh = function stopMonitoringRefresh() {
    if (OC.monitorTimer) {
      clearInterval(OC.monitorTimer);
      OC.monitorTimer = null;
    }
  };

  loadPrefs();
})();
