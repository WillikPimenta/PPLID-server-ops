/* global window, document */
(function () {
  window.OpsConsole = window.OpsConsole || {};
  const OC = window.OpsConsole;

  const labels = {
    disabled: "Desabilitado",
    manual: "Failover manual",
    ready: "Failover automatico habilitado",
    blocked: "Failover automatico bloqueado",
  };

  function escape(value) {
    return OC.escapeHtml(String(value ?? "—"));
  }

  function statusClass(status) {
    if (status === "ready") return "ha-status-ready";
    if (status === "blocked") return "ha-status-blocked";
    if (status === "manual") return "ha-status-manual";
    return "ha-status-disabled";
  }

  function envRows(environments) {
    return Object.entries(environments || {})
      .map(([name, data]) => `
        <div class="ha-env-row">
          <strong>${escape(name)}</strong>
          <span class="ha-role">Postgres: ${escape(data.role || "unknown")}</span>
          <span class="ha-repl">Replicacao: ${escape(data.replicationStatus || "unknown")}</span>
          <span class="ha-write ${data.writeReady ? "is-ok" : "is-warn"}">${data.writeReady ? "gravavel" : "somente leitura/indisponivel"}</span>
        </div>`)
      .join("");
  }

  OC.renderHaPanel = function renderHaPanel(data) {
    const root = document.getElementById("ha-panel");
    if (!root) return;
    const ha = data || {};
    const status = ha.status || "disabled";
    const database = ha.database || {};
    const witness = ha.witness || {};
    const fencing = ha.fencing || {};
    const peer = ha.node?.peer || {};
    const automatic = ha.automaticFailoverAllowed === true;

    root.innerHTML = `
      <div class="section-head-row ha-panel-head">
        <div>
          <h2 class="section-title">Alta disponibilidade</h2>
          <p class="section-hint">Estado operacional local; acoes de cluster continuam protegidas por quorum e fencing.</p>
        </div>
        <span class="ha-status ${statusClass(status)}">${escape(labels[status] || status)}</span>
      </div>
      <div class="ha-grid">
        <div class="ha-summary-card">
          <dl class="detail-grid">
            <dt class="detail-label">No local</dt><dd class="detail-value"><code>${escape(ha.node?.id)}</code></dd>
            <dt class="detail-label">Endpoint DB</dt><dd class="detail-value"><code>${escape(database.endpoint)}:${escape(database.port)}</code></dd>
            <dt class="detail-label">Modo</dt><dd class="detail-value">${escape(database.failoverMode || "manual")}</dd>
          </dl>
        </div>
        <div class="ha-summary-card">
          <dl class="detail-grid">
            <dt class="detail-label">Testemunha</dt><dd class="detail-value">${escape(witness.status || "not_configured")}</dd>
            <dt class="detail-label">Fencing</dt><dd class="detail-value">${escape(fencing.state || "unknown")}</dd>
            <dt class="detail-label">Parceiro Ops</dt><dd class="detail-value">${escape(peer.status || "not_configured")}</dd>
          </dl>
          <p class="ha-safety-note ${automatic ? "is-ok" : "is-warn"}">${automatic ? "Promocao automatica pode ser considerada." : "Promocao automatica permanece bloqueada."}</p>
        </div>
      </div>
      <div class="ha-env-grid">${envRows(ha.environments)}</div>`;
    root.classList.remove("hidden");
  };
})();
