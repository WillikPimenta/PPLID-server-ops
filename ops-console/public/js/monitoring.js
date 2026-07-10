/* global window, document */
(function () {
  window.OpsConsole = window.OpsConsole || {};
  const OC = window.OpsConsole;

  const STORAGE_ENVS = "pplid-monitor-envs";
  const STORAGE_CATS = "pplid-monitor-categories";
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
  OC.monitorState = {
    config: null,
    selectedEnvs: ["MAIN", "DEV", "HOM"],
    categories: {
      api: true,
      availability: true,
      postgres: true,
      syncs: true,
      deploy: true,
      logs: true,
    },
    dataByEnv: {},
    lastRefreshedAt: null,
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
  }

  function savePrefs() {
    localStorage.setItem(STORAGE_ENVS, JSON.stringify(OC.monitorState.selectedEnvs));
    localStorage.setItem(STORAGE_CATS, JSON.stringify(OC.monitorState.categories));
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

  function evalHealthMetric(summary, slos) {
    const health = summary?.health || {};
    const count = health.count || 0;
    const max = health.max;
    const avg = health.avg;
    if (!count || max == null) {
      return { level: "unknown", label: "Sem dados", detail: "Coleta indisponível ou sem amostras" };
    }
    let level = "ok";
    let label = "OK";
    if (max >= slos.healthP95CriticalMs) {
      level = "critical";
      label = "Violando SLO";
    } else if (max >= slos.healthP95WarnMs) {
      level = "warn";
      label = "Acima do esperado";
    }
    const delta = pctChange(max, avg);
    return {
      level,
      label,
      value: `${Math.round(max)} ms`,
      slo: `<= ${slos.healthP95WarnMs} ms`,
      sub: avg != null ? `média ${Math.round(avg)} ms` : "",
      delta: delta != null ? `${delta >= 0 ? "+" : ""}${delta}% vs média 24h` : "",
    };
  }

  function evalApi5xxMetric(summary) {
    const api = summary?.api || {};
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
      return {
        level: "neutral",
        label: "Sem tráfego",
        detail: "Nenhuma requisição nas últimas 24h",
        value: "0",
        sub: "reqs 0",
      };
    }
    if (errors === 0) {
      return {
        level: "ok",
        label: "Sem erros",
        detail: "Sem erros 5xx nas últimas 24h",
        value: "0",
        sub: `reqs ${requests}`,
      };
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
      return {
        level: "ok",
        label: "Pipeline estável",
        value: `${rate ?? 100}%`,
        sub: `${aggregates.total24h} deploy(s)`,
      };
    }
    const level =
      rate != null && rate < 100 - slos.deployFailureRateWarnPct ? "critical" : "warn";
    return {
      level,
      label: `${failed} falha(s)`,
      value: rate != null ? `${rate}%` : String(failed),
      sub: `${aggregates.total24h} deploy(s)`,
    };
  }

  function computeEnvOverview(env, summary, deployData, slos) {
    const health = evalHealthMetric(summary, slos);
    const api = evalApi5xxMetric(summary);
    const sync = evalSyncMetric(summary, slos);
    const deploy = evalDeployMetric(deployData?.aggregates24h, slos);
    const uptime = summary?.uptimePct;
    let uptimeLevel = "unknown";
    if (uptime != null) {
      uptimeLevel = uptime < slos.uptimeWarnPct ? "warn" : "ok";
    }
    const overall = worstLevel(health.level, api.level, sync.level, deploy.level, uptimeLevel);
    const reasons = [];
    if (health.level === "critical" || health.level === "warn") reasons.push(`latência p95 ${health.value}`);
    if (api.level === "warn" || api.level === "critical") reasons.push(api.label);
    if (deploy.level === "warn" || deploy.level === "critical") reasons.push(deploy.label);
    if (sync.level === "warn") reasons.push("falhas de sync");
    return { env, overall, label: STATUS_LABELS[overall] || overall, reasons, health, api, sync, deploy, uptime };
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
    return `<article class="monitor-metric-card monitor-metric-${metric.level}">
      <header class="monitor-metric-head">
        <span class="monitor-metric-env">${OC.escapeHtml(env)}</span>
        <span class="monitor-metric-title">${OC.escapeHtml(title)}</span>
        ${statusBadge(metric.level, metric.label)}
      </header>
      <p class="monitor-metric-value">${OC.escapeHtml(metric.value ?? "—")}</p>
      ${metric.slo ? `<p class="monitor-metric-slo">SLO: ${OC.escapeHtml(metric.slo)}</p>` : ""}
      ${metric.delta ? `<p class="monitor-metric-delta">${OC.escapeHtml(metric.delta)}</p>` : ""}
      ${metric.sub ? `<p class="monitor-metric-sub">${OC.escapeHtml(metric.sub)}</p>` : ""}
      ${metric.detail ? `<p class="monitor-metric-detail">${OC.escapeHtml(metric.detail)}</p>` : ""}
    </article>`;
  }

  function renderMetaBar(config, warnings) {
    const refreshed = OC.monitorState.lastRefreshedAt
      ? OC.formatDate(OC.monitorState.lastRefreshedAt)
      : "—";
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

  function renderStatusOverview(overviews) {
    const sorted = [...overviews].sort((a, b) => SEV_ORDER[a.overall] - SEV_ORDER[b.overall]);
    const cards = sorted
      .map(
        (o) => `<div class="monitor-env-status monitor-env-status-${o.overall}">
          <span class="monitor-env-status-name">${OC.escapeHtml(o.env)}</span>
          ${statusBadge(o.overall, o.label)}
          <p class="monitor-env-status-reason">${OC.escapeHtml(o.reasons.join(" · ") || "Sem alertas ativos")}</p>
        </div>`
      )
      .join("");
    const worst = sorted[0];
    return `<section class="monitor-section monitor-overview">
      <h3 class="monitor-section-title">Status geral</h3>
      <p class="monitor-overview-note">${worst && worst.overall !== "ok" ? `Ambiente mais crítico: <strong>${OC.escapeHtml(worst.env)}</strong> (${OC.escapeHtml(worst.label)})` : "Todos os ambientes selecionados estão dentro do esperado."}</p>
      <div class="monitor-env-status-grid">${cards}</div>
    </section>`;
  }

  function renderActiveAlerts(overviews, events) {
    const alerts = [];
    overviews.forEach((o) => {
      if (o.overall === "critical" || o.overall === "warn") {
        alerts.push({
          severity: o.overall === "critical" ? "critical" : "warn",
          env: o.env,
          category: "saúde",
          title: o.reasons[0] || "Degradação detectada",
          action: "Ver métricas do ambiente",
          link: "#/monitoring",
        });
      }
      const agg = o.deploy;
      if (agg?.level === "critical" || agg?.level === "warn") {
        alerts.push({
          severity: agg.level === "critical" ? "critical" : "warn",
          env: o.env,
          category: "deploy",
          title: agg.label,
          action: "Ver pipeline de deploy",
          link: `#/deploy?env=${o.env}`,
        });
      }
    });
    (events || []).slice(0, 8).forEach((e) => {
      const sev = String(e.severity || "info").toLowerCase();
      if (sev === "info") return;
      alerts.push({
        severity: sev === "critical" ? "critical" : "warn",
        env: e.environment,
        category: e.category,
        title: e.title,
        action: e.recommendedAction || "Investigar",
        link: e.investigationLink || "#/monitoring",
        when: e.recorded_at,
      });
    });
    alerts.sort((a, b) => SEV_ORDER[a.severity] - SEV_ORDER[b.severity]);
    const unique = [];
    const seen = new Set();
    alerts.forEach((a) => {
      const key = `${a.env}:${a.category}:${a.title}`;
      if (seen.has(key)) return;
      seen.add(key);
      unique.push(a);
    });
    if (!unique.length) {
      return `<section class="monitor-section"><h3 class="monitor-section-title">Alertas ativos</h3><p class="monitor-empty monitor-empty-ok">Nenhum alerta ativo no momento.</p></section>`;
    }
    const items = unique
      .slice(0, 6)
      .map(
        (a) => `<li class="monitor-alert-item monitor-alert-${a.severity}">
          <span class="monitor-sev monitor-sev-${OC.escapeHtml(a.severity)}">${OC.escapeHtml(a.severity)}</span>
          <span class="monitor-alert-env">${OC.escapeHtml(a.env || "—")}</span>
          <span class="monitor-alert-title">${OC.escapeHtml(a.title || "")}</span>
          ${a.when ? `<span class="monitor-alert-when">${OC.escapeHtml(OC.formatRelativeTime(a.when))}</span>` : ""}
          <a class="monitor-alert-action" href="${OC.escapeHtml(a.link)}">${OC.escapeHtml(a.action)}</a>
        </li>`
      )
      .join("");
    return `<section class="monitor-section"><h3 class="monitor-section-title">Alertas ativos</h3><ul class="monitor-alert-list">${items}</ul></section>`;
  }

  function renderHealthCards(overviews, slos) {
    const cards = [];
    const sorted = [...overviews].sort((a, b) => SEV_ORDER[a.overall] - SEV_ORDER[b.overall]);
    sorted.forEach((o) => {
      cards.push(metricCard({ env: o.env, title: "Health p95", metric: o.health }));
      if (OC.monitorState.categories.api) {
        cards.push(metricCard({ env: o.env, title: "Erros 5xx", metric: o.api }));
      }
      if (OC.monitorState.categories.syncs) {
        cards.push(metricCard({ env: o.env, title: "Falhas de sync", metric: o.sync }));
      }
      if (OC.monitorState.categories.deploy) {
        cards.push(metricCard({ env: o.env, title: "Pipeline deploy", metric: o.deploy }));
      }
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
    return `<section class="monitor-section"><h3 class="monitor-section-title">Saúde por ambiente</h3><div class="monitor-metric-grid">${cards.join("")}</div></section>`;
  }

  function renderEventsTable(events) {
    if (!events?.length) {
      return `<p class="monitor-empty monitor-empty-neutral">Nenhum evento recente.</p>`;
    }
    const rows = events
      .sort((a, b) => {
        const sev = SEV_ORDER[String(a.severity).toLowerCase()] - SEV_ORDER[String(b.severity).toLowerCase()];
        if (sev !== 0) return sev;
        return String(b.recorded_at).localeCompare(String(a.recorded_at));
      })
      .map(
        (e) => `<tr>
          <td title="${OC.escapeHtml(e.recorded_at || "")}">${OC.escapeHtml(OC.formatDate(e.recorded_at))}</td>
          <td><span class="monitor-sev monitor-sev-${OC.escapeHtml(e.severity || "info")}">${OC.escapeHtml(e.severity || "")}</span></td>
          <td>${OC.escapeHtml(e.environment || "")}</td>
          <td>${OC.escapeHtml(e.category || "")}</td>
          <td>${OC.escapeHtml(e.title || "")}</td>
          <td class="monitor-detail-col">${OC.escapeHtml(e.detail || "")}</td>
          <td>${e.recommendedAction ? `<a href="${OC.escapeHtml(e.investigationLink || "#/monitoring")}">${OC.escapeHtml(e.recommendedAction)}</a>` : "—"}</td>
        </tr>`
      )
      .join("");
    return `<div class="monitor-table-wrap"><table class="monitor-table">
      <thead><tr><th>Quando</th><th>Severidade</th><th>Ambiente</th><th>Categoria</th><th>Título</th><th>Detalhe</th><th>Ação recomendada</th></tr></thead>
      <tbody>${rows}</tbody>
    </table></div>`;
  }

  function renderApiRoutesTable(routesByEnv) {
    const rows = [];
    let instrumentationNotes = [];
    Object.entries(routesByEnv).forEach(([env, data]) => {
      const instr = data.instrumentation || "unavailable";
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
      return `<div class="monitor-empty-states">${instrumentationNotes
        .map((n) => `<p class="monitor-empty monitor-empty-neutral">${OC.escapeHtml(n)}</p>`)
        .join("")}</div>`;
    }
    if (!rows.length) {
      return `<p class="monitor-empty monitor-empty-ok">Nenhuma rota lenta encontrada nas últimas 24h.</p>`;
    }
    return `<div class="monitor-table-wrap"><table class="monitor-table">
      <thead><tr><th>Ambiente</th><th>Rota</th><th>Média ms</th><th>Max ms</th><th>5xx</th><th>Amostras</th></tr></thead>
      <tbody>${rows.join("")}</tbody>
    </table></div>`;
  }

  function renderSyncTimeline(syncsByEnv) {
    const items = [];
    let total = 0;
    Object.entries(syncsByEnv).forEach(([env, data]) => {
      (data.syncs || []).slice(0, 30).forEach((s) => {
        total += 1;
        const ok = s.success ? "ok" : "fail";
        items.push(`<li class="monitor-sync-item monitor-sync-${ok}">
          <span class="monitor-sync-env">${OC.escapeHtml(env)}</span>
          <span class="monitor-sync-src">${OC.escapeHtml(s.source || "")}/${OC.escapeHtml(s.kind || "")}</span>
          <span class="monitor-sync-time" title="${OC.escapeHtml(s.startedAt || "")}">${OC.escapeHtml(OC.formatDate(s.startedAt))}</span>
          <span class="monitor-sync-dur">${s.duration_seconds != null ? `${Number(s.duration_seconds).toFixed(1)}s` : "—"}</span>
          <span class="monitor-sync-status">${s.success ? "OK" : "FALHA"}</span>
        </li>`);
      });
    });
    if (!items.length) {
      return `<p class="monitor-empty monitor-empty-neutral">Nenhum sync nos últimos 7 dias. Verifique se há agenda configurada ou se a coleta está ativa.</p>`;
    }
    return `<p class="monitor-section-hint">${total} execução(ões) recentes</p><ul class="monitor-sync-list">${items.join("")}</ul>`;
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
          <span class="monitor-sync-time" title="${OC.escapeHtml(r.started_at || "")}">${OC.escapeHtml(OC.formatDate(r.started_at))}</span>
          ${r.failed_step ? `<span class="monitor-sync-status">falhou: ${OC.escapeHtml(r.failed_step)}</span>` : ""}
        </li>`
        )
        .join("");
      details.push(`<details class="monitor-deploy-details">
        <summary>${OC.escapeHtml(env)} — ${runs.length} deploy(s) recentes (7d: ${agg7.failed24h ?? 0} falhas)</summary>
        ${runItems ? `<ul class="monitor-sync-list">${runItems}</ul>` : `<p class="monitor-empty">Sem deploys.</p>`}
      </details>`);
    });
    if (!summaries.length) {
      return `<p class="monitor-empty monitor-empty-neutral">Nenhum deploy registrado.</p>`;
    }
    return `<div class="monitor-deploy-summary-grid">${summaries.join("")}</div>${details.join("")}`;
  }

  function bindMonitoringInteractions(root) {
    OC.bindBackNavigation(root);
    root.querySelector("#monitor-refresh-now")?.addEventListener("click", () => OC.refreshMonitoring());
    root.querySelector("#monitor-clear-filters")?.addEventListener("click", () => {
      OC.monitorState.selectedEnvs = [...OC.ENV_ORDER];
      CATEGORY_KEYS.forEach((k) => {
        OC.monitorState.categories[k] = true;
      });
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
  }

  OC.renderMonitoringView = function renderMonitoringView(payload) {
    const root = document.getElementById("view-monitoring");
    if (!root) return;

    const retention = payload?.config?.retentionDays ?? 7;
    const warnings = payload?.warnings || [];
    const slos = getSlos(payload?.config);
    const overviews = (payload?.envSummaries || []).map(({ env, summary }) => {
      const overview = computeEnvOverview(env, summary, payload?.deploys?.[env], slos);
      overview.summary = summary;
      return overview;
    });
    const healthSeries = payload?.healthSeries || {};
    const events = payload?.events || [];
    const apiRoutes = payload?.apiRoutes || {};
    const syncs = payload?.syncs || {};
    const deploys = payload?.deploys || {};

    root.innerHTML = `${OC.renderInternalPageHeader({
      title: "Monitoramento",
      subtitle: `<span class="monitor-retention-badge">Retenção automática: ${retention} dias</span>`,
    })}
    ${renderMetaBar(payload?.config, warnings)}
    ${OC.renderMonitoringFilters()}
    ${renderStatusOverview(overviews)}
    ${renderActiveAlerts(overviews, events)}
    ${renderHealthCards(overviews, slos)}
    ${
      OC.monitorState.categories.availability
        ? `<section class="monitor-section"><h3 class="monitor-section-title">Tendência de latência (7 dias)</h3>${buildSvgLineChart(healthSeries, "latência health", slos.healthP95WarnMs)}</section>`
        : ""
    }
  ${
    OC.monitorState.categories.deploy
      ? `<section class="monitor-section"><h3 class="monitor-section-title">Pipeline de deploy</h3>${renderDeploySection(deploys)}</section>`
      : ""
  }
    <section class="monitor-section"><h3 class="monitor-section-title">Eventos recentes</h3>${renderEventsTable(events)}</section>
    ${
      OC.monitorState.categories.api
        ? `<section class="monitor-section"><h3 class="monitor-section-title">Rotas mais lentas</h3>${renderApiRoutesTable(apiRoutes)}</section>`
        : ""
    }
    ${
      OC.monitorState.categories.syncs
        ? `<section class="monitor-section"><h3 class="monitor-section-title">Processamento de dados (syncs)</h3>${renderSyncTimeline(syncs)}</section>`
        : ""
    }`;

    bindMonitoringInteractions(root);
  };

  OC.fetchMonitoringJson = async function fetchMonitoringJson(url, fallback = null) {
    try {
      return await OC.fetchJson(url);
    } catch (err) {
      return { error: err.message || String(err), ...(fallback || {}) };
    }
  };

  OC.refreshMonitoring = async function refreshMonitoring() {
    if (OC.currentRoute?.view !== "monitoring") return;
    loadPrefs();
    const warnings = [];
    try {
      const config = await OC.fetchMonitoringJson("/api/v1/monitoring/config", {
        retentionDays: 7,
        enabledCategories: OC.monitorState.categories,
      });
      if (config.error) warnings.push(config.error);
      OC.monitorState.config = config;
      const envs = OC.monitorState.selectedEnvs.filter((e) => OC.ENV_ORDER.includes(e));
      if (!envs.length) envs.push("DEV");

      const envSummaries = await Promise.all(
        envs.map(async (env) => {
          const summary = await OC.fetchMonitoringJson(`/api/v1/monitoring/${env}/summary`, {
            environment: env,
          });
          if (summary.error) warnings.push(`${env} summary: ${summary.error}`);
          return { env, summary };
        })
      );

      const healthSeries = {};
      if (OC.monitorState.categories.availability) {
        await Promise.all(
          envs.map(async (env) => {
            healthSeries[env] = await OC.fetchMonitoringJson(
              `/api/v1/monitoring/${env}/series?metric=health_latency_ms&hours=168`,
              { environment: env, points: [] }
            );
          })
        );
      }

      const eventsLists = await Promise.all(
        envs.map((env) =>
          OC.fetchMonitoringJson(`/api/v1/monitoring/${env}/events?limit=50`, { events: [] })
        )
      );
      const events = eventsLists
        .flatMap((r) => r.events || [])
        .sort((a, b) => String(b.recorded_at).localeCompare(String(a.recorded_at)))
        .slice(0, 80);

      const apiRoutes = {};
      if (OC.monitorState.categories.api) {
        await Promise.all(
          envs.map(async (env) => {
            apiRoutes[env] = await OC.fetchMonitoringJson(
              `/api/v1/monitoring/${env}/api-routes?window=24h`,
              { environment: env, slowRoutes: [] }
            );
          })
        );
      }

      const syncs = {};
      if (OC.monitorState.categories.syncs) {
        await Promise.all(
          envs.map(async (env) => {
            syncs[env] = await OC.fetchMonitoringJson(`/api/v1/monitoring/${env}/syncs`, {
              environment: env,
              syncs: [],
            });
          })
        );
      }

      const deploys = {};
      if (OC.monitorState.categories.deploy) {
        await Promise.all(
          envs.map(async (env) => {
            deploys[env] = await OC.fetchMonitoringJson(`/api/v1/monitoring/${env}/deploy`, {
              environment: env,
              runs: [],
              steps: [],
            });
          })
        );
      }

      OC.monitorState.lastRefreshedAt = new Date().toISOString();
      OC.renderMonitoringView({
        config,
        envSummaries,
        healthSeries,
        events,
        apiRoutes,
        syncs,
        deploys,
        warnings,
      });
    } catch (err) {
      const root = document.getElementById("view-monitoring");
      if (root) {
        root.innerHTML = `${OC.renderBackToEnvironments("DEV")}
          <p class="global-error" role="alert">Erro ao carregar monitoramento: ${OC.escapeHtml(err.message)}</p>`;
        OC.bindBackNavigation(root);
      }
    }
  };

  OC.startMonitoringRefresh = function startMonitoringRefresh() {
    OC.stopMonitoringRefresh();
    OC.monitorTimer = setInterval(() => OC.refreshMonitoring(), MONITOR_REFRESH_MS);
  };

  OC.stopMonitoringRefresh = function stopMonitoringRefresh() {
    if (OC.monitorTimer) {
      clearInterval(OC.monitorTimer);
      OC.monitorTimer = null;
    }
  };

  loadPrefs();
})();
