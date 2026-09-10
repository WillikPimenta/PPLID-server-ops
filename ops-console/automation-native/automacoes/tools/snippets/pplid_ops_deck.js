/**
 * PPLID Ops Deck — painel flutuante de exemplo (estilo extensão).
 *
 * Funcionalidades:
 *   • FAB expansível com glass morphism e animações
 *   • Timer de sessão na página + info do site atual
 *   • Pomodoro foco (25/5) com alerta sonoro
 *   • Notas rápidas por domínio (localStorage)
 *   • Checklist de tarefas da sessão
 *   • Atalhos: copiar URL, título, markdown link
 *
 * Uso: DevTools → Sources → Snippets → colar e executar (Ctrl+Enter).
 *      Executar de novo para mostrar/ocultar. Ctrl+Shift+. para toggle global.
 */
(() => {
  const NS = "__PPLID_OPS_DECK__";
  const VERSION = "1.0.0";
  const STORAGE_KEY = "pplid_ops_deck_v1";
  const POMO_WORK = 25 * 60;
  const POMO_BREAK = 5 * 60;

  if (window[NS]) {
    window[NS].toggle();
    return;
  }

  const hostKey = () => location.hostname.replace(/\W/g, "_") || "default";

  const loadStore = () => {
    try {
      return JSON.parse(localStorage.getItem(STORAGE_KEY) || "{}");
    } catch {
      return {};
    }
  };

  const saveStore = (data) => {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(data));
    } catch {
      /* quota */
    }
  };

  const state = {
    open: true,
    tab: "home",
    sessionStart: Date.now(),
    pomo: { phase: "idle", remaining: POMO_WORK, running: false, tick: null },
    notes: loadStore().notes?.[hostKey()] || "",
    tasks: loadStore().tasks?.[hostKey()] || [],
    newTask: "",
    drag: null,
    fabDrag: null,
    fabMoved: false,
    pos: loadStore().pos || { x: null, y: null },
    fabPos: loadStore().fabPos || { x: null, y: null },
  };

  const persist = () => {
    const all = loadStore();
    all.notes = { ...(all.notes || {}), [hostKey()]: state.notes };
    all.tasks = { ...(all.tasks || {}), [hostKey()]: state.tasks };
    all.pos = state.pos;
    all.fabPos = state.fabPos;
    saveStore(all);
  };

  const fmtClock = (sec) => {
    const m = Math.floor(sec / 60);
    const s = sec % 60;
    return `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
  };

  const fmtDuration = (ms) => {
    const sec = Math.floor(ms / 1000);
    const h = Math.floor(sec / 3600);
    const m = Math.floor((sec % 3600) / 60);
    const s = sec % 60;
    if (h > 0) return `${h}h ${String(m).padStart(2, "0")}m`;
    return `${m}m ${String(s).padStart(2, "0")}s`;
  };

  const beep = () => {
    try {
      const ctx = new (window.AudioContext || window.webkitAudioContext)();
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.connect(gain);
      gain.connect(ctx.destination);
      osc.frequency.value = 880;
      gain.gain.setValueAtTime(0.08, ctx.currentTime);
      gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + 0.4);
      osc.start();
      osc.stop(ctx.currentTime + 0.4);
    } catch {
      /* sem áudio */
    }
  };

  const toast = (msg) => {
    let el = shadow.querySelector(".deck-toast");
    if (!el) {
      el = document.createElement("div");
      el.className = "deck-toast";
      shadow.appendChild(el);
    }
    el.textContent = msg;
    el.classList.add("visible");
    clearTimeout(toast._t);
    toast._t = setTimeout(() => el.classList.remove("visible"), 2200);
  };

  const copyText = async (text, label) => {
    try {
      await navigator.clipboard.writeText(text);
      toast(`${label} copiado`);
    } catch {
      toast("Falha ao copiar");
    }
  };

  /* ── DOM ── */
  const root = document.createElement("div");
  root.id = "pplid-ops-deck-root";
  document.body.appendChild(root);

  const shadow = root.attachShadow({ mode: "open" });
  shadow.innerHTML = `
<style>
  :host, * { box-sizing: border-box; margin: 0; padding: 0; }

  .deck-wrap {
    position: fixed;
    z-index: 2147483646;
    font-family: "Segoe UI", system-ui, -apple-system, sans-serif;
    font-size: 13px;
    color: #e8ecf4;
    -webkit-font-smoothing: antialiased;
  }

  /* ── FAB ── */
  .fab {
    position: fixed;
    bottom: 28px;
    right: 28px;
    width: 56px;
    height: 56px;
    border: none;
    border-radius: 18px;
    cursor: pointer;
    background: linear-gradient(135deg, #2a5595 0%, #1087aa 50%, #64c2bb 100%);
    box-shadow:
      0 8px 32px rgba(42, 85, 149, 0.45),
      0 0 0 1px rgba(255, 255, 255, 0.12) inset;
    display: flex;
    align-items: center;
    justify-content: center;
    transition: transform 0.25s cubic-bezier(0.34, 1.4, 0.64, 1), box-shadow 0.2s;
    z-index: 2;
  }
  .fab:hover { transform: scale(1.06); box-shadow: 0 12px 40px rgba(42, 85, 149, 0.55); }
  .fab:active { transform: scale(0.96); }
  .fab svg { width: 26px; height: 26px; color: #fff; filter: drop-shadow(0 1px 2px rgba(0,0,0,.2)); }
  .fab.hidden { opacity: 0; pointer-events: none; transform: scale(0.5); }

  /* ── Panel ── */
  .panel {
    position: fixed;
    bottom: 96px;
    right: 28px;
    width: 340px;
    max-height: min(520px, calc(100vh - 120px));
    border-radius: 20px;
    background: rgba(18, 22, 32, 0.82);
    backdrop-filter: blur(20px) saturate(1.4);
    -webkit-backdrop-filter: blur(20px) saturate(1.4);
    border: 1px solid rgba(255, 255, 255, 0.1);
    box-shadow:
      0 24px 64px rgba(0, 0, 0, 0.45),
      0 0 0 1px rgba(100, 194, 187, 0.06);
    display: flex;
    flex-direction: column;
    overflow: hidden;
    transform-origin: bottom right;
    transition:
      opacity 0.28s cubic-bezier(0.4, 0, 0.2, 1),
      transform 0.28s cubic-bezier(0.4, 0, 0.2, 1);
  }
  .panel.closed {
    opacity: 0;
    pointer-events: none;
    transform: scale(0.88) translateY(12px);
  }

  .panel-head {
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 14px 16px 12px;
    cursor: grab;
    user-select: none;
    border-bottom: 1px solid rgba(255, 255, 255, 0.06);
    background: linear-gradient(180deg, rgba(42, 85, 149, 0.18) 0%, transparent 100%);
  }
  .panel-head:active { cursor: grabbing; }

  .brand-icon {
    width: 36px;
    height: 36px;
    border-radius: 12px;
    background: linear-gradient(135deg, #2a5595, #1087aa);
    display: flex;
    align-items: center;
    justify-content: center;
    font-weight: 700;
    font-size: 14px;
    color: #fff;
    flex-shrink: 0;
    box-shadow: 0 4px 12px rgba(42, 85, 149, 0.35);
  }

  .brand-text h1 {
    font-size: 14px;
    font-weight: 600;
    letter-spacing: -0.02em;
    color: #f1f5f9;
  }
  .brand-text p {
    font-size: 10px;
    color: #8b95a8;
    margin-top: 1px;
  }

  .head-actions {
    margin-left: auto;
    display: flex;
    gap: 4px;
  }

  .icon-btn {
    width: 30px;
    height: 30px;
    border: none;
    border-radius: 10px;
    background: rgba(255, 255, 255, 0.06);
    color: #aab4c4;
    cursor: pointer;
    display: flex;
    align-items: center;
    justify-content: center;
    transition: background 0.15s, color 0.15s;
  }
  .icon-btn:hover { background: rgba(255, 255, 255, 0.12); color: #e8ecf4; }

  /* ── Tabs ── */
  .tabs {
    display: flex;
    gap: 2px;
    padding: 8px 12px 0;
    border-bottom: 1px solid rgba(255, 255, 255, 0.05);
  }
  .tab {
    flex: 1;
    padding: 8px 4px;
    border: none;
    background: none;
    color: #6b7280;
    font-size: 11px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    cursor: pointer;
    border-radius: 10px 10px 0 0;
    transition: color 0.15s, background 0.15s;
  }
  .tab:hover { color: #aab4c4; }
  .tab.active {
    color: #64c2bb;
    background: rgba(100, 194, 187, 0.08);
  }

  .panel-body {
    flex: 1;
    overflow-y: auto;
    padding: 14px 16px 16px;
    scrollbar-width: thin;
    scrollbar-color: rgba(255,255,255,.15) transparent;
  }
  .panel-body::-webkit-scrollbar { width: 5px; }
  .panel-body::-webkit-scrollbar-thumb { background: rgba(255,255,255,.15); border-radius: 4px; }

  .view { display: none; }
  .view.active { display: block; }

  /* ── Cards ── */
  .stat-grid {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 8px;
    margin-bottom: 12px;
  }
  .stat {
    padding: 12px;
    border-radius: 14px;
    background: rgba(255, 255, 255, 0.04);
    border: 1px solid rgba(255, 255, 255, 0.06);
  }
  .stat-label {
    font-size: 10px;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    color: #6b7280;
    margin-bottom: 4px;
  }
  .stat-value {
    font-size: 20px;
    font-weight: 700;
    font-variant-numeric: tabular-nums;
    color: #f1f5f9;
    letter-spacing: -0.02em;
  }
  .stat-value.accent { color: #64c2bb; }

  .site-card {
    padding: 12px;
    border-radius: 14px;
    background: rgba(42, 85, 149, 0.12);
    border: 1px solid rgba(42, 85, 149, 0.25);
    margin-bottom: 12px;
  }
  .site-domain {
    font-size: 11px;
    font-weight: 600;
    color: #64c2bb;
    margin-bottom: 4px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .site-title {
    font-size: 12px;
    color: #cbd5e1;
    line-height: 1.4;
    display: -webkit-box;
    -webkit-line-clamp: 2;
    -webkit-box-orient: vertical;
    overflow: hidden;
  }

  .actions {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 6px;
  }
  .action-btn {
    padding: 9px 10px;
    border: 1px solid rgba(255, 255, 255, 0.08);
    border-radius: 12px;
    background: rgba(255, 255, 255, 0.04);
    color: #cbd5e1;
    font-size: 11px;
    font-weight: 600;
    cursor: pointer;
    transition: background 0.15s, border-color 0.15s, transform 0.1s;
  }
  .action-btn:hover {
    background: rgba(100, 194, 187, 0.1);
    border-color: rgba(100, 194, 187, 0.3);
    color: #e8ecf4;
  }
  .action-btn:active { transform: scale(0.97); }
  .action-btn.full { grid-column: span 2; }

  /* ── Pomodoro ── */
  .pomo-ring-wrap {
    display: flex;
    flex-direction: column;
    align-items: center;
    padding: 8px 0 16px;
  }
  .pomo-ring {
    position: relative;
    width: 140px;
    height: 140px;
    margin-bottom: 16px;
  }
  .pomo-ring svg { transform: rotate(-90deg); }
  .pomo-ring circle { fill: none; stroke-width: 6; }
  .pomo-ring .track { stroke: rgba(255, 255, 255, 0.06); }
  .pomo-ring .progress {
    stroke: url(#pomoGrad);
    stroke-linecap: round;
    transition: stroke-dashoffset 0.5s linear;
  }
  .pomo-time {
    position: absolute;
    inset: 0;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
  }
  .pomo-time strong {
    font-size: 32px;
    font-weight: 700;
    font-variant-numeric: tabular-nums;
    color: #f1f5f9;
    letter-spacing: -0.03em;
  }
  .pomo-time span {
    font-size: 10px;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    color: #6b7280;
    margin-top: 2px;
  }

  .pomo-controls {
    display: flex;
    gap: 8px;
    width: 100%;
  }
  .pomo-btn {
    flex: 1;
    padding: 11px;
    border: none;
    border-radius: 12px;
    font-size: 12px;
    font-weight: 600;
    cursor: pointer;
    transition: transform 0.1s, opacity 0.15s;
  }
  .pomo-btn:active { transform: scale(0.97); }
  .pomo-btn.primary {
    background: linear-gradient(135deg, #2a5595, #1087aa);
    color: #fff;
    box-shadow: 0 4px 16px rgba(42, 85, 149, 0.4);
  }
  .pomo-btn.secondary {
    background: rgba(255, 255, 255, 0.06);
    color: #aab4c4;
    border: 1px solid rgba(255, 255, 255, 0.08);
  }

  /* ── Notes ── */
  .notes-area {
    width: 100%;
    min-height: 180px;
    padding: 12px;
    border-radius: 14px;
    border: 1px solid rgba(255, 255, 255, 0.08);
    background: rgba(0, 0, 0, 0.25);
    color: #e8ecf4;
    font-family: inherit;
    font-size: 12px;
    line-height: 1.55;
    resize: vertical;
    outline: none;
    transition: border-color 0.15s;
  }
  .notes-area:focus { border-color: rgba(100, 194, 187, 0.4); }
  .notes-hint {
    font-size: 10px;
    color: #5c6578;
    margin-top: 8px;
    text-align: center;
  }

  /* ── Tasks ── */
  .task-input-row {
    display: flex;
    gap: 6px;
    margin-bottom: 10px;
  }
  .task-input {
    flex: 1;
    padding: 10px 12px;
    border-radius: 12px;
    border: 1px solid rgba(255, 255, 255, 0.08);
    background: rgba(0, 0, 0, 0.25);
    color: #e8ecf4;
    font-size: 12px;
    outline: none;
  }
  .task-input:focus { border-color: rgba(100, 194, 187, 0.4); }
  .task-add {
    width: 40px;
    border: none;
    border-radius: 12px;
    background: linear-gradient(135deg, #2a5595, #1087aa);
    color: #fff;
    font-size: 18px;
    cursor: pointer;
    flex-shrink: 0;
  }
  .task-list { list-style: none; display: flex; flex-direction: column; gap: 4px; }
  .task-item {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 9px 10px;
    border-radius: 12px;
    background: rgba(255, 255, 255, 0.03);
    border: 1px solid rgba(255, 255, 255, 0.05);
    transition: opacity 0.2s;
  }
  .task-item.done { opacity: 0.45; }
  .task-item.done .task-text { text-decoration: line-through; }
  .task-check {
    width: 18px;
    height: 18px;
    border-radius: 6px;
    border: 2px solid rgba(100, 194, 187, 0.5);
    background: transparent;
    cursor: pointer;
    flex-shrink: 0;
    display: flex;
    align-items: center;
    justify-content: center;
    color: transparent;
    font-size: 11px;
    transition: background 0.15s, color 0.15s;
  }
  .task-item.done .task-check {
    background: #64c2bb;
    border-color: #64c2bb;
    color: #0f172a;
  }
  .task-text { flex: 1; font-size: 12px; color: #cbd5e1; }
  .task-del {
    border: none;
    background: none;
    color: #5c6578;
    cursor: pointer;
    font-size: 14px;
    padding: 2px 4px;
    border-radius: 6px;
  }
  .task-del:hover { color: #f87171; background: rgba(239, 68, 68, 0.1); }
  .task-empty {
    text-align: center;
    padding: 24px;
    color: #5c6578;
    font-size: 12px;
  }

  /* ── Toast ── */
  .deck-toast {
    position: fixed;
    bottom: 100px;
    left: 50%;
    transform: translateX(-50%) translateY(8px);
    padding: 8px 16px;
    border-radius: 999px;
    background: rgba(15, 23, 42, 0.92);
    border: 1px solid rgba(100, 194, 187, 0.3);
    color: #64c2bb;
    font-size: 12px;
    font-weight: 600;
    opacity: 0;
    pointer-events: none;
    transition: opacity 0.2s, transform 0.2s;
    z-index: 10;
    white-space: nowrap;
  }
  .deck-toast.visible {
    opacity: 1;
    transform: translateX(-50%) translateY(0);
  }

  .panel-footer {
    padding: 8px 16px 12px;
    font-size: 9px;
    color: #4b5563;
    text-align: center;
    border-top: 1px solid rgba(255, 255, 255, 0.04);
  }
  kbd {
    padding: 1px 5px;
    border-radius: 4px;
    background: rgba(255, 255, 255, 0.08);
    font-family: inherit;
    font-size: 9px;
  }
</style>

<div class="deck-wrap">
  <button class="fab" id="deck-fab" title="Ops Deck" aria-label="Abrir Ops Deck">
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round">
      <rect x="3" y="3" width="7" height="7" rx="1.5"/>
      <rect x="14" y="3" width="7" height="7" rx="1.5"/>
      <rect x="3" y="14" width="7" height="7" rx="1.5"/>
      <rect x="14" y="14" width="7" height="7" rx="1.5"/>
    </svg>
  </button>

  <div class="panel" id="deck-panel">
    <div class="panel-head" id="deck-drag">
      <div class="brand-icon">OP</div>
      <div class="brand-text">
        <h1>Ops Deck</h1>
        <p>PPLID · v${VERSION}</p>
      </div>
      <div class="head-actions">
        <button class="icon-btn" id="deck-min" title="Minimizar">−</button>
        <button class="icon-btn" id="deck-close" title="Fechar">×</button>
      </div>
    </div>

    <div class="tabs" role="tablist">
      <button class="tab active" data-tab="home">Início</button>
      <button class="tab" data-tab="focus">Foco</button>
      <button class="tab" data-tab="notes">Notas</button>
      <button class="tab" data-tab="tasks">Tarefas</button>
    </div>

    <div class="panel-body">
      <div class="view active" data-view="home">
        <div class="stat-grid">
          <div class="stat">
            <div class="stat-label">Sessão</div>
            <div class="stat-value accent" id="deck-session">0m 00s</div>
          </div>
          <div class="stat">
            <div class="stat-label">Tarefas</div>
            <div class="stat-value" id="deck-task-count">0</div>
          </div>
        </div>
        <div class="site-card">
          <div class="site-domain" id="deck-domain">—</div>
          <div class="site-title" id="deck-title">—</div>
        </div>
        <div class="actions">
          <button class="action-btn" data-copy="url">Copiar URL</button>
          <button class="action-btn" data-copy="title">Copiar título</button>
          <button class="action-btn" data-copy="md">Markdown link</button>
          <button class="action-btn" data-copy="jira">Ticket Jira</button>
        </div>
      </div>

      <div class="view" data-view="focus">
        <div class="pomo-ring-wrap">
          <div class="pomo-ring">
            <svg width="140" height="140" viewBox="0 0 140 140">
              <defs>
                <linearGradient id="pomoGrad" x1="0%" y1="0%" x2="100%" y2="100%">
                  <stop offset="0%" stop-color="#2a5595"/>
                  <stop offset="100%" stop-color="#64c2bb"/>
                </linearGradient>
              </defs>
              <circle class="track" cx="70" cy="70" r="62"/>
              <circle class="progress" id="deck-pomo-ring" cx="70" cy="70" r="62"
                stroke-dasharray="389.56" stroke-dashoffset="0"/>
            </svg>
            <div class="pomo-time">
              <strong id="deck-pomo-clock">25:00</strong>
              <span id="deck-pomo-phase">Foco</span>
            </div>
          </div>
          <div class="pomo-controls">
            <button class="pomo-btn primary" id="deck-pomo-toggle">Iniciar</button>
            <button class="pomo-btn secondary" id="deck-pomo-reset">Reset</button>
          </div>
        </div>
      </div>

      <div class="view" data-view="notes">
        <textarea class="notes-area" id="deck-notes" placeholder="Anotações desta página…"></textarea>
        <p class="notes-hint">Salvo automaticamente por domínio</p>
      </div>

      <div class="view" data-view="tasks">
        <div class="task-input-row">
          <input class="task-input" id="deck-task-input" placeholder="Nova tarefa…" maxlength="120"/>
          <button class="task-add" id="deck-task-add" title="Adicionar">+</button>
        </div>
        <ul class="task-list" id="deck-task-list"></ul>
      </div>
    </div>

    <div class="panel-footer">Arraste pelo topo · <kbd>Ctrl</kbd>+<kbd>Shift</kbd>+<kbd>.</kbd> toggle</div>
  </div>
</div>`;

  const $ = (sel) => shadow.querySelector(sel);
  const panel = $("#deck-panel");
  const fab = $("#deck-fab");
  const wrap = shadow.querySelector(".deck-wrap");

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
    state.open = open;
    panel.classList.toggle("closed", !open);
    fab.classList.toggle("hidden", open);
  };

  const setTab = (tab) => {
    state.tab = tab;
    shadow.querySelectorAll(".tab").forEach((el) => {
      el.classList.toggle("active", el.dataset.tab === tab);
    });
    shadow.querySelectorAll(".view").forEach((el) => {
      el.classList.toggle("active", el.dataset.view === tab);
    });
  };

  const pomoTotal = () =>
    state.pomo.phase === "break" ? POMO_BREAK : POMO_WORK;

  const updatePomoRing = () => {
    const total = pomoTotal();
    const pct = state.pomo.remaining / total;
    const circumference = 2 * Math.PI * 62;
    const ring = $("#deck-pomo-ring");
    ring.style.strokeDashoffset = String(circumference * (1 - pct));
    $("#deck-pomo-clock").textContent = fmtClock(state.pomo.remaining);
    $("#deck-pomo-phase").textContent =
      state.pomo.phase === "break" ? "Pausa" : state.pomo.phase === "idle" ? "Pronto" : "Foco";
    $("#deck-pomo-toggle").textContent = state.pomo.running ? "Pausar" : "Iniciar";
  };

  const stopPomo = () => {
    if (state.pomo.tick) {
      clearInterval(state.pomo.tick);
      state.pomo.tick = null;
    }
    state.pomo.running = false;
  };

  const startPomo = () => {
    if (state.pomo.phase === "idle") {
      state.pomo.phase = "work";
      state.pomo.remaining = POMO_WORK;
    }
    state.pomo.running = true;
    stopPomo();
    state.pomo.tick = setInterval(() => {
      state.pomo.remaining -= 1;
      if (state.pomo.remaining <= 0) {
        beep();
        if (state.pomo.phase === "work") {
          state.pomo.phase = "break";
          state.pomo.remaining = POMO_BREAK;
          toast("Pausa! 5 minutos");
        } else {
          state.pomo.phase = "work";
          state.pomo.remaining = POMO_WORK;
          toast("Hora de focar!");
        }
      }
      updatePomoRing();
    }, 1000);
    updatePomoRing();
  };

  const renderTasks = () => {
    const list = $("#deck-task-list");
    const pending = state.tasks.filter((t) => !t.done).length;
    $("#deck-task-count").textContent = String(pending);

    if (!state.tasks.length) {
      list.innerHTML = '<li class="task-empty">Nenhuma tarefa ainda</li>';
      return;
    }

    list.innerHTML = state.tasks
      .map(
        (t, i) => `
      <li class="task-item ${t.done ? "done" : ""}" data-idx="${i}">
        <button class="task-check" data-action="toggle" data-idx="${i}">${t.done ? "✓" : ""}</button>
        <span class="task-text">${escapeHtml(t.text)}</span>
        <button class="task-del" data-action="del" data-idx="${i}">×</button>
      </li>`,
      )
      .join("");
  };

  const escapeHtml = (s) =>
    s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");

  const renderHome = () => {
    $("#deck-session").textContent = fmtDuration(Date.now() - state.sessionStart);
    $("#deck-domain").textContent = location.hostname || "local";
    $("#deck-title").textContent = document.title || "Sem título";
    renderTasks();
  };

  const render = () => {
    renderHome();
    updatePomoRing();
    $("#deck-notes").value = state.notes;
    renderTasks();
    setOpen(state.open);
    setTab(state.tab);
  };

  /* ── Events ── */
  $("#deck-min").addEventListener("click", (e) => {
    e.stopPropagation();
    setOpen(false);
  });
  $("#deck-close").addEventListener("click", (e) => {
    e.stopPropagation();
    api.destroy();
  });

  shadow.querySelectorAll(".tab").forEach((btn) => {
    btn.addEventListener("click", () => setTab(btn.dataset.tab));
  });

  shadow.querySelectorAll("[data-copy]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const kind = btn.dataset.copy;
      const title = document.title;
      const url = location.href;
      const map = {
        url: [url, "URL"],
        title: [title, "Título"],
        md: [`[${title}](${url})`, "Markdown"],
        jira: [`${title}\n${url}`, "Jira"],
      };
      const [text, label] = map[kind] || [url, "Texto"];
      copyText(text, label);
    });
  });

  $("#deck-pomo-toggle").addEventListener("click", () => {
    if (state.pomo.running) stopPomo();
    else startPomo();
    updatePomoRing();
  });

  $("#deck-pomo-reset").addEventListener("click", () => {
    stopPomo();
    state.pomo.phase = "idle";
    state.pomo.remaining = POMO_WORK;
    updatePomoRing();
  });

  $("#deck-notes").addEventListener("input", (e) => {
    state.notes = e.target.value;
    persist();
  });

  const addTask = () => {
    const text = state.newTask.trim() || $("#deck-task-input").value.trim();
    if (!text) return;
    state.tasks.unshift({ text, done: false, at: Date.now() });
    state.newTask = "";
    $("#deck-task-input").value = "";
    persist();
    renderTasks();
  };

  $("#deck-task-add").addEventListener("click", addTask);
  $("#deck-task-input").addEventListener("keydown", (e) => {
    if (e.key === "Enter") addTask();
  });

  $("#deck-task-list").addEventListener("click", (e) => {
    const btn = e.target.closest("[data-action]");
    if (!btn) return;
    const idx = Number(btn.dataset.idx);
    if (btn.dataset.action === "toggle") {
      state.tasks[idx].done = !state.tasks[idx].done;
    } else if (btn.dataset.action === "del") {
      state.tasks.splice(idx, 1);
    }
    persist();
    renderTasks();
  });

  /* ── Drag panel ── */
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
    if (state.drag || state.fabDrag) persist();
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

  /* ── Keyboard shortcut ── */
  const onKey = (e) => {
    if (e.ctrlKey && e.shiftKey && e.key === ".") {
      e.preventDefault();
      setOpen(!state.open);
    }
  };
  document.addEventListener("keydown", onKey);

  /* ── Session ticker ── */
  const sessionTick = setInterval(() => {
    if (state.open && state.tab === "home") {
      $("#deck-session").textContent = fmtDuration(Date.now() - state.sessionStart);
    }
  }, 1000);

  const api = {
    toggle() {
      setOpen(!state.open);
    },
    destroy() {
      stopPomo();
      clearInterval(sessionTick);
      document.removeEventListener("keydown", onKey);
      root.remove();
      delete window[NS];
    },
  };

  applyPositions();
  render();
  window[NS] = api;
  console.info(`[Ops Deck] v${VERSION} ativo — Ctrl+Shift+. para toggle`);
})();
