/* global window */
(function () {
  window.OpsConsole = window.OpsConsole || {};
  const OC = window.OpsConsole;

  OC.currentRoute = { view: "deploy", env: "DEV" };

  OC.parseRoute = function parseRoute(hash) {
    const raw = (hash || window.location.hash || "#/").replace(/^#/, "");
    const parts = raw.split("/").filter(Boolean);
    if (!parts.length || parts[0] === "deploy") {
      return { view: "deploy", env: "DEV" };
    }
    if (parts[0] === "env") {
      const env = (parts[1] || "DEV").toUpperCase();
      return { view: "env", env: OC.ENV_ORDER.includes(env) ? env : "DEV" };
    }
    if (parts[0] === "database") {
      const env = (parts[1] || "DEV").toUpperCase();
      return { view: "database", env: OC.ENV_ORDER.includes(env) ? env : "DEV" };
    }
    if (parts[0] === "monitoring") {
      return { view: "monitoring", env: "DEV" };
    }
    return { view: "deploy", env: "DEV" };
  };

  OC.navigate = function navigate(view, env) {
    let hash = "#/";
    if (view === "env") hash = `#/env/${env || "DEV"}`;
    else if (view === "database") hash = `#/database/${env || "DEV"}`;
    else if (view === "monitoring") hash = "#/monitoring";
    if (window.location.hash !== hash) {
      window.location.hash = hash;
    } else {
      OC.renderRoute();
    }
  };

  OC.navigateToEnvironments = function navigateToEnvironments(env) {
    OC.expandedEnvCards = OC.expandedEnvCards || new Set();
    if (env) OC.expandedEnvCards.add(env);
    OC.navigate("deploy", env);
  };

  OC.renderInternalPageHeader = function renderInternalPageHeader(opts) {
    const title = OC.escapeHtml(opts?.title || "");
    const env = opts?.env ? OC.escapeHtml(opts.env) : "";
    const subtitle = opts?.subtitle ? `<p class="page-internal-subtitle">${opts.subtitle}</p>` : "";
    return `<header class="page-internal-header">
      <nav class="page-internal-nav">
        <button type="button" class="view-back-btn" data-nav-back="environments" data-env="${env}">
          ← Voltar para Ambientes
        </button>
        <div class="page-internal-title-block">
          <h2 class="page-internal-title">${title}${env ? ` <span class="page-internal-env">— ${env}</span>` : ""}</h2>
          ${subtitle}
        </div>
      </nav>
    </header>`;
  };

  OC.renderBackToEnvironments = function renderBackToEnvironments(env) {
    return OC.renderInternalPageHeader({ title: "", env });
  };

  OC.bindBackNavigation = function bindBackNavigation(container) {
    if (!container) return;
    container.querySelector('[data-nav-back="environments"]')?.addEventListener("click", (e) => {
      const env = e.currentTarget.getAttribute("data-env") || null;
      OC.navigateToEnvironments(env || undefined);
    });
  };

  OC.renderRoute = function renderRoute() {
    OC.currentRoute = OC.parseRoute(window.location.hash);

    const deployView = document.getElementById("view-deploy");
    const envView = document.getElementById("view-env");
    const dbView = document.getElementById("view-database");
    const monitorView = document.getElementById("view-monitoring");

    deployView?.classList.toggle("hidden", OC.currentRoute.view !== "deploy");
    envView?.classList.toggle("hidden", OC.currentRoute.view !== "env");
    dbView?.classList.toggle("hidden", OC.currentRoute.view !== "database");
    monitorView?.classList.toggle("hidden", OC.currentRoute.view !== "monitoring");

    OC.stopMonitoringRefresh?.();

    if (OC.currentRoute.view === "deploy" && OC.lastOverview) {
      OC.renderDashboard(OC.lastOverview);
    } else if (OC.currentRoute.view === "env" && OC.renderEnvConfig) {
      OC.renderEnvConfig(OC.currentRoute.env);
    } else if (OC.currentRoute.view === "database" && OC.renderDatabaseExplorer) {
      OC.renderDatabaseExplorer(OC.currentRoute.env);
    } else if (OC.currentRoute.view === "monitoring" && OC.refreshMonitoring) {
      OC.refreshMonitoring();
      OC.startMonitoringRefresh?.();
    }
  };

  OC.bindRouter = function bindRouter() {
    window.addEventListener("hashchange", () => OC.renderRoute());
  };
})();
