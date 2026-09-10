/* global window, document */
(function () {
  window.OpsConsole = window.OpsConsole || {};
  const OC = window.OpsConsole;

  const STORAGE_ENVS = "pplid-monitor-envs";
  const STORAGE_CATS = "pplid-monitor-categories";
  const STORAGE_FILTERS = "pplid-monitor-filters";
  const MONITOR_REFRESH_MS = 30000;
  const MONITOR_APIS_REFRESH_MS = 15000;
  const MONITOR_LOGS_REFRESH_MS = 15000;
  const STORAGE_API_WINDOW = "pplid-monitor-api-window";
  const STORAGE_LATENCY_WINDOW = "pplid-monitor-latency-window";
  const STORAGE_LOGS_PATTERN = "pplid-monitor-logs-pattern";
  const STORAGE_LOGS_FILTERS = "pplid-monitor-logs-filters-v2";
  const STORAGE_LOGS_FOLLOWING = "pplid-monitor-logs-following";
  const LOG_LEVELS = ["ERROR", "WARN", "INFO", "DEBUG", "OTHER"];
  const DEFAULT_LOG_FILTERS = {
    q: "",
    levels: [],
    services: [],
    streams: [],
    period: "24h",
    since: "",
    until: "",
    order: "asc",
  };

  const ENV_COLORS = { MAIN: "#2a5595", DEV: "#0fac67", HOM: "#ff8a00" };
  const CATEGORY_KEYS = ["api", "availability", "postgres", "syncs", "deploy", "logs", "host"];
  const CATEGORY_LABELS = {
    api: "APIs",
    availability: "Disponibilidade",
    postgres: "PostgreSQL",
    syncs: "Syncs",
    deploy: "Deploy",
    logs: "Logs",
    host: "Host",
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
      host: true,
    },
    eventFilters: { severity: "", category: "", hours: 24 },
    dataByEnv: {},
    lastRefreshedAt: null,
    payload: null,
    apiWindow: "6h",
    latencyWindow: "24h",
    logsPattern: "",
    logFilters: { ...DEFAULT_LOG_FILTERS },
    logsPaused: false,
    logsFollowing: true,
    logExpanded: new Set(),
    logNewCount: 0,
    logHistoryMode: false,
    dayDrill: null,
    apiRouteSort: "priority",
    apiRouteModalSort: "timeDesc",
  };

  const API_ROUTE_SORT_OPTIONS = [
    { value: "priority", label: "Prioridade" },
    { value: "maxMsDesc", label: "Máximo (maior)" },
    { value: "successAvgDesc", label: "Latência sucesso" },
    { value: "samplesDesc", label: "Amostras" },
    { value: "status5xxDesc", label: "Erros 5xx" },
    { value: "routeAsc", label: "Rota A–Z" },
  ];

  const API_ROUTE_MODAL_SORT_OPTIONS = [
    { value: "timeDesc", label: "Horário (recente)" },
    { value: "timeAsc", label: "Horário (antigo)" },
    { value: "msDesc", label: "ms (maior)" },
    { value: "msAsc", label: "ms (menor)" },
    { value: "requesterAsc", label: "Quem requisitou" },
    { value: "statusDesc", label: "Status" },
  ];

  function renderApiTableSortToolbar(id, options, value, extraClass = "") {
    const toolbarClass = extraClass ? `monitor-api-table-toolbar ${extraClass}` : "monitor-api-table-toolbar";
    const opts = (options || [])
      .map(
        (option) =>
          `<option value="${OC.escapeHtml(option.value)}"${option.value === value ? " selected" : ""}>${OC.escapeHtml(option.label)}</option>`
      )
      .join("");
    return `<div class="${toolbarClass}">
      <label class="monitor-api-table-sort-label" for="${OC.escapeHtml(id)}">Ordenar</label>
      <select id="${OC.escapeHtml(id)}" class="monitor-api-table-sort" aria-label="Ordenar tabela">${opts}</select>
    </div>`;
  }

  function compareApiRouteRows(a, b, sortKey) {
    const num = (value) => {
      const parsed = Number(value);
      return Number.isFinite(parsed) ? parsed : -1;
    };
    if (sortKey === "maxMsDesc") return num(b.maxMs) - num(a.maxMs) || b.samples - a.samples;
    if (sortKey === "successAvgDesc") {
      return num(b.successAvgMs) - num(a.successAvgMs) || num(b.maxMs) - num(a.maxMs);
    }
    if (sortKey === "samplesDesc") return b.samples - a.samples || num(b.maxMs) - num(a.maxMs);
    if (sortKey === "status5xxDesc") {
      return b.status5xx - a.status5xx || b.status4xx - a.status4xx || b.samples - a.samples;
    }
    if (sortKey === "routeAsc") {
      return String(a.key || "").localeCompare(String(b.key || ""), "pt-BR", { sensitivity: "base" });
    }
    return (
      a.rank - b.rank ||
      num(b.successAvgMs) - num(a.successAvgMs) ||
      b.status5xx - a.status5xx ||
      b.status4xx - a.status4xx ||
      b.samples - a.samples
    );
  }

  function sortApiRouteRowsInDom(tbody, sortKey) {
    if (!tbody) return;
    const rows = [...tbody.querySelectorAll("tr[data-api-route]")];
    rows.sort((left, right) => {
      const pick = (tr, name) => tr.getAttribute(name) || "";
      const a = {
        rank: Number(pick(left, "data-sort-rank") || 0),
        maxMs: Number(pick(left, "data-sort-max-ms") || -1),
        successAvgMs: Number(pick(left, "data-sort-success-avg-ms") || -1),
        samples: Number(pick(left, "data-sort-samples") || 0),
        status5xx: Number(pick(left, "data-sort-status5xx") || 0),
        status4xx: Number(pick(left, "data-sort-status4xx") || 0),
        key: pick(left, "data-sort-route") || "",
      };
      const b = {
        rank: Number(pick(right, "data-sort-rank") || 0),
        maxMs: Number(pick(right, "data-sort-max-ms") || -1),
        successAvgMs: Number(pick(right, "data-sort-success-avg-ms") || -1),
        samples: Number(pick(right, "data-sort-samples") || 0),
        status5xx: Number(pick(right, "data-sort-status5xx") || 0),
        status4xx: Number(pick(right, "data-sort-status4xx") || 0),
        key: pick(right, "data-sort-route") || "",
      };
      return compareApiRouteRows(a, b, sortKey);
    });
    rows.forEach((row) => tbody.appendChild(row));
  }

  function sortApiRouteModalSamples(samples, sortKey) {
    const rows = [...(samples || [])];
    rows.sort((a, b) => {
      const timeA = new Date(a.recordedAt || 0).getTime();
      const timeB = new Date(b.recordedAt || 0).getTime();
      const msA = Number(a.durationMs || 0);
      const msB = Number(b.durationMs || 0);
      const statusA = Number(a.statusCode || 0);
      const statusB = Number(b.statusCode || 0);
      const requesterA = String(a.requester || "").toLocaleLowerCase("pt-BR");
      const requesterB = String(b.requester || "").toLocaleLowerCase("pt-BR");
      if (sortKey === "timeAsc") return timeA - timeB || msB - msA;
      if (sortKey === "msDesc") return msB - msA || timeB - timeA;
      if (sortKey === "msAsc") return msA - msB || timeB - timeA;
      if (sortKey === "requesterAsc") {
        return requesterA.localeCompare(requesterB, "pt-BR", { sensitivity: "base" }) || timeB - timeA;
      }
      if (sortKey === "statusDesc") return statusB - statusA || timeB - timeA;
      return timeB - timeA || msB - msA;
    });
    return rows;
  }

  function normalizeLogFilterList(values, allowed = null) {
    const list = Array.isArray(values) ? values : String(values || "").split(",");
    const normalized = [...new Set(list.map((value) => String(value).trim()).filter(Boolean))];
    return allowed ? normalized.filter((value) => allowed.includes(value)) : normalized;
  }

  function applyLogRoutePrefs() {
    if (OC.currentRoute?.tab !== "logs") return;
    const query = OC.currentRoute.query || {};
    const filters = OC.monitorState.logFilters;
    if (query.q != null) filters.q = query.q;
    if (query.levels != null) filters.levels = normalizeLogFilterList(query.levels, LOG_LEVELS);
    if (query.services != null) filters.services = normalizeLogFilterList(query.services);
    if (query.streams != null) filters.streams = normalizeLogFilterList(query.streams);
    if (query.period && ["15m", "1h", "6h", "24h", "custom"].includes(query.period)) {
      filters.period = query.period;
    }
    if (query.since != null) {
      filters.since = query.since;
      filters.period = "custom";
    }
    if (query.until != null) filters.until = query.until;
    if (query.order && ["asc", "desc"].includes(query.order)) filters.order = query.order;
    if (query.envs) {
      const envs = normalizeLogFilterList(query.envs.toUpperCase(), OC.ENV_ORDER);
      if (envs.length) OC.monitorState.selectedEnvs = envs;
    }
  }

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
      if (win && ["1h", "6h", "24h", "7d"].includes(win)) OC.monitorState.apiWindow = win;
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
      if (pat != null) {
        OC.monitorState.logsPattern = pat;
      }
    } catch {
      /* ignore */
    }
    try {
      const filters = JSON.parse(localStorage.getItem(STORAGE_LOGS_FILTERS) || "null");
      if (filters && typeof filters === "object") {
        OC.monitorState.logFilters = {
          ...DEFAULT_LOG_FILTERS,
          ...filters,
          levels: normalizeLogFilterList(filters.levels, LOG_LEVELS),
          services: normalizeLogFilterList(filters.services),
          streams: normalizeLogFilterList(filters.streams),
        };
      } else if (OC.monitorState.logsPattern) {
        OC.monitorState.logFilters.q = OC.monitorState.logsPattern;
      }
      OC.monitorState.logsFollowing = localStorage.getItem(STORAGE_LOGS_FOLLOWING) !== "0";
    } catch {
      /* ignore */
    }
    if (OC.currentRoute?.tab) OC.monitorState.activeTab = OC.currentRoute.tab;
    applyLogRoutePrefs();
  }

  function savePrefs() {
    localStorage.setItem(STORAGE_ENVS, JSON.stringify(OC.monitorState.selectedEnvs));
    localStorage.setItem(STORAGE_CATS, JSON.stringify(OC.monitorState.categories));
    localStorage.setItem(STORAGE_FILTERS, JSON.stringify(OC.monitorState.eventFilters));
    localStorage.setItem(STORAGE_API_WINDOW, OC.monitorState.apiWindow || "6h");
    localStorage.setItem(STORAGE_LATENCY_WINDOW, OC.monitorState.latencyWindow || "24h");
    localStorage.setItem(STORAGE_LOGS_PATTERN, OC.monitorState.logFilters?.q ?? "");
    localStorage.setItem(STORAGE_LOGS_FILTERS, JSON.stringify(OC.monitorState.logFilters || DEFAULT_LOG_FILTERS));
    localStorage.setItem(STORAGE_LOGS_FOLLOWING, OC.monitorState.logsFollowing ? "1" : "0");
  }

  function syncLogFiltersToUrl() {
    if (OC.currentRoute?.view !== "monitoring" || OC.monitorState.activeTab !== "logs") return;
    const filters = OC.monitorState.logFilters || DEFAULT_LOG_FILTERS;
    const query = { ...(OC.currentRoute.query || {}) };
    ["q", "levels", "services", "streams", "period", "since", "until", "order", "envs"].forEach(
      (key) => delete query[key]
    );
    if (filters.q) query.q = filters.q;
    if (filters.levels.length) query.levels = filters.levels.join(",");
    if (filters.services.length) query.services = filters.services.join(",");
    if (filters.streams.length) query.streams = filters.streams.join(",");
    if (filters.period !== "24h") query.period = filters.period;
    if (filters.period === "custom" && filters.since) query.since = filters.since;
    if (filters.period === "custom" && filters.until) query.until = filters.until;
    if (filters.order !== "asc") query.order = filters.order;
    if (OC.monitorState.selectedEnvs.length !== OC.ENV_ORDER.length) {
      query.envs = OC.monitorState.selectedEnvs.join(",");
    }
    const path = OC.buildAppPath("monitoring", null, { tab: "logs", query });
    window.history.replaceState({ view: "monitoring" }, "", path);
    OC.currentRoute.query = query;
  }

  function setMonitoringBusy(isBusy, message = "") {
    const root = document.getElementById("view-monitoring");
    if (!root) return;

    root.classList.toggle("is-monitor-loading", isBusy);
    root.setAttribute("aria-busy", isBusy ? "true" : "false");
    root.querySelectorAll("[data-monitor-tab]").forEach((tab) => {
      tab.classList.toggle("is-loading", isBusy && tab.classList.contains("is-active"));
    });

    const current = root.querySelector(".monitor-loading-feedback");
    if (!isBusy) {
      current?.remove();
      return;
    }

    const panel = root.querySelector(".monitor-tab-panel");
    if (!panel) return;
    const feedback = current || document.createElement("div");
    if (!current) {
      feedback.className = "monitor-loading-feedback";
      feedback.setAttribute("role", "status");
      feedback.setAttribute("aria-live", "polite");
      feedback.innerHTML = '<span class="loading-spinner loading-spinner-sm" aria-hidden="true"></span><span></span>';
      panel.prepend(feedback);
    }
    const text = feedback.querySelector("span:last-child");
    if (text && (message || !text.textContent)) text.textContent = message || "Atualizando dados…";
  }

  function setSelectedMonitoringTab(root, activeTab) {
    root.querySelectorAll("[data-monitor-tab]").forEach((tab) => {
      const active = tab.getAttribute("data-monitor-tab") === activeTab;
      tab.classList.toggle("is-active", active);
      tab.setAttribute("aria-selected", active ? "true" : "false");
    });
  }

  function refreshFromMonitoringControl(message) {
    return OC.refreshMonitoring({
      force: true,
      showFeedback: true,
      feedbackMessage: message || "Aplicando filtros…",
    });
  }

  OC.setMonitoringBusy = setMonitoringBusy;

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
    // Sync é um indicador operacional independente. Não compõe saúde/disponibilidade.
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

  function formatChartMetricValue(value, format = "latency") {
    const number = Number(value);
    if (!Number.isFinite(number)) return "—";
    if (format === "integer") return Math.round(number).toLocaleString("pt-BR");
    if (format === "rate") {
      const rounded = Math.round(number * 10) / 10;
      return Number.isInteger(rounded) ? String(rounded) : rounded.toFixed(1);
    }
    return formatLatencyMs(number);
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
    const valueFormat = options.valueFormat || "latency";
    const valueSuffix = options.valueSuffix ?? " ms";
    const formatValue = (value) => formatChartMetricValue(value, valueFormat);
    const fingerprint = `${chartId}|${metricLabel}|${sloMs ?? ""}|${windowHours ?? ""}|${valueFormat}|${valueSuffix}|${chartPointsFingerprint(seriesByEnv)}`;
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
      legend += `<span class="monitor-legend-item"><span class="monitor-legend-swatch" style="background:${color}"></span>${env} · atual ${formatValue(last.v)}${OC.escapeHtml(valueSuffix)}</span>`;
    });

    spikes.sort((a, b) => b.v - a.v);
    spikes.slice(0, 8).forEach((sp) => {
      dots += `<circle class="monitor-spike-dot" cx="${x(sp.t).toFixed(1)}" cy="${y(sp.v).toFixed(1)}" r="4" fill="${sp.color}" data-env="${OC.escapeHtml(sp.env)}" data-iso="${OC.escapeHtml(sp.iso)}" data-ms="${formatLatencyMs(sp.v)}" />`;
    });

    const gridY = [0, 0.5, 1].map((f) => {
      const val = vMin + vSpan * f;
      const yy = y(val);
      return `<line x1="${pad.left}" y1="${yy}" x2="${width - pad.right}" y2="${yy}" class="monitor-grid-line" />
        <text x="${pad.left - 6}" y="${yy + 4}" text-anchor="end" class="monitor-axis-label">${formatValue(val)}</text>`;
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
      ? `${worst.env} em ${formatValue(worst.last)}${valueSuffix} no último ponto (média ${formatValue(worst.avg || 0)}${valueSuffix}).`
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
    <div class="monitor-chart-wrap" data-chart-id="${OC.escapeHtml(chartId)}" data-slo="${sloMs != null ? sloMs : ""}" data-value-format="${OC.escapeHtml(valueFormat)}" data-value-suffix="${OC.escapeHtml(valueSuffix)}">
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

  function buildEnvLatencyCharts(seriesByEnv, sloMs) {
    const hours = latencyWindowHours(OC.monitorState.latencyWindow || "24h");
    const selected = OC.monitorState.selectedEnvs?.length
      ? OC.monitorState.selectedEnvs
      : OC.ENV_ORDER;
    const envs = selected.filter((env) => seriesByEnv[env]);
    if (!envs.length) {
      return `<p class="monitor-empty monitor-empty-neutral">Sem séries de latência.</p>`;
    }
    return `<div class="monitor-latency-env-grid">${envs
      .map((env) => {
        const single = { [env]: seriesByEnv[env] };
        const points = seriesByEnv[env]?.points || [];
        const latest = points[points.length - 1];
        const average = seriesAvg(points);
        return `<article class="monitor-latency-env-card">
          <header class="monitor-latency-chart-head">
            <div>
              <h4 class="monitor-latency-env-title">${OC.escapeHtml(env)}</h4>
              <span>${points.length} amostra(s) no período</span>
            </div>
            <div class="monitor-latency-chart-stats">
              <span>Atual <strong>${latest ? `${formatLatencyMs(latest.v)} ms` : "—"}</strong></span>
              <span>Média <strong>${average != null ? `${formatLatencyMs(average)} ms` : "—"}</strong></span>
            </div>
          </header>
          ${chartHtml(single, `latência ${env}`, sloMs, { windowHours: hours, chartId: `lat-${env}` })}
        </article>`;
      })
      .join("")}</div>`;
  }

  function renderUptimeStatusBars(uptimeByEnv, { compact = false, drillable = false } = {}) {
    const envs = OC.monitorState.selectedEnvs?.length
      ? OC.monitorState.selectedEnvs
      : OC.ENV_ORDER;
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
    return `<div class="monitor-status-grid monitor-uptime-panel">${cards}</div>`;
  }

  function renderLatencyOverview(overviews, uptimeByEnv, slos) {
    if (!overviews?.length) {
      return `<p class="monitor-empty monitor-empty-neutral">Sem indicadores de latência para os ambientes selecionados.</p>`;
    }
    const cards = overviews.map((overview) => {
      const metric = overview.health || {};
      const level = ["ok", "warn", "critical"].includes(metric.level) ? metric.level : "unknown";
      const uptime = uptimeByEnv?.[overview.env]?.uptimePct;
      return `<article class="monitor-latency-summary-card is-${OC.escapeHtml(level)}">
        <header class="monitor-latency-summary-head">
          <div>
            <span class="monitor-latency-summary-env">${OC.escapeHtml(overview.env)}</span>
            <span class="monitor-latency-summary-caption">Endpoint de health</span>
          </div>
          <span class="monitor-latency-health is-${OC.escapeHtml(level)}"><span aria-hidden="true"></span>${OC.escapeHtml(metric.label || "Sem dados")}</span>
        </header>
        <dl class="monitor-latency-summary-metrics">
          <div><dt>Atual</dt><dd>${OC.escapeHtml(metric.latest || metric.value || "—")}</dd></div>
          <div><dt>p95 · 24h</dt><dd>${OC.escapeHtml(metric.p95 || "—")}</dd></div>
          <div><dt>Máxima · 24h</dt><dd>${OC.escapeHtml(metric.max || "—")}</dd></div>
          <div><dt>Disponibilidade · 7d</dt><dd>${uptime != null ? `${OC.escapeHtml(String(uptime))}%` : "—"}</dd></div>
        </dl>
        <footer class="monitor-latency-summary-foot">
          <span>Meta: p95 ≤ ${OC.escapeHtml(String(slos.healthP95WarnMs))} ms</span>
          ${metric.delta ? `<strong class="is-${OC.escapeHtml(level)}">${OC.escapeHtml(metric.delta)}</strong>` : ""}
        </footer>
      </article>`;
    }).join("");
    return `<div class="monitor-latency-summary-grid">${cards}</div>`;
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
    const tab = opts?.tab || "summary";
    const uptimeLabel = kpis.avgUptime != null ? `${kpis.avgUptime}%` : "—";
    const collectLabel = kpis.lastSampleAt
      ? OC.formatRelativeTime(kpis.lastSampleAt)
      : kpis.collectorLabel || "—";
    return OC.renderOpsHero({
      title: tab === "summary" ? "Monitoramento" : `Monitoramento · ${tabLabel}`,
      subtitle: compact
        ? tab === "latency"
          ? "Latência atual, tendência e disponibilidade dos ambientes selecionados."
          : "Indicadores operacionais dos ambientes selecionados."
        : "Visão consolidada da saúde dos ambientes e serviços.",
      compact,
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
    const uptimeEnvs = OC.monitorState.selectedEnvs?.length
      ? OC.monitorState.selectedEnvs
      : OC.ENV_ORDER;
    const uptimes = uptimeEnvs.map((env) => uptimeByEnv?.[env]?.uptimePct).filter(
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
    return `<div class="monitor-meta-wrap">
      <div class="monitor-meta-bar" aria-label="Atualização dos dados">
        <div class="monitor-meta-items">
          <span class="monitor-collector-state monitor-collector-${collectorClass}"><span class="monitor-collector-dot" aria-hidden="true"></span><strong>${OC.escapeHtml(collector.label || "Coleta indisponível")}</strong></span>
          <span>Atualizado <strong>${OC.escapeHtml(refreshed)}</strong></span>
          <span>Atualização automática <strong>${refreshSec}s</strong></span>
          ${collector.lastSampleAt ? `<span class="monitor-meta-muted">Última amostra ${OC.formatRelativeTime(collector.lastSampleAt)}</span>` : ""}
        </div>
        <button type="button" class="btn btn-secondary btn-sm" id="monitor-refresh-now">Atualizar dados</button>
      </div>
      ${renderStaleBanner(config)}
      ${recentErr ? `<div class="monitor-section-hint">Último erro de coleta: ${OC.escapeHtml(recentErr.message || recentErr)} (${OC.escapeHtml(OC.formatRelativeTime(recentErr.at))})</div>` : ""}
      ${warnings.length ? `<div class="global-error" role="alert">${warnings.map((w) => OC.escapeHtml(w)).join("<br>")}</div>` : ""}
    </div>`;
  }

  function activeFilterCount(tab) {
    const envDiff = OC.ENV_ORDER.length - OC.monitorState.selectedEnvs.length;
    const catOff = tab === "summary"
      ? CATEGORY_KEYS.filter((k) => !OC.monitorState.categories[k]).length
      : 0;
    const windowDiff = tab === "latency" && OC.monitorState.latencyWindow !== "24h" ? 1 : 0;
    const logFilters = OC.monitorState.logFilters || DEFAULT_LOG_FILTERS;
    const logDiff = tab === "logs"
      ? Number(!!logFilters.q) +
        logFilters.levels.length +
        logFilters.services.length +
        logFilters.streams.length +
        Number(logFilters.period !== "24h") +
        Number(logFilters.order !== "asc")
      : 0;
    return envDiff + catOff + windowDiff + logDiff;
  }

  OC.renderMonitoringFilters = function renderMonitoringFilters() {
    const activeTab = OC.monitorState.activeTab || "summary";
    const showCategories = activeTab === "summary";
    const showLatencyWindow = activeTab === "latency";
    const envPills = OC.ENV_ORDER.map((env) => {
      const active = OC.monitorState.selectedEnvs.includes(env);
      return `<button type="button" class="monitor-pill ${active ? "is-active" : ""}" data-monitor-env="${env}" aria-pressed="${active ? "true" : "false"}">${env}</button>`;
    }).join("");
    const catPills = CATEGORY_KEYS.map((key) => {
      const active = OC.monitorState.categories[key];
      return `<button type="button" class="monitor-pill ${active ? "is-active" : ""}" data-monitor-cat="${key}" aria-pressed="${active ? "true" : "false"}">${CATEGORY_LABELS[key]}</button>`;
    }).join("");
    const latencyWindow = OC.monitorState.latencyWindow || "24h";
    const latencyPills = ["1h", "6h", "24h", "7d"].map((value) => {
      const active = latencyWindow === value;
      return `<button type="button" class="monitor-pill ${active ? "is-active" : ""}" data-monitor-chip="latency-window" data-value="${value}" aria-pressed="${active ? "true" : "false"}">${value}</button>`;
    }).join("");
    const count = activeFilterCount(activeTab);
    const selectedCount = OC.monitorState.selectedEnvs.length;
    const scopeLabel = selectedCount === OC.ENV_ORDER.length
      ? "Todos os ambientes"
      : `${selectedCount} de ${OC.ENV_ORDER.length} ambientes`;
    const descriptions = {
      summary: "Escolha os ambientes e as categorias exibidas no resumo.",
      incidents: "Escolha os ambientes; severidade e período ficam na lista de incidentes.",
      latency: "Compare latência e disponibilidade usando o mesmo escopo.",
      syncs: "Escolha os ambientes que deseja comparar.",
      apis: "Escolha os ambientes que deseja analisar.",
      logs: "Escolha os ambientes que terão logs carregados.",
    };
    return `<section class="monitor-filters monitor-filter-panel" aria-label="Filtros do monitoramento">
      <header class="monitor-filter-header">
        <div>
          <h2 class="monitor-filter-title">Escopo da análise</h2>
          <p class="monitor-filter-description">${OC.escapeHtml(descriptions[activeTab] || descriptions.summary)}</p>
        </div>
        <div class="monitor-filter-actions">
          <span class="monitor-filter-count">${OC.escapeHtml(scopeLabel)}${showLatencyWindow ? ` · ${OC.escapeHtml(latencyWindowLabel(latencyWindow))}` : ""}</span>
          ${count ? `<button type="button" class="btn btn-ghost btn-sm" id="monitor-clear-filters">Restaurar padrão</button>` : ""}
        </div>
      </header>
      <div class="monitor-filter-body">
        <div class="monitor-filter-group">
          <span class="monitor-filter-label">Ambientes</span>
          <div class="monitor-pill-row">${envPills}</div>
        </div>
        ${showLatencyWindow ? `<div class="monitor-filter-group">
          <span class="monitor-filter-label">Período</span>
          <div class="monitor-pill-row">${latencyPills}</div>
        </div>` : ""}
        ${showCategories ? `<div class="monitor-filter-group monitor-filter-group--categories">
          <span class="monitor-filter-label">Categorias</span>
          <div class="monitor-pill-row">${catPills}</div>
        </div>` : ""}
      </div>
    </section>`;
  };

  function renderTabBar(activeTab) {
    const tabs = Object.entries(TAB_LABELS)
      .map(
        ([key, label]) =>
          `<button type="button" role="tab" class="monitor-tab ${key === activeTab ? "is-active" : ""}" data-monitor-tab="${key}" aria-selected="${key === activeTab ? "true" : "false"}" aria-controls="monitor-tab-panel">${OC.escapeHtml(label)}</button>`
      )
      .join("");
    return `<nav class="monitor-tabs" role="tablist" aria-label="Visões de monitoramento">${tabs}</nav>`;
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
      <p class="monitor-overview-note">Erros 5xx, deploy e p95 no período, mesmo já estabilizado. Sync é acompanhado separadamente.</p>
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
      // Sync possui painel/indicador próprios e não compõe os cartões de saúde.
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
    const sampleTotals = { samples: 0, success: 0, status4xx: 0, status5xx: 0 };
    let hasStructuredSampling = false;
    let normalRatePct = 10;
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
      const totals = data.totals || {};
      const samples = Number(totals.sampleCount ?? totals.requests ?? 0);
      const status4xx = Number(totals.status4xx ?? totals.errors4xx ?? 0);
      const status5xx = Number(totals.status5xx ?? totals.errors5xx ?? 0);
      const success = Number(
        totals.successSamples ??
        ((totals.status2xx != null || totals.status3xx != null)
          ? Number(totals.status2xx || 0) + Number(totals.status3xx || 0)
          : Math.max(0, samples - status4xx - status5xx))
      );
      sampleTotals.samples += samples;
      sampleTotals.success += success;
      sampleTotals.status4xx += status4xx;
      sampleTotals.status5xx += status5xx;
      if (data.sampling?.mode) {
        hasStructuredSampling = true;
        normalRatePct = Number(data.sampling.normalRatePct || normalRatePct);
      }

      const sourceRows = data.routeStats?.length ? data.routeStats : (data.slowRoutes || []);
      sourceRows.forEach((r) => {
        const key = `${String(r.method || "").toUpperCase()} ${r.route || ""}`.trim();
        const matched = !filterSet.size || filterSet.has(key);
        if (filterSet.size && !matched) return;
        const routeSamples = Number(r.sampleCount ?? r.count ?? 0);
        const route4xx = Number(r.status4xx ?? r.errors4xx ?? 0);
        const route5xx = Number(r.status5xx ?? r.errors5xx ?? 0);
        const routeSuccess = Number(
          r.successSamples ??
          ((r.status2xx != null || r.status3xx != null)
            ? Number(r.status2xx || 0) + Number(r.status3xx || 0)
            : Math.max(0, routeSamples - route4xx - route5xx))
        );
        const structured = r.successAvgMs !== undefined || r.clientErrorAvgMs !== undefined;
        const successAvgMs = structured
          ? r.successAvgMs
          : route4xx || route5xx
            ? null
            : r.avgMs;
        rows.push({
          env,
          key,
          method: r.method || "",
          route: r.route || "",
          samples: routeSamples,
          success: routeSuccess,
          status4xx: route4xx,
          status5xx: route5xx,
          successAvgMs,
          clientErrorAvgMs: structured ? r.clientErrorAvgMs : null,
          serverErrorAvgMs: structured ? r.serverErrorAvgMs : null,
          maxMs: r.maxMs,
          rank: route5xx > 0 ? 0 : routeSuccess > 0 ? 1 : route4xx > 0 ? 2 : 3,
        });
      });
    });
    if (instrumentationNotes.length && !rows.length) {
      return `<div class="monitor-empty-states">${instrumentationNotes.map((n) => `<p class="monitor-empty monitor-empty-neutral">${OC.escapeHtml(n)}</p>`).join("")}</div>`;
    }
    if (!rows.length) {
      return filterSet.size
        ? `<p class="monitor-empty monitor-empty-neutral">Nenhuma das rotas do instante selecionado aparece no ranking da janela atual.</p>`
        : `<p class="monitor-empty monitor-empty-ok">Nenhuma amostra de rota encontrada no período.</p>`;
    }
    rows.sort((a, b) => compareApiRouteRows(a, b, OC.monitorState.apiRouteSort || "priority"));
    const successPct = sampleTotals.samples
      ? (sampleTotals.success / sampleTotals.samples) * 100
      : 0;
    const clientErrorPct = sampleTotals.samples
      ? (sampleTotals.status4xx / sampleTotals.samples) * 100
      : 0;
    const latencyValue = (value) =>
      value == null ? "—" : Number(value) === 0 ? "< 1 ms" : `${formatLatencyMs(value)} ms`;
    const statusBadge = (kind, label, value) =>
      `<span class="monitor-api-http-badge is-${kind}"><span>${label}</span><strong>${formatAccessNumber(value)}</strong></span>`;
    const rowHtml = rows.map((row) => {
      const state = row.status5xx
        ? { kind: "critical", label: "Falha de servidor" }
        : row.success
          ? { kind: row.status4xx ? "mixed" : "healthy", label: row.status4xx ? "Sucesso + 4xx" : "Com sucesso" }
          : row.status4xx
            ? { kind: "warning", label: "Somente 4xx" }
            : { kind: "neutral", label: "Sem classificação" };
      return `<tr class="${filterSet.size ? "is-api-filter-hit" : ""} is-${state.kind} monitor-api-route-row is-clickable" data-api-route="${OC.escapeHtml(row.key)}" data-env="${OC.escapeHtml(row.env)}" data-method="${OC.escapeHtml(row.method)}" data-route="${OC.escapeHtml(row.route)}" data-sort-rank="${row.rank}" data-sort-max-ms="${Number(row.maxMs ?? -1)}" data-sort-success-avg-ms="${Number(row.successAvgMs ?? -1)}" data-sort-samples="${row.samples}" data-sort-status5xx="${row.status5xx}" data-sort-status4xx="${row.status4xx}" data-sort-route="${OC.escapeHtml(row.key.toLowerCase())}" role="button" tabindex="0" aria-label="Ver amostras de ${OC.escapeHtml(row.method)} ${OC.escapeHtml(row.route)}">
        <td><strong>${OC.escapeHtml(row.env)}</strong></td>
        <td class="monitor-api-route-cell">
          <code>${OC.escapeHtml(row.method)} ${OC.escapeHtml(row.route)}</code>
          <span class="monitor-api-route-state is-${state.kind}">${OC.escapeHtml(state.label)}</span>
        </td>
        <td><div class="monitor-api-latency-stack">
          <span class="is-success"><small>Sucesso</small><strong>${latencyValue(row.successAvgMs)}</strong></span>
          <span class="is-4xx"><small>4xx</small><strong>${latencyValue(row.clientErrorAvgMs)}</strong></span>
          <span class="is-5xx"><small>5xx</small><strong>${latencyValue(row.serverErrorAvgMs)}</strong></span>
        </div></td>
        <td class="monitor-api-max-cell">${latencyValue(row.maxMs)}</td>
        <td><div class="monitor-api-http-badges">
          ${statusBadge("ok", "2xx/3xx", row.success)}
          ${statusBadge("4xx", "4xx", row.status4xx)}
          ${statusBadge("5xx", "5xx", row.status5xx)}
        </div></td>
        <td class="monitor-api-sample-count"><strong>${formatAccessNumber(row.samples)}</strong><small>registros</small></td>
      </tr>`;
    }).join("");
    const policyNote = hasStructuredSampling
      ? "Erros HTTP e requisições lentas são registrados integralmente; respostas normais usam amostragem."
      : "O ambiente ainda usa o contrato antigo; reinicie o backend para separar a latência por resultado HTTP.";
    const concentrationWarning = clientErrorPct >= 25
      ? `<div class="monitor-api-sampling-alert" role="note"><strong>${clientErrorPct.toFixed(1)}% das amostras são 4xx.</strong> Esta proporção é amostral e não deve ser lida como taxa real de erro; use o bloco de volume exato acima.</div>`
      : "";
    return `<section class="monitor-api-diagnostics" aria-labelledby="monitor-api-diagnostics-title">
      <div class="monitor-api-diagnostics-head">
        <div><h5 id="monitor-api-diagnostics-title">Leitura das amostras</h5><p>${policyNote}</p></div>
        <span class="monitor-api-sampling-policy">Normais ~${formatAccessNumber(normalRatePct)}%</span>
      </div>
      <div class="monitor-api-sample-kpis">
        <article><span>Amostras</span><strong>${formatAccessNumber(sampleTotals.samples)}</strong><small>não equivale a acessos</small></article>
        <article class="is-success"><span>2xx/3xx</span><strong>${formatAccessNumber(sampleTotals.success)}</strong><small>${successPct.toFixed(1)}% das amostras</small></article>
        <article class="${sampleTotals.status4xx ? "is-warning" : ""}"><span>4xx</span><strong>${formatAccessNumber(sampleTotals.status4xx)}</strong><small>cliente, autenticação ou permissão</small></article>
        <article class="${sampleTotals.status5xx ? "is-critical" : ""}"><span>5xx</span><strong>${formatAccessNumber(sampleTotals.status5xx)}</strong><small>falhas de servidor</small></article>
      </div>
      ${concentrationWarning}
      <div class="monitor-api-data-card monitor-api-data-card--diagnostics">
        ${renderApiTableSortToolbar(
          "monitor-api-route-sort",
          API_ROUTE_SORT_OPTIONS,
          OC.monitorState.apiRouteSort || "priority",
          "monitor-api-table-toolbar--card-bar"
        )}
        <div class="monitor-api-data-card-table" id="monitor-api-routes-table">
          <table class="monitor-table monitor-api-route-diagnostics-table">
            <thead><tr><th>Ambiente</th><th>Rota</th><th>Latência por resultado</th><th>Máximo</th><th>Respostas amostradas</th><th>Amostras</th></tr></thead>
            <tbody>${rowHtml}</tbody>
          </table>
        </div>
      </div>
    </section>`;
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

  function apiRouteModalHost() {
    let host = document.getElementById("monitor-api-route-modal-host");
    if (!host) {
      host = document.createElement("div");
      host.id = "monitor-api-route-modal-host";
      host.setAttribute("aria-live", "polite");
      document.body.appendChild(host);
    }
    return host;
  }

  function closeApiRouteDetailModal() {
    document.body.classList.remove("monitor-api-route-modal-open");
    const host = document.getElementById("monitor-api-route-modal-host");
    if (host) host.innerHTML = "";
  }

  function renderApiRouteSampleStatusBadge(code) {
    const value = Number(code);
    if (value >= 500) return `<span class="monitor-api-route-modal-status is-5xx">${value}</span>`;
    if (value >= 400) return `<span class="monitor-api-route-modal-status is-4xx">${value}</span>`;
    return `<span class="monitor-api-route-modal-status is-ok">${value}</span>`;
  }

  function apiRouteSampleKey(sample) {
    return `${sample.recordedAt}|${sample.statusCode}|${sample.durationMs}|${sample.requester || ""}`;
  }

  function formatApiRouteSampleParamsValue(value) {
    if (value == null) return "";
    if (typeof value === "string") return value;
    try {
      return JSON.stringify(value, null, 2);
    } catch (_err) {
      return String(value);
    }
  }

  function renderApiRouteSampleParamsBlock(label, value) {
    if (value == null) return "";
    if (typeof value === "object" && !Array.isArray(value) && !Object.keys(value).length) return "";
    const text = formatApiRouteSampleParamsValue(value);
    if (!text) return "";
    return `<div class="monitor-api-route-modal-params-block">
      <span class="monitor-api-route-modal-params-label">${OC.escapeHtml(label)}</span>
      <pre class="monitor-api-route-modal-params-code">${OC.escapeHtml(text)}</pre>
    </div>`;
  }

  function renderApiRouteSampleParams(params) {
    if (!params) {
      return `<p class="monitor-api-route-modal-params-empty">Parâmetros não registrados nesta amostra. Novas requisições passam a registrar query string e corpo.</p>`;
    }
    const query = renderApiRouteSampleParamsBlock("Query string", params.query);
    const body = renderApiRouteSampleParamsBlock("Corpo", params.body);
    if (!query && !body) {
      return `<p class="monitor-api-route-modal-params-empty">Nenhum parâmetro nesta requisição.</p>`;
    }
    return `<div class="monitor-api-route-modal-params">${query}${body}</div>`;
  }

  function renderApiRouteModalSamplesTable(samples, sortKey) {
    const sorted = sortApiRouteModalSamples(samples, sortKey);
    return sorted
      .map((sample) => {
        const key = apiRouteSampleKey(sample);
        return `<tr class="monitor-api-route-modal-row is-clickable" data-sample-key="${OC.escapeHtml(key)}" tabindex="0" role="button" aria-expanded="false" aria-label="Ver parâmetros da requisição">
                <td class="monitor-api-route-modal-time">${OC.escapeHtml(OC.formatDate(sample.recordedAt))}</td>
                <td class="monitor-api-route-modal-requester">${OC.escapeHtml(sample.requester || "—")}</td>
                <td class="monitor-api-route-modal-status-cell">${renderApiRouteSampleStatusBadge(sample.statusCode)}</td>
                <td class="monitor-api-route-modal-ms">${formatLatencyMs(sample.durationMs)}</td>
              </tr>
              <tr class="monitor-api-route-modal-detail hidden" data-sample-detail="${OC.escapeHtml(key)}" hidden>
                <td colspan="4">${renderApiRouteSampleParams(sample.requestParams)}</td>
              </tr>`;
      })
      .join("");
  }

  function renderApiRouteDetailModalContent(data, meta, sortKey = "timeDesc") {
    const samplingNote =
      "Erros HTTP e requisições lentas são registrados integralmente; respostas normais usam amostragem.";
    if (data.error) {
      const hint =
        data.error === "Sub-rota invalida"
          ? " Reinicie o ops-console para carregar as rotas novas."
          : String(data.error).startsWith("HTTP 404")
            ? " O backend ainda nao expoe /ops-metrics/route-samples/. Reinicie o backend ou confira o acesso ao Postgres."
            : "";
      return `<p class="global-error">${OC.escapeHtml(data.error)}${OC.escapeHtml(hint)}</p>`;
    }
    const samples = data.samples || [];
    if (!samples.length) {
      return `<p class="monitor-empty monitor-empty-neutral">Nenhuma amostra no período selecionado.</p>
        <p class="monitor-meta-muted">${OC.escapeHtml(samplingNote)}</p>`;
    }
    return `<div class="monitor-api-data-card monitor-api-data-card--modal">
      ${renderApiTableSortToolbar(
        "monitor-api-route-modal-sort",
        API_ROUTE_MODAL_SORT_OPTIONS,
        sortKey,
        "monitor-api-table-toolbar--card-bar"
      )}
      <div class="monitor-api-data-card-table">
        <table class="monitor-table monitor-api-route-modal-table">
          <thead><tr>
            <th>Horário</th>
            <th>Quem requisitou</th>
            <th class="is-numeric">Status</th>
            <th class="is-numeric">ms</th>
          </tr></thead>
          <tbody id="monitor-api-route-modal-tbody">${renderApiRouteModalSamplesTable(samples, sortKey)}</tbody>
        </table>
      </div>
      <p class="monitor-api-data-card-foot">${OC.escapeHtml(samplingNote)} Clique em uma linha para ver os parâmetros da requisição.</p>
    </div>`;
  }

  function findApiRouteSampleDetail(tableWrap, key) {
    return (
      [...tableWrap.querySelectorAll("tr[data-sample-detail]")].find(
        (row) => row.getAttribute("data-sample-detail") === key
      ) || null
    );
  }

  function closeApiRouteSampleDetails(tableWrap, exceptRow = null) {
    tableWrap.querySelectorAll(".monitor-api-route-modal-row.is-expanded").forEach((row) => {
      if (exceptRow && row === exceptRow) return;
      row.classList.remove("is-expanded");
      row.setAttribute("aria-expanded", "false");
      const detail = findApiRouteSampleDetail(tableWrap, row.getAttribute("data-sample-key") || "");
      if (detail) {
        detail.hidden = true;
        detail.classList.add("hidden");
      }
    });
  }

  function toggleApiRouteSampleDetail(tableWrap, row) {
    const key = row.getAttribute("data-sample-key") || "";
    const detail = findApiRouteSampleDetail(tableWrap, key);
    if (!detail) return;
    const open = !row.classList.contains("is-expanded");
    closeApiRouteSampleDetails(tableWrap, open ? row : null);
    row.classList.toggle("is-expanded", open);
    row.setAttribute("aria-expanded", open ? "true" : "false");
    detail.hidden = !open;
    detail.classList.toggle("hidden", !open);
  }

  function bindApiRouteModalSort(modal, body, samples, meta) {
    const select = body.querySelector("#monitor-api-route-modal-sort");
    const tbody = body.querySelector("#monitor-api-route-modal-tbody");
    const tableWrap = body.querySelector(".monitor-api-data-card-table");
    if (!select || !tbody || select.dataset.bound === "1") return;
    select.dataset.bound = "1";
    select.addEventListener("change", () => {
      OC.monitorState.apiRouteModalSort = select.value || "timeDesc";
      tbody.innerHTML = renderApiRouteModalSamplesTable(samples, OC.monitorState.apiRouteModalSort);
    });
    if (!tableWrap || tableWrap.dataset.sampleExpandBound === "1") return;
    tableWrap.dataset.sampleExpandBound = "1";
    tableWrap.addEventListener("click", (event) => {
      const row = event.target.closest(".monitor-api-route-modal-row.is-clickable");
      if (!row || !tableWrap.contains(row)) return;
      toggleApiRouteSampleDetail(tableWrap, row);
    });
    tableWrap.addEventListener("keydown", (event) => {
      const row = event.target.closest(".monitor-api-route-modal-row.is-clickable");
      if (!row || !tableWrap.contains(row)) return;
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        toggleApiRouteSampleDetail(tableWrap, row);
      }
    });
  }

  async function openApiRouteDetailModal({ env, method, route }) {
    const win = OC.monitorState.apiWindow || "6h";
    const host = apiRouteModalHost();
    host.innerHTML = `<div id="monitor-api-route-modal" class="monitor-api-route-modal" aria-hidden="false">
      <div class="monitor-api-route-modal-backdrop" data-monitor-api-route-close></div>
      <div class="monitor-api-route-modal-card" role="dialog" aria-modal="true" aria-labelledby="monitor-api-route-modal-title">
        <header class="monitor-api-route-modal-header">
          <div class="monitor-api-route-modal-title-wrap">
            <div class="monitor-api-route-modal-title-row">
              <span class="monitor-api-route-modal-env">${OC.escapeHtml(env)}</span>
              <span class="monitor-api-route-modal-method">${OC.escapeHtml(method)}</span>
            </div>
            <h2 id="monitor-api-route-modal-title" class="monitor-api-route-modal-route">${OC.escapeHtml(route)}</h2>
            <p class="monitor-api-route-modal-meta" id="monitor-api-route-modal-meta" hidden></p>
          </div>
          <button type="button" class="drawer-close monitor-api-route-modal-close" data-monitor-api-route-close aria-label="Fechar">×</button>
        </header>
        <div class="monitor-api-route-modal-body" id="monitor-api-route-modal-body">
          <div class="loading-inline"><div class="loading-spinner loading-spinner-sm"></div> Carregando amostras…</div>
        </div>
      </div>
    </div>`;
    document.body.classList.add("monitor-api-route-modal-open");

    const modal = host.querySelector("#monitor-api-route-modal");
    const body = host.querySelector("#monitor-api-route-modal-body");
    modal?.querySelectorAll("[data-monitor-api-route-close]").forEach((el) => {
      el.addEventListener("click", closeApiRouteDetailModal);
    });
    modal?.querySelector(".monitor-api-route-modal-card")?.addEventListener("click", (event) => {
      event.stopPropagation();
    });
    const onKeyDown = (event) => {
      if (event.key === "Escape") {
        closeApiRouteDetailModal();
        document.removeEventListener("keydown", onKeyDown);
      }
    };
    document.addEventListener("keydown", onKeyDown);

    try {
      const data = await OC.fetchMonitoringJson(
        `/api/v1/monitoring/${encodeURIComponent(env)}/api-samples?window=${encodeURIComponent(win)}&method=${encodeURIComponent(method)}&route=${encodeURIComponent(route)}`,
        { samples: [], sampleCount: 0 }
      );
      if (body) {
        const meta = { env, method, route, window: win };
        const sortKey = OC.monitorState.apiRouteModalSort || "timeDesc";
        body.innerHTML = renderApiRouteDetailModalContent(data, meta, sortKey);
        bindApiRouteModalSort(modal, body, data.samples || [], meta);
        const metaEl = modal?.querySelector("#monitor-api-route-modal-meta");
        const sampleCount = data.sampleCount ?? (data.samples || []).length;
        if (metaEl && sampleCount) {
          metaEl.textContent = `Janela ${meta.window} · ${formatAccessNumber(sampleCount)} amostra(s)`;
          metaEl.hidden = false;
        }
      }
    } catch (err) {
      if (body) {
        body.innerHTML = `<p class="global-error">${OC.escapeHtml(err.message)}</p>`;
      }
    }
  }

  function bindApiRouteTableSort(root) {
    const select = root.querySelector("#monitor-api-route-sort");
    const tbody = root.querySelector("#monitor-api-routes-table tbody");
    if (!select || !tbody) return;
    if (select.dataset.bound !== "1") {
      select.dataset.bound = "1";
      select.addEventListener("change", () => {
        OC.monitorState.apiRouteSort = select.value || "priority";
        sortApiRouteRowsInDom(tbody, OC.monitorState.apiRouteSort);
      });
    }
    select.value = OC.monitorState.apiRouteSort || "priority";
    sortApiRouteRowsInDom(tbody, select.value);
  }

  function bindApiRouteDetailRows(root) {
    if (root.dataset.apiRouteModalBound === "1") return;
    root.dataset.apiRouteModalBound = "1";
    const activateRow = (row) => {
      const env = row.getAttribute("data-env");
      const method = row.getAttribute("data-method");
      const route = row.getAttribute("data-route");
      if (env && method && route) openApiRouteDetailModal({ env, method, route });
    };
    root.addEventListener("click", (event) => {
      const row = event.target.closest(".monitor-api-route-row.is-clickable");
      if (!row || !root.contains(row)) return;
      activateRow(row);
    });
    root.addEventListener("keydown", (event) => {
      const row = event.target.closest(".monitor-api-route-row.is-clickable");
      if (!row || !root.contains(row)) return;
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        activateRow(row);
      }
    });
  }

  function apiWindowHours(window) {
    if (window === "1h") return 1;
    if (window === "24h") return 24;
    if (window === "7d") return 168;
    return 6;
  }

  function formatAccessNumber(value) {
    const number = Number(value || 0);
    return Number.isFinite(number) ? Math.round(number).toLocaleString("pt-BR") : "0";
  }

  function selectedApiEnvs() {
    const selected = OC.monitorState.selectedEnvs?.length
      ? OC.monitorState.selectedEnvs
      : OC.ENV_ORDER;
    return selected.filter((env) => OC.ENV_ORDER.includes(env));
  }

  function aggregateApiTraffic(apiRoutes, envs) {
    const totals = {
      requests: 0,
      totalDurationMs: 0,
      maxMs: 0,
      peakRpm: 0,
      uniqueUsers: 0,
      status2xx: 0,
      status3xx: 0,
      status4xx: 0,
      status5xx: 0,
    };
    const rpmByTime = new Map();
    let available = false;
    let unavailable = 0;

    envs.forEach((env) => {
      const traffic = apiRoutes?.[env]?.traffic;
      if (!traffic || traffic.available === false) {
        unavailable += 1;
        return;
      }
      available = true;
      const current = traffic.totals || {};
      const requests = Number(current.requests || 0);
      totals.requests += requests;
      totals.totalDurationMs += requests * Number(current.avgMs || 0);
      totals.maxMs = Math.max(totals.maxMs, Number(current.maxMs || 0));
      totals.uniqueUsers += Number(current.uniqueUsers || 0);
      ["status2xx", "status3xx", "status4xx", "status5xx"].forEach((key) => {
        totals[key] += Number(current[key] || 0);
      });
      (traffic.points || []).forEach((point) => {
        rpmByTime.set(point.at, (rpmByTime.get(point.at) || 0) + Number(point.rpm || 0));
      });
    });

    totals.peakRpm = Math.max(0, ...rpmByTime.values());
    totals.avgMs = totals.requests ? totals.totalDurationMs / totals.requests : 0;
    totals.errorRatePct = totals.requests
      ? ((totals.status4xx + totals.status5xx) / totals.requests) * 100
      : 0;
    return { available, unavailable, totals };
  }

  function renderApiTrafficStatus(totals) {
    const total = Math.max(1, Number(totals.requests || 0));
    const ok = Number(totals.status2xx || 0) + Number(totals.status3xx || 0);
    const client = Number(totals.status4xx || 0);
    const server = Number(totals.status5xx || 0);
    const width = (value) => `${Math.max(0, Math.min(100, (value / total) * 100)).toFixed(2)}%`;
    return `<div class="monitor-access-status" aria-label="Distribuição por status HTTP">
      <div class="monitor-access-status-bar" aria-hidden="true">
        <span class="is-ok" style="width:${width(ok)}"></span>
        <span class="is-4xx" style="width:${width(client)}"></span>
        <span class="is-5xx" style="width:${width(server)}"></span>
      </div>
      <div class="monitor-access-status-legend">
        <span><i class="is-ok"></i>2xx/3xx <strong>${formatAccessNumber(ok)}</strong></span>
        <span><i class="is-4xx"></i>4xx <strong>${formatAccessNumber(client)}</strong></span>
        <span><i class="is-5xx"></i>5xx <strong>${formatAccessNumber(server)}</strong></span>
      </div>
    </div>`;
  }

  function renderApiTrafficRoutes(apiRoutes, envs) {
    const rows = [];
    envs.forEach((env) => {
      const total = Number(apiRoutes?.[env]?.traffic?.totals?.requests || 0);
      (apiRoutes?.[env]?.traffic?.topRoutes || []).forEach((route) => {
        const requests = Number(route.requests || 0);
        rows.push({ env, total, requests, ...route });
      });
    });
    rows.sort((a, b) => b.requests - a.requests);
    if (!rows.length) {
      return `<p class="monitor-empty monitor-empty-neutral">As rotas mais acessadas aparecerão após os primeiros minutos de coleta.</p>`;
    }
    return `<div class="monitor-table-wrap ops-table-wrap"><table class="monitor-table monitor-access-routes-table">
      <thead><tr><th>Ambiente</th><th>Rota</th><th>Acessos</th><th>Participação</th><th>Média ms</th><th>4xx</th><th>5xx</th></tr></thead>
      <tbody>${rows.slice(0, 15).map((row) => `<tr>
        <td>${OC.escapeHtml(row.env)}</td>
        <td><code>${OC.escapeHtml(row.method || "")} ${OC.escapeHtml(row.route || "")}</code></td>
        <td><strong>${formatAccessNumber(row.requests)}</strong></td>
        <td>${row.total ? ((row.requests / row.total) * 100).toFixed(1) : "0.0"}%</td>
        <td>${formatLatencyMs(row.avgMs)}</td>
        <td>${formatAccessNumber(row.errors4xx)}</td>
        <td>${formatAccessNumber(row.errors5xx)}</td>
      </tr>`).join("")}</tbody>
    </table></div>`;
  }

  function renderApiTrafficOverview(apiRoutes, envs, win, hours) {
    const aggregate = aggregateApiTraffic(apiRoutes, envs);
    const totals = aggregate.totals;
    const series = {};
    envs.forEach((env) => {
      const traffic = apiRoutes?.[env]?.traffic;
      if (!traffic || traffic.available === false) return;
      const leadingRoute = traffic.topRoutes?.[0];
      const leadingRouteLabel = leadingRoute
        ? `${leadingRoute.method || ""} ${leadingRoute.route || ""}`.trim()
        : "";
      series[env] = {
        windowFrom: traffic.since,
        windowTo: traffic.until,
        lastSampleAt: traffic.until,
        points: (traffic.points || []).map((point) => ({
          t: point.at,
          v: Number(point.rpm || 0),
          labels: {
            requests: Number(point.requests || 0),
            avgMs: Number(point.avgMs || 0),
            status4xx: Number(point.status4xx || 0),
            status5xx: Number(point.status5xx || 0),
            leadingRoute: leadingRouteLabel,
          },
        })),
      };
    });
    const graph = totals.requests
      ? buildSvgLineChart(series, "Volume de acessos por minuto", null, {
          windowHours: hours,
          chartId: "api-access-volume",
          valueFormat: "rate",
          valueSuffix: " req/min",
        }).html
      : `<p class="monitor-empty monitor-empty-neutral">Nenhum acesso contabilizado em ${win}. A coleta exata começa a preencher o gráfico após a implantação desta versão.</p>`;
    const availability = !aggregate.available
      ? `<div class="monitor-section-hint is-warning">A agregação exata ainda não está disponível nos ambientes selecionados. Verifique a migration 0003 e reinicie os backends.</div>`
      : aggregate.unavailable
        ? `<div class="monitor-section-hint is-warning">${aggregate.unavailable} ambiente(s) ainda não fornecem a série exata.</div>`
        : "";

    return `<section class="monitor-access-overview" aria-labelledby="monitor-access-title">
      <header class="monitor-access-head">
        <div>
          <h4 id="monitor-access-title">Volume de acessos</h4>
          <p>Contagem exata agregada no backend · ${OC.escapeHtml(envs.join(", "))}</p>
        </div>
        <span class="monitor-access-source ${aggregate.available ? "" : "is-unavailable"}"><span aria-hidden="true"></span>${aggregate.available ? "Contagem real" : "Aguardando coleta"}</span>
      </header>
      ${availability}
      <div class="monitor-access-kpis">
        <article><span>Total de acessos</span><strong>${formatAccessNumber(totals.requests)}</strong><small>janela ${OC.escapeHtml(win)}</small></article>
        <article><span>Pico de tráfego</span><strong>${formatChartMetricValue(totals.peakRpm, "rate")}</strong><small>requisições/min</small></article>
        <article><span>Usuários autenticados</span><strong>${formatAccessNumber(totals.uniqueUsers)}</strong><small>${envs.length > 1 ? "soma por ambiente" : "únicos no período"}</small></article>
        <article class="${totals.errorRatePct > 2 ? "is-warning" : ""}"><span>Taxa de erro</span><strong>${totals.errorRatePct.toFixed(2)}%</strong><small>respostas 4xx + 5xx</small></article>
      </div>
      <div class="monitor-access-chart">${graph}</div>
      ${renderApiTrafficStatus(totals)}
      <div class="monitor-access-routes">
        <div class="monitor-access-subhead"><h5>Rotas mais acessadas</h5><span>contagem real no período</span></div>
        ${renderApiTrafficRoutes(apiRoutes, envs)}
      </div>
    </section>`;
  }

  function renderApisSection(apiRoutes, apiSeries, slos) {
    const win = OC.monitorState.apiWindow || "6h";
    const hours = apiWindowHours(win);
    const sloMs = slos?.healthP95WarnMs ?? 2000;
    const focus = selectedApiEnvs();
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
        { value: "7d", label: "7d" },
      ],
      extra: `<span class="monitor-meta-muted">Atualização a cada 15s · ${OC.escapeHtml(focus.join(", "))} · SLO ${sloMs} ms</span>`,
    })}
    ${renderApiTrafficOverview(apiRoutes, focus, win, hours)}
    <div class="monitor-access-subhead monitor-access-subhead--latency"><h4>Desempenho das APIs</h4><span>latência média e picos acima do SLO</span></div>
    <div class="monitor-latency-env-grid">${charts}</div>
    <div class="monitor-access-subhead monitor-access-subhead--diagnostics"><h4>Diagnóstico por rota</h4><span>latência de sucesso separada de respostas 4xx e 5xx</span></div>
    <p class="monitor-section-hint" id="monitor-api-routes-filter-note" hidden></p>
    <div id="monitor-api-routes-wrap">${renderApiRoutesTable(routesFocus)}</div>
    <p class="monitor-meta-muted">Dica: clique em uma rota para ver amostras individuais, ou em um ponto do gráfico para filtrar por instante.</p>`;
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

  function logIcon(name) {
    const paths = {
      search: '<circle cx="11" cy="11" r="7"></circle><path d="m20 20-4-4"></path>',
      pause: '<rect x="6" y="5" width="4" height="14" rx="1"></rect><rect x="14" y="5" width="4" height="14" rx="1"></rect>',
      play: '<path d="m8 5 11 7-11 7z"></path>',
      follow: '<path d="M12 5v14M6 13l6 6 6-6"></path>',
      copy: '<rect x="8" y="8" width="11" height="11" rx="2"></rect><path d="M16 8V6a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v8a2 2 0 0 0 2 2h2"></path>',
      related: '<path d="M4 7h10M4 12h16M4 17h10"></path><path d="m16 5 3 2-3 2M14 15l-3 2 3 2"></path>',
      chevron: '<path d="m9 18 6-6-6-6"></path>',
      download: '<path d="M12 3v12m0 0 5-5m-5 5-5-5"></path><path d="M5 21h14"></path>',
    };
    return `<svg class="monitor-log-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${paths[name] || ""}</svg>`;
  }

  function logPeriodLabel(filters) {
    if (filters.period === "custom") {
      const from = filters.since ? OC.formatDate(filters.since) : "início aberto";
      const to = filters.until ? OC.formatDate(filters.until) : "agora";
      return `${from} até ${to}`;
    }
    return { "15m": "Últimos 15 min", "1h": "Última hora", "6h": "Últimas 6 h", "24h": "Últimas 24 h" }[
      filters.period
    ] || "Últimas 24 h";
  }

  function periodStartIso(period) {
    const millis = { "15m": 15 * 60_000, "1h": 60 * 60_000, "6h": 6 * 60 * 60_000, "24h": 24 * 60 * 60_000 }[
      period
    ];
    return millis ? new Date(Date.now() - millis).toISOString() : "";
  }

  function toDateTimeLocal(iso) {
    if (!iso) return "";
    const date = new Date(iso);
    if (Number.isNaN(date.getTime())) return "";
    const local = new Date(date.getTime() - date.getTimezoneOffset() * 60_000);
    return local.toISOString().slice(0, 16);
  }

  function collectLogViewData(logsByEnv) {
    const services = new Set();
    const streams = new Set();
    const sources = new Set();
    const lines = [];
    const errors = [];
    let hasMore = false;
    let refreshedAt = "";
    Object.entries(logsByEnv || {}).forEach(([env, data]) => {
      if (data?.error) errors.push({ env, message: data.error });
      (data?.facets?.services || []).forEach((value) => value && services.add(value));
      (data?.facets?.streams || []).forEach((value) => value && streams.add(value));
      (data?.sources || []).forEach((source) => {
        if (source?.service) services.add(source.service);
        if (source?.stream) streams.add(source.stream);
        if (source?.exists) sources.add(`${env}:${source.service}:${source.stream}:${source.file}`);
      });
      (data?.lines || []).forEach((line) => lines.push({ ...line, environment: env }));
      hasMore = hasMore || !!data?.hasMore;
      if ((data?.refreshedAt || "") > refreshedAt) refreshedAt = data.refreshedAt;
    });
    const seen = new Set();
    const chronological = lines
      .filter((line) => {
        const key = `${line.environment}:${line.key || `${line.logged_at}|${line.service}|${line.line}`}`;
        if (seen.has(key)) return false;
        seen.add(key);
        return true;
      })
      .sort((a, b) => {
        const cmp = String(a.logged_at || "").localeCompare(String(b.logged_at || ""));
        return cmp || String(a.key || "").localeCompare(String(b.key || ""));
      });
    const capped = OC.monitorState.logHistoryMode
      ? chronological.slice(0, 1000)
      : chronological.slice(-1000);
    const unique = (OC.monitorState.logFilters?.order || "asc") === "desc"
      ? [...capped].reverse()
      : capped;
    const byLevel = Object.fromEntries(LOG_LEVELS.map((level) => [level, 0]));
    unique.forEach((line) => {
      byLevel[LOG_LEVELS.includes(line.level) ? line.level : "OTHER"] += 1;
    });
    return {
      lines: unique,
      services: [...services].sort(),
      streams: [...streams].sort(),
      sourceCount: sources.size,
      errors,
      hasMore,
      refreshedAt,
      byLevel,
    };
  }

  function renderLogSummary(view) {
    const cards = [
      { label: "Registros carregados", value: view.lines.length, tone: "neutral" },
      { label: "Erros", value: view.byLevel.ERROR, tone: "error" },
      { label: "Avisos", value: view.byLevel.WARN, tone: "warn" },
      { label: "Fontes disponíveis", value: view.sourceCount, tone: "ok" },
    ];
    return `<div class="monitor-log-summary" aria-label="Resumo dos logs">${cards
      .map(
        (card) => `<article class="monitor-log-summary-card is-${card.tone}">
          <span>${OC.escapeHtml(card.label)}</span><strong>${OC.escapeHtml(card.value)}</strong>
        </article>`
      )
      .join("")}</div>`;
  }

  function renderLogsToolbar(logsByEnv) {
    const filters = OC.monitorState.logFilters || DEFAULT_LOG_FILTERS;
    const view = collectLogViewData(logsByEnv);
    const serviceValues = [...new Set([...view.services, ...filters.services])].sort();
    const streamValues = [...new Set([...view.streams, ...filters.streams])].sort();
    const selectedService = filters.services[0] || "";
    const selectedStream = filters.streams[0] || "";
    const activeChips = [];
    if (filters.q) activeChips.push({ key: "q", label: `Texto: ${filters.q}` });
    filters.levels.forEach((value) => activeChips.push({ key: `level:${value}`, label: value }));
    if (selectedService) activeChips.push({ key: "services", label: `Serviço: ${selectedService}` });
    if (selectedStream) activeChips.push({ key: "streams", label: `Stream: ${selectedStream}` });
    if (filters.period !== "24h") activeChips.push({ key: "period", label: logPeriodLabel(filters) });
    const customFields = filters.period === "custom"
      ? `<div class="monitor-log-custom-period">
          <label>De<input type="datetime-local" id="monitor-log-since" value="${OC.escapeHtml(toDateTimeLocal(filters.since))}"></label>
          <label>Até<input type="datetime-local" id="monitor-log-until" value="${OC.escapeHtml(toDateTimeLocal(filters.until))}"></label>
          <button type="button" class="btn btn-secondary btn-sm" id="monitor-log-apply-period">Aplicar período</button>
        </div>`
      : "";
    return `${renderLogSummary(view)}
      <div class="monitor-log-toolbar">
        <form class="monitor-log-search" id="monitor-log-search-form" role="search">
          ${logIcon("search")}
          <label class="visually-hidden" for="monitor-log-search">Buscar nos logs</label>
          <input id="monitor-log-search" type="search" value="${OC.escapeHtml(filters.q)}" placeholder="Buscar mensagem, código ou identificador…" autocomplete="off">
          <kbd>Enter</kbd>
        </form>
        <label class="monitor-log-select">Período
          <select id="monitor-log-period">
            ${[["15m", "15 minutos"], ["1h", "1 hora"], ["6h", "6 horas"], ["24h", "24 horas"], ["custom", "Personalizado"]]
              .map(([value, label]) => `<option value="${value}" ${filters.period === value ? "selected" : ""}>${label}</option>`)
              .join("")}
          </select>
        </label>
        <label class="monitor-log-select">Serviço
          <select id="monitor-log-service"><option value="">Todos</option>${serviceValues
            .map((value) => `<option value="${OC.escapeHtml(value)}" ${selectedService === value ? "selected" : ""}>${OC.escapeHtml(value)}</option>`)
            .join("")}</select>
        </label>
        <label class="monitor-log-select">Stream
          <select id="monitor-log-stream"><option value="">Todos</option>${streamValues
            .map((value) => `<option value="${OC.escapeHtml(value)}" ${selectedStream === value ? "selected" : ""}>${OC.escapeHtml(value)}</option>`)
            .join("")}</select>
        </label>
        <label class="monitor-log-select">Ordem
          <select id="monitor-log-order"><option value="asc" ${filters.order === "asc" ? "selected" : ""}>Mais antigos primeiro</option><option value="desc" ${filters.order === "desc" ? "selected" : ""}>Mais recentes primeiro</option></select>
        </label>
      </div>
      ${customFields}
      <div class="monitor-log-levels" aria-label="Níveis do log">
        <span>Nível</span>
        ${LOG_LEVELS.map((level) => {
          const active = filters.levels.includes(level);
          return `<button type="button" class="monitor-log-level is-${level.toLowerCase()} ${active ? "is-active" : ""}" data-log-level="${level}" aria-pressed="${active}">${level}</button>`;
        }).join("")}
      </div>
      ${activeChips.length
        ? `<div class="monitor-log-active-filters"><span>Filtros ativos</span>${activeChips
            .map((chip) => `<button type="button" data-log-remove-filter="${OC.escapeHtml(chip.key)}">${OC.escapeHtml(chip.label)} <span aria-hidden="true">×</span></button>`)
            .join("")}<button type="button" class="monitor-log-clear" id="monitor-log-clear-filters">Limpar tudo</button></div>`
        : ""}
      <div class="monitor-log-actions">
        <div class="monitor-log-live-state" role="status">
          <span class="monitor-log-live-dot ${OC.monitorState.logsPaused ? "is-paused" : ""}"></span>
          <strong>${OC.monitorState.logsPaused ? "Atualização pausada" : "Atualizando a cada 15 s"}</strong>
          <span>${OC.escapeHtml(logPeriodLabel(filters))}${view.refreshedAt ? ` · atualizado ${OC.escapeHtml(OC.formatRelativeTime(view.refreshedAt))}` : ""}</span>
        </div>
        <div class="monitor-log-action-buttons">
          <button type="button" class="btn btn-secondary btn-sm" id="monitor-log-toggle-pause">${logIcon(OC.monitorState.logsPaused ? "play" : "pause")}${OC.monitorState.logsPaused ? "Retomar" : "Pausar"}</button>
          <button type="button" class="btn btn-secondary btn-sm ${OC.monitorState.logsFollowing ? "is-active" : ""}" id="monitor-log-toggle-follow" aria-pressed="${OC.monitorState.logsFollowing}">${logIcon("follow")}Seguir novos</button>
          <label class="monitor-log-export-env"><span class="visually-hidden">Ambiente para exportar</span><select id="monitor-log-export-env">${OC.monitorState.selectedEnvs
            .map((env) => `<option value="${env}">${env}</option>`)
            .join("")}</select></label>
          <button type="button" class="btn btn-secondary btn-sm" data-log-export="csv">${logIcon("download")}CSV</button>
          <button type="button" class="btn btn-secondary btn-sm" data-log-export="json">JSON</button>
        </div>
      </div>`;
  }

  function prettyLogLine(line) {
    const raw = String(line || "");
    if (!raw.trim().startsWith("{") && !raw.trim().startsWith("[")) return raw;
    try {
      return JSON.stringify(JSON.parse(raw), null, 2);
    } catch {
      return raw;
    }
  }

  function renderLogEntry(line) {
    const key = `${line.environment}:${line.key || `${line.logged_at}|${line.service}|${line.line}`}`;
    const expanded = OC.monitorState.logExpanded?.has(key);
    const level = LOG_LEVELS.includes(line.level) ? line.level : "OTHER";
    const sourceDetail = [line.source, line.file].filter(Boolean).join(" · ");
    return `<article class="monitor-log-entry is-${level.toLowerCase()} ${expanded ? "is-expanded" : ""}" data-log-entry="${OC.escapeHtml(key)}">
      <button type="button" class="monitor-log-entry-main" data-log-toggle="${OC.escapeHtml(key)}" aria-expanded="${expanded ? "true" : "false"}">
        <span class="monitor-log-time"><time datetime="${OC.escapeHtml(line.logged_at || "")}" title="${OC.escapeHtml(OC.formatDate(line.logged_at))}">${OC.escapeHtml(OC.formatDate(line.logged_at))}</time></span>
        <span class="monitor-log-env-badge is-${String(line.environment || "").toLowerCase()}">${OC.escapeHtml(line.environment || "—")}</span>
        <span class="monitor-log-level-badge">${OC.escapeHtml(level)}</span>
        <span class="monitor-log-origin">${OC.escapeHtml(line.service || "—")}<small>${OC.escapeHtml(line.stream || "—")}</small></span>
        <code class="monitor-log-message">${OC.escapeHtml(line.line || "")}</code>
        ${logIcon("chevron")}
      </button>
      ${expanded
        ? `<div class="monitor-log-detail">
            <div class="monitor-log-detail-meta"><span><strong>Horário</strong>${OC.escapeHtml(line.logged_at || "—")}</span><span><strong>Origem</strong>${OC.escapeHtml(sourceDetail || "Não informada")}</span><span><strong>Chave</strong>${OC.escapeHtml(line.key || "—")}</span></div>
            <pre><code>${OC.escapeHtml(prettyLogLine(line.line))}</code></pre>
            <div class="monitor-log-detail-actions">
              <button type="button" class="btn btn-secondary btn-sm" data-log-copy="line" data-log-key="${OC.escapeHtml(key)}">${logIcon("copy")}Copiar linha</button>
              <button type="button" class="btn btn-secondary btn-sm" data-log-copy="detail" data-log-key="${OC.escapeHtml(key)}">${logIcon("copy")}Copiar detalhes</button>
              <button type="button" class="btn btn-ghost btn-sm" data-log-related="${OC.escapeHtml(key)}">${logIcon("related")}Buscar semelhantes</button>
            </div>
          </div>`
        : ""}
    </article>`;
  }

  function renderLogsViewer(logsByEnv) {
    const view = collectLogViewData(logsByEnv);
    const notices = view.errors.length
      ? `<div class="monitor-log-errors" role="alert">${view.errors
          .map((error) => `<span><strong>${OC.escapeHtml(error.env)}</strong>: ${OC.escapeHtml(error.message)}</span>`)
          .join("")}<button type="button" class="btn btn-ghost btn-sm" id="monitor-log-retry">Tentar novamente</button></div>`
      : "";
    const older = view.hasMore
      ? `<button type="button" class="monitor-log-load-older" id="monitor-log-load-older">Carregar registros anteriores</button>`
      : `<span class="monitor-log-history-start">Início dos registros disponíveis neste período</span>`;
    const content = view.lines.length
      ? view.lines.map(renderLogEntry).join("")
      : `<div class="monitor-log-empty"><span aria-hidden="true">⌁</span><strong>Nenhum log encontrado</strong><p>Ajuste o período ou remova alguns filtros para ampliar a busca.</p><button type="button" class="btn btn-secondary btn-sm" id="monitor-log-empty-clear">Limpar filtros</button></div>`;
    return `${notices}
      <div class="monitor-log-console-shell">
        <div class="monitor-log-console-head"><span>Console</span><span>${OC.escapeHtml(view.lines.length)} linha(s) carregada(s)</span></div>
        <div class="monitor-log-history-control">${older}</div>
        <div class="monitor-log-console" id="monitor-log-console" tabindex="0" aria-label="Linhas de log em ordem cronológica">${content}</div>
        ${OC.monitorState.logNewCount > 0 ? `<button type="button" class="monitor-log-new" id="monitor-log-show-new">${OC.escapeHtml(OC.monitorState.logNewCount)} novo(s) log(s) ↓</button>` : ""}
      </div>`;
  }

  function buildLogRequestParams(options = {}) {
    const filters = OC.monitorState.logFilters || DEFAULT_LOG_FILTERS;
    const params = new URLSearchParams({
      limit: String(options.limit || 200),
      order: filters.order || "asc",
    });
    const since = filters.period === "custom" ? filters.since : periodStartIso(filters.period);
    if (options.incrementalSince) params.set("since", options.incrementalSince);
    else if (since) params.set("since", since);
    if (filters.period === "custom" && filters.until) params.set("until", filters.until);
    if (filters.q) params.set("q", filters.q);
    if (filters.levels.length) params.set("levels", filters.levels.join(","));
    if (filters.services.length) params.set("services", filters.services.join(","));
    if (filters.streams.length) params.set("streams", filters.streams.join(","));
    if (options.cursor) params.set("cursor", options.cursor);
    return params;
  }

  function logFilterFingerprint() {
    const filters = OC.monitorState.logFilters || DEFAULT_LOG_FILTERS;
    return JSON.stringify({
      ...filters,
      levels: [...filters.levels].sort(),
      services: [...filters.services].sort(),
      streams: [...filters.streams].sort(),
    });
  }

  function mergeLogPayload(previous, incoming, options = {}) {
    if (!previous || previous._filterFingerprint !== incoming._filterFingerprint) return incoming;
    const seen = new Set();
    const mergedLines = [];
    [...(previous.lines || []), ...(incoming.lines || [])].forEach((line) => {
      const key = line.key || `${line.logged_at}|${line.service}|${line.stream}|${line.line}`;
      if (seen.has(key)) return;
      seen.add(key);
      mergedLines.push(line);
    });
    mergedLines.sort((a, b) => String(a.logged_at || "").localeCompare(String(b.logged_at || "")));
    const lines = options.older || options.preserveHistory
      ? mergedLines.slice(0, 1000)
      : mergedLines.slice(-1000);
    const union = (left, right) => [...new Set([...(left || []), ...(right || [])])].sort();
    return {
      ...previous,
      ...incoming,
      lines,
      count: lines.length,
      facets: {
        levels: LOG_LEVELS,
        services: union(previous.facets?.services, incoming.facets?.services),
        streams: union(previous.facets?.streams, incoming.facets?.streams),
      },
      sources: [...(previous.sources || []), ...(incoming.sources || [])].filter(
        (source, index, all) =>
          all.findIndex(
            (candidate) =>
              `${candidate.service}|${candidate.stream}|${candidate.file}` ===
              `${source.service}|${source.stream}|${source.file}`
          ) === index
      ),
      hasMore: options.older ? incoming.hasMore : previous.hasMore,
      nextCursor: options.older ? incoming.nextCursor : previous.nextCursor,
    };
  }

  function resetLogFilters() {
    OC.monitorState.logFilters = { ...DEFAULT_LOG_FILTERS };
    OC.monitorState.logsPattern = "";
    OC.monitorState.logNewCount = 0;
    OC.monitorState.logHistoryMode = false;
    savePrefs();
    syncLogFiltersToUrl();
  }

  function refreshLogsWithFilters(message) {
    OC.monitorState.logNewCount = 0;
    OC.monitorState.logHistoryMode = false;
    savePrefs();
    syncLogFiltersToUrl();
    return refreshFromMonitoringControl(message || "Aplicando filtros de logs…");
  }

  function findRenderedLogLine(key) {
    const view = collectLogViewData(OC.monitorState.payload?.logs || {});
    return view.lines.find((line) => {
      const lineKey = `${line.environment}:${line.key || `${line.logged_at}|${line.service}|${line.line}`}`;
      return lineKey === key;
    });
  }

  async function copyLogText(text, button) {
    try {
      await navigator.clipboard.writeText(String(text || ""));
      if (button) {
        const original = button.innerHTML;
        button.textContent = "Copiado";
        window.setTimeout(() => {
          if (button.isConnected) button.innerHTML = original;
        }, 1400);
      }
    } catch {
      window.alert("Não foi possível copiar o conteúdo.");
    }
  }

  function rerenderLogsPreservingScroll(options = {}) {
    const consoleEl = document.getElementById("monitor-log-console");
    const scrollTop = consoleEl?.scrollTop || 0;
    const scrollHeight = consoleEl?.scrollHeight || 0;
    OC.renderMonitoringView(OC.monitorState.payload, { partial: true });
    window.requestAnimationFrame(() => {
      const next = document.getElementById("monitor-log-console");
      if (!next) return;
      if (options.prepended) next.scrollTop = scrollTop + Math.max(0, next.scrollHeight - scrollHeight);
      else next.scrollTop = scrollTop;
    });
  }

  async function loadOlderLogs(root) {
    const button = root.querySelector("#monitor-log-load-older");
    if (button) {
      button.disabled = true;
      button.innerHTML = '<span class="loading-spinner loading-spinner-sm" aria-hidden="true"></span> Carregando histórico…';
    }
    const logs = OC.monitorState.payload?.logs || {};
    const fingerprint = logFilterFingerprint();
    try {
      await Promise.all(
        OC.monitorState.selectedEnvs.map(async (env) => {
          const current = logs[env];
          if (!current?.hasMore || !current?.nextCursor) return;
          const params = buildLogRequestParams({ cursor: current.nextCursor, limit: 200 });
          const incoming = await OC.fetchMonitoringJson(
            `/api/v1/monitoring/${env}/logs?${params}`,
            { environment: env, lines: [] },
            { perfLabel: `logs-older:${env}` }
          );
          incoming._filterFingerprint = fingerprint;
          logs[env] = mergeLogPayload(current, incoming, { older: true });
        })
      );
      OC.monitorState.logHistoryMode = true;
      rerenderLogsPreservingScroll({ prepended: true });
    } catch (error) {
      if (button) {
        button.disabled = false;
        button.textContent = "Falha ao carregar. Tentar novamente";
      }
    }
  }

  function buildLogExportUrl(env, format) {
    const params = buildLogRequestParams({ limit: 10000 });
    params.delete("limit");
    params.delete("order");
    params.set("format", format);
    return `/api/v1/monitoring/${encodeURIComponent(env)}/logs/export?${params}`;
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
          title: "Latência por ambiente",
          hint: "Leitura atual e indicadores das últimas 24 horas",
          className: "monitor-section--latency-overview",
          body: renderLatencyOverview(overviews, uptimeByEnv, slos),
        })}${OC.renderOpsSection({
          title: "Evolução da latência",
          hint: `${latencyWindowLabel(OC.monitorState.latencyWindow || "24h")} · limite p95 ${slos.healthP95WarnMs} ms`,
          className: "monitor-section--latency-trend",
          body: loading?.healthSeries
            ? renderLoadingSection("Tendência de latência")
            : buildEnvLatencyCharts(healthSeries, slos.healthP95WarnMs),
        })}${OC.renderOpsSection({
          title: "Disponibilidade por dia",
          hint: "Últimos 7 dias · selecione um dia para abrir o detalhe por hora",
          className: "monitor-section--uptime",
          body: loading?.uptime
            ? renderLoadingSection("Disponibilidade por dia")
            : `${renderUptimeStatusBars(uptimeByEnv, { drillable: true })}${renderDayHourDrill(OC.monitorState.dayDrill)}`,
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
              title: "APIs e acessos",
              hint: "Volume real, usuários, status HTTP, latência e rotas",
              body: renderApisSection(apiRoutes, apiSeries, slos),
            });
      case "logs":
        return loading?.logs
          ? renderLoadingSection("Logs de serviço")
          : OC.renderOpsSection({
              title: "Logs operacionais",
              hint: "Investigue eventos entre ambientes, serviços e streams",
              body: `${renderLogsToolbar(logs)}${renderLogsViewer(logs)}`,
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
            <thead><tr><th>Método</th><th>Rota</th><th>Média sucesso</th><th>Média 4xx</th><th>Máx</th><th>Amostras</th><th>4xx</th><th>5xx</th></tr></thead>
            <tbody>${slowRoutes
              .slice(0, 15)
              .map(
                (r) => `<tr>
                <td>${OC.escapeHtml(r.method || "")}</td>
                <td><code>${OC.escapeHtml(r.route || "")}</code></td>
                <td>${r.successAvgMs != null ? formatLatencyMs(r.successAvgMs) : "—"}</td>
                <td>${r.clientErrorAvgMs != null ? formatLatencyMs(r.clientErrorAvgMs) : "—"}</td>
                <td>${r.maxMs != null ? formatLatencyMs(r.maxMs) : "—"}</td>
                <td>${r.sampleCount ?? r.count ?? "—"}</td>
                <td>${r.status4xx ?? r.errors4xx ?? 0}</td>
                <td>${r.status5xx ?? r.errors5xx ?? 0}</td>
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
      const valueFormat = wrap.getAttribute("data-value-format") || "latency";
      const valueSuffix = wrap.getAttribute("data-value-suffix") ?? " ms";
      const formatValue = (value) => formatChartMetricValue(value, valueFormat);

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
              ? ` · +${formatValue(nearest.v - sloMs)}${valueSuffix} acima do SLO`
              : ` · ${formatValue(sloMs - nearest.v)}${valueSuffix} abaixo do SLO`
            : "";
        const avgNote = nearest.avg != null ? ` · média ${formatValue(nearest.avg)}${valueSuffix}` : "";
        const accessNote =
          valueFormat === "rate" && nearest.labels?.requests != null
            ? `<br>${Number(nearest.labels.requests).toLocaleString("pt-BR")} acesso(s) no intervalo · latência média ${formatLatencyMs(nearest.labels.avgMs || 0)} ms` +
              `<br>4xx: ${formatAccessNumber(nearest.labels.status4xx)} · 5xx: ${formatAccessNumber(nearest.labels.status5xx)}` +
              (nearest.labels.leadingRoute
                ? `<br>Rota líder no período: <code>${OC.escapeHtml(nearest.labels.leadingRoute)}</code>`
                : "")
            : "";
        tooltip.innerHTML = `<strong>${OC.escapeHtml(OC.formatDate(nearest.iso))}</strong><br>${OC.escapeHtml(nearest.env)}: <strong>${formatValue(nearest.v)}${OC.escapeHtml(valueSuffix)}</strong>${OC.escapeHtml(avgNote)}${OC.escapeHtml(vsSlo)}${accessNote}`;
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
    bindApiRouteDetailRows(root);
    bindApiRouteTableSort(root);
    const openAlerts = () => {
      if (OC.openMonitorAlertsDrawer) OC.openMonitorAlertsDrawer(OC.lastAlertGroups || [], { refresh: false });
      else OC.navigate("monitoring", null, { tab: "incidents" });
    };
    OC.bindOpsStatActions?.(root, { alerts: openAlerts });
    root.querySelector("#monitor-refresh-now")?.addEventListener("click", () =>
      refreshFromMonitoringControl("Atualizando monitoramento…")
    );
    root.querySelector("#monitor-clear-filters")?.addEventListener("click", () => {
      OC.monitorState.selectedEnvs = [...OC.ENV_ORDER];
      CATEGORY_KEYS.forEach((k) => {
        OC.monitorState.categories[k] = true;
      });
      OC.monitorState.eventFilters = { severity: "", category: "", hours: 24 };
      OC.monitorState.latencyWindow = "24h";
      OC.monitorState.apiWindow = "6h";
      OC.monitorState.logsPattern = "";
      OC.monitorState.logFilters = { ...DEFAULT_LOG_FILTERS };
      OC.monitorState.logNewCount = 0;
      OC.monitorState.logHistoryMode = false;
      savePrefs();
      syncLogFiltersToUrl();
      refreshFromMonitoringControl("Restaurando filtros…");
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
        const active = OC.monitorState.selectedEnvs.includes(env);
        el.classList.toggle("is-active", active);
        el.setAttribute("aria-pressed", active ? "true" : "false");
        savePrefs();
        syncLogFiltersToUrl();
        if (OC.monitorState.activeTab === "logs") OC.monitorState.logHistoryMode = false;
        refreshFromMonitoringControl("Aplicando filtro de ambientes…");
      });
    });
    root.querySelectorAll("[data-monitor-cat]").forEach((el) => {
      el.addEventListener("click", () => {
        const key = el.getAttribute("data-monitor-cat");
        OC.monitorState.categories[key] = !OC.monitorState.categories[key];
        el.classList.toggle("is-active", OC.monitorState.categories[key]);
        el.setAttribute("aria-pressed", OC.monitorState.categories[key] ? "true" : "false");
        savePrefs();
        refreshFromMonitoringControl("Aplicando filtro de categorias…");
      });
    });
    root.querySelectorAll("[data-monitor-tab]").forEach((el) => {
      el.addEventListener("click", () => {
        const tab = el.getAttribute("data-monitor-tab");
        if (!tab || tab === OC.monitorState.activeTab) return;
        setSelectedMonitoringTab(root, tab);
        setMonitoringBusy(true, `Carregando ${TAB_LABELS[tab] || tab}…`);
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
        refreshFromMonitoringControl("Aplicando filtros de incidentes…");
        return;
      }
      if (id === "api-window") {
        OC.monitorState.apiWindow = value || "6h";
        savePrefs();
        OC.startMonitoringRefresh();
        refreshFromMonitoringControl("Alterando período das APIs…");
        return;
      }
      if (id === "latency-window") {
        OC.monitorState.latencyWindow = value || "24h";
        savePrefs();
        refreshFromMonitoringControl("Alterando período da latência…");
        return;
      }
    });

    const searchInput = root.querySelector("#monitor-log-search");
    const applyLogSearch = () => {
      if (!searchInput) return;
      const value = searchInput.value.trim();
      if (value === OC.monitorState.logFilters.q) return;
      OC.monitorState.logFilters.q = value;
      OC.monitorState.logsPattern = value;
      refreshLogsWithFilters("Buscando nos logs…");
    };
    let logSearchTimer = null;
    searchInput?.addEventListener("input", () => {
      window.clearTimeout(logSearchTimer);
      logSearchTimer = window.setTimeout(applyLogSearch, 300);
    });
    root.querySelector("#monitor-log-search-form")?.addEventListener("submit", (event) => {
      event.preventDefault();
      window.clearTimeout(logSearchTimer);
      applyLogSearch();
    });
    root.querySelectorAll("[data-log-level]").forEach((button) => {
      button.addEventListener("click", () => {
        const level = button.getAttribute("data-log-level");
        const levels = OC.monitorState.logFilters.levels;
        OC.monitorState.logFilters.levels = levels.includes(level)
          ? levels.filter((value) => value !== level)
          : [...levels, level];
        refreshLogsWithFilters("Filtrando níveis de log…");
      });
    });
    root.querySelector("#monitor-log-service")?.addEventListener("change", (event) => {
      OC.monitorState.logFilters.services = event.target.value ? [event.target.value] : [];
      refreshLogsWithFilters("Filtrando serviço…");
    });
    root.querySelector("#monitor-log-stream")?.addEventListener("change", (event) => {
      OC.monitorState.logFilters.streams = event.target.value ? [event.target.value] : [];
      refreshLogsWithFilters("Filtrando stream…");
    });
    root.querySelector("#monitor-log-order")?.addEventListener("change", (event) => {
      OC.monitorState.logFilters.order = event.target.value === "desc" ? "desc" : "asc";
      refreshLogsWithFilters("Alterando ordem dos logs…");
    });
    root.querySelector("#monitor-log-period")?.addEventListener("change", (event) => {
      const period = event.target.value;
      OC.monitorState.logFilters.period = period;
      if (period !== "custom") {
        OC.monitorState.logFilters.since = "";
        OC.monitorState.logFilters.until = "";
        refreshLogsWithFilters("Alterando período dos logs…");
      } else {
        OC.monitorState.logFilters.since = periodStartIso("1h");
        OC.monitorState.logFilters.until = "";
        savePrefs();
        syncLogFiltersToUrl();
        rerenderLogsPreservingScroll();
      }
    });
    root.querySelector("#monitor-log-apply-period")?.addEventListener("click", () => {
      const sinceValue = root.querySelector("#monitor-log-since")?.value || "";
      const untilValue = root.querySelector("#monitor-log-until")?.value || "";
      const since = sinceValue ? new Date(sinceValue).toISOString() : "";
      const until = untilValue ? new Date(untilValue).toISOString() : "";
      if (since && until && since > until) {
        window.alert("O início do período precisa ser anterior ao fim.");
        return;
      }
      OC.monitorState.logFilters.since = since;
      OC.monitorState.logFilters.until = until;
      refreshLogsWithFilters("Aplicando período personalizado…");
    });
    root.querySelectorAll("[data-log-remove-filter]").forEach((button) => {
      button.addEventListener("click", () => {
        const key = button.getAttribute("data-log-remove-filter");
        if (key?.startsWith("level:")) {
          const level = key.split(":")[1];
          OC.monitorState.logFilters.levels = OC.monitorState.logFilters.levels.filter(
            (value) => value !== level
          );
        } else if (key === "period") {
          OC.monitorState.logFilters.period = "24h";
          OC.monitorState.logFilters.since = "";
          OC.monitorState.logFilters.until = "";
        } else if (key === "q") {
          OC.monitorState.logFilters.q = "";
        } else if (key === "services" || key === "streams") {
          OC.monitorState.logFilters[key] = [];
        }
        refreshLogsWithFilters("Removendo filtro…");
      });
    });
    root.querySelector("#monitor-log-clear-filters")?.addEventListener("click", () => {
      resetLogFilters();
      refreshFromMonitoringControl("Limpando filtros de logs…");
    });
    root.querySelector("#monitor-log-empty-clear")?.addEventListener("click", () => {
      resetLogFilters();
      refreshFromMonitoringControl("Ampliando busca de logs…");
    });
    root.querySelector("#monitor-log-toggle-pause")?.addEventListener("click", () => {
      OC.monitorState.logsPaused = !OC.monitorState.logsPaused;
      OC.startMonitoringRefresh();
      rerenderLogsPreservingScroll();
    });
    root.querySelector("#monitor-log-toggle-follow")?.addEventListener("click", () => {
      OC.monitorState.logsFollowing = !OC.monitorState.logsFollowing;
      savePrefs();
      const returnToLatest = OC.monitorState.logsFollowing && OC.monitorState.logHistoryMode;
      if (OC.monitorState.logsFollowing) {
        OC.monitorState.logHistoryMode = false;
        OC.monitorState.logNewCount = 0;
      }
      if (returnToLatest) refreshFromMonitoringControl("Voltando aos logs mais recentes…");
      else rerenderLogsPreservingScroll();
    });
    root.querySelectorAll("[data-log-export]").forEach((button) => {
      button.addEventListener("click", () => {
        const env = root.querySelector("#monitor-log-export-env")?.value || OC.monitorState.selectedEnvs[0];
        const format = button.getAttribute("data-log-export") || "csv";
        const link = document.createElement("a");
        link.href = buildLogExportUrl(env, format);
        link.hidden = true;
        document.body.appendChild(link);
        link.click();
        link.remove();
      });
    });
    root.querySelectorAll("[data-log-toggle]").forEach((button) => {
      button.addEventListener("click", () => {
        const key = button.getAttribute("data-log-toggle");
        if (OC.monitorState.logExpanded.has(key)) OC.monitorState.logExpanded.delete(key);
        else OC.monitorState.logExpanded.add(key);
        rerenderLogsPreservingScroll();
      });
    });
    root.querySelectorAll("[data-log-copy]").forEach((button) => {
      button.addEventListener("click", () => {
        const line = findRenderedLogLine(button.getAttribute("data-log-key"));
        if (!line) return;
        const text = button.getAttribute("data-log-copy") === "detail"
          ? `${line.logged_at} | ${line.environment} | ${line.level} | ${line.service}/${line.stream}\n${line.line}`
          : line.line;
        copyLogText(text, button);
      });
    });
    root.querySelectorAll("[data-log-related]").forEach((button) => {
      button.addEventListener("click", () => {
        const line = findRenderedLogLine(button.getAttribute("data-log-related"));
        if (!line) return;
        const identifier = String(line.line || "").match(/[a-f0-9]{8}-[a-f0-9-]{20,}|\b[A-Z][A-Z0-9_]{5,}\b/i)?.[0];
        OC.monitorState.logFilters.q = identifier || String(line.line || "").trim().slice(0, 80);
        refreshLogsWithFilters("Buscando ocorrências semelhantes…");
      });
    });
    root.querySelector("#monitor-log-load-older")?.addEventListener("click", () => loadOlderLogs(root));
    root.querySelector("#monitor-log-retry")?.addEventListener("click", () =>
      refreshFromMonitoringControl("Tentando carregar os logs novamente…")
    );
    root.querySelector("#monitor-log-show-new")?.addEventListener("click", () => {
      const refreshLatest = OC.monitorState.logHistoryMode;
      OC.monitorState.logHistoryMode = false;
      OC.monitorState.logNewCount = 0;
      if (refreshLatest) {
        refreshFromMonitoringControl("Carregando logs mais recentes…");
        return;
      }
      OC.renderMonitoringView(OC.monitorState.payload, { partial: true });
      window.requestAnimationFrame(() => {
        const consoleEl = document.getElementById("monitor-log-console");
        if (consoleEl) {
          consoleEl.scrollTop = (OC.monitorState.logFilters?.order || "asc") === "desc"
            ? 0
            : consoleEl.scrollHeight;
        }
      });
    });
    root.querySelector("#monitor-log-console")?.addEventListener("scroll", (event) => {
      const consoleEl = event.currentTarget;
      const atBottom = consoleEl.scrollHeight - consoleEl.scrollTop - consoleEl.clientHeight < 32;
      if (atBottom && OC.monitorState.logNewCount) {
        OC.monitorState.logNewCount = 0;
        root.querySelector("#monitor-log-show-new")?.remove();
      }
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
      tab: activeTab,
    })}
    <div class="monitor-control-surface">
      ${renderTabBar(activeTab)}
      ${OC.renderMonitoringFilters()}
    </div>
    <div class="monitor-tab-panel" id="monitor-tab-panel" role="tabpanel"><div class="loading-inline"><div class="loading-spinner loading-spinner-sm"></div> Carregando monitoramento…</div></div>`;
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
      const hero = root.querySelector(".ops-hero");
      if (hero) {
        hero.outerHTML = renderMonitorHero(kpis, {
          compact: activeTab !== "summary",
          tabLabel: TAB_LABELS[activeTab] || activeTab,
          tab: activeTab,
        });
      }
      const tabs = root.querySelector(".monitor-tabs");
      if (tabs) tabs.outerHTML = renderTabBar(activeTab);
      const meta = root.querySelector(".monitor-meta-wrap");
      if (meta) meta.outerHTML = renderMetaBar(payload?.config, warnings);
      const filters = root.querySelector(".monitor-filter-panel");
      if (filters) filters.outerHTML = OC.renderMonitoringFilters();
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
      tab: activeTab,
    })}
    <div class="monitor-control-surface">
      ${renderTabBar(activeTab)}
      ${OC.renderMonitoringFilters()}
      ${renderMetaBar(payload?.config, warnings)}
    </div>
    <div class="monitor-tab-panel" id="monitor-tab-panel" role="tabpanel">${renderTabContent(activeTab, payload, overviews, slos)}</div>`;

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
    const currentLogConsole = document.getElementById("monitor-log-console");
    const logScrollState = currentLogConsole
      ? {
          exists: true,
          top: currentLogConsole.scrollTop,
          height: currentLogConsole.scrollHeight,
          atNewest:
            (OC.monitorState.logFilters?.order || "asc") === "desc"
              ? currentLogConsole.scrollTop < 32
              : currentLogConsole.scrollHeight - currentLogConsole.scrollTop - currentLogConsole.clientHeight < 32,
        }
      : { exists: false, top: 0, height: 0, atNewest: true };

    if (options.showFeedback) {
      setMonitoringBusy(true, options.feedbackMessage || "");
    }

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
      if (tab === "logs") {
        window.requestAnimationFrame(() => {
          const consoleEl = document.getElementById("monitor-log-console");
          if (!consoleEl) return;
          const newestAtTop = (OC.monitorState.logFilters?.order || "asc") === "desc";
          if (
            !logScrollState.exists ||
            options.showFeedback ||
            (OC.monitorState.logsFollowing && logScrollState.atNewest)
          ) {
            consoleEl.scrollTop = newestAtTop ? 0 : consoleEl.scrollHeight;
          } else {
            consoleEl.scrollTop = logScrollState.top;
          }
        });
      }

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
        envs.map(async (env) => {
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
            ? dash.groupedEvents.filter((group) => {
                const envVisible = envs.includes(group.environment) || group.environment === "HOST";
                const categoryVisible = OC.monitorState.categories[group.category] !== false;
                const severityVisible = !ef.severity || group.severity === ef.severity;
                const selectedCategory = !ef.category || group.category === ef.category;
                return envVisible && categoryVisible && severityVisible && selectedCategory;
              })
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
            groupedEvents = all.filter(
              (g) => envs.includes(g.environment) || (g.environment === "HOST" && OC.monitorState.categories.host)
            );
            delete loading.grouped;
          })
        );
      }

      if (plan.apiRoutes) {
        const win = OC.monitorState.apiWindow || "6h";
        phase2.push(
          Promise.all(
            envs.map(async (env) => {
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
            envs.map(async (env) => {
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
        const fingerprint = logFilterFingerprint();
        let newLineCount = 0;
        phase2.push(
          Promise.all(
            envs.map(async (env) => {
              const previous = logs[env];
              const canIncrement =
                options.incremental === true &&
                previous?._filterFingerprint === fingerprint &&
                previous?.lines?.length;
              const latest = canIncrement
                ? previous.lines.reduce(
                    (value, line) => String(line.logged_at || "") > value ? String(line.logged_at) : value,
                    ""
                  )
                : "";
              const params = buildLogRequestParams({ incrementalSince: latest, limit: 200 });
              const incoming = await OC.fetchMonitoringJson(
                `/api/v1/monitoring/${env}/logs?${params}`,
                { environment: env, lines: [] },
                fetchOpts(`logs:${env}`)
              );
              incoming._filterFingerprint = fingerprint;
              if (canIncrement && !incoming.error) {
                const known = new Set((previous.lines || []).map((line) => line.key));
                newLineCount += (incoming.lines || []).filter((line) => !known.has(line.key)).length;
                logs[env] = mergeLogPayload(previous, incoming, {
                  preserveHistory: OC.monitorState.logHistoryMode,
                });
              } else {
                logs[env] = incoming;
              }
            })
          ).then(() => {
            if (newLineCount && !(OC.monitorState.logsFollowing && logScrollState.atNewest)) {
              OC.monitorState.logNewCount += newLineCount;
            } else if (OC.monitorState.logsFollowing && logScrollState.atNewest) {
              OC.monitorState.logNewCount = 0;
            }
            delete loading.logs;
          })
        );
      }

      await Promise.all(phase2);

      finishMonitoringRefresh({
        ...payloadFields(),
        loading: {},
      });
      // Monitoring has an independent polling loop. A successful poll is
      // activity and must prevent the idle lock while the dashboard is live.
      OC.resetIdleTimer?.();
    } catch (err) {
      if (err.name === "AbortError") return;
      const root = document.getElementById("view-monitoring");
      if (root) {
        root.innerHTML = `${OC.renderBackToEnvironments("DEV")}
          <p class="global-error" role="alert">Erro ao carregar monitoramento: ${OC.escapeHtml(err.message)}</p>`;
        OC.bindBackNavigation(root);
      }
    } finally {
      if (generation !== OC._monitorRefreshGeneration) return;
      setMonitoringBusy(false);
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
    if (tab === "logs" && OC.monitorState.logsPaused) return;
    let ms = MONITOR_REFRESH_MS;
    if (tab === "apis") ms = MONITOR_APIS_REFRESH_MS;
    else if (tab === "logs") ms = MONITOR_LOGS_REFRESH_MS;
    OC.monitorTimer = setInterval(() => {
      if (!OC._monitorRefreshInFlight) OC.refreshMonitoring({ incremental: tab === "logs" });
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
