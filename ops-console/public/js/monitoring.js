/* global window, document */
(function () {
  window.OpsConsole = window.OpsConsole || {};
  const OC = window.OpsConsole;

  const STORAGE_ENVS = "pplid-monitor-envs";
  const STORAGE_CATS = "pplid-monitor-categories";
  const STORAGE_FILTERS = "pplid-monitor-filters";
  const MONITOR_REFRESH_MS = 30000;
  const MONITOR_APIS_REFRESH_MS = 15000;
  const MONITOR_LOGS_REFRESH_MS = 60000;
  const STATUS_FOCUS_ENVS = ["MAIN", "HOM"];
  const STORAGE_API_WINDOW = "pplid-monitor-api-window";
  const STORAGE_LATENCY_WINDOW = "pplid-monitor-latency-window";
  const STORAGE_LOGS_PATTERN = "pplid-monitor-logs-pattern";

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
    healthP95WarnMs: 2000,
    healthP95CriticalMs: 3000,
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
    apiWindow: "6h",
    latencyWindow: "24h",
    logsPattern: "",
    dayDrill: null,
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
    try {
      const win = localStorage.getItem(STORAGE_API_WINDOW);
      if (win && ["1h", "6h", "24h"].includes(win)) OC.monitorState.apiWindow = win;
    } catch {
      /* ignore */
    }
    try {
      const latWin = localStorage.getItem(STORAGE_LATENCY_WINDOW);
      if (latWin && ["1h", "6h", "24h", "7d"].includes(latWin)) {
        OC.monitorState.latencyWindow = latWin;
      }
    } catch {
      /* ignore */
    }
    try {
      const pat = localStorage.getItem(STORAGE_LOGS_PATTERN);
      if (pat != null && ["", "ERROR", "WARN", "Traceback"].includes(pat)) {
        OC.monitorState.logsPattern = pat;
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
    localStorage.setItem(STORAGE_API_WINDOW, OC.monitorState.apiWindow || "6h");
    localStorage.setItem(STORAGE_LATENCY_WINDOW, OC.monitorState.latencyWindow || "24h");
    localStorage.setItem(STORAGE_LOGS_PATTERN, OC.monitorState.logsPattern ?? "");
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
    // Latência: amarelo (warn). Vermelho fica só para offline (computeEnvOverview).
    if (sloMetric >= slos.healthP95WarnMs) {
      level = "warn";
      label = sloMetric >= slos.healthP95CriticalMs ? "Muito lenta" : "Acima do esperado";
    }

    const delta = pctChange(latest, avg);
    return {
      level,
      label,
      value: `${formatLatencyMs(latest)} ms`,
      latest: `${formatLatencyMs(latest)} ms`,
      p95: p95 != null ? `${formatLatencyMs(p95)} ms` : "—",
      max: max != null ? `${formatLatencyMs(max)} ms` : "—",
      slo: `p95 <= ${slos.healthP95WarnMs} ms`,
      sub: avg != null ? `média 24h ${formatLatencyMs(avg)} ms` : "",
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

  function evalCurrentLatency(summary, slos, dataFresh) {
    const latest = summary?.health?.latest;
    if (!dataFresh) return { level: "unknown", label: null };
    if (latest == null) return { level: "unknown", label: null };
    let level = "ok";
    if (latest >= slos.healthP95CriticalMs) level = "critical";
    else if (latest >= slos.healthP95WarnMs) level = "warn";
    return { level, label: `${formatLatencyMs(latest)} ms` };
  }

  function isDeployRunFailed(run) {
    if (!run) return false;
    const status = String(run.result || run.status || "").toLowerCase();
    return status === "failed" || status === "error" || status === "failure";
  }

  function runTimestamp(run) {
    return String(run?.finished_at || run?.started_at || "");
  }

  function isDeployCurrentlyBroken(aggregates, deployData) {
    const runs = deployData?.runs || [];
    const latestRun = runs[0];
    if (latestRun && isDeployRunFailed(latestRun)) return true;
    const lastFailed = aggregates?.lastFailed;
    if (!lastFailed) return false;
    const lastSuccess = aggregates?.lastSuccess;
    if (!lastSuccess) return true;
    return runTimestamp(lastFailed) > runTimestamp(lastSuccess);
  }

  function computeEnvOverview(env, summary, deployData, slos) {
    const dataFresh = summary?.dataFresh !== false;
    const health = evalHealthMetric(summary, slos, dataFresh);
    const api = evalApi5xxMetric(summary);
    const sync = evalSyncMetric(summary, slos);
    const aggregates = deployData?.aggregates24h;
    const deploy = evalDeployMetric(aggregates, slos);
    const currentLatency = evalCurrentLatency(summary, slos, dataFresh);
    const deployBrokenNow = isDeployCurrentlyBroken(aggregates, deployData);
    const offlineNow = dataFresh && summary?.latestReachable === false;

    const activeReasons = [];
    let activeOverall = "ok";
    if (!dataFresh) {
      activeOverall = "unknown";
      activeReasons.push("coleta atrasada");
    } else if (offlineNow) {
      activeOverall = "critical";
      activeReasons.push("backend offline");
    } else {
      if (currentLatency.level === "critical" || currentLatency.level === "warn") {
        activeOverall = worstLevel(activeOverall, currentLatency.level);
        activeReasons.push(`lentidão atual ${currentLatency.label}`);
      }
      if (deployBrokenNow) {
        activeOverall = worstLevel(activeOverall, "critical");
        const step = aggregates?.lastFailed?.failed_step;
        activeReasons.push(step ? `deploy quebrado (${step})` : "deploy quebrado");
      }
    }

    const occurredReasons = [];
    let occurredOverall = "ok";
    if (api.level === "warn" || api.level === "critical") {
      occurredOverall = worstLevel(occurredOverall, api.level);
      occurredReasons.push(api.label);
    }
    if (sync.level === "warn") {
      occurredOverall = worstLevel(occurredOverall, "warn");
      occurredReasons.push(`${sync.value} falha(s) de sync`);
    }
    if (deploy.level === "warn" || deploy.level === "critical") {
      occurredOverall = worstLevel(occurredOverall, deploy.level);
      if (!deployBrokenNow) {
        occurredReasons.push(deploy.label);
      } else if ((aggregates?.failed24h || 0) > 1) {
        occurredReasons.push(`${aggregates.failed24h} falha(s) de deploy`);
      }
    }
    if (currentLatency.level === "ok" && (health.level === "warn" || health.level === "critical")) {
      occurredOverall = worstLevel(occurredOverall, health.level);
      occurredReasons.push(`p95 24h ${health.p95 || health.value}`);
    }
    if (occurredOverall === "info") occurredOverall = "ok";
    if (activeOverall === "info") activeOverall = "ok";

    const activeLabel = STATUS_LABELS[activeOverall] || activeOverall;
    const occurredLabel = STATUS_LABELS[occurredOverall] || occurredOverall;

    return {
      env,
      overall: activeOverall,
      label: activeLabel,
      reasons: activeReasons,
      activeOverall,
      activeLabel,
      activeReasons,
      occurredOverall,
      occurredLabel,
      occurredReasons,
      health,
      api,
      sync,
      deploy,
      dataFresh,
      summary,
    };
  }

  function seriesAvg(points) {
    const vals = (points || []).map((p) => Number(p.v)).filter((v) => !Number.isNaN(v));
    if (!vals.length) return null;
    return vals.reduce((a, b) => a + b, 0) / vals.length;
  }

  /** Format ms with decimals when values are sub-1 / low — Math.round(0.1) === 0 was hiding real latency. */
  function formatLatencyMs(value) {
    const n = Number(value);
    if (!Number.isFinite(n)) return "—";
    const abs = Math.abs(n);
    if (abs === 0) return "0";
    if (abs < 1) return (Math.round(n * 100) / 100).toFixed(2);
    if (abs < 10) {
      const one = Math.round(n * 10) / 10;
      return Number.isInteger(one) ? String(one) : one.toFixed(1);
    }
    return String(Math.round(n));
  }

  /** Scale Y to observed data; don't stretch the chart to a far-away SLO (e.g. 0.1ms vs 2000ms). */
  function chartValueDomain(values, sloMs) {
    const nums = (values || []).filter((v) => Number.isFinite(v));
    const dataMax = nums.length ? Math.max(...nums) : 0;
    const dataMin = nums.length ? Math.min(...nums, 0) : 0;
    let vMax = Math.max(dataMax * 1.25, dataMax + (dataMax < 5 ? 1 : dataMax * 0.1), 1);
    const vMin = Math.min(0, dataMin);
    let sloInScale = false;
    if (sloMs != null && Number.isFinite(sloMs) && sloMs > 0) {
      if (dataMax <= 0 || sloMs <= Math.max(vMax * 3, 50)) {
        vMax = Math.max(vMax, sloMs);
        sloInScale = true;
      }
    }
    return { vMin, vMax, sloInScale };
  }

  function formatChartAxisLabel(tMs, tSpanMs, position) {
    const d = new Date(tMs);
    const tz = OC.DISPLAY_TIMEZONE || "America/Sao_Paulo";
    const hours = tSpanMs / 3600000;
    if (hours <= 6) {
      return d.toLocaleTimeString("pt-BR", { timeZone: tz, hour: "2-digit", minute: "2-digit" });
    }
    if (hours <= 48) {
      if (position === "mid") {
        return d.toLocaleTimeString("pt-BR", { timeZone: tz, hour: "2-digit", minute: "2-digit" });
      }
      return `${d.toLocaleDateString("pt-BR", { timeZone: tz, day: "2-digit", month: "2-digit" })} ${d.toLocaleTimeString("pt-BR", { timeZone: tz, hour: "2-digit", minute: "2-digit" })}`;
    }
    return d.toLocaleDateString("pt-BR", { timeZone: tz, day: "2-digit", month: "2-digit" });
  }

  function chartPointsFingerprint(seriesByEnv) {
    const parts = [];
    Object.keys(seriesByEnv || {})
      .sort()
      .forEach((env) => {
        const pts = seriesByEnv[env]?.points || [];
        const last = pts[pts.length - 1];
        parts.push(`${env}:${pts.length}:${last?.t || ""}:${last?.v ?? ""}`);
      });
    return parts.join("|");
  }

  function buildSvgLineChart(seriesByEnv, metricLabel, sloMs, options = {}) {
    const width = 720;
    const height = 200;
    const pad = { top: 16, right: 16, bottom: 36, left: 48 };
    const innerW = width - pad.left - pad.right;
    const innerH = height - pad.top - pad.bottom;
    const chartId = options.chartId || `chart-${Math.random().toString(36).slice(2, 9)}`;
    const windowHours = options.windowHours;
    const fingerprint = `${chartId}|${metricLabel}|${sloMs ?? ""}|${windowHours ?? ""}|${chartPointsFingerprint(seriesByEnv)}`;
    OC._chartSvgCache = OC._chartSvgCache || {};
    if (OC._chartSvgCache[chartId]?.fp === fingerprint) {
      return OC._chartSvgCache[chartId].built;
    }

    const allPoints = [];
    let windowFrom = null;
    let windowTo = null;
    let lastSampleAt = null;
    Object.values(seriesByEnv).forEach((s) => {
      (s.points || []).forEach((p) => allPoints.push(p));
      if (s.windowFrom && (!windowFrom || s.windowFrom < windowFrom)) windowFrom = s.windowFrom;
      if (s.windowTo && (!windowTo || s.windowTo > windowTo)) windowTo = s.windowTo;
      if (s.since && (!windowFrom || s.since < windowFrom)) windowFrom = s.since;
      if (s.lastSampleAt && (!lastSampleAt || s.lastSampleAt > lastSampleAt)) lastSampleAt = s.lastSampleAt;
    });
    if (!allPoints.length && !windowFrom) {
      const empty = { html: `<p class="monitor-empty monitor-empty-neutral">Sem dados de ${OC.escapeHtml(metricLabel)} no período.</p>`, spikes: [], chartId };
      OC._chartSvgCache[chartId] = { fp: fingerprint, built: empty };
      return empty;
    }

    const times = allPoints
      .map((p) => (OC.parseDate ? OC.parseDate(p.t)?.getTime() : new Date(p.t).getTime()))
      .filter((t) => t != null && !Number.isNaN(t));
    const values = allPoints.length
      ? allPoints.map((p) => Number(p.v)).filter((v) => !Number.isNaN(v))
      : [0];
    let tMin = windowFrom
      ? (OC.parseDate ? OC.parseDate(windowFrom)?.getTime() : new Date(windowFrom).getTime())
      : Math.min(...times);
    let tMax = windowTo
      ? (OC.parseDate ? OC.parseDate(windowTo)?.getTime() : new Date(windowTo).getTime())
      : Math.max(...(times.length ? times : [Date.now()]));
    if (Number.isNaN(tMin)) tMin = Math.min(...times);
    if (Number.isNaN(tMax)) tMax = Math.max(...(times.length ? times : [Date.now()]));
    if (windowHours && Number.isFinite(windowHours) && (!windowFrom || !windowTo)) {
      tMax = Date.now();
      tMin = tMax - windowHours * 3600000;
    }
    const { vMin, vMax, sloInScale } = chartValueDomain(values, sloMs);
    const tSpan = tMax - tMin || 1;
    const vSpan = vMax - vMin || 1;

    const x = (t) => pad.left + ((t - tMin) / tSpan) * innerW;
    const y = (v) => pad.top + innerH - ((v - vMin) / vSpan) * innerH;

    let paths = "";
    let dots = "";
    let legend = "";
    const summaries = [];
    const spikes = [];
    const hoverPts = [];

    Object.entries(seriesByEnv).forEach(([env, series]) => {
      const pts = (series.points || [])
        .map((p) => {
          const parsed = OC.parseDate ? OC.parseDate(p.t) : new Date(p.t);
          const t = parsed?.getTime?.() ?? NaN;
          return {
            t,
            v: Number(p.v),
            iso: p.t,
            labels: p.labels || {},
          };
        })
        .filter((p) => !Number.isNaN(p.t) && !Number.isNaN(p.v))
        .sort((a, b) => a.t - b.t);
      if (!pts.length) return;
      const last = pts[pts.length - 1];
      const avg = seriesAvg(pts);
      summaries.push({ env, last: last.v, avg, lastAt: series.lastSampleAt || last.iso });
      const d = pts.map((p, i) => `${i === 0 ? "M" : "L"}${x(p.t).toFixed(1)},${y(p.v).toFixed(1)}`).join(" ");
      const color = ENV_COLORS[env] || "#666";
      paths += `<path d="${d}" fill="none" stroke="${color}" stroke-width="2" />`;
      pts.forEach((p) => {
        hoverPts.push({
          env,
          t: p.t,
          v: p.v,
          iso: p.iso || new Date(p.t).toISOString(),
          avg,
          x: x(p.t),
          y: y(p.v),
          labels: p.labels || {},
        });
        if (sloMs && p.v >= sloMs) {
          spikes.push({
            env,
            t: p.t,
            iso: p.iso || new Date(p.t).toISOString(),
            v: p.v,
            color,
          });
        }
      });
      legend += `<span class="monitor-legend-item"><span class="monitor-legend-swatch" style="background:${color}"></span>${env} · atual ${formatLatencyMs(last.v)} ms</span>`;
    });

    spikes.sort((a, b) => b.v - a.v);
    spikes.slice(0, 8).forEach((sp) => {
      dots += `<circle class="monitor-spike-dot" cx="${x(sp.t).toFixed(1)}" cy="${y(sp.v).toFixed(1)}" r="4" fill="${sp.color}" data-env="${OC.escapeHtml(sp.env)}" data-iso="${OC.escapeHtml(sp.iso)}" data-ms="${formatLatencyMs(sp.v)}" />`;
    });

    const gridY = [0, 0.5, 1].map((f) => {
      const val = vMin + vSpan * f;
      const yy = y(val);
      return `<line x1="${pad.left}" y1="${yy}" x2="${width - pad.right}" y2="${yy}" class="monitor-grid-line" />
        <text x="${pad.left - 6}" y="${yy + 4}" text-anchor="end" class="monitor-axis-label">${formatLatencyMs(val)}</text>`;
    });

    const positions =
      tSpan <= 2 * 3600000
        ? [
            { f: 0, pos: "start" },
            { f: 0.25, pos: "mid" },
            { f: 0.5, pos: "mid" },
            { f: 0.75, pos: "mid" },
            { f: 1, pos: "end" },
          ]
        : [
            { f: 0, pos: "start" },
            { f: 0.5, pos: "mid" },
            { f: 1, pos: "end" },
          ];
    const xLabels = positions.map(({ f, pos }) => {
      const t = tMin + tSpan * f;
      const label = formatChartAxisLabel(t, tSpan, pos);
      return `<text x="${x(t).toFixed(1)}" y="${height - 8}" text-anchor="middle" class="monitor-axis-label">${OC.escapeHtml(label)}</text>`;
    });

    let sloLine = "";
    if (sloMs != null && Number.isFinite(sloMs)) {
      if (sloInScale) {
        sloLine = `<line x1="${pad.left}" y1="${y(sloMs)}" x2="${width - pad.right}" y2="${y(sloMs)}" class="monitor-slo-line" />
           <text x="${width - pad.right - 4}" y="${y(sloMs) - 4}" text-anchor="end" class="monitor-slo-label">SLO ${formatLatencyMs(sloMs)} ms</text>`;
      } else {
        sloLine = `<text x="${width - pad.right - 4}" y="${pad.top + 12}" text-anchor="end" class="monitor-slo-label">SLO ${formatLatencyMs(sloMs)} ms (fora da escala)</text>`;
      }
    }

    const worst = summaries.sort((a, b) => b.last - a.last)[0];
    const trendNote = worst
      ? `${worst.env} em ${formatLatencyMs(worst.last)} ms no período (média ${formatLatencyMs(worst.avg || 0)} ms).`
      : "Sem amostras no período solicitado.";
    const freshnessNote = lastSampleAt
      ? `Última amostra: ${OC.formatDate(lastSampleAt)}`
      : "Nenhuma amostra recente — coleta pode estar parada.";

    if (!allPoints.length) {
      const emptyPts = {
        html: `<p class="monitor-chart-summary">${OC.escapeHtml(freshnessNote)}</p>
        <p class="monitor-empty monitor-empty-neutral">Sem pontos de ${OC.escapeHtml(metricLabel)} no intervalo.</p>`,
        spikes: [],
        chartId,
      };
      OC._chartSvgCache[chartId] = { fp: fingerprint, built: emptyPts };
      return emptyPts;
    }

    OC._chartHoverData = OC._chartHoverData || {};
    OC._chartHoverData[chartId] = hoverPts;

    const html = `<p class="monitor-chart-summary">${OC.escapeHtml(trendNote)} · ${OC.escapeHtml(freshnessNote)}</p>
    <div class="monitor-chart-wrap" data-chart-id="${OC.escapeHtml(chartId)}" data-slo="${sloMs != null ? sloMs : ""}">
      <svg class="monitor-chart" viewBox="0 0 ${width} ${height}" role="img" aria-label="${OC.escapeHtml(metricLabel)}" data-chart-svg="${OC.escapeHtml(chartId)}">
        ${gridY.join("")}
        ${sloLine}
        ${paths}
        ${dots}
        ${xLabels.join("")}
        <line class="monitor-chart-cursor hidden" x1="${pad.left}" y1="${pad.top}" x2="${pad.left}" y2="${height - pad.bottom}" />
        <rect class="monitor-chart-hit" x="${pad.left}" y="${pad.top}" width="${innerW}" height="${innerH}" fill="transparent" data-chart-hit="${OC.escapeHtml(chartId)}" />
      </svg>
      <div class="monitor-chart-tooltip hidden" data-chart-tooltip="${OC.escapeHtml(chartId)}"></div>
      <div class="monitor-legend">${legend}</div>
    </div>`;

    const built = { html, spikes, chartId, hoverPts };
    OC._chartSvgCache[chartId] = { fp: fingerprint, built };
    return built;
  }

  function chartHtml(seriesByEnv, metricLabel, sloMs, options) {
    const built = buildSvgLineChart(seriesByEnv, metricLabel, sloMs, options);
    return typeof built === "string" ? built : built.html;
  }

  function latencyWindowHours(window) {
    if (window === "1h") return 1;
    if (window === "6h") return 6;
    if (window === "7d") return 168;
    return 24;
  }

  function latencyWindowLabel(window) {
    if (window === "1h") return "1 hora";
    if (window === "6h") return "6 horas";
    if (window === "7d") return "7 dias";
    return "24 horas";
  }

  function renderLatencyToolbar() {
    const win = OC.monitorState.latencyWindow || "24h";
    return OC.renderOpsChipToolbar({
      id: "latency-window",
      label: "Janela",
      attr: "data-monitor-chip",
      value: win,
      options: [
        { value: "1h", label: "1h" },
        { value: "6h", label: "6h" },
        { value: "24h", label: "24h" },
        { value: "7d", label: "7d" },
      ],
      extra: `<span class="monitor-meta-muted">Tendência · status diário permanece em 7 dias</span>`,
    });
  }

  function buildEnvLatencyCharts(seriesByEnv, sloMs) {
    const hours = latencyWindowHours(OC.monitorState.latencyWindow || "24h");
    const envs = STATUS_FOCUS_ENVS.filter((e) => seriesByEnv[e]).concat(
      Object.keys(seriesByEnv).filter((e) => !STATUS_FOCUS_ENVS.includes(e))
    );
    if (!envs.length) {
      return `<p class="monitor-empty monitor-empty-neutral">Sem séries de latência.</p>`;
    }
    return `<div class="monitor-latency-env-grid">${envs
      .map((env) => {
        const single = { [env]: seriesByEnv[env] };
        return `<article class="monitor-latency-env-card">
          <h4 class="monitor-latency-env-title">${OC.escapeHtml(env)}</h4>
          ${chartHtml(single, `latência ${env}`, sloMs, { windowHours: hours, chartId: `lat-${env}` })}
        </article>`;
      })
      .join("")}</div>`;
  }

  function renderUptimeStatusBars(uptimeByEnv, { compact = false, drillable = false } = {}) {
    const envs = STATUS_FOCUS_ENVS;
    const drill = OC.monitorState.dayDrill;
    const cards = envs
      .map((env) => {
        const data = uptimeByEnv?.[env];
        if (!data || data.error) {
          return `<article class="monitor-status-card">
            <header class="monitor-status-card-head"><span>${OC.escapeHtml(env)}</span><span class="monitor-status-pill monitor-status-none">Sem dados</span></header>
            <p class="monitor-empty monitor-empty-neutral">${data?.error ? OC.escapeHtml(data.error) : "Coleta indisponível"}</p>
          </article>`;
        }
        const bars = (data.dayBars || [])
          .map((d) => {
            const title = `${d.date}: ${d.uptimePct != null ? d.uptimePct + "% uptime" : "sem amostras"}${d.p95Ms != null ? ` · p95 ${d.p95Ms}ms` : ""}${d.incidentCount ? ` · ${d.incidentCount} incidente(s)` : ""}`;
            const isActive = drill?.env === env && drill?.date === d.date;
            const clickable = drillable
              ? `role="button" tabindex="0" data-day-env="${OC.escapeHtml(env)}" data-day-date="${OC.escapeHtml(d.date)}" class="monitor-day-bar monitor-day-${OC.escapeHtml(d.status || "none")}${isActive ? " is-active" : ""}"`
              : `class="monitor-day-bar monitor-day-${OC.escapeHtml(d.status || "none")}"`;
            return `<span ${clickable} title="${OC.escapeHtml(title)}"></span>`;
          })
          .join("");
        const uptime =
          data.uptimePct != null ? `${data.uptimePct}% uptime` : "—";
        const label =
          data.uptimePct == null
            ? "Sem dados"
            : data.uptimePct >= 99.9
              ? "Normal"
              : data.uptimePct >= 99
                ? "Degradado"
                : "Interrupções";
        return `<article class="monitor-status-card ${compact ? "is-compact" : ""}">
          <header class="monitor-status-card-head">
            <span class="monitor-status-card-env">${OC.escapeHtml(env)}</span>
            <span class="monitor-status-pill monitor-status-${data.uptimePct == null ? "none" : data.uptimePct >= 99.9 ? "ok" : data.uptimePct >= 99 ? "degraded" : "major"}">${OC.escapeHtml(label)}</span>
          </header>
          <div class="monitor-uptime-headline">
            <span class="monitor-uptime-pct">${OC.escapeHtml(uptime)}</span>
            <span class="monitor-uptime-trend">${(data.days || 7)} dias · foco operacional</span>
          </div>
          <div class="monitor-day-bars" aria-label="Histórico ${OC.escapeHtml(env)}">${bars}</div>
          <div class="monitor-day-bars-meta">
            <span>${(data.days || 7)} dias atrás</span>
            <span>Hoje</span>
          </div>
        </article>`;
      })
      .join("");
    const hint = drillable
      ? `<p class="monitor-section-hint">Clique em um dia para ver o detalhe por hora.</p>`
      : "";
    return `${hint}<div class="monitor-status-grid monitor-uptime-panel">${cards}</div>`;
  }

  function renderDayHourDrill(drill) {
    if (!drill) return "";
    const { env, date, loading, error, data, hourContext } = drill;
    const head = `<div class="monitor-day-drill-head">
      <h4 class="monitor-drawer-subtitle">Detalhe do dia ${OC.escapeHtml(date)} · ${OC.escapeHtml(env)}</h4>
      <button type="button" class="btn btn-ghost btn-sm" id="monitor-day-drill-close">Fechar</button>
    </div>`;
    if (loading) {
      return `<section class="monitor-section monitor-day-drill" data-day-drill>
        ${head}
        <div class="loading-inline"><div class="loading-spinner loading-spinner-sm"></div> Carregando horas…</div>
      </section>`;
    }
    if (error) {
      return `<section class="monitor-section monitor-day-drill" data-day-drill>
        ${head}
        <p class="monitor-empty monitor-empty-neutral">${OC.escapeHtml(error)}</p>
      </section>`;
    }
    const sloMs = data?.sloWarnMs ?? 2000;
    const hourBars = (data?.hourBars || [])
      .map((h) => {
        const tip = `${h.label}: ${h.avgMs != null ? `avg ${h.avgMs}ms` : "sem dados"}${h.p95Ms != null ? ` · p95 ${h.p95Ms}ms` : ""}${h.maxMs != null ? ` · máx ${h.maxMs}ms` : ""}${h.uptimePct != null ? ` · uptime ${h.uptimePct}%` : ""}${h.incidentCount ? ` · ${h.incidentCount} evento(s)` : ""}${h.eventTitles?.length ? ` — ${h.eventTitles.slice(0, 2).join("; ")}` : ""}`;
        const active = hourContext?.hour === h.hour ? " is-active" : "";
        return `<button type="button" class="monitor-hour-bar monitor-day-${OC.escapeHtml(h.status || "none")}${active}"
          data-hour-env="${OC.escapeHtml(env)}" data-hour="${h.hour}" data-hour-iso="${OC.escapeHtml(h.iso || "")}"
          title="${OC.escapeHtml(tip)}" aria-label="${OC.escapeHtml(tip)}">
          <span class="monitor-hour-bar-label">${String(h.hour).padStart(2, "0")}</span>
        </button>`;
      })
      .join("");
    const series = data?.series ? { [env]: data.series } : {};
    const chart = series[env]
      ? chartHtml(series, `latência horária ${env}`, sloMs, { windowHours: 24, chartId: `day-${env}-${date}` })
      : `<p class="monitor-empty monitor-empty-neutral">Sem pontos de latência neste dia.</p>`;
    let contextHtml = "";
    if (hourContext) {
      if (hourContext.loading) {
        contextHtml = `<div class="monitor-spike-context" data-hour-context>
          <div class="loading-inline"><div class="loading-spinner loading-spinner-sm"></div> Buscando contexto…</div>
        </div>`;
      } else {
        const evList = (hourContext.events || [])
          .map(
            (e) =>
              `<li><button type="button" class="btn btn-ghost btn-sm monitor-open-event" data-event-id="${e.id}" data-event-env="${OC.escapeHtml(e.environment || env)}">${OC.escapeHtml(OC.formatDate(e.recorded_at))} · ${OC.escapeHtml(e.title || "")}</button></li>`
          )
          .join("");
        const titles = (hourContext.bar?.eventTitles || []).join("; ");
        contextHtml = `<div class="monitor-spike-context" data-hour-context>
          <div class="monitor-spike-context-head">
            <strong>${OC.escapeHtml(env)} · ${OC.escapeHtml(hourContext.bar?.label || String(hourContext.hour))}</strong>
            <span class="monitor-meta-muted">SLO ${sloMs} ms · avg ${hourContext.bar?.avgMs ?? "—"} · p95 ${hourContext.bar?.p95Ms ?? "—"} · máx ${hourContext.bar?.maxMs ?? "—"}</span>
          </div>
          ${titles ? `<p class="monitor-section-hint">Causas amostradas: ${OC.escapeHtml(titles)}</p>` : ""}
          ${evList ? `<ul class="monitor-spike-list">${evList}</ul>` : `<p class="monitor-empty monitor-empty-neutral">Nenhum evento ±30 min desta hora.</p>`}
          <div class="monitor-spike-context-actions">
            <button type="button" class="btn btn-ghost btn-sm" data-hour-logs-env="${OC.escapeHtml(env)}" data-hour-logs-since="${OC.escapeHtml(hourContext.iso || "")}">Ver logs</button>
          </div>
        </div>`;
      }
    }
    return `<section class="monitor-section monitor-day-drill" data-day-drill>
      ${head}
      <div class="monitor-hour-bars" aria-label="Horas do dia">${hourBars}</div>
      ${chart}
      ${contextHtml}
    </section>`;
  }

  function svgIcon(kind) {
    return OC.opsSvgIcon ? OC.opsSvgIcon(kind) : "";
  }

  function renderMonitorHero(kpis, opts) {
    const compact = opts?.compact === true;
    const tabLabel = opts?.tabLabel || "Resumo";
    const uptimeLabel = kpis.avgUptime != null ? `${kpis.avgUptime}%` : "—";
    const collectLabel = kpis.lastSampleAt
      ? OC.formatRelativeTime(kpis.lastSampleAt)
      : kpis.collectorLabel || "—";
    return OC.renderOpsHero({
      title: `Monitoramento · ${tabLabel}`,
      subtitle: compact
        ? "Indicadores operacionais do período selecionado."
        : "Visão consolidada da saúde dos ambientes e serviços.",
      compact,
      back: true,
      stats: [
        { label: "Disponibilidade 7d", value: uptimeLabel },
        { label: "Alertas ativos", value: String(kpis.alertCount ?? 0), action: "alerts" },
        { label: "Problemas 24h", value: String(kpis.occurredCount ?? 0), action: "alerts" },
        { label: "Última coleta", value: collectLabel },
      ],
    });
  }

  function renderKpiRow(kpis) {
    const deployRel = kpis.lastDeployAt ? OC.formatRelativeTime(kpis.lastDeployAt) : "Sem registros";
    const alertTone = kpis.alertCount > 0 ? "warn" : "ok";
    const occurredTone = kpis.occurredCount > 0 ? "warn" : "ok";
    return OC.renderOpsKpiRow([
      {
        label: "Ambientes saudáveis",
        value: `${kpis.healthy} de ${kpis.total}`,
        hint: "Sem impacto ativo agora",
        tone: "ok",
        icon: "check",
      },
      {
        label: "Alertas ativos",
        value: String(kpis.alertCount),
        hint: "Impacto agora · offline/lentidão/deploy",
        tone: alertTone,
        icon: "alert",
        action: "alerts",
      },
      {
        label: "Problemas ocorridos",
        value: String(kpis.occurredCount ?? 0),
        hint: "Warn/critical nas últimas 24h",
        tone: occurredTone,
        icon: "pulse",
        action: "alerts",
      },
      {
        label: "Último deploy",
        value: deployRel,
        hint: "Entre os ambientes selecionados",
        tone: "deploy",
        icon: "rocket",
      },
    ]);
  }
  function computeExecutiveKpis(overviews, groupedEvents, uptimeByEnv, deploys, config) {
    const total = overviews.length || OC.ENV_ORDER.length;
    const healthy = overviews.filter((o) => o.activeOverall === "ok").length;
    // Ativos: preferir sinais ao vivo do overview; fallback a grupos com offlineOngoing
    const liveFromOverviews = overviews.reduce(
      (n, o) => n + ((o.activeReasons && o.activeReasons.length) || 0),
      0
    );
    const alertSource =
      Array.isArray(OC.lastAlertGroups) && OC.lastAlertGroups.length
        ? OC.lastAlertGroups
        : groupedEvents || [];
    const alertCountFromGroups = OC.countActiveAlerts
      ? OC.countActiveAlerts(alertSource)
      : alertSource.filter((g) => g.offlineOngoing === true).length;
    const alertCount = Math.max(liveFromOverviews, alertCountFromGroups);
    const occurredCount = OC.countOccurredProblems
      ? OC.countOccurredProblems(alertSource)
      : alertSource.filter((g) => {
          const sev = String(g.severity || "").toLowerCase();
          return (sev === "critical" || sev === "warn") && g.offlineOngoing !== true;
        }).length;

    let lastDeployAt = null;
    Object.values(deploys || {}).forEach((d) => {
      (d.runs || []).forEach((run) => {
        const t = run.finished_at || run.started_at;
        if (t && (!lastDeployAt || String(t) > String(lastDeployAt))) lastDeployAt = t;
      });
    });
    const collector = config?.collectorStatus || {};
    const uptimes = STATUS_FOCUS_ENVS.map((env) => uptimeByEnv?.[env]?.uptimePct).filter(
      (v) => v != null && !Number.isNaN(Number(v))
    );
    const avgUptime = uptimes.length
      ? Math.round((uptimes.reduce((a, b) => a + Number(b), 0) / uptimes.length) * 100) / 100
      : null;
    return {
      healthy,
      total,
      alertCount,
      occurredCount,
      avgUptime,
      lastDeployAt,
      lastSampleAt: collector.lastSampleAt || null,
      collectorLabel: collector.label || "—",
    };
  }

  function renderGlobalStatusBanner(overviews, config) {
    const collector = config?.collectorStatus || {};
    const sorted = [...overviews].sort(
      (a, b) => SEV_ORDER[a.activeOverall] - SEV_ORDER[b.activeOverall]
    );
    const worst = sorted[0];
    let level = "ok";
    let text = "Todos os sistemas operacionais";
    if (collector.status === "stale" || collector.status === "no_data") {
      level = "warn";
      text = `Coleta ${collector.label || "atrasada"} — status pode estar desatualizado`;
    } else if (worst?.activeOverall === "critical") {
      level = "critical";
      text = `Incidente ativo em ${worst.env}: ${worst.activeReasons.join(", ") || worst.activeLabel}`;
    } else if (worst?.activeOverall === "warn") {
      level = "warn";
      text = `Degradação em ${worst.env}: ${worst.activeReasons.join(", ") || worst.activeLabel}`;
    } else if (worst?.activeOverall === "unknown") {
      level = "warn";
      text = "Sem dados recentes em um ou mais ambientes";
    }
    return `<div class="monitor-global-banner monitor-global-${level}" role="status">
      <strong>${OC.escapeHtml(text)}</strong>
      ${collector.lastSampleAt ? `<span class="monitor-meta-muted">Última amostra ${OC.formatRelativeTime(collector.lastSampleAt)}</span>` : ""}
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
      ${metric.slo ? `<p class="monitor-metric-slo">Meta: ${OC.escapeHtml(metric.slo)}</p>` : ""}
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
    const tab = OC.monitorState.activeTab || "summary";
    const refreshSec = Math.round(
      (tab === "apis"
        ? MONITOR_APIS_REFRESH_MS
        : tab === "logs"
          ? MONITOR_LOGS_REFRESH_MS
          : MONITOR_REFRESH_MS) / 1000
    );
    const recentErr = (collector.recentErrors || []).slice(-1)[0];
    return `<div class="monitor-meta-bar">
      <div class="monitor-meta-items">
        <span>Atualizado: <strong>${OC.escapeHtml(refreshed)}</strong></span>
        <span>Auto-refresh: <strong>${refreshSec}s</strong></span>
        <span>Coleta: <strong class="monitor-collector-${collectorClass}">${OC.escapeHtml(collector.label || "—")}</strong></span>
        ${collector.lastSampleAt ? `<span class="monitor-meta-muted">amostra ${OC.formatRelativeTime(collector.lastSampleAt)}</span>` : ""}
      </div>
      <button type="button" class="btn btn-secondary btn-sm" id="monitor-refresh-now">Atualizar agora</button>
    </div>
    ${renderStaleBanner(config)}
    ${recentErr ? `<div class="monitor-section-hint">Último erro de coleta: ${OC.escapeHtml(recentErr.message || recentErr)} (${OC.escapeHtml(OC.formatRelativeTime(recentErr.at))})</div>` : ""}
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
        ${count ? `<span class="monitor-filter-count">${count} filtro(s)</span>` : ""}
        <button type="button" class="btn btn-ghost btn-sm" id="monitor-clear-filters">Limpar</button>
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

  function renderStatusOverview(overviews, uptimeByEnv) {
    const sortedActive = [...overviews].sort(
      (a, b) => SEV_ORDER[a.activeOverall] - SEV_ORDER[b.activeOverall]
    );
    const activeCards = sortedActive
      .map((o) => {
        const up = uptimeByEnv?.[o.env]?.uptimePct;
        const level = o.activeOverall;
        const glyph =
          level === "ok" ? "✓" : level === "warn" ? "!" : level === "critical" ? "×" : "?";
        const reason = (o.activeReasons || []).join(" · ") || "Nenhum alerta ativo";
        return `<button type="button" class="monitor-env-vision is-${OC.escapeHtml(level)} monitor-env-drill" data-monitor-env-focus="${OC.escapeHtml(o.env)}">
          <div class="monitor-env-vision-top">
            <div>
              <p class="monitor-env-vision-name">${OC.escapeHtml(o.env)}</p>
              <p class="monitor-env-vision-status">${OC.escapeHtml(o.activeLabel || o.label)}</p>
            </div>
            <span class="monitor-env-vision-glyph" aria-hidden="true">${glyph}</span>
          </div>
          <p class="monitor-env-vision-status">${OC.escapeHtml(reason)}</p>
          <div class="monitor-env-vision-meta">
            <span>Uptime 7d <strong>${up != null ? `${up}%` : "—"}</strong></span>
            <span>Latência <strong>${OC.escapeHtml(o.health?.value || "—")}</strong></span>
          </div>
        </button>`;
      })
      .join("");

    const sortedOccurred = [...overviews].sort(
      (a, b) => SEV_ORDER[a.occurredOverall] - SEV_ORDER[b.occurredOverall]
    );
    const occurredCards = sortedOccurred
      .map((o) => {
        const level = o.occurredOverall || "ok";
        const glyph =
          level === "ok" ? "✓" : level === "warn" ? "!" : level === "critical" ? "×" : "?";
        const reason = (o.occurredReasons || []).join(" · ") || "Nenhum problema nas últimas 24h";
        return `<button type="button" class="monitor-env-vision is-${OC.escapeHtml(level)} monitor-env-drill" data-monitor-env-focus="${OC.escapeHtml(o.env)}">
          <div class="monitor-env-vision-top">
            <div>
              <p class="monitor-env-vision-name">${OC.escapeHtml(o.env)}</p>
              <p class="monitor-env-vision-status">${OC.escapeHtml(o.occurredLabel || STATUS_LABELS[level])}</p>
            </div>
            <span class="monitor-env-vision-glyph" aria-hidden="true">${glyph}</span>
          </div>
          <p class="monitor-env-vision-status">${OC.escapeHtml(reason)}</p>
        </button>`;
      })
      .join("");

    return `<section class="monitor-section">
      <div class="monitor-section-head">
        <h3 class="monitor-section-title">Alertas ativos</h3>
        <span class="monitor-meta-muted">Impacto em tempo real</span>
      </div>
      <p class="monitor-overview-note">Offline, lentidão atual ou deploy ainda quebrado.</p>
      <div class="monitor-env-vision-grid">${activeCards}</div>
    </section>
    <section class="monitor-section monitor-overview-occurred">
      <div class="monitor-section-head">
        <h3 class="monitor-section-title">Problemas ocorridos (24h)</h3>
        <span class="monitor-meta-muted">Histórico — sem impacto ativo</span>
      </div>
      <p class="monitor-overview-note">Falhas de sync, 5xx, deploy e p95 no período, mesmo já estabilizado.</p>
      <div class="monitor-env-vision-grid">${occurredCards}</div>
    </section>`;
  }

  function renderIncidentTimeline(groups, limit) {
    if (!groups?.length) {
      return `<p class="monitor-empty monitor-empty-ok">Nenhum incidente recente.</p>`;
    }
    const items = groups.slice(0, limit || 8).map((g) => {
      const sev = String(g.severity || "info").toLowerCase();
      const countLabel = g.count > 1 ? ` · ${g.count}×` : "";
      const offlineLabel = g.offlineDurationLabel
        ? ` · offline ${g.offlineDurationLabel}${g.offlineOngoing ? " (ativo)" : ""}`
        : "";
      return `<li class="monitor-timeline-item is-${OC.escapeHtml(sev)}">
        <span class="monitor-timeline-dot" aria-hidden="true"></span>
        <span class="monitor-timeline-sev">${OC.escapeHtml(sev)}</span>
        <div class="monitor-timeline-body">
          <p class="monitor-timeline-title">${OC.escapeHtml(g.environment || "")} · ${OC.escapeHtml(g.title || "")}${OC.escapeHtml(countLabel)}${OC.escapeHtml(offlineLabel)}</p>
          <p class="monitor-timeline-when">${OC.escapeHtml(OC.formatDate(g.lastAt || g.firstAt))}</p>
        </div>
        <button type="button" class="btn btn-ghost btn-sm monitor-open-event" data-event-id="${g.sampleEventId}" data-event-env="${OC.escapeHtml(g.environment || "")}">Detalhes</button>
      </li>`;
    });
    return `<ul class="monitor-timeline">${items.join("")}</ul>`;
  }

  function renderGroupedIncidents(groups, limit) {
    return renderIncidentTimeline(groups, limit);
  }

  function renderIncidentFilters() {
    const f = OC.monitorState.eventFilters;
    return `${OC.renderOpsChipToolbar({
      id: "severity",
      label: "Severidade",
      attr: "data-monitor-chip",
      value: f.severity || "",
      options: [
        { value: "", label: "Todas" },
        { value: "critical", label: "Critical" },
        { value: "warn", label: "Warn" },
        { value: "info", label: "Info" },
      ],
    })}${OC.renderOpsChipToolbar({
      id: "category",
      label: "Categoria",
      attr: "data-monitor-chip",
      value: f.category || "",
      options: [
        { value: "", label: "Todas" },
        ...CATEGORY_KEYS.map((c) => ({ value: c, label: CATEGORY_LABELS[c] })),
      ],
    })}${OC.renderOpsChipToolbar({
      id: "hours",
      label: "Período",
      attr: "data-monitor-chip",
      value: String(f.hours || 24),
      options: [
        { value: "1", label: "1h" },
        { value: "6", label: "6h" },
        { value: "24", label: "24h" },
        { value: "168", label: "7d" },
      ],
      extra: `<button type="button" class="btn btn-danger btn-sm" id="monitor-clear-events">Limpar incidentes</button>`,
    })}`;
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
          <td>${OC.escapeHtml(g.title || "")}${countLabel}${g.offlineDurationLabel ? ` · offline ${OC.escapeHtml(g.offlineDurationLabel)}` : ""}</td>
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

  function renderApiRoutesTable(routesByEnv, filterRoutes) {
    const filterSet = new Set(
      (filterRoutes || []).map((r) => `${String(r.method || "").toUpperCase()} ${r.route || ""}`.trim())
    );
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
        const key = `${String(r.method || "").toUpperCase()} ${r.route || ""}`.trim();
        const matched = !filterSet.size || filterSet.has(key);
        if (filterSet.size && !matched) return;
        rows.push(`<tr class="${filterSet.size ? "is-api-filter-hit" : ""}" data-api-route="${OC.escapeHtml(key)}">
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
    if (!rows.length) {
      return filterSet.size
        ? `<p class="monitor-empty monitor-empty-neutral">Nenhuma das rotas do instante selecionado aparece no ranking da janela atual.</p>`
        : `<p class="monitor-empty monitor-empty-ok">Nenhuma rota lenta encontrada no período.</p>`;
    }
    return `<div class="monitor-table-wrap ops-table-wrap" id="monitor-api-routes-table"><table class="monitor-table">
      <thead><tr><th>Ambiente</th><th>Rota</th><th>Média ms</th><th>Max ms</th><th>5xx</th><th>Amostras</th></tr></thead>
      <tbody>${rows.join("")}</tbody>
    </table></div>`;
  }

  function applyApiRouteFilter(routes) {
    const wrap = document.getElementById("monitor-api-routes-wrap");
    if (!wrap) return;
    const note = document.getElementById("monitor-api-routes-filter-note");
    if (!routes?.length) {
      if (note) note.hidden = true;
      wrap.querySelectorAll("tr[data-api-route]").forEach((tr) => {
        tr.hidden = false;
        tr.classList.remove("is-api-filter-hit");
      });
      return;
    }
    const keys = new Set(
      routes.map((r) => `${String(r.method || "").toUpperCase()} ${r.route || ""}`.trim())
    );
    let hits = 0;
    wrap.querySelectorAll("tr[data-api-route]").forEach((tr) => {
      const key = tr.getAttribute("data-api-route") || "";
      const match = keys.has(key);
      tr.hidden = !match;
      tr.classList.toggle("is-api-filter-hit", match);
      if (match) hits += 1;
    });
    if (note) {
      note.hidden = false;
      note.innerHTML = `Filtrado pelo clique no gráfico · ${hits} rota(s) · <button type="button" class="btn btn-ghost btn-sm" id="monitor-api-clear-route-filter">Limpar filtro</button>`;
      note.querySelector("#monitor-api-clear-route-filter")?.addEventListener("click", () => {
        applyApiRouteFilter([]);
      });
    }
  }

  function apiWindowHours(window) {
    if (window === "1h") return 1;
    if (window === "24h") return 24;
    return 6;
  }

  function renderApisSection(apiRoutes, apiSeries, slos) {
    const win = OC.monitorState.apiWindow || "6h";
    const hours = apiWindowHours(win);
    const sloMs = slos?.healthP95WarnMs ?? 2000;
    const focus = STATUS_FOCUS_ENVS;
    const charts = focus
      .map((env) => {
        const series = apiSeries?.[env];
        const single = series ? { [env]: series } : {};
        if (!series) {
          return `<article class="monitor-api-env-card" data-api-env="${OC.escapeHtml(env)}">
            <h4 class="monitor-latency-env-title">${OC.escapeHtml(env)} · latência média API</h4>
            <p class="monitor-empty monitor-empty-neutral">Sem amostras de api_avg_ms ainda. O collector passa a gravar a cada ~1 min.</p>
            <div class="monitor-spike-context" data-spike-context="${OC.escapeHtml(env)}" hidden></div>
          </article>`;
        }
        const built = buildSvgLineChart(single, `API avg ${env}`, sloMs, {
          windowHours: hours,
          chartId: `api-${env}`,
        });
        const spikeList =
          built.spikes && built.spikes.length
            ? `<div class="monitor-spike-list">
                <p class="monitor-section-hint">Picos ≥ SLO ${sloMs} ms (${built.spikes.length})</p>
                <ul>${built.spikes
                  .slice(0, 8)
                  .map(
                    (s) =>
                      `<li><button type="button" class="btn btn-ghost btn-sm monitor-spike-jump" data-env="${OC.escapeHtml(s.env)}" data-iso="${OC.escapeHtml(s.iso)}" data-ms="${formatLatencyMs(s.v)}">${OC.escapeHtml(OC.formatDate(s.iso))} · <strong>${formatLatencyMs(s.v)} ms</strong></button></li>`
                  )
                  .join("")}</ul>
              </div>`
            : `<p class="monitor-section-hint">Nenhum pico ≥ ${sloMs} ms neste período.</p>`;
        return `<article class="monitor-api-env-card" data-api-env="${OC.escapeHtml(env)}">
          <h4 class="monitor-latency-env-title">${OC.escapeHtml(env)} · latência média API</h4>
          ${built.html}
          ${spikeList}
          <div class="monitor-spike-context" data-spike-context="${OC.escapeHtml(env)}" hidden></div>
        </article>`;
      })
      .join("");
    const routesFocus = {};
    focus.forEach((env) => {
      if (apiRoutes?.[env]) routesFocus[env] = apiRoutes[env];
    });
    return `${OC.renderOpsChipToolbar({
      id: "api-window",
      label: "Janela",
      attr: "data-monitor-chip",
      value: win,
      options: [
        { value: "1h", label: "1h" },
        { value: "6h", label: "6h" },
        { value: "24h", label: "24h" },
      ],
      extra: `<span class="monitor-meta-muted">Atualização a cada 15s · MAIN e HOM · SLO ${sloMs} ms</span>`,
    })}
    <div class="monitor-latency-env-grid">${charts}</div>
    <h4 class="monitor-drawer-subtitle">Rotas mais lentas</h4>
    <p class="monitor-section-hint" id="monitor-api-routes-filter-note" hidden></p>
    <div id="monitor-api-routes-wrap">${renderApiRoutesTable(routesFocus)}</div>
    <p class="monitor-meta-muted">Dica: clique em um ponto do gráfico para ver as APIs avaliadas naquele instante.</p>`;
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

  function renderLogsToolbar(since) {
    const pat = OC.monitorState.logsPattern ?? "";
    const sinceChip = since
      ? `<span class="monitor-meta-muted">desde ${OC.escapeHtml(OC.formatDate(since))}</span>
         <button type="button" class="btn btn-ghost btn-sm" id="monitor-logs-clear-since">Limpar horário</button>`
      : `<span class="monitor-meta-muted">últimas 24h</span>`;
    return OC.renderOpsChipToolbar({
      id: "logs-pattern",
      label: "Filtro",
      attr: "data-monitor-chip",
      value: pat,
      options: [
        { value: "", label: "Todas" },
        { value: "ERROR", label: "ERROR" },
        { value: "WARN", label: "WARN" },
        { value: "Traceback", label: "Traceback" },
      ],
      extra: sinceChip,
    });
  }

  function renderLogsViewer(logsByEnv) {
    const blocks = [];
    Object.entries(logsByEnv).forEach(([env, data]) => {
      const lines = data.lines || [];
      if (data.error) {
        blocks.push(`<p class="monitor-empty monitor-empty-neutral">${OC.escapeHtml(env)}: ${OC.escapeHtml(data.error)}</p>`);
        return;
      }
      if (!lines.length) {
        const hint = data.pattern
          ? `nenhuma linha com “${data.pattern}” no período.`
          : "nenhuma linha no período.";
        blocks.push(`<p class="monitor-empty monitor-empty-neutral">${OC.escapeHtml(env)}: ${OC.escapeHtml(hint)}</p>`);
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
      blocks.push(`<h4 class="monitor-log-env">${OC.escapeHtml(env)} <span class="monitor-meta-muted">(${lines.length})</span></h4>
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
      apiSeries: false,
      uptime: false,
      syncs: false,
      deploys: false,
      logs: false,
    };
    switch (tab) {
      case "summary":
        plan.summaries = true;
        plan.events = true;
        plan.grouped = true;
        plan.uptime = true;
        plan.deploys = true; // KPI "Último deploy" no Resumo
        break;
      case "incidents":
        plan.events = true;
        plan.grouped = true;
        break;
      case "latency":
        plan.summaries = true;
        plan.healthSeries = true;
        plan.uptime = true;
        break;
      case "syncs":
        plan.syncs = true;
        break;
      case "apis":
        plan.summaries = true;
        plan.apiRoutes = true;
        plan.apiSeries = true;
        break;
      case "logs":
        plan.logs = true;
        break;
      default:
        plan.summaries = true;
        plan.events = true;
        plan.grouped = true;
        plan.uptime = true;
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
      apiSeries: {},
      uptimeByEnv: {},
      syncs: {},
      deploys: {},
      logs: {},
    };
  }

  function renderTabContent(tab, payload, overviews, slos) {
    const {
      healthSeries,
      groupedEvents,
      events,
      apiRoutes,
      apiSeries,
      uptimeByEnv,
      syncs,
      deploys,
      logs,
      loading,
      config,
    } = payload;
    const highlight = OC.currentRoute?.query?.highlight || "";
    const since = OC.currentRoute?.query?.since || "";

    switch (tab) {
      case "summary": {
        const kpis = computeExecutiveKpis(
          overviews,
          groupedEvents,
          uptimeByEnv,
          deploys,
          config
        );
        return `${renderKpiRow(kpis)}
          ${renderGlobalStatusBanner(overviews, config)}
          ${renderStatusOverview(overviews, uptimeByEnv)}
          ${OC.renderOpsSection({
            title: "Disponibilidade",
            hint: "MAIN e HOM · 7 dias",
            body: loading?.uptime
              ? renderLoadingSection("Status diário")
              : renderUptimeStatusBars(uptimeByEnv, { compact: true }),
          })}
          ${OC.renderOpsSection({
            title: "Timeline de incidentes",
            hint: "Eventos recentes",
            body: loading?.grouped
              ? renderLoadingSection("Timeline de incidentes")
              : renderIncidentTimeline(groupedEvents, 6),
          })}
          ${OC.renderOpsSection({
            title: "Painel de saúde",
            hint: "Indicadores por ambiente",
            body: renderHealthCards(overviews),
          })}
          ${
            OC.monitorState.categories.deploy
              ? OC.renderOpsSection({
                  title: "Pipeline de deploy",
                  body: loading?.deploys
                    ? renderLoadingSection("Pipeline de deploy")
                    : renderDeploySection(deploys),
                })
              : ""
          }`;
      }
      case "incidents":
        return loading?.events
          ? renderLoadingSection("Incidentes")
          : OC.renderOpsSection({
              title: "Incidentes",
              hint: "Filtros por severidade, categoria e período",
              body: `${renderIncidentFilters()}${
                groupedEvents?.length
                  ? renderIncidentTimeline(groupedEvents, 40)
                  : renderEventsTable(events, groupedEvents)
              }`,
            });
      case "latency":
        return `${OC.renderOpsSection({
          title: "Status diário",
          hint: "MAIN e HOM · clique no dia para detalhe horário",
          body: loading?.uptime
            ? renderLoadingSection("Status diário")
            : `${renderUptimeStatusBars(uptimeByEnv, { drillable: true })}${renderDayHourDrill(OC.monitorState.dayDrill)}`,
        })}${OC.renderOpsSection({
          title: `Tendência de latência`,
          hint: latencyWindowLabel(OC.monitorState.latencyWindow || "24h"),
          body: loading?.healthSeries
            ? renderLoadingSection("Tendência de latência")
            : `${renderLatencyToolbar()}${buildEnvLatencyCharts(healthSeries, slos.healthP95WarnMs)}`,
        })}`;
      case "syncs":
        return loading?.syncs
          ? renderLoadingSection("Processamento de dados (syncs)")
          : OC.renderOpsSection({
              title: "Processamento de dados (syncs)",
              hint: "Últimas execuções",
              body: renderSyncTimeline(syncs, highlight),
            });
      case "apis":
        return loading?.apiRoutes || loading?.apiSeries
          ? renderLoadingSection("Monitoramento de APIs")
          : OC.renderOpsSection({
              title: "APIs · MAIN e HOM",
              hint: "Latência média e rotas lentas",
              body: renderApisSection(apiRoutes, apiSeries, slos),
            });
      case "logs":
        return loading?.logs
          ? renderLoadingSection("Logs de serviço")
          : OC.renderOpsSection({
              title: "Logs de serviço",
              hint: since ? `desde ${OC.formatDate(since)}` : "últimas 24h",
              body: `${renderLogsToolbar(since)}${renderLogsViewer(logs)}`,
            });
      default:
        return renderTabContent("summary", payload, overviews, slos);
    }
  }

  function nearestHoverPoint(points, clientX, svg) {
    if (!points?.length || !svg) return null;
    const rect = svg.getBoundingClientRect();
    const viewW = svg.viewBox.baseVal.width || 720;
    const scaleX = rect.width / viewW;
    const svgX = (clientX - rect.left) / scaleX;
    let best = null;
    let bestDist = Infinity;
    points.forEach((p) => {
      const d = Math.abs(p.x - svgX);
      if (d < bestDist) {
        bestDist = d;
        best = p;
      }
    });
    return best;
  }

  async function showSpikeContext(env, iso, ms) {
    const panel = document.querySelector(`[data-spike-context="${env}"]`);
    if (!panel) return;
    panel.hidden = false;
    panel.innerHTML = `<div class="loading-inline"><div class="loading-spinner loading-spinner-sm"></div> Buscando APIs e contexto em ${OC.escapeHtml(OC.formatDate(iso))}…</div>`;

    const center = new Date(iso).getTime();
    const winMs = 15 * 60 * 1000;
    let events = [];
    let apiDrill = null;
    try {
      const [eventsData, samplesData] = await Promise.all([
        OC.fetchMonitoringJson(`/api/v1/monitoring/${env}/events?hours=24&limit=100`, {
          events: [],
        }),
        OC.fetchMonitoringJson(
          `/api/v1/monitoring/${env}/api-samples?at=${encodeURIComponent(iso)}&radiusMinutes=5&minMs=0`,
          { samples: [], slowRoutes: [] }
        ),
      ]);
      events = (eventsData.events || []).filter((e) => {
        const t = new Date(e.recorded_at).getTime();
        return !Number.isNaN(t) && Math.abs(t - center) <= winMs;
      });
      apiDrill = samplesData;
    } catch {
      events = [];
    }

    // Fallback: labels do ponto do gráfico (topRoutes do collector)
    const hoverMatch = (OC._chartHoverData?.[`api-${env}`] || []).find((p) => p.iso === iso);
    const labelRoutes = hoverMatch?.labels?.topRoutes || [];
    const slowRoutes =
      (apiDrill?.slowRoutes && apiDrill.slowRoutes.length
        ? apiDrill.slowRoutes
        : apiDrill?.collectorTopRoutes && apiDrill.collectorTopRoutes.length
          ? apiDrill.collectorTopRoutes
          : labelRoutes) || [];
    const samples = apiDrill?.samples || [];
    const totals = apiDrill?.totals || {};
    const source = apiDrill?.source || (labelRoutes.length ? "collector_labels" : "unavailable");

    applyApiRouteFilter(slowRoutes);

    const routesHtml = slowRoutes.length
      ? `<div class="monitor-api-drill-routes">
          <p class="monitor-section-hint">APIs impactadas (±5 min)${source === "live" ? "" : " · snapshot do collector"}</p>
          <div class="monitor-table-wrap ops-table-wrap"><table class="monitor-table">
            <thead><tr><th>Método</th><th>Rota</th><th>Média</th><th>Máx</th><th>Amostras</th><th>5xx</th></tr></thead>
            <tbody>${slowRoutes
              .slice(0, 15)
              .map(
                (r) => `<tr>
                <td>${OC.escapeHtml(r.method || "")}</td>
                <td><code>${OC.escapeHtml(r.route || "")}</code></td>
                <td>${r.avgMs != null ? formatLatencyMs(r.avgMs) : "—"}</td>
                <td>${r.maxMs != null ? formatLatencyMs(r.maxMs) : "—"}</td>
                <td>${r.count ?? "—"}</td>
                <td>${r.errors5xx ?? 0}</td>
              </tr>`
              )
              .join("")}</tbody>
          </table></div>
        </div>`
      : `<p class="monitor-empty monitor-empty-neutral">${
          apiDrill?.liveError
            ? `Não foi possível consultar amostras ao vivo (${OC.escapeHtml(apiDrill.liveError)}). Deploy do endpoint /ops-metrics/around/ pode estar pendente.`
            : "Nenhuma rota registrada neste instante."
        }</p>`;

    const samplesHtml = samples.length
      ? `<details class="monitor-api-samples" open>
          <summary>Amostras individuais (${samples.length}${totals.requests ? ` de ${totals.requests}` : ""})</summary>
          <div class="monitor-table-wrap ops-table-wrap"><table class="monitor-table">
            <thead><tr><th>Horário</th><th>Rota</th><th>Status</th><th>ms</th></tr></thead>
            <tbody>${samples
              .slice(0, 30)
              .map(
                (s) => `<tr>
                <td>${OC.escapeHtml(OC.formatDate(s.recordedAt))}</td>
                <td><code>${OC.escapeHtml(s.method || "")} ${OC.escapeHtml(s.route || "")}</code></td>
                <td>${OC.escapeHtml(String(s.statusCode ?? ""))}</td>
                <td>${formatLatencyMs(s.durationMs)}</td>
              </tr>`
              )
              .join("")}</tbody>
          </table></div>
        </details>`
      : "";

    const list = events.length
      ? `<ul class="monitor-related-list">${events
          .slice(0, 10)
          .map(
            (e) =>
              `<li><span class="monitor-sev monitor-sev-${OC.escapeHtml(e.severity || "info")}">${OC.escapeHtml(e.severity || "")}</span>
              ${OC.escapeHtml(e.title || "")}
              <span class="monitor-meta-muted">${OC.escapeHtml(OC.formatRelativeTime(e.recorded_at))}</span>
              ${e.id ? `<button type="button" class="btn btn-ghost btn-sm monitor-open-event" data-event-id="${e.id}" data-event-env="${OC.escapeHtml(env)}">Detalhes</button>` : ""}
              </li>`
          )
          .join("")}</ul>`
      : `<p class="monitor-empty monitor-empty-neutral">Nenhum evento de monitoramento ±15 min deste instante.</p>`;

    const sinceParam = encodeURIComponent(iso);
    panel.innerHTML = `<div class="monitor-spike-context-head">
        <strong>APIs em ${OC.escapeHtml(OC.formatDate(iso))}</strong>
        ${ms != null ? `<span>· média ${formatLatencyMs(Number(ms))} ms</span>` : ""}
      </div>
      ${routesHtml}
      ${samplesHtml}
      <div class="monitor-spike-context-head" style="margin-top:var(--spacing-3)"><strong>Eventos correlacionados</strong></div>
      ${list}
      <div class="monitor-spike-context-actions">
        <button type="button" class="btn btn-ghost btn-sm" id="monitor-api-clear-drill-${OC.escapeHtml(env)}">Limpar seleção</button>
        <a class="btn btn-secondary btn-sm" href="/monitoring/logs?since=${sinceParam}">Ver logs</a>
        <a class="btn btn-ghost btn-sm" href="/monitoring/incidents">Ver incidentes</a>
      </div>`;

    panel.querySelectorAll(".monitor-open-event").forEach((el) => {
      el.addEventListener("click", () => {
        const id = el.getAttribute("data-event-id");
        const e = el.getAttribute("data-event-env");
        if (id && e && OC.openMonitorIncidentDrawer) OC.openMonitorIncidentDrawer(e, id);
      });
    });
    panel.querySelector(`#monitor-api-clear-drill-${env}`)?.addEventListener("click", () => {
      panel.hidden = true;
      panel.innerHTML = "";
      applyApiRouteFilter([]);
    });

    // Highlight cursor on matching chart
    const chartId = `api-${env}`;
    const svg = document.querySelector(`[data-chart-svg="${chartId}"]`);
    const cursor = svg?.querySelector(".monitor-chart-cursor");
    const pts = OC._chartHoverData?.[chartId] || [];
    const match =
      pts.find((p) => p.iso === iso) ||
      pts.reduce((best, p) => {
        if (!best) return p;
        return Math.abs(p.t - center) < Math.abs(best.t - center) ? p : best;
      }, null);
    if (cursor && match) {
      cursor.setAttribute("x1", String(match.x));
      cursor.setAttribute("x2", String(match.x));
      cursor.classList.remove("hidden");
    }
  }

  function bindChartInteractions(root) {
    root.querySelectorAll("[data-chart-hit]").forEach((hit) => {
      const chartId = hit.getAttribute("data-chart-hit");
      const svg = root.querySelector(`[data-chart-svg="${chartId}"]`);
      const tooltip = root.querySelector(`[data-chart-tooltip="${chartId}"]`);
      const wrap = root.querySelector(`[data-chart-id="${chartId}"]`);
      const cursor = svg?.querySelector(".monitor-chart-cursor");
      if (!svg || !tooltip || !wrap) return;
      const sloRaw = wrap.getAttribute("data-slo");
      const sloMs = sloRaw ? Number(sloRaw) : null;

      const onMove = (e) => {
        const points = OC._chartHoverData?.[chartId] || [];
        const nearest = nearestHoverPoint(points, e.clientX, svg);
        if (!nearest) {
          tooltip.classList.add("hidden");
          cursor?.classList.add("hidden");
          return;
        }
        cursor?.setAttribute("x1", String(nearest.x));
        cursor?.setAttribute("x2", String(nearest.x));
        cursor?.classList.remove("hidden");
        const vsSlo =
          sloMs != null
            ? nearest.v >= sloMs
              ? ` · +${formatLatencyMs(nearest.v - sloMs)} ms acima do SLO`
              : ` · ${formatLatencyMs(sloMs - nearest.v)} ms abaixo do SLO`
            : "";
        const avgNote = nearest.avg != null ? ` · média ${formatLatencyMs(nearest.avg)} ms` : "";
        tooltip.innerHTML = `<strong>${OC.escapeHtml(OC.formatDate(nearest.iso))}</strong><br>${OC.escapeHtml(nearest.env)}: <strong>${formatLatencyMs(nearest.v)} ms</strong>${OC.escapeHtml(avgNote)}${OC.escapeHtml(vsSlo)}`;
        tooltip.classList.remove("hidden");
        const wrapRect = wrap.getBoundingClientRect();
        tooltip.style.left = `${Math.min(Math.max(8, e.clientX - wrapRect.left + 12), wrapRect.width - 180)}px`;
        tooltip.style.top = `${Math.max(8, e.clientY - wrapRect.top - 48)}px`;
      };

      const onLeave = () => {
        tooltip.classList.add("hidden");
      };

      hit.addEventListener("mousemove", onMove);
      hit.addEventListener("mouseleave", onLeave);
      hit.addEventListener("click", (e) => {
        const points = OC._chartHoverData?.[chartId] || [];
        const nearest = nearestHoverPoint(points, e.clientX, svg);
        if (!nearest) return;
        if (chartId.startsWith("api-")) {
          showSpikeContext(nearest.env, nearest.iso, nearest.v);
        }
      });
    });

    root.querySelectorAll(".monitor-spike-jump, .monitor-spike-dot").forEach((el) => {
      el.addEventListener("click", (e) => {
        e.stopPropagation();
        const env = el.getAttribute("data-env");
        const iso = el.getAttribute("data-iso");
        const ms = el.getAttribute("data-ms");
        if (env && iso) showSpikeContext(env, iso, ms);
      });
    });
  }

  function bindMonitoringInteractions(root) {
    OC.bindBackNavigation(root);
    const openAlerts = () => {
      if (OC.openMonitorAlertsDrawer) OC.openMonitorAlertsDrawer(OC.lastAlertGroups || [], { refresh: false });
      else OC.navigate("monitoring", null, { tab: "incidents" });
    };
    OC.bindOpsStatActions?.(root, { alerts: openAlerts });
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
        const query = { ...(OC.currentRoute.query || {}) };
        delete query.env; // visão multi-ambiente; ?env= só via foco explícito
        // since/filtro de horário só permanece se o usuário ficou em Logs e veio de drill-down
        if (tab !== "logs") delete query.since;
        OC.navigate("monitoring", null, { tab, query });
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
    OC.bindOpsChipToolbar?.(root, "data-monitor-chip", (id, value) => {
      if (id === "severity" || id === "category" || id === "hours") {
        OC.monitorState.eventFilters[id] = id === "hours" ? Number(value) || 24 : value;
        savePrefs();
        OC.refreshMonitoring({ force: true });
        return;
      }
      if (id === "api-window") {
        OC.monitorState.apiWindow = value || "6h";
        savePrefs();
        OC.startMonitoringRefresh();
        OC.refreshMonitoring({ force: true });
        return;
      }
      if (id === "latency-window") {
        OC.monitorState.latencyWindow = value || "24h";
        savePrefs();
        OC.refreshMonitoring({ force: true });
        return;
      }
      if (id === "logs-pattern") {
        OC.monitorState.logsPattern = value || "";
        savePrefs();
        OC.refreshMonitoring({ force: true });
      }
    });
    root.querySelector("#monitor-logs-clear-since")?.addEventListener("click", () => {
      const query = { ...(OC.currentRoute.query || {}) };
      delete query.since;
      OC.navigate("monitoring", null, { tab: "logs", query });
    });
    root.querySelector("#monitor-clear-events")?.addEventListener("click", async () => {
      if (
        !window.confirm(
          "Apagar TODOS os incidentes (monitor_events)? O histórico de latência será mantido."
        )
      ) {
        return;
      }
      const btn = root.querySelector("#monitor-clear-events");
      if (btn) btn.disabled = true;
      try {
        const result = await OC.fetchJson("/api/v1/monitoring/events/clear", {
          method: "POST",
          body: "{}",
        });
        if (result?.error) {
          window.alert(result.error);
        } else {
          window.alert(`Incidentes limpos (${result?.deleted ?? 0} removidos).`);
          OC.refreshMonitoring({ force: true });
        }
      } catch (err) {
        window.alert(err.message || String(err));
      } finally {
        if (btn) btn.disabled = false;
      }
    });
    root.querySelectorAll("[data-day-env][data-day-date]").forEach((el) => {
      const open = () => {
        const env = el.getAttribute("data-day-env");
        const date = el.getAttribute("data-day-date");
        if (env && date) openDayDrill(env, date);
      };
      el.addEventListener("click", open);
      el.addEventListener("keydown", (e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          open();
        }
      });
    });
    root.querySelector("#monitor-day-drill-close")?.addEventListener("click", () => {
      OC.monitorState.dayDrill = null;
      if (OC.monitorState.payload) OC.renderMonitoringView(OC.monitorState.payload);
    });
    root.querySelectorAll("[data-hour-env][data-hour]").forEach((el) => {
      el.addEventListener("click", () => {
        const env = el.getAttribute("data-hour-env");
        const hour = Number(el.getAttribute("data-hour"));
        const iso = el.getAttribute("data-hour-iso");
        if (env != null && !Number.isNaN(hour)) openHourContext(env, hour, iso);
      });
    });
    root.querySelectorAll("[data-hour-logs-env]").forEach((el) => {
      el.addEventListener("click", () => {
        const env = el.getAttribute("data-hour-logs-env");
        const since = el.getAttribute("data-hour-logs-since") || "";
        OC.navigate("monitoring", env, {
          tab: "logs",
          focusEnv: env || undefined,
          query: since ? { since } : {},
        });
      });
    });
    bindChartInteractions(root);
  }

  async function openDayDrill(env, date) {
    OC.monitorState.dayDrill = { env, date, loading: true, hourContext: null };
    if (OC.monitorState.payload) OC.renderMonitoringView(OC.monitorState.payload);
    try {
      const data = await OC.fetchMonitoringJson(
        `/api/v1/monitoring/${env}/uptime-hours?date=${encodeURIComponent(date)}`,
        { environment: env, hourBars: [] }
      );
      if (data.error) {
        OC.monitorState.dayDrill = { env, date, error: data.error, hourContext: null };
      } else {
        OC.monitorState.dayDrill = { env, date, data, hourContext: null };
      }
    } catch (err) {
      OC.monitorState.dayDrill = {
        env,
        date,
        error: err.message || String(err),
        hourContext: null,
      };
    }
    if (OC.monitorState.payload) OC.renderMonitoringView(OC.monitorState.payload);
  }

  async function openHourContext(env, hour, iso) {
    const drill = OC.monitorState.dayDrill;
    if (!drill || drill.env !== env) return;
    const bar = (drill.data?.hourBars || []).find((h) => h.hour === hour) || null;
    OC.monitorState.dayDrill = {
      ...drill,
      hourContext: { hour, iso, bar, loading: true, events: [] },
    };
    if (OC.monitorState.payload) OC.renderMonitoringView(OC.monitorState.payload);

    const center = new Date(iso || drill.date).getTime();
    const winMs = 30 * 60 * 1000;
    let events = [];
    try {
      const data = await OC.fetchMonitoringJson(
        `/api/v1/monitoring/${env}/events?hours=168&limit=200`,
        { events: [] }
      );
      events = (data.events || []).filter((e) => {
        const t = new Date(e.recorded_at).getTime();
        return !Number.isNaN(t) && Math.abs(t - center) <= winMs;
      });
    } catch {
      events = [];
    }
    if (OC.monitorState.dayDrill?.env === env && OC.monitorState.dayDrill?.date === drill.date) {
      OC.monitorState.dayDrill = {
        ...OC.monitorState.dayDrill,
        hourContext: { hour, iso, bar, loading: false, events },
      };
      if (OC.monitorState.payload) OC.renderMonitoringView(OC.monitorState.payload);
    }
  }

  OC.showMonitoringLoading = function showMonitoringLoading() {
    const root = document.getElementById("view-monitoring");
    if (!root) return;
    loadPrefs();
    const activeTab = OC.monitorState.activeTab || "summary";
    const emptyKpis = {
      healthy: 0,
      total: OC.ENV_ORDER.length,
      alertCount: 0,
      occurredCount: 0,
      avgUptime: null,
      lastDeployAt: null,
      lastSampleAt: null,
      collectorLabel: "Carregando…",
    };
    root.innerHTML = `${renderMonitorHero(emptyKpis, {
      compact: activeTab !== "summary",
      tabLabel: TAB_LABELS[activeTab] || activeTab,
    })}
    ${OC.renderMonitoringFilters()}
    ${renderTabBar(activeTab)}
    <div class="monitor-tab-panel"><div class="loading-inline"><div class="loading-spinner loading-spinner-sm"></div> Carregando monitoramento…</div></div>`;
    bindMonitoringInteractions(root);
  };

  OC.renderMonitoringView = function renderMonitoringView(payload, options = {}) {
    const root = document.getElementById("view-monitoring");
    if (!root) return;
    const tRender0 = typeof performance !== "undefined" ? performance.now() : Date.now();

    const warnings = payload?.warnings || [];
    const slos = getSlos(payload?.config);
    const overviews = (payload?.envSummaries || []).map(({ env, summary }) =>
      computeEnvOverview(env, summary, payload?.deploys?.[env], slos)
    );
    const activeTab = OC.monitorState.activeTab || "summary";
    const kpis = computeExecutiveKpis(
      overviews,
      payload?.groupedEvents || [],
      payload?.uptimeByEnv || {},
      payload?.deploys || {},
      payload?.config
    );

    const panel = root.querySelector(".monitor-tab-panel");
    const canPartial =
      options.partial === true &&
      panel &&
      root.querySelector(".ops-hero") &&
      root.querySelector("[data-monitor-tab]");

    if (canPartial) {
      OC.updateAlertCountInPlace?.(kpis.alertCount);
      root.querySelectorAll(".ops-hero-stat-value, .ops-kpi-value").forEach((el) => {
        const card = el.closest("[data-ops-stat-action], [data-ops-kpi-action], .ops-hero-stat, .ops-kpi");
        const label = card?.querySelector(".ops-hero-stat-label, .ops-kpi-label")?.textContent || "";
        if (/alertas ativos/i.test(label)) el.textContent = String(kpis.alertCount ?? 0);
        else if (/problemas/i.test(label)) el.textContent = String(kpis.occurredCount ?? 0);
        else if (/saudáveis|healthy|ambientes ok/i.test(label)) el.textContent = `${kpis.healthy}/${kpis.total}`;
        else if (/disponibilidade/i.test(label) && kpis.avgUptime != null) el.textContent = `${kpis.avgUptime}%`;
      });
      const meta = root.querySelector(".monitor-meta-bar");
      if (meta) meta.outerHTML = renderMetaBar(payload?.config, warnings);
      panel.innerHTML = renderTabContent(activeTab, payload, overviews, slos);
      bindMonitoringInteractions(root);
      const ms = (typeof performance !== "undefined" ? performance.now() : Date.now()) - tRender0;
      OC.perfRecord?.({ name: "render:monitoring:partial", ms: Math.round(ms * 10) / 10, tab: activeTab });
      OC._monitorRenderCount = (OC._monitorRenderCount || 0) + 1;
      return;
    }

    root.innerHTML = `${renderMonitorHero(kpis, {
      compact: activeTab !== "summary",
      tabLabel: TAB_LABELS[activeTab] || activeTab,
    })}
    ${OC.renderMonitoringFilters()}
    ${renderTabBar(activeTab)}
    ${renderMetaBar(payload?.config, warnings)}
    <div class="monitor-tab-panel">${renderTabContent(activeTab, payload, overviews, slos)}</div>`;

    bindMonitoringInteractions(root);
    const ms = (typeof performance !== "undefined" ? performance.now() : Date.now()) - tRender0;
    OC.perfRecord?.({ name: "render:monitoring:full", ms: Math.round(ms * 10) / 10, tab: activeTab });
    OC._monitorRenderCount = (OC._monitorRenderCount || 0) + 1;
  };

  OC.fetchMonitoringJson = async function fetchMonitoringJson(url, fallback = null, options = {}) {
    try {
      if (OC.timedFetchJson) {
        return await OC.timedFetchJson(url, { ...options, perfLabel: options.perfLabel });
      }
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
    const fetchOpts = (perfLabel) => ({ signal: abortController.signal, perfLabel });
    const tCycle0 = typeof performance !== "undefined" ? performance.now() : Date.now();
    OC._monitorRenderCount = 0;

    loadPrefs();
    const warnings = [];
    const tab = OC.monitorState.activeTab || "summary";
    const plan = monitoringFetchPlan(tab, OC.monitorState.categories);
    const prev = OC.monitorState.payload || {};
    const hadShell = !!document.querySelector("#view-monitoring .monitor-tab-panel");

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
    if (plan.apiSeries) loading.apiSeries = true;
    if (plan.uptime) loading.uptime = true;
    if (plan.syncs) loading.syncs = true;
    if (plan.deploys) loading.deploys = true;
    if (plan.logs) loading.logs = true;

    let config = prev.config;
    let envSummaries = prev.envSummaries || [];
    let healthSeries = { ...emptyPayloadExtras().healthSeries, ...(prev.healthSeries || {}) };
    let events = prev.events || [];
    let groupedEvents = prev.groupedEvents || [];
    let apiRoutes = { ...emptyPayloadExtras().apiRoutes, ...(prev.apiRoutes || {}) };
    let apiSeries = { ...emptyPayloadExtras().apiSeries, ...(prev.apiSeries || {}) };
    let uptimeByEnv = { ...emptyPayloadExtras().uptimeByEnv, ...(prev.uptimeByEnv || {}) };
    let syncs = { ...emptyPayloadExtras().syncs, ...(prev.syncs || {}) };
    let deploys = { ...emptyPayloadExtras().deploys, ...(prev.deploys || {}) };
    let logs = { ...emptyPayloadExtras().logs, ...(prev.logs || {}) };

    const payloadFields = () => ({
      config,
      envSummaries,
      healthSeries,
      events,
      groupedEvents,
      apiRoutes,
      apiSeries,
      uptimeByEnv,
      syncs,
      deploys,
      logs,
      warnings,
    });

    const finishMonitoringRefresh = (payload) => {
      if (generation !== OC._monitorRefreshGeneration) return;
      OC.monitorState.lastRefreshedAt = new Date().toISOString();
      OC.monitorState.payload = payload;

      const alertSource = Array.isArray(payload.alertGroups)
        ? payload.alertGroups
        : Array.isArray(payload.groupedEvents)
          ? payload.groupedEvents
          : null;
      if (alertSource) {
        OC.lastAlertGroups = alertSource;
        OC._alertGroupsFetchedAt = Date.now();
        const next = OC.countActiveAlerts?.(alertSource) ?? 0;
        OC.updateAlertCountInPlace?.(next);
      }

      const usePartial = hadShell && !options.showLoading;
      OC.renderMonitoringView(payload, { partial: usePartial });

      const openEventId = OC.currentRoute?.query?.event;
      const openEnv = OC.currentRoute?.query?.env;
      if (openEventId && openEnv && OC.openMonitorIncidentDrawer) {
        OC.openMonitorIncidentDrawer(openEnv.toUpperCase(), openEventId);
      }

      const totalMs = (typeof performance !== "undefined" ? performance.now() : Date.now()) - tCycle0;
      OC.perfRecord?.({
        name: "cycle:monitoring",
        ms: Math.round(totalMs * 10) / 10,
        tab,
        renders: OC._monitorRenderCount || 0,
      });
    };

    const fetchUptimeDays = async () => {
      await Promise.all(
        STATUS_FOCUS_ENVS.map(async (env) => {
          uptimeByEnv[env] = await OC.fetchMonitoringJson(
            `/api/v1/monitoring/${env}/uptime-days?days=7`,
            { environment: env, dayBars: [] },
            fetchOpts(`uptime:${env}`)
          );
        })
      );
      delete loading.uptime;
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
        if (plan.healthSeries) {
          dashParams.set(
            "seriesHours",
            String(latencyWindowHours(OC.monitorState.latencyWindow || "24h"))
          );
        }
        if (plan.deploys) dashParams.set("deploy", "1");
        if (OC.monitorState.categories.api) dashParams.set("api", "1");
        if (ef.severity) dashParams.set("severity", ef.severity);
        if (ef.category) dashParams.set("category", ef.category);

        const dashPromise = OC.fetchMonitoringJson(
          `/api/v1/monitoring/dashboard?${dashParams}`,
          {},
          fetchOpts(`dashboard:${tab}`)
        );
        const uptimePromise = plan.uptime ? fetchUptimeDays() : Promise.resolve();
        const [dash] = await Promise.all([dashPromise, uptimePromise]);
        if (dash.error) warnings.push(dash.error);

        config = dash.config || config;
        if (config?.error) warnings.push(config.error);
        OC.monitorState.config = config;
        envSummaries = dash.envSummaries || envSummaries;
        healthSeries = { ...healthSeries, ...(dash.healthSeries || {}) };
        deploys = { ...deploys, ...(dash.deploys || {}) };
        groupedEvents =
          Array.isArray(dash.groupedEvents) && (plan.grouped || dash.groupedEvents.length)
            ? dash.groupedEvents
            : groupedEvents;
        events = Array.isArray(dash.events) ? dash.events : events;

        finishMonitoringRefresh({
          ...payloadFields(),
          alertGroups: dash.alertGroups || dash.groupedEvents,
          loading: {},
        });
        return;
      }

      const configPromise = plan.config
        ? OC.fetchMonitoringJson(
            "/api/v1/monitoring/config",
            { retentionDays: 7, enabledCategories: OC.monitorState.categories },
            fetchOpts("config")
          )
        : Promise.resolve(config);

      // APIs/Logs usam summary lite (sem PG sync); Syncs precisa do summary completo via /syncs.
      const summaryLite = tab === "apis" || tab === "logs";
      const summariesPromise = plan.summaries
        ? Promise.all(
            envs.map(async (env) => {
              const q = summaryLite ? "?lite=1" : "";
              const summary = await OC.fetchMonitoringJson(
                `/api/v1/monitoring/${env}/summary${q}`,
                { environment: env },
                fetchOpts(`summary${summaryLite ? "_lite" : ""}:${env}`)
              );
              if (summary.error) warnings.push(`${env} summary: ${summary.error}`);
              return { env, summary };
            })
          )
        : Promise.resolve(envSummaries);

      [config, envSummaries] = await Promise.all([configPromise, summariesPromise]);
      if (config?.error) warnings.push(config.error);
      OC.monitorState.config = config;

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
                `/api/v1/monitoring/${env}/series?metric=health_latency_ms&hours=${latencyWindowHours(OC.monitorState.latencyWindow || "24h")}`,
                { environment: env, points: [] },
                fetchOpts(`series:${env}`)
              );
            })
          ).then(() => {
            delete loading.healthSeries;
          })
        );
      }

      if (plan.uptime) {
        phase2.push(fetchUptimeDays());
      }

      if (plan.events) {
        phase2.push(
          Promise.all(
            envs.map((env) =>
              OC.fetchMonitoringJson(
                `/api/v1/monitoring/${env}/events?${eventParams}`,
                { events: [] },
                fetchOpts(`events:${env}`)
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
            fetchOpts("grouped")
          ).then((groupedResp) => {
            const all = groupedResp.groups || [];
            OC.lastAlertGroups = all;
            OC._alertGroupsFetchedAt = Date.now();
            groupedEvents = all.filter((g) => envs.includes(g.environment));
            delete loading.grouped;
          })
        );
      }

      if (plan.apiRoutes) {
        const win = OC.monitorState.apiWindow || "6h";
        phase2.push(
          Promise.all(
            STATUS_FOCUS_ENVS.map(async (env) => {
              apiRoutes[env] = await OC.fetchMonitoringJson(
                `/api/v1/monitoring/${env}/api-routes?window=${encodeURIComponent(win)}`,
                { environment: env, slowRoutes: [] },
                fetchOpts(`api-routes:${env}`)
              );
            })
          ).then(() => {
            delete loading.apiRoutes;
          })
        );
      }

      if (plan.apiSeries) {
        const hours = apiWindowHours(OC.monitorState.apiWindow || "6h");
        phase2.push(
          Promise.all(
            STATUS_FOCUS_ENVS.map(async (env) => {
              apiSeries[env] = await OC.fetchMonitoringJson(
                `/api/v1/monitoring/${env}/series?metric=api_avg_ms&hours=${hours}`,
                { environment: env, points: [] },
                fetchOpts(`api-series:${env}`)
              );
            })
          ).then(() => {
            delete loading.apiSeries;
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
                fetchOpts(`syncs:${env}`)
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
                fetchOpts(`deploy:${env}`)
              );
            })
          ).then(() => {
            delete loading.deploys;
          })
        );
      }

      if (plan.logs) {
        const since = OC.currentRoute?.query?.since || "";
        const pattern = OC.monitorState.logsPattern ?? "";
        phase2.push(
          Promise.all(
            envs.map(async (env) => {
              const params = new URLSearchParams({ limit: "200" });
              if (since) params.set("since", since);
              if (pattern) params.set("pattern", pattern);
              logs[env] = await OC.fetchMonitoringJson(
                `/api/v1/monitoring/${env}/logs?${params}`,
                { environment: env, lines: [], pattern },
                fetchOpts(`logs:${env}`)
              );
            })
          ).then(() => {
            delete loading.logs;
          })
        );
      }

      await Promise.all(phase2);

      finishMonitoringRefresh({
        ...payloadFields(),
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
    const tab = OC.monitorState.activeTab || "summary";
    let ms = MONITOR_REFRESH_MS;
    if (tab === "apis") ms = MONITOR_APIS_REFRESH_MS;
    else if (tab === "logs") ms = MONITOR_LOGS_REFRESH_MS;
    OC.monitorTimer = setInterval(() => {
      if (!OC._monitorRefreshInFlight) OC.refreshMonitoring();
    }, ms);
  };

  OC.stopMonitoringRefresh = function stopMonitoringRefresh() {
    if (OC.monitorTimer) {
      clearInterval(OC.monitorTimer);
      OC.monitorTimer = null;
    }
  };

  /** Expostos para o drawer de incidente reutilizar o gráfico de latência. */
  OC.buildSvgLineChart = buildSvgLineChart;
  OC.bindChartInteractions = bindChartInteractions;
  OC.getMonitoringSlos = getSlos;

  loadPrefs();
})();
