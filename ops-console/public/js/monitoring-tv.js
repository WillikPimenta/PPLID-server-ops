/* global window, document, fetch */
(function () {
  "use strict";

  const ENV_ORDER = ["MAIN"];
  const TRAFFIC_COLORS = { requests: "var(--cyan)", uniqueUsers: "var(--purple)", memory: "var(--green)" };
  const LATENCY_COLOR = "var(--amber)";
  const REFRESH_MS = 30_000;
  const THEME_STORAGE_KEY = "ops-monitoring-tv-theme";
  let refreshing = false;

  const byId = (id) => document.getElementById(id);
  const safeNumber = (value) => {
    if (value == null || value === "") return null;
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
  };
  const escapeHtml = (value) => String(value ?? "")
    .replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;").replaceAll("'", "&#039;");

  function formatMs(value) {
    const number = safeNumber(value);
    if (number == null) return "—";
    return `${number < 10 ? number.toFixed(1) : Math.round(number)} <small>ms</small>`;
  }

  function formatPct(value, digits = 1) {
    const number = safeNumber(value);
    return number == null ? "—" : `${number.toFixed(digits)}%`;
  }

  function formatCount(value) {
    const number = safeNumber(value);
    if (number == null) return "—";
    return Intl.NumberFormat("pt-BR", { notation: number >= 1000 ? "compact" : "standard", maximumFractionDigits: 1 }).format(number);
  }

  function relativeTime(value) {
    const timestamp = new Date(value).getTime();
    if (!Number.isFinite(timestamp)) return "agora";
    const seconds = Math.max(0, Math.round((Date.now() - timestamp) / 1000));
    if (seconds < 60) return "agora";
    if (seconds < 3600) return `há ${Math.floor(seconds / 60)} min`;
    if (seconds < 86400) return `há ${Math.floor(seconds / 3600)} h`;
    return `há ${Math.floor(seconds / 86400)} d`;
  }

  async function fetchJson(url) {
    const response = await fetch(url, { headers: { Accept: "application/json" }, cache: "no-store" });
    if (!response.ok) {
      if (response.status === 401) throw new Error("Console bloqueado — desbloqueie a sessão para retomar o painel");
      throw new Error(`Falha ao atualizar (${response.status})`);
    }
    return response.json();
  }

  function summaryMap(payload) {
    return Object.fromEntries((payload.envSummaries || []).map((item) => [item.env, item.summary || {}]));
  }

  function renderKpis(payload, summaries, overview, syncPayload, apiRoutes, latencyP95) {
    const p95Limit = safeNumber(payload.config?.slos?.healthP95WarnMs) || 1000;
    const mainSummary = summaries.MAIN || {};
    byId("kpi-uptime").textContent = formatPct(safeNumber(mainSummary.uptimePct), 1);

    if (apiRoutes?.traffic) {
      const activeUsers = apiRoutes.traffic.activeUsers || {};
      const activeCount = safeNumber(activeUsers.count ?? apiRoutes.traffic.totals?.activeUsersNow);
      const peakCount = safeNumber(activeUsers.peakCount);
      const windowMinutes = safeNumber(activeUsers.windowMinutes) || 5;
      byId("kpi-active-users").textContent = formatCount(activeCount);
      byId("kpi-active-users-note").textContent = `atividade autenticada · últimos ${windowMinutes} min`;
      byId("kpi-active-users-peak").textContent = peakCount == null
        ? "Maior pico registrado: — agentes"
        : `Maior pico registrado: ${formatCount(peakCount)} agentes`;
    }

    const p95 = latencyP95 ?? safeNumber(mainSummary.health?.p95);
    byId("kpi-latency").innerHTML = formatMs(p95);
    byId("kpi-latency-note").textContent = `${latencyP95 != null ? "últimas 6 horas" : "resumo disponível"} · limite ${Math.round(p95Limit)} ms`;
    byId("kpi-latency-card").className = `kpi-card ${p95 != null && p95 >= p95Limit ? "tone-red" : "tone-amber"}`;

    if (overview?.environments?.MAIN) {
      const main = overview.environments.MAIN;
      const services = main.services || [];
      const collectionFresh = mainSummary.dataFresh !== false && !main.runtime?.stale;
      const failedServices = services.filter((service) => service.status !== "ok");
      const offline = main.runtime?.availabilityClass === "offline" || main.runtime?.reachable === false;
      const degraded = !collectionFresh || failedServices.length || ["degraded", "saturated", "stale"].includes(main.availabilityAggregate);
      const health = offline
        ? { label: "Indisponível", tone: "red" }
        : degraded
          ? { label: "Atenção", tone: "amber" }
          : { label: "Saudável", tone: "green" };
      byId("kpi-health").textContent = health.label;
      byId("kpi-health-card").className = `kpi-card health-kpi tone-${health.tone}`;
      const componentRows = [
        ...services.map((service) => ({ label: service.name, ok: service.status === "ok" })),
        { label: "Coleta", ok: collectionFresh },
      ];
      byId("health-components").innerHTML = componentRows.map((item) =>
        `<span class="health-chip ${item.ok ? "is-ok" : "is-fail"}">${escapeHtml(item.label)}</span>`
      ).join("");
    }

    if (syncPayload) {
      const syncStatus = syncPayload.status || {};
      const activeFailures = safeNumber(syncStatus.activeFailureCount ?? syncPayload.activeFailureCount);
      if (syncPayload.error && activeFailures == null) {
        byId("kpi-sync").textContent = "—";
        byId("kpi-sync-note").textContent = "consulta indisponível";
        byId("kpi-sync-card").className = "kpi-card tone-amber";
      } else {
        byId("kpi-sync").textContent = activeFailures ? String(activeFailures) : "OK";
        byId("kpi-sync-note").textContent = activeFailures
          ? `${activeFailures} falha(s) não recuperada(s)`
          : syncPayload.error ? "sem falhas ativas · consulta parcial" : "nenhuma falha ativa · últimas 24h";
        byId("kpi-sync-card").className = `kpi-card ${activeFailures ? "tone-red" : syncPayload.error ? "tone-amber" : "tone-green"}`;
      }
    }
  }

  function renderProductivity(payload) {
    const statuses = {
      completed: { label: "Concluído", color: "#16875b" },
      ok: { label: "Concluído", color: "#16875b" },
      failed: { label: "Falhou", color: "#c33d50" },
      error: { label: "Falhou", color: "#c33d50" },
      running: { label: "Executando", color: "#c77800" },
      stale: { label: "Atrasado", color: "#c33d50" },
      pending: { label: "Aguardando", color: "#65758b" },
    };
    const statusCell = (value) => {
      const state = statuses[value?.status] || statuses.pending;
      return `<span class="system-status" style="--status-color:${state.color}">${state.label}</span>`;
    };
    const cycleLabels = { completed: "ciclo concluído", failed: "ciclo com falha", running: "ciclo em execução", stale: "ciclo atrasado", pending: "aguardando ciclo" };
    const rows = (payload.systems || []).flatMap((system) => {
      const steps = Array.isArray(system.steps) && system.steps.length
        ? system.steps
        : [
            { label: "Produção", ...(system.production || {}) },
            { label: "Monitor", ...(system.monitor || {}) },
          ];
      return steps
        .filter((step) => step && (step.status || step.updatedAt))
        .map((step) => ({ system: system.system, ...step }));
    });
    byId("productivity-cycle").textContent = payload.cycleStartedAt
      ? `${cycleLabels[payload.cycleStatus] || "ciclo atual"} · ${new Date(payload.cycleStartedAt).toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit" })}`
      : "aguardando ciclo";
    byId("productivity-table-body").innerHTML = rows.length ? rows.map((row) => {
      const updatedLabel = row.updatedAt
        ? new Date(row.updatedAt).toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit", second: "2-digit" })
        : "—";
      return `<tr><td><span class="system-name">${escapeHtml(row.system)}</span></td><td>${escapeHtml(row.label)}</td><td>${statusCell(row)}</td><td>${updatedLabel}</td></tr>`;
    }).join("") : `<tr><td colspan="4"><div class="empty-state">${payload.sourceAvailable === false ? "Log de produtividade indisponível." : "Aguardando dados do ciclo atual."}</div></td></tr>`;
  }

  function renderIncidents(payload, syncPayload) {
    const severityColor = { critical: "#c33d50", warn: "#c77800", info: "#1769aa" };
    const syncRows = (syncPayload?.activeFailures || []).map((item) => ({
      severity: "warn", environment: "MAIN", category: "sync",
      title: `Sync falhou (${item.source || "sync"}/${item.kind || ""})`, lastAt: item.finishedAt || item.startedAt,
    }));
    const rows = [...syncRows, ...(payload.groupedEvents || []).filter((item) => item.category !== "sync")]
      .filter((item) => ["critical", "warn"].includes(String(item.severity).toLowerCase()))
      .slice(0, 5);
    byId("incident-badge").textContent = rows.length ? `${rows.length} ativos` : "24h";
    byId("incident-list").innerHTML = rows.length ? rows.map((item) => {
      const severity = String(item.severity || "info").toLowerCase();
      const color = severityColor[severity] || severityColor.info;
      return `<article class="incident-item" style="--severity:${color}">
        <span class="incident-severity"></span>
        <div><strong>${escapeHtml(item.title || "Ocorrência operacional")}</strong>
          <div class="incident-meta"><span class="incident-env">${escapeHtml(item.environment || "OPS")}</span><span>${escapeHtml(item.category || "monitoramento")} · ${relativeTime(item.lastAt)}</span></div>
        </div>
      </article>`;
    }).join("") : '<div class="empty-state">Nenhum alerta ativo nas últimas 24 horas.</div>';
  }

  function renderEndpoints(apiRoutes) {
    const routeMap = new Map();
    [...(apiRoutes?.traffic?.topRoutes || []), ...(apiRoutes?.routeStats || [])].forEach((route) => {
      const key = `${route.method || "GET"} ${route.route || "/"}`;
      const current = routeMap.get(key) || {};
      routeMap.set(key, {
        ...current,
        ...route,
        requests: Math.max(safeNumber(current.requests) || 0, safeNumber(route.requests ?? route.count) || 0),
        errors4xx: Math.max(safeNumber(current.errors4xx) || 0, safeNumber(route.errors4xx ?? route.status4xx) || 0),
        errors5xx: Math.max(safeNumber(current.errors5xx) || 0, safeNumber(route.errors5xx ?? route.status5xx) || 0),
        maxMs: Math.max(safeNumber(current.maxMs) || 0, safeNumber(route.maxMs) || 0),
      });
    });
    const routes = [...routeMap.values()]
      .sort((a, b) => (safeNumber(b.errors5xx) || 0) - (safeNumber(a.errors5xx) || 0)
        || (safeNumber(b.maxMs) || 0) - (safeNumber(a.maxMs) || 0))
      .slice(0, 3);
    const criticalCount = routes.filter((route) => (safeNumber(route.errors5xx) || 0) > 0).length;
    byId("endpoint-badge").textContent = criticalCount ? `${criticalCount} críticos` : "1h";
    byId("endpoint-list").innerHTML = routes.length ? routes.map((route) => {
      const errors5xx = safeNumber(route.errors5xx) || 0;
      const errors4xx = safeNumber(route.errors4xx) || 0;
      const maxMs = safeNumber(route.maxMs) || 0;
      const state = errors5xx > 0
        ? { label: "Crítico", color: "#ff6f7f" }
        : errors4xx > 0 || maxMs >= 2000
          ? { label: "Atenção", color: "#ffc65c" }
          : { label: "OK", color: "#52e39a" };
      return `<article class="endpoint-item">
        <div class="endpoint-route"><code>${escapeHtml(route.method || "GET")} ${escapeHtml(route.route || "/")}</code><small>${formatCount(route.requests)} req · ${errors4xx + errors5xx} erros</small></div>
        <div class="endpoint-state" style="--endpoint-color:${state.color}"><strong>${Math.round(maxMs)} ms</strong><small>${state.label}</small></div>
      </article>`;
    }).join("") : '<div class="empty-state">Nenhum endpoint instrumentado na última hora.</div>';
  }

  function renderHost(host) {
    const disk = (host.disks || []).reduce((worst, row) => safeNumber(row.usedPct) > safeNumber(worst?.usedPct) ? row : worst, null);
    const resources = [
      { label: "CPU", value: safeNumber(host.cpu?.usedPct), note: `${host.cpu?.logicalCores || "—"} processadores lógicos`, color: "#4ed8e6" },
      { label: "Memória RAM", value: safeNumber(host.memory?.usedPct), note: "memória utilizada", color: "#a18cff" },
      { label: "Disco", value: safeNumber(disk?.usedPct), note: disk?.mount || "volume principal", color: "#ffc65c" },
    ];
    byId("host-name").textContent = host.hostname || "servidor";
    byId("resource-grid").innerHTML = resources.map((resource) => {
      const value = resource.value == null ? 0 : Math.max(0, Math.min(100, resource.value));
      return `<div class="resource-item" style="--resource:${resource.color}">
        <div class="resource-head"><span>${resource.label}</span><strong>${resource.value == null ? "—" : `${Math.round(resource.value)}%`}</strong></div>
        <div class="resource-track"><span class="resource-fill" style="width:${value}%"></span></div>
        <small>${escapeHtml(resource.note)}</small>
      </div>`;
    }).join("");
  }

  function chartPath(points, xFor, yFor) {
    return points.map((point, index) => `${index ? "L" : "M"}${xFor(point.t).toFixed(1)},${yFor(point.v).toFixed(1)}`).join(" ");
  }

  function samplePoints(points, maxPoints = 12) {
    if (points.length <= maxPoints) return points;
    return Array.from({ length: maxPoints }, (_, index) => points[Math.round(index * (points.length - 1) / (maxPoints - 1))]);
  }

  function percentile95(values) {
    const sorted = values.filter((value) => value != null).sort((a, b) => a - b);
    return sorted.length ? sorted[Math.max(0, Math.ceil(sorted.length * 0.95) - 1)] : null;
  }

  function latencyBuckets(seriesPayload) {
    const buckets = new Map();
    (seriesPayload?.points || []).forEach((point) => {
      const t = new Date(point.t).getTime();
      const v = safeNumber(point.v);
      if (!Number.isFinite(t) || v == null) return;
      const bucketAt = Math.floor(t / 1_800_000) * 1_800_000;
      if (!buckets.has(bucketAt)) buckets.set(bucketAt, []);
      buckets.get(bucketAt).push(v);
    });
    return [...buckets.entries()].sort((a, b) => a[0] - b[0]).slice(-12)
      .map(([t, values]) => ({ t, v: percentile95(values) })).filter((point) => point.v != null);
  }

  function pointLabels(points, xFor, yFor, formatter, color) {
    return points.map((point, index) => {
      const y = yFor(point.v);
      const offset = index % 2 ? 17 : -9;
      return `<text class="chart-point-label" x="${xFor(point.t)}" y="${Math.max(12, y + offset)}" text-anchor="middle" fill="${color}">${formatter(point.v)}</text>`;
    }).join("");
  }

  function renderTrafficChart(apiRoutes, memorySeries) {
    const trafficPoints = samplePoints(apiRoutes?.traffic?.points || []);
    const series = [
      { key: "requests", label: "Requisições", color: TRAFFIC_COLORS.requests, axis: "traffic", points: trafficPoints.map((point) => ({ t: new Date(point.at).getTime(), v: safeNumber(point.requests) })) },
      { key: "uniqueUsers", label: "Acessos únicos", color: TRAFFIC_COLORS.uniqueUsers, axis: "traffic", points: trafficPoints.map((point) => ({ t: new Date(point.at).getTime(), v: safeNumber(point.uniqueUsers) })) },
      { key: "memory", label: "Memória RAM", color: TRAFFIC_COLORS.memory, axis: "memory", points: samplePoints(memorySeries?.points || []).map((point) => ({ t: new Date(point.t).getTime(), v: safeNumber(point.v) })) },
    ].map((item) => ({ ...item, points: item.points.filter((point) => Number.isFinite(point.t) && point.v != null) }))
      .filter((item) => item.points.length);
    byId("traffic-legend").innerHTML = Object.entries({ requests: "Requisições", uniqueUsers: "Acessos únicos", memory: "RAM (%)" })
      .map(([key, label]) => `<span class="legend-item"><i class="legend-line" style="--legend:${TRAFFIC_COLORS[key]}"></i>${label}</span>`).join("");
    if (!series.length) {
      byId("traffic-chart").innerHTML = '<div class="empty-state">Ainda não há histórico de tráfego para exibir.</div>';
      return;
    }

    const width = 920;
    const height = 260;
    const pad = { top: 24, right: 48, bottom: 30, left: 48 };
    const all = series.flatMap((item) => item.points);
    const minT = Math.min(...all.map((p) => p.t));
    const maxT = Math.max(...all.map((p) => p.t));
    const trafficValues = series.filter((item) => item.axis === "traffic").flatMap((item) => item.points.map((point) => point.v));
    const maxTraffic = Math.max(...trafficValues, 10);
    const trafficStep = maxTraffic <= 100 ? 25 : Math.pow(10, Math.floor(Math.log10(maxTraffic)));
    const roundedMax = Math.ceil(maxTraffic / trafficStep) * trafficStep;
    const xFor = (t) => pad.left + ((t - minT) / Math.max(1, maxT - minT)) * (width - pad.left - pad.right);
    const yForTraffic = (v) => pad.top + (1 - v / roundedMax) * (height - pad.top - pad.bottom);
    const yForMemory = (v) => pad.top + (1 - Math.max(0, Math.min(100, v)) / 100) * (height - pad.top - pad.bottom);
    const grid = Array.from({ length: 5 }, (_, index) => {
      const value = roundedMax * (1 - index / 4);
      const y = yForTraffic(value);
      return `<line class="chart-grid" x1="${pad.left}" y1="${y}" x2="${width - pad.right}" y2="${y}"/><text class="chart-label" x="${pad.left - 9}" y="${y + 3}" text-anchor="end">${formatCount(value)}</text><text class="chart-label" x="${width - pad.right + 9}" y="${y + 3}">${Math.round(100 * (1 - index / 4))}%</text>`;
    }).join("");
    const times = [0, 0.25, 0.5, 0.75, 1].map((ratio) => {
      const t = minT + (maxT - minT) * ratio;
      const x = xFor(t);
      const label = new Date(t).toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit" });
      return `<text class="chart-label" x="${x}" y="${height - 5}" text-anchor="middle">${label}</text>`;
    }).join("");
    const paths = series.map((item) => {
      const yFor = item.axis === "memory" ? yForMemory : yForTraffic;
      const line = chartPath(item.points, xFor, yFor);
      const first = item.points[0];
      const last = item.points[item.points.length - 1];
      const area = `${line} L${xFor(last.t).toFixed(1)},${height - pad.bottom} L${xFor(first.t).toFixed(1)},${height - pad.bottom} Z`;
      const dots = item.points.map((point) => `<circle class="chart-dot" cx="${xFor(point.t)}" cy="${yFor(point.v)}" r="3" fill="${item.color}"/>`).join("");
      const labels = pointLabels(item.points, xFor, yFor, (value) => item.axis === "memory" ? `${Math.round(value)}%` : formatCount(value), item.color);
      return `<path class="chart-area" d="${area}" fill="${item.color}"/><path class="chart-line" d="${line}" stroke="${item.color}"/>${dots}${labels}`;
    }).join("");
    byId("traffic-chart").innerHTML = `<svg viewBox="0 0 ${width} ${height}" preserveAspectRatio="xMidYMid meet" aria-hidden="true">${grid}${paths}${times}</svg>`;
  }

  function renderLatencyChart(seriesPayload, p95Limit) {
    const points = latencyBuckets(seriesPayload);
    byId("latency-legend").innerHTML = `<span class="legend-item"><i class="legend-line" style="--legend:${LATENCY_COLOR}"></i>p95 (ms)</span><span class="legend-item"><i class="legend-dash"></i>SLO ${Math.round(p95Limit)} ms</span>`;
    if (!points.length) {
      byId("health-latency-chart").innerHTML = '<div class="empty-state">Ainda não há histórico de latência para exibir.</div>';
      return points;
    }
    const width = 760;
    const height = 260;
    const pad = { top: 24, right: 24, bottom: 30, left: 52 };
    const minT = points[0].t;
    const maxT = points.at(-1).t;
    const maxV = Math.max(p95Limit, ...points.map((point) => point.v), 10);
    const roundedMax = Math.ceil(maxV / 500) * 500 || 500;
    const xFor = (t) => pad.left + ((t - minT) / Math.max(1, maxT - minT)) * (width - pad.left - pad.right);
    const yFor = (v) => pad.top + (1 - v / roundedMax) * (height - pad.top - pad.bottom);
    const grid = Array.from({ length: 5 }, (_, index) => {
      const value = roundedMax * (1 - index / 4);
      const y = yFor(value);
      return `<line class="chart-grid" x1="${pad.left}" y1="${y}" x2="${width - pad.right}" y2="${y}"/><text class="chart-label" x="${pad.left - 8}" y="${y + 3}" text-anchor="end">${Math.round(value)} ms</text>`;
    }).join("");
    const times = points.map((point, index) => index % 2 === 0 || index === points.length - 1
      ? `<text class="chart-label" x="${xFor(point.t)}" y="${height - 5}" text-anchor="middle">${new Date(point.t).toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit" })}</text>` : "").join("");
    const line = chartPath(points, xFor, yFor);
    const dots = points.map((point) => `<circle class="chart-dot" cx="${xFor(point.t)}" cy="${yFor(point.v)}" r="3.5" fill="${LATENCY_COLOR}"/>`).join("");
    const labels = pointLabels(points, xFor, yFor, (value) => `${Math.round(value)} ms`, LATENCY_COLOR);
    const thresholdY = yFor(Math.min(p95Limit, roundedMax));
    const threshold = `<line class="chart-threshold" x1="${pad.left}" y1="${thresholdY}" x2="${width - pad.right}" y2="${thresholdY}"/>`;
    byId("health-latency-chart").innerHTML = `<svg viewBox="0 0 ${width} ${height}" preserveAspectRatio="xMidYMid meet" aria-hidden="true">${grid}${threshold}<path class="chart-line" d="${line}" stroke="${LATENCY_COLOR}"/>${dots}${labels}${times}</svg>`;
    return points;
  }

  function updateCollector(payload) {
    const status = payload.config?.collectorStatus || {};
    const ok = status.status === "ok";
    const live = document.querySelector(".tv-live-state");
    live.style.color = ok ? "var(--green)" : status.status === "stale" ? "var(--amber)" : "var(--red)";
    byId("collector-label").textContent = status.label || (ok ? "Coleta ativa" : "Coleta indisponível");
    byId("last-update").textContent = `Atualizado ${relativeTime(payload.generatedAt || new Date())} · a cada 30s`;
  }

  async function refresh() {
    if (refreshing || document.hidden) return;
    refreshing = true;
    try {
      const sources = [
        { label: "Resumo MAIN", url: "/api/v1/monitoring/dashboard?envs=MAIN&tab=summary&eventHours=24&limit=100" },
        { label: "Saúde do ambiente", url: "/api/v1/overview-lite" },
        { label: "Capacidade do host", url: "/api/v1/host/summary" },
        { label: "Produtividade H/H", url: "/api/v1/monitoring/productivity-hourly" },
        { label: "Tráfego e usuários", url: "/api/v1/monitoring/MAIN/api-routes?window=1h" },
        { label: "Memória do host", url: "/api/v1/host/series?metric=host_memory_used_pct&hours=1" },
        { label: "Latência do MAIN", url: "/api/v1/monitoring/MAIN/series?metric=health_latency_ms&hours=6" },
        { label: "Sincronizações", url: "/api/v1/monitoring/MAIN/syncs" },
      ];
      const settled = await Promise.allSettled(sources.map((source) => fetchJson(source.url)));
      const values = settled.map((result) => result.status === "fulfilled" ? result.value : null);
      const [payload, overview, host, productivity, apiRoutes, memorySeries, latencySeries, syncPayload] = values;
      if (!payload) throw settled[0].reason || new Error("Resumo do monitoramento indisponível");
      const summaries = summaryMap(payload);
      const p95Limit = safeNumber(payload.config?.slos?.healthP95WarnMs) || 1000;
      if (latencySeries) renderLatencyChart(latencySeries, p95Limit);
      const latencyP95 = latencySeries
        ? percentile95((latencySeries.points || []).map((point) => safeNumber(point.v)))
        : null;
      renderKpis(payload, summaries, overview, syncPayload, apiRoutes, latencyP95);
      if (productivity) renderProductivity(productivity);
      renderIncidents(payload, syncPayload);
      if (apiRoutes) renderEndpoints(apiRoutes);
      if (host) renderHost(host);
      if (apiRoutes || memorySeries) renderTrafficChart(apiRoutes, memorySeries);
      updateCollector(payload);
      const failedSources = sources.filter((_, index) => settled[index].status === "rejected");
      if (failedSources.length) {
        byId("connection-banner").textContent = `Fonte(s) indisponível(is): ${failedSources.map((source) => source.label).join(", ")}. Demais dados preservados.`;
        byId("connection-banner").hidden = false;
      } else {
        byId("connection-banner").hidden = true;
      }
    } catch (error) {
      byId("connection-banner").textContent = error.message || "Não foi possível atualizar o monitoramento";
      byId("connection-banner").hidden = false;
      document.querySelector(".tv-live-state").style.color = "var(--red)";
      byId("collector-label").textContent = "Sem conexão";
    } finally {
      refreshing = false;
    }
  }

  function tickClock() {
    const now = new Date();
    byId("clock").dateTime = now.toISOString();
    byId("clock").textContent = now.toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  }

  function applyTheme(theme, persist = true) {
    const selected = theme === "dark" ? "dark" : "light";
    const dark = selected === "dark";
    document.documentElement.dataset.theme = selected;
    byId("theme-toggle").setAttribute("aria-pressed", String(dark));
    byId("theme-toggle").setAttribute("aria-label", dark ? "Ativar modo claro" : "Ativar modo escuro");
    byId("theme-toggle-icon").textContent = dark ? "☀" : "☾";
    byId("theme-toggle-label").textContent = dark ? "Claro" : "Escuro";
    if (persist) {
      try { window.localStorage.setItem(THEME_STORAGE_KEY, selected); } catch (_) { /* armazenamento pode estar bloqueado */ }
    }
  }

  function setupThemeToggle() {
    applyTheme(document.documentElement.dataset.theme, false);
    byId("theme-toggle").addEventListener("click", () => {
      applyTheme(document.documentElement.dataset.theme === "dark" ? "light" : "dark");
    });
  }

  setupThemeToggle();
  tickClock();
  window.setInterval(tickClock, 1000);
  window.setInterval(refresh, REFRESH_MS);
  document.addEventListener("visibilitychange", () => { if (!document.hidden) refresh(); });
  refresh();
}());
