/**
 * PPLID Automação Deck — monitoramento e controle dos robôs (estilo extensão).
 *
 * APIs: Django /api/v1/automacoes/* (PPLID logado) ou Flask http://127.0.0.1:5000/api/robots/* (BRFlow).
 *
 * Uso: DevTools → Sources → Snippets → colar e executar (Ctrl+Enter).
 *      Executar de novo para toggle. Ctrl+Shift+, para mostrar/ocultar.
 */
(() => {
  const NS = "__PPLID_AUTOMACAO_DECK__";
  const VERSION = "1.0.0";
  const STORAGE_KEY = "pplid_automacao_deck_v1";
  const POLL_MS = 3500;
  const LOG_TAIL = 40;
  const FLASK_BASE = "http://127.0.0.1:5000";
  const PPLID_AUTOMACAO_PATH = "/planejamento/automacao";

  const ROBOT_MODES_PRIMARY = [
    "nivel",
    "monitor",
    "excel",
    "production",
    "rotina",
    "confer",
    "ged",
    "replicacao_auditoria_d1",
  ];

  const MODE_LABELS = {
    nivel: "Nível hierárquico",
    monitor: "Monitor de eventos",
    excel: "Alterações no BRFlow",
    production: "Produção (H/H)",
    rotina: "Rotina diária",
    confer: "Confer (Eventos)",
    ged: "GED",
    replicacao_auditoria_d1: "Replicação de Auditoria",
    onedrive: "Monitorar OneDrive (legado)",
    onedrive_ui: "Monitorar OneDrive + Cisco (visual)",
    cisco_ui: "Monitorar OneDrive + Cisco (visual)",
    tray_ui: "Monitorar bandeja (OneDrive + Cisco)",
    replicacao_auditoria: "Replicação de Auditoria (legado)",
  };

  const CREDENTIALLESS_MODES = new Set(["onedrive", "onedrive_ui", "cisco_ui", "tray_ui"]);
  const BLOCKED_START_MODES = new Set(["rotina", "replicacao_auditoria_d1", "replicacao_auditoria"]);

  if (window[NS]) {
    window[NS].toggle();
    return;
  }

  const loadStore = () => {
    try {
      return JSON.parse(localStorage.getItem(STORAGE_KEY) || "{}");
    } catch {
      return {};
    }
  };

  const saveStore = (patch) => {
    try {
      const all = { ...loadStore(), ...patch };
      localStorage.setItem(STORAGE_KEY, JSON.stringify(all));
    } catch {
      /* quota */
    }
  };

  const stored = loadStore();

  const state = {
    open: true,
    tab: "fleet",
    selectedMode: stored.selectedMode || ROBOT_MODES_PRIMARY[0],
    modes: [...ROBOT_MODES_PRIMARY],
    labels: { ...MODE_LABELS },
    robots: {},
    credentials: { validated: false, checking: false, message: "", last_checked_at: null },
    logs: [],
    configs: {},
    apiMode: null,
    apiError: null,
    loading: false,
    actionPending: null,
    confirmStopAll: false,
    drag: null,
    fabDrag: null,
    fabMoved: false,
    pos: stored.pos || { x: null, y: null },
    fabPos: stored.fabPos || { x: null, y: null },
    pollTimer: null,
  };

  /* ── ApiAdapter ── */
  const api = {
    mode: null,
    csrf: null,

    routes() {
      if (this.mode === "django") {
        return {
          meta: "/api/v1/automacoes/meta/",
          status: "/api/v1/automacoes/status/",
          logs: (mode, tail) =>
            `/api/v1/automacoes/logs/?mode=${encodeURIComponent(mode)}&tail=${tail}`,
          config: (mode) => `/api/v1/automacoes/config/?mode=${encodeURIComponent(mode)}`,
          start: "/api/v1/automacoes/start/",
          stop: "/api/v1/automacoes/stop/",
          stopAll: "/api/v1/automacoes/stop-all/",
          csrf: "/api/v1/auth/csrf/",
        };
      }
      const base = `${FLASK_BASE}/api/robots`;
      return {
        meta: `${base}/meta`,
        status: `${base}/status`,
        logs: (mode, tail) => `${base}/logs?mode=${encodeURIComponent(mode)}&tail=${tail}`,
        config: (mode) => `${base}/config?mode=${encodeURIComponent(mode)}`,
        start: `${base}/start`,
        stop: `${base}/stop`,
        stopAll: `${base}/stop-all`,
        csrf: null,
      };
    },

    async readCsrf() {
      const match = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/);
      if (match) {
        this.csrf = decodeURIComponent(match[1]);
        return;
      }
      try {
        const res = await fetch("/api/v1/auth/csrf/", { credentials: "include" });
        if (res.ok) {
          const data = await res.json();
          if (data.csrfToken) this.csrf = data.csrfToken;
        }
      } catch {
        /* ignore */
      }
    },

    async request(url, options = {}) {
      const headers = { ...(options.headers || {}) };
      if (options.body && !headers["Content-Type"]) {
        headers["Content-Type"] = "application/json";
      }
      if (this.mode === "django" && options.method && options.method !== "GET") {
        if (!this.csrf) await this.readCsrf();
        if (this.csrf) headers["X-CSRFToken"] = this.csrf;
      }
      const res = await fetch(url, {
        ...options,
        headers,
        credentials: this.mode === "django" ? "include" : "omit",
      });
      let data = null;
      const text = await res.text();
      try {
        data = text ? JSON.parse(text) : null;
      } catch {
        data = { ok: false, message: text || res.statusText };
      }
      if (!res.ok && data && !data.message && data.detail) {
        data.message = typeof data.detail === "string" ? data.detail : res.statusText;
      }
      return { ok: res.ok, status: res.status, data };
    },

    async detect() {
      try {
        const res = await fetch("/api/v1/automacoes/meta/", { credentials: "include" });
        if (res.ok) {
          this.mode = "django";
          state.apiMode = "django";
          state.apiError = null;
          return true;
        }
      } catch {
        /* try flask */
      }
      try {
        const res = await fetch(`${FLASK_BASE}/api/robots/status`);
        if (res.ok) {
          this.mode = "flask";
          state.apiMode = "flask";
          state.apiError = null;
          return true;
        }
      } catch {
        /* unavailable */
      }
      this.mode = null;
      state.apiMode = null;
      state.apiError = "API indisponível. Logue no PPLID ou execute: python -m sistematest-web";
      return false;
    },

    async meta() {
      const { data } = await this.request(this.routes().meta);
      if (data?.modes?.length) {
        state.modes = data.modes.filter((m) => ROBOT_MODES_PRIMARY.includes(m) || !m.includes("legacy"));
        if (!state.modes.length) state.modes = data.modes;
      }
      if (data?.labels) state.labels = { ...state.labels, ...data.labels };
      return data;
    },

    async status() {
      const { ok, data } = await this.request(this.routes().status);
      if (ok && data) {
        if (data.robots) state.robots = data.robots;
        if (data.credentials) state.credentials = data.credentials;
      }
      return { ok, data };
    },

    async logs(mode) {
      const { ok, data } = await this.request(this.routes().logs(mode, LOG_TAIL));
      if (ok && data?.logs?.[mode]) {
        state.logs = data.logs[mode];
      } else if (ok && data?.logs) {
        const first = Object.keys(data.logs)[0];
        state.logs = first ? data.logs[first] : [];
      }
      return { ok, data };
    },

    async config(mode) {
      const { ok, data } = await this.request(this.routes().config(mode));
      if (ok && data?.configs?.[mode]) {
        state.configs[mode] = data.configs[mode];
      }
      return { ok, data };
    },

    async start(mode) {
      let robot_config = state.configs[mode];
      if (!robot_config) {
        await this.config(mode);
        robot_config = state.configs[mode] || {};
      }
      return this.request(this.routes().start, {
        method: "POST",
        body: JSON.stringify({ mode, require_okta_validation: false, robot_config }),
      });
    },

    async stop(mode) {
      return this.request(this.routes().stop, {
        method: "POST",
        body: JSON.stringify({ mode }),
      });
    },

    async stopAll() {
      return this.request(this.routes().stopAll, { method: "POST", body: "{}" });
    },
  };

  /* ── Helpers ── */
  const escapeHtml = (s) =>
    String(s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");

  const labelFor = (mode) => state.labels[mode] || MODE_LABELS[mode] || mode;

  const normalizeResult = (result) => {
    const r = String(result || "").toLowerCase();
    if (!r || r === "—" || r === "-") return { label: "—", cls: "idle" };
    if (r.includes("sucesso") || r === "ok" || r === "success") return { label: "Sucesso", cls: "ok" };
    if (r.includes("erro") || r.includes("fail")) return { label: "Erro", cls: "err" };
    if (r.includes("parad") || r.includes("stop")) return { label: "Parado", cls: "stopped" };
    return { label: result, cls: "idle" };
  };

  const robotState = (info) => {
    if (!info) return "idle";
    if (info.running) return "running";
    const result = normalizeResult(info.execution?.result || info.last_result);
    if (result.cls === "ok") return "ok";
    if (result.cls === "err") return "err";
    if (result.cls === "stopped") return "stopped";
    return "idle";
  };

  const progressOf = (info) => {
    const raw = Number(info?.runtime?.progress);
    return Number.isFinite(raw) ? Math.max(0, Math.min(100, Math.round(raw))) : 0;
  };

  const fmtDuration = (startedAt) => {
    if (!startedAt) return "—";
    const ms = Date.now() - new Date(startedAt).getTime();
    if (Number.isNaN(ms) || ms < 0) return "—";
    const sec = Math.floor(ms / 1000);
    const h = Math.floor(sec / 3600);
    const m = Math.floor((sec % 3600) / 60);
    const s = sec % 60;
    if (h > 0) return `${h}h ${String(m).padStart(2, "0")}m`;
    return `${m}m ${String(s).padStart(2, "0")}s`;
  };

  const runningCount = () =>
    state.modes.filter((m) => state.robots[m]?.running).length;

  const avgProgress = () => {
    const running = state.modes.filter((m) => state.robots[m]?.running);
    if (!running.length) return 0;
    const sum = running.reduce((acc, m) => acc + progressOf(state.robots[m]), 0);
    return Math.round(sum / running.length);
  };

  const anyRunning = () => state.modes.some((m) => state.robots[m]?.running);

  const canStartMode = (mode) => {
    if (state.robots[mode]?.running) return { ok: false, reason: "Robô em execução" };
    if (BLOCKED_START_MODES.has(mode)) {
      return { ok: false, reason: "Configure e inicie na tela Automação do PPLID" };
    }
    if (CREDENTIALLESS_MODES.has(mode)) return { ok: true, reason: "" };
    return { ok: false, reason: "Valide credenciais na tela Automação do PPLID" };
  };

  const pplidAutomacaoUrl = () => {
    if (state.apiMode === "django") {
      return `${location.origin}${PPLID_AUTOMACAO_PATH}`;
    }
    return PPLID_AUTOMACAO_PATH;
  };

  /* ── DOM ── */
  const root = document.createElement("div");
  root.id = "pplid-automacao-deck-root";
  document.body.appendChild(root);

  const shadow = root.attachShadow({ mode: "open" });
  shadow.innerHTML = `
<style>
  :host, * { box-sizing: border-box; margin: 0; padding: 0; }
  .deck-wrap {
    position: fixed; z-index: 2147483646;
    font-family: "Segoe UI", system-ui, -apple-system, sans-serif;
    font-size: 13px; color: #e8ecf4;
    -webkit-font-smoothing: antialiased;
  }
  .fab {
    position: fixed; bottom: 28px; right: 28px;
    width: 56px; height: 56px; border: none; border-radius: 18px; cursor: pointer;
    background: linear-gradient(135deg, #2a5595 0%, #77127b 55%, #e80070 100%);
    box-shadow: 0 8px 32px rgba(42, 85, 149, 0.45), 0 0 0 1px rgba(255,255,255,.12) inset;
    display: flex; align-items: center; justify-content: center;
    transition: transform .25s cubic-bezier(.34,1.4,.64,1), box-shadow .2s;
    z-index: 2;
  }
  .fab:hover { transform: scale(1.06); }
  .fab.hidden { opacity: 0; pointer-events: none; transform: scale(.5); }
  .fab svg { width: 26px; height: 26px; color: #fff; }
  .fab-badge {
    position: absolute; top: -4px; right: -4px;
    min-width: 18px; height: 18px; padding: 0 5px;
    border-radius: 999px; background: #64c2bb; color: #0f172a;
    font-size: 10px; font-weight: 700; line-height: 18px; text-align: center;
    box-shadow: 0 2px 8px rgba(0,0,0,.3);
  }
  .fab-badge:empty { display: none; }

  .panel {
    position: fixed; bottom: 96px; right: 28px;
    width: 360px; max-height: min(560px, calc(100vh - 120px));
    border-radius: 20px;
    background: rgba(18, 22, 32, 0.88);
    backdrop-filter: blur(20px) saturate(1.4);
    -webkit-backdrop-filter: blur(20px) saturate(1.4);
    border: 1px solid rgba(255,255,255,.1);
    box-shadow: 0 24px 64px rgba(0,0,0,.45), 0 0 0 1px rgba(100,194,187,.06);
    display: flex; flex-direction: column; overflow: hidden;
    transform-origin: bottom right;
    transition: opacity .28s, transform .28s;
  }
  .panel.closed { opacity: 0; pointer-events: none; transform: scale(.88) translateY(12px); }

  .panel-head {
    display: flex; align-items: center; gap: 10px;
    padding: 14px 16px 12px; cursor: grab; user-select: none;
    border-bottom: 1px solid rgba(255,255,255,.06);
    background: linear-gradient(180deg, rgba(42,85,149,.2) 0%, transparent 100%);
  }
  .panel-head:active { cursor: grabbing; }
  .brand-icon {
    width: 36px; height: 36px; border-radius: 12px;
    background: linear-gradient(135deg, #2a5595, #77127b);
    display: flex; align-items: center; justify-content: center;
    font-weight: 700; font-size: 13px; color: #fff; flex-shrink: 0;
  }
  .brand-text h1 { font-size: 14px; font-weight: 600; color: #f1f5f9; }
  .brand-text p { font-size: 10px; color: #8b95a8; margin-top: 1px; }
  .head-actions { margin-left: auto; display: flex; gap: 4px; }
  .icon-btn {
    width: 30px; height: 30px; border: none; border-radius: 10px;
    background: rgba(255,255,255,.06); color: #aab4c4; cursor: pointer;
    font-size: 16px; line-height: 1;
  }
  .icon-btn:hover { background: rgba(255,255,255,.12); color: #e8ecf4; }

  .api-banner {
    margin: 0 12px 0; padding: 8px 10px; border-radius: 10px;
    background: rgba(250, 21, 32, .12); border: 1px solid rgba(250, 21, 32, .25);
    color: #fca5a5; font-size: 11px; line-height: 1.4;
  }
  .api-banner.ok {
    background: rgba(100, 194, 187, .08); border-color: rgba(100, 194, 187, .2); color: #64c2bb;
  }

  .tabs { display: flex; gap: 2px; padding: 8px 10px 0; border-bottom: 1px solid rgba(255,255,255,.05); }
  .tab {
    flex: 1; padding: 7px 2px; border: none; background: none;
    color: #6b7280; font-size: 10px; font-weight: 600; text-transform: uppercase;
    letter-spacing: .03em; cursor: pointer; border-radius: 10px 10px 0 0;
  }
  .tab.active { color: #64c2bb; background: rgba(100,194,187,.08); }

  .panel-body { flex: 1; overflow-y: auto; padding: 12px 14px 14px; scrollbar-width: thin; }
  .view { display: none; }
  .view.active { display: block; }

  .kpi-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 6px; margin-bottom: 10px; }
  .kpi {
    padding: 10px 8px; border-radius: 12px;
    background: rgba(255,255,255,.04); border: 1px solid rgba(255,255,255,.06); text-align: center;
  }
  .kpi-label { font-size: 9px; text-transform: uppercase; letter-spacing: .05em; color: #6b7280; }
  .kpi-value { font-size: 16px; font-weight: 700; color: #f1f5f9; margin-top: 2px; font-variant-numeric: tabular-nums; }
  .kpi-value.accent { color: #64c2bb; }
  .kpi-value.warn { color: #fbbf24; }
  .kpi-value.ok { color: #4ade80; }

  .fleet-list { list-style: none; display: flex; flex-direction: column; gap: 4px; }
  .fleet-row {
    display: grid; grid-template-columns: 8px 1fr auto; gap: 8px; align-items: center;
    padding: 9px 10px; border-radius: 12px; cursor: pointer;
    background: rgba(255,255,255,.03); border: 1px solid rgba(255,255,255,.05);
    transition: border-color .15s, background .15s;
  }
  .fleet-row:hover { background: rgba(255,255,255,.05); }
  .fleet-row.selected { border-color: rgba(100,194,187,.35); background: rgba(100,194,187,.06); }
  .fleet-dot { width: 8px; height: 8px; border-radius: 50%; background: #4b5563; }
  .fleet-row.running .fleet-dot { background: #64c2bb; box-shadow: 0 0 8px rgba(100,194,187,.6); animation: pulse 1.2s infinite; }
  .fleet-row.ok .fleet-dot { background: #4ade80; }
  .fleet-row.err .fleet-dot { background: #f87171; }
  .fleet-row.stopped .fleet-dot { background: #9ca3af; }
  @keyframes pulse { 50% { opacity: .45; } }
  .fleet-name { font-size: 12px; font-weight: 500; color: #e8ecf4; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .fleet-meta { font-size: 10px; color: #8b95a8; margin-top: 2px; }
  .fleet-pct { font-size: 11px; font-weight: 600; color: #64c2bb; font-variant-numeric: tabular-nums; }
  .progress-track { height: 3px; border-radius: 2px; background: rgba(255,255,255,.08); margin-top: 6px; grid-column: 1 / -1; }
  .progress-fill { height: 100%; border-radius: 2px; background: linear-gradient(90deg, #2a5595, #64c2bb); transition: width .4s; }

  .detail-card {
    padding: 12px; border-radius: 14px; margin-bottom: 10px;
    background: rgba(42,85,149,.12); border: 1px solid rgba(42,85,149,.25);
  }
  .detail-title { font-size: 14px; font-weight: 600; color: #f1f5f9; margin-bottom: 4px; }
  .detail-status { font-size: 12px; color: #8b95a8; margin-bottom: 10px; }
  .detail-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; margin-bottom: 12px; }
  .detail-stat { font-size: 11px; color: #8b95a8; }
  .detail-stat strong { display: block; font-size: 15px; color: #e8ecf4; font-variant-numeric: tabular-nums; }

  .btn-row { display: grid; grid-template-columns: 1fr 1fr; gap: 6px; margin-bottom: 8px; }
  .btn {
    padding: 10px; border: none; border-radius: 12px;
    font-size: 12px; font-weight: 600; cursor: pointer; transition: opacity .15s, transform .1s;
  }
  .btn:active { transform: scale(.97); }
  .btn:disabled { opacity: .4; cursor: not-allowed; }
  .btn-primary { background: linear-gradient(135deg, #2a5595, #1087aa); color: #fff; }
  .btn-danger { background: rgba(239,68,68,.15); color: #fca5a5; border: 1px solid rgba(239,68,68,.25); }
  .btn-ghost {
    grid-column: span 2; background: rgba(255,255,255,.06); color: #cbd5e1;
    border: 1px solid rgba(255,255,255,.08);
  }
  .hint { font-size: 10px; color: #6b7280; line-height: 1.45; margin-top: 6px; }

  .log-box {
    max-height: 280px; overflow-y: auto; padding: 10px; border-radius: 12px;
    background: rgba(0,0,0,.3); border: 1px solid rgba(255,255,255,.06);
    font-family: ui-monospace, "Cascadia Code", Consolas, monospace;
    font-size: 10px; line-height: 1.5; color: #94a3b8; white-space: pre-wrap; word-break: break-all;
  }
  .log-empty { text-align: center; padding: 32px 12px; color: #5c6578; font-size: 12px; }

  .action-block { margin-bottom: 10px; }
  .action-label { font-size: 10px; text-transform: uppercase; letter-spacing: .05em; color: #6b7280; margin-bottom: 6px; }

  .deck-toast {
    position: fixed; bottom: 100px; left: 50%;
    transform: translateX(-50%) translateY(8px);
    padding: 8px 16px; border-radius: 999px;
    background: rgba(15,23,42,.92); border: 1px solid rgba(100,194,187,.3);
    color: #64c2bb; font-size: 12px; font-weight: 600;
    opacity: 0; pointer-events: none; transition: opacity .2s, transform .2s; z-index: 10;
  }
  .deck-toast.visible { opacity: 1; transform: translateX(-50%) translateY(0); }
  .deck-toast.err { border-color: rgba(248,113,113,.4); color: #f87171; }

  .panel-footer {
    padding: 6px 14px 10px; font-size: 9px; color: #4b5563; text-align: center;
    border-top: 1px solid rgba(255,255,255,.04);
  }
  kbd { padding: 1px 5px; border-radius: 4px; background: rgba(255,255,255,.08); font-size: 9px; }
</style>

<div class="deck-wrap">
  <button class="fab" id="deck-fab" title="Automação Deck">
    <span class="fab-badge" id="deck-fab-badge"></span>
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round">
      <rect x="4" y="4" width="16" height="16" rx="2"/>
      <path d="M9 9h.01M15 9h.01M9 15h6"/>
    </svg>
  </button>

  <div class="panel" id="deck-panel">
    <div class="panel-head" id="deck-drag">
      <div class="brand-icon">BOT</div>
      <div class="brand-text">
        <h1>Automação Deck</h1>
        <p id="deck-backend-label">conectando…</p>
      </div>
      <div class="head-actions">
        <button class="icon-btn" id="deck-min" title="Minimizar">−</button>
        <button class="icon-btn" id="deck-close" title="Fechar">×</button>
      </div>
    </div>

    <div class="api-banner" id="deck-api-banner" style="display:none;margin-top:8px"></div>

    <div class="tabs" role="tablist">
      <button class="tab active" data-tab="fleet">Frota</button>
      <button class="tab" data-tab="robot">Robô</button>
      <button class="tab" data-tab="logs">Logs</button>
      <button class="tab" data-tab="actions">Ações</button>
    </div>

    <div class="panel-body">
      <div class="view active" data-view="fleet">
        <div class="kpi-grid">
          <div class="kpi">
            <div class="kpi-label">Rodando</div>
            <div class="kpi-value accent" id="kpi-running">0</div>
          </div>
          <div class="kpi">
            <div class="kpi-label">Okta</div>
            <div class="kpi-value" id="kpi-okta">—</div>
          </div>
          <div class="kpi">
            <div class="kpi-label">Prog.</div>
            <div class="kpi-value" id="kpi-progress">0%</div>
          </div>
        </div>
        <ul class="fleet-list" id="fleet-list"></ul>
      </div>

      <div class="view" data-view="robot">
        <div class="detail-card" id="robot-detail"></div>
        <div class="btn-row">
          <button class="btn btn-primary" id="btn-start">Iniciar</button>
          <button class="btn btn-danger" id="btn-stop">Parar</button>
          <button class="btn btn-ghost" id="btn-open-pplid">Abrir Automação no PPLID</button>
        </div>
        <p class="hint" id="start-hint"></p>
      </div>

      <div class="view" data-view="logs">
        <div class="action-block">
          <div class="action-label" id="logs-label">Logs</div>
          <div class="log-box" id="log-box"></div>
        </div>
        <button class="btn btn-ghost" id="btn-refresh-logs" style="width:100%;margin-top:6px">Atualizar agora</button>
      </div>

      <div class="view" data-view="actions">
        <div class="action-block">
          <div class="action-label">Emergência</div>
          <button class="btn btn-danger" id="btn-stop-all" style="width:100%">Parar todos</button>
        </div>
        <div class="action-block">
          <div class="action-label">Sincronização</div>
          <button class="btn btn-ghost" id="btn-refresh" style="width:100%">Atualizar frota</button>
        </div>
        <div class="action-block">
          <div class="action-label">Backend</div>
          <div class="detail-card" id="backend-info" style="margin:0"></div>
        </div>
        <button class="btn btn-ghost" id="btn-open-pplid2" style="width:100%;margin-top:6px">Abrir tela completa de Automação</button>
      </div>
    </div>

    <div class="panel-footer">Arraste pelo topo · <kbd>Ctrl</kbd>+<kbd>Shift</kbd>+<kbd>,</kbd> toggle</div>
  </div>
  <div class="deck-toast" id="deck-toast"></div>
</div>`;

  const $ = (sel) => shadow.querySelector(sel);
  const panel = $("#deck-panel");
  const fab = $("#deck-fab");

  const toast = (msg, isErr = false) => {
    const el = $("#deck-toast");
    el.textContent = msg;
    el.classList.toggle("err", isErr);
    el.classList.add("visible");
    clearTimeout(toast._t);
    toast._t = setTimeout(() => el.classList.remove("visible"), 2400);
  };

  const persistUi = () => {
    saveStore({ pos: state.pos, fabPos: state.fabPos, selectedMode: state.selectedMode });
  };

  const applyPositions = () => {
    if (state.pos.x != null) {
      panel.style.right = "auto";
      panel.style.bottom = "auto";
      panel.style.left = `${state.pos.x}px`;
      panel.style.top = `${state.pos.y}px`;
    }
    if (state.fabPos.x != null) {
      fab.style.right = "auto";
      fab.style.bottom = "auto";
      fab.style.left = `${state.fabPos.x}px`;
      fab.style.top = `${state.fabPos.y}px`;
    }
  };

  const setOpen = (open) => {
    const wasOpen = state.open;
    state.open = open;
    panel.classList.toggle("closed", !open);
    fab.classList.toggle("hidden", open);
    if (open && !wasOpen) startPolling();
    else if (!open && wasOpen) stopPolling();
  };

  const setTab = (tab) => {
    state.tab = tab;
    shadow.querySelectorAll(".tab").forEach((el) => el.classList.toggle("active", el.dataset.tab === tab));
    shadow.querySelectorAll(".view").forEach((el) => el.classList.toggle("active", el.dataset.view === tab));
    if (tab === "logs") void refreshLogs();
    render();
  };

  const renderFleet = () => {
    $("#kpi-running").textContent = `${runningCount()}/${state.modes.length}`;
    const oktaEl = $("#kpi-okta");
    if (state.credentials.checking) {
      oktaEl.textContent = "…";
      oktaEl.className = "kpi-value warn";
    } else if (state.credentials.validated) {
      oktaEl.textContent = "OK";
      oktaEl.className = "kpi-value ok";
    } else {
      oktaEl.textContent = "—";
      oktaEl.className = "kpi-value";
    }
    $("#kpi-progress").textContent = `${avgProgress()}%`;

    const list = $("#fleet-list");
    list.innerHTML = state.modes
      .map((mode) => {
        const info = state.robots[mode];
        const st = robotState(info);
        const pct = progressOf(info);
        const result = normalizeResult(info?.execution?.result);
        const meta = info?.running
          ? `${info.runtime?.status || "Em execução"} · ${pct}%`
          : result.label;
        const progressBar = info?.running
          ? `<div class="progress-track"><div class="progress-fill" style="width:${pct}%"></div></div>`
          : "";
        return `<li class="fleet-row ${st} ${mode === state.selectedMode ? "selected" : ""}" data-mode="${mode}">
          <span class="fleet-dot"></span>
          <div>
            <div class="fleet-name">${escapeHtml(labelFor(mode))}</div>
            <div class="fleet-meta">${escapeHtml(meta)}</div>
            ${progressBar}
          </div>
          <span class="fleet-pct">${info?.running ? `${pct}%` : ""}</span>
        </li>`;
      })
      .join("");

    const badge = $("#deck-fab-badge");
    const n = runningCount();
    badge.textContent = n > 0 ? String(n) : "";
  };

  const renderRobot = () => {
    const mode = state.selectedMode;
    const info = state.robots[mode];
    const pct = progressOf(info);
    const result = normalizeResult(info?.execution?.result);
    const st = robotState(info);

    $("#robot-detail").innerHTML = `
      <div class="detail-title">${escapeHtml(labelFor(mode))}</div>
      <div class="detail-status">${info?.running ? escapeHtml(info.runtime?.status || "Em execução") : `Último: ${escapeHtml(result.label)}`}</div>
      <div class="detail-grid">
        <div class="detail-stat">Progresso<strong>${pct}%</strong></div>
        <div class="detail-stat">Tempo<strong>${fmtDuration(info?.execution?.started_at)}</strong></div>
        <div class="detail-stat">PID<strong>${info?.pid ?? "—"}</strong></div>
        <div class="detail-stat">Estado<strong>${st}</strong></div>
      </div>`;

    const startCheck = canStartMode(mode);
    const btnStart = $("#btn-start");
    btnStart.disabled = !startCheck.ok || state.actionPending === "start";
    $("#btn-stop").disabled = !info?.running || state.actionPending === "stop";
    $("#start-hint").textContent = startCheck.ok ? "" : startCheck.reason;
  };

  const renderLogs = () => {
    $("#logs-label").textContent = `Logs · ${labelFor(state.selectedMode)}`;
    const box = $("#log-box");
    if (!state.logs.length) {
      box.innerHTML = '<div class="log-empty">Nenhuma linha de log</div>';
      return;
    }
    box.textContent = state.logs.join("\n");
    if (state.robots[state.selectedMode]?.running) {
      box.scrollTop = box.scrollHeight;
    }
  };

  const renderBackend = () => {
    const banner = $("#deck-api-banner");
    if (state.apiError) {
      banner.style.display = "block";
      banner.className = "api-banner";
      banner.textContent = state.apiError;
    } else {
      banner.style.display = "none";
    }

    const modeLabel = state.apiMode === "django" ? "Django (PPLID)" : state.apiMode === "flask" ? "Flask local" : "—";
    $("#deck-backend-label").textContent = `${modeLabel} · v${VERSION}`;
    $("#backend-info").innerHTML = `
      <div class="detail-status" style="margin:0">
        <strong style="color:#64c2bb">${escapeHtml(modeLabel)}</strong><br>
        Polling a cada ${POLL_MS / 1000}s quando aberto
      </div>`;
  };

  const render = () => {
    renderFleet();
    renderRobot();
    renderLogs();
    renderBackend();
    panel.classList.toggle("closed", !state.open);
    fab.classList.toggle("hidden", state.open);
  };

  /* ── API actions ── */
  const refreshStatus = async () => {
    if (!api.mode && !(await api.detect())) {
      render();
      return false;
    }
    const { ok } = await api.status();
    if (!ok) state.apiError = "Falha ao buscar status";
    else state.apiError = null;
    render();
    return ok;
  };

  const refreshLogs = async () => {
    if (!api.mode) return;
    await api.logs(state.selectedMode);
    renderLogs();
  };

  const bootstrap = async () => {
    state.loading = true;
    if (await api.detect()) {
      await api.meta();
      await refreshStatus();
      if (state.tab === "logs" || anyRunning()) await refreshLogs();
    }
    state.loading = false;
    render();
  };

  const runAction = async (name, fn) => {
    state.actionPending = name;
    render();
    try {
      const { ok, data } = await fn();
      const msg = data?.message || (ok ? "OK" : "Falha");
      toast(msg, !ok);
      if (data?.robots) state.robots = data.robots;
      if (data?.credentials) state.credentials = data.credentials;
      if (!ok) state.apiError = msg;
      else state.apiError = null;
      await refreshLogs();
    } catch {
      toast("Erro de rede", true);
    } finally {
      state.actionPending = null;
      render();
    }
  };

  const startPolling = () => {
    stopPolling();
    state.pollTimer = setInterval(async () => {
      if (!state.open) return;
      await refreshStatus();
      if (state.tab === "logs" || anyRunning()) await refreshLogs();
    }, POLL_MS);
  };

  const stopPolling = () => {
    if (state.pollTimer) {
      clearInterval(state.pollTimer);
      state.pollTimer = null;
    }
  };

  /* ── Events ── */
  $("#deck-min").addEventListener("click", (e) => {
    e.stopPropagation();
    setOpen(false);
  });

  $("#deck-close").addEventListener("click", (e) => {
    e.stopPropagation();
    deckApi.destroy();
  });

  shadow.querySelectorAll(".tab").forEach((btn) => {
    btn.addEventListener("click", () => setTab(btn.dataset.tab));
  });

  $("#fleet-list").addEventListener("click", (e) => {
    const row = e.target.closest("[data-mode]");
    if (!row) return;
    state.selectedMode = row.dataset.mode;
    persistUi();
    render();
  });

  $("#btn-start").addEventListener("click", () => {
    const mode = state.selectedMode;
    const check = canStartMode(mode);
    if (!check.ok) {
      toast(check.reason, true);
      return;
    }
    void runAction("start", () => api.start(mode));
  });

  $("#btn-stop").addEventListener("click", () => {
    void runAction("stop", () => api.stop(state.selectedMode));
  });

  $("#btn-stop-all").addEventListener("click", () => {
    if (!state.confirmStopAll) {
      state.confirmStopAll = true;
      $("#btn-stop-all").textContent = "Confirmar parar todos?";
      setTimeout(() => {
        state.confirmStopAll = false;
        $("#btn-stop-all").textContent = "Parar todos";
      }, 3000);
      return;
    }
    state.confirmStopAll = false;
    $("#btn-stop-all").textContent = "Parar todos";
    void runAction("stopAll", () => api.stopAll());
  });

  $("#btn-refresh").addEventListener("click", () => void bootstrap());
  $("#btn-refresh-logs").addEventListener("click", () => void refreshLogs());

  const openPplid = () => {
    const url = pplidAutomacaoUrl();
    if (url.startsWith("http")) window.open(url, "_blank");
    else toast("Abra o PPLID e navegue para Planejamento → Automação", true);
  };
  $("#btn-open-pplid").addEventListener("click", openPplid);
  $("#btn-open-pplid2").addEventListener("click", openPplid);

  $("#deck-drag").addEventListener("mousedown", (e) => {
    if (e.target.closest(".icon-btn")) return;
    const rect = panel.getBoundingClientRect();
    state.drag = { x: e.clientX, y: e.clientY, l: rect.left, t: rect.top };
    panel.style.right = "auto";
    panel.style.bottom = "auto";
    panel.style.left = `${rect.left}px`;
    panel.style.top = `${rect.top}px`;
  });

  fab.addEventListener("mousedown", (e) => {
    if (e.button !== 0) return;
    const rect = fab.getBoundingClientRect();
    state.fabMoved = false;
    state.fabDrag = { x: e.clientX, y: e.clientY, l: rect.left, t: rect.top };
    e.preventDefault();
  });

  document.addEventListener("mousemove", (e) => {
    if (state.drag) {
      const l = state.drag.l + e.clientX - state.drag.x;
      const t = state.drag.t + e.clientY - state.drag.y;
      panel.style.left = `${l}px`;
      panel.style.top = `${t}px`;
      state.pos = { x: l, y: t };
    }
    if (state.fabDrag) {
      const dx = e.clientX - state.fabDrag.x;
      const dy = e.clientY - state.fabDrag.y;
      if (Math.abs(dx) > 4 || Math.abs(dy) > 4) state.fabMoved = true;
      const l = state.fabDrag.l + dx;
      const t = state.fabDrag.t + dy;
      fab.style.right = "auto";
      fab.style.bottom = "auto";
      fab.style.left = `${l}px`;
      fab.style.top = `${t}px`;
      state.fabPos = { x: l, y: t };
    }
  });

  document.addEventListener("mouseup", () => {
    if (state.drag || state.fabDrag) persistUi();
    state.drag = null;
    state.fabDrag = null;
  });

  fab.addEventListener("click", () => {
    if (state.fabMoved) {
      state.fabMoved = false;
      return;
    }
    setOpen(true);
  });

  const onKey = (e) => {
    if (e.ctrlKey && e.shiftKey && e.key === ",") {
      e.preventDefault();
      setOpen(!state.open);
    }
  };
  document.addEventListener("keydown", onKey);

  const deckApi = {
    toggle() {
      setOpen(!state.open);
    },
    destroy() {
      stopPolling();
      document.removeEventListener("keydown", onKey);
      root.remove();
      delete window[NS];
    },
  };

  applyPositions();
  void bootstrap().then(() => startPolling());
  window[NS] = deckApi;
  console.info(`[Automação Deck] v${VERSION} ativo — Ctrl+Shift+, para toggle`);
})();
