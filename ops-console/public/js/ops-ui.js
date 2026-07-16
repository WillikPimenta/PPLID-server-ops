/* global window */
(function () {
  window.OpsConsole = window.OpsConsole || {};
  const OC = window.OpsConsole;

  OC.opsSvgIcon = function opsSvgIcon(kind) {
    const common = 'width="16" height="16" viewBox="0 0 16 16" fill="none" aria-hidden="true"';
    if (kind === "check") {
      return `<svg ${common}><path d="M3.5 8.5l3 3 6-7" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>`;
    }
    if (kind === "alert") {
      return `<svg ${common}><path d="M8 2.5L14 13.5H2L8 2.5Z" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round"/><path d="M8 6.5v3.2M8 11.5h.01" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg>`;
    }
    if (kind === "pulse") {
      return `<svg ${common}><path d="M1 8h3l2-4 3 8 2-4h4" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>`;
    }
    if (kind === "rocket") {
      return `<svg ${common}><path d="M9.5 2.5c2.2.4 3.6 1.8 4 4-1.6 1.7-3.5 2.8-5.2 3.2L6.5 12l-1-2.2L3 8.8l2.3-1.8C5.8 5.2 7 3.5 9.5 2.5Z" stroke="currentColor" stroke-width="1.4" stroke-linejoin="round"/><circle cx="10.2" cy="5.8" r="0.9" fill="currentColor"/></svg>`;
    }
    if (kind === "clock") {
      return `<svg ${common}><circle cx="8" cy="8" r="5.5" stroke="currentColor" stroke-width="1.5"/><path d="M8 5v3.2l2 1.3" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/></svg>`;
    }
    return "";
  };

  /**
   * @param {{ title: string, subtitle?: string, stats?: Array<{label:string,value:string,action?:string}>, compact?: boolean, back?: boolean, backLabel?: string }} opts
   */
  OC.renderOpsHero = function renderOpsHero(opts) {
    const title = OC.escapeHtml(opts?.title || "");
    const subtitle = opts?.subtitle
      ? `<p>${OC.escapeHtml(opts.subtitle)}</p>`
      : "";
    const compact = opts?.compact === true;
    const back = opts?.back
      ? `<div class="ops-hero-back monitor-hero-back">
          <button type="button" class="view-back-btn" data-nav-back="environments">${OC.escapeHtml(opts.backLabel || "← Voltar para Ambientes")}</button>
        </div>`
      : "";
    const stats = (opts?.stats || [])
      .map((s) => {
        const action = s.action ? ` data-ops-stat-action="${OC.escapeHtml(s.action)}" role="button" tabindex="0"` : "";
        const clickable = s.action ? " is-clickable" : "";
        return `<div class="ops-hero-stat monitor-hero-stat${clickable}"${action}><span class="ops-hero-stat-label monitor-hero-stat-label">${OC.escapeHtml(s.label)}</span><span class="ops-hero-stat-value monitor-hero-stat-value">${OC.escapeHtml(String(s.value ?? "—"))}</span></div>`;
      })
      .join("");
    return `<header class="ops-hero monitor-hero ${compact ? "ops-hero--compact" : ""}">
      <div class="ops-hero-inner monitor-hero-inner">
        <div class="ops-hero-copy monitor-hero-copy">
          ${back}
          <h1>${title}</h1>
          ${subtitle}
        </div>
        ${stats ? `<div class="ops-hero-stats monitor-hero-stats" aria-label="Indicadores rápidos">${stats}</div>` : ""}
      </div>
    </header>`;
  };

  /**
   * @param {Array<{ label: string, value: string, hint?: string, tone?: string, icon?: string, action?: string }>} items
   */
  OC.renderOpsKpiRow = function renderOpsKpiRow(items) {
    const cards = (items || [])
      .map((item) => {
        const tone = item.tone || "info";
        const icon = item.icon ? OC.opsSvgIcon(item.icon) : "";
        const action = item.action
          ? ` data-ops-kpi-action="${OC.escapeHtml(item.action)}" role="button" tabindex="0"`
          : "";
        const clickable = item.action ? " is-clickable" : "";
        return `<article class="ops-kpi-card monitor-kpi-card is-${OC.escapeHtml(tone)}${clickable}"${action} role="listitem">
          <div class="ops-kpi-label monitor-kpi-label">${icon ? `<span class="ops-kpi-icon monitor-kpi-icon">${icon}</span>` : ""}${OC.escapeHtml(item.label)}</div>
          <p class="ops-kpi-value monitor-kpi-value">${OC.escapeHtml(String(item.value ?? "—"))}</p>
          ${item.hint ? `<p class="ops-kpi-hint monitor-kpi-hint">${OC.escapeHtml(item.hint)}</p>` : ""}
        </article>`;
      })
      .join("");
    return `<div class="ops-kpi-grid monitor-kpi-grid" role="list">${cards}</div>`;
  };

  OC.bindOpsStatActions = function bindOpsStatActions(root, handlers) {
    if (!root || !handlers) return;
    const fire = (action, el) => {
      const fn = handlers[action];
      if (typeof fn === "function") fn(el);
    };
    root.querySelectorAll("[data-ops-stat-action], [data-ops-kpi-action]").forEach((el) => {
      const action = el.getAttribute("data-ops-stat-action") || el.getAttribute("data-ops-kpi-action");
      if (!action) return;
      el.addEventListener("click", () => fire(action, el));
      el.addEventListener("keydown", (e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          fire(action, el);
        }
      });
    });
  };

  OC.renderOpsSection = function renderOpsSection({ title, hint, body, className }) {
    const head =
      title || hint
        ? `<div class="ops-section-head monitor-section-head">
            ${title ? `<h3 class="ops-section-title monitor-section-title">${OC.escapeHtml(title)}</h3>` : ""}
            ${hint ? `<span class="monitor-meta-muted">${OC.escapeHtml(hint)}</span>` : ""}
          </div>`
        : "";
    return `<section class="ops-section monitor-section ${className || ""}">${head}${body || ""}</section>`;
  };

  /**
   * Single-select chip toolbar.
   * @param {{ id: string, label?: string, options: Array<{value:string,label:string}>, value: string, attr?: string }} opts
   */
  OC.renderOpsChipToolbar = function renderOpsChipToolbar(opts) {
    const attr = opts.attr || "data-ops-chip";
    const chips = (opts.options || [])
      .map((o) => {
        const active = String(o.value) === String(opts.value);
        return `<button type="button" class="ops-pill monitor-pill ${active ? "is-active" : ""}" ${attr}="${OC.escapeHtml(opts.id)}" data-value="${OC.escapeHtml(String(o.value))}">${OC.escapeHtml(o.label)}</button>`;
      })
      .join("");
    return `<div class="ops-toolbar monitor-api-toolbar">
      ${opts.label ? `<span class="ops-toolbar-label monitor-filter-label">${OC.escapeHtml(opts.label)}</span>` : ""}
      <div class="ops-pill-row monitor-pill-row">${chips}</div>
      ${opts.extra || ""}
    </div>`;
  };

  OC.bindOpsChipToolbar = function bindOpsChipToolbar(root, attr, onChange) {
    const name = attr || "data-ops-chip";
    root.querySelectorAll(`[${name}]`).forEach((el) => {
      el.addEventListener("click", () => {
        const id = el.getAttribute(name);
        const value = el.getAttribute("data-value");
        onChange(id, value, el);
      });
    });
  };
})();
