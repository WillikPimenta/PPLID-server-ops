/* global window, document */
(function () {
  window.OpsConsole = window.OpsConsole || {};
  const OC = window.OpsConsole;

  OC.MONITOR_TABS = ["summary", "incidents", "latency", "syncs", "apis", "logs"];

  OC.currentRoute = { view: "deploy", env: "DEV" };

  OC.parseMonitoringRoute = function parseMonitoringRoute(parts, query) {
    const tabNames = OC.MONITOR_TABS;
    let tab = "summary";
    let env = null;
    if (parts.length >= 2) {
      const second = parts[1].toUpperCase();
      if (OC.ENV_ORDER.includes(second)) {
        env = second;
        if (parts[2] && tabNames.includes(parts[2].toLowerCase())) {
          tab = parts[2].toLowerCase();
        }
      } else if (tabNames.includes(parts[1].toLowerCase())) {
        tab = parts[1].toLowerCase();
      }
    }
    if (query.env && OC.ENV_ORDER.includes(query.env.toUpperCase())) {
      env = query.env.toUpperCase();
    }
    return { tab, env, query };
  };

  /** Normalize path+query or legacy #/hash into a route object. */
  OC.parseRoute = function parseRoute(locationLike) {
    let raw = locationLike;
    if (raw == null || raw === "") {
      const path = window.location.pathname || "/";
      const search = window.location.search || "";
      const hash = window.location.hash || "";
      // Prefer pathname; fall back to legacy hash when still on /
      if ((path === "/" || path === "") && hash.startsWith("#/")) {
        raw = hash.replace(/^#/, "");
      } else {
        raw = path + search;
      }
    } else if (String(raw).startsWith("#")) {
      raw = String(raw).replace(/^#/, "");
    }

    raw = String(raw);
    if (!raw.startsWith("/")) raw = `/${raw}`;

    const qIndex = raw.indexOf("?");
    const pathPart = qIndex >= 0 ? raw.slice(0, qIndex) : raw;
    const queryPart = qIndex >= 0 ? raw.slice(qIndex + 1) : "";
    const query = {};
    if (queryPart) {
      queryPart.split("&").forEach((pair) => {
        const [k, v] = pair.split("=");
        if (k) query[decodeURIComponent(k)] = decodeURIComponent(v || "");
      });
    }
    const parts = pathPart.split("/").filter(Boolean);
    if (!parts.length || parts[0] === "deploy") {
      return { view: "deploy", env: "DEV", query: {} };
    }
    if (parts[0] === "env") {
      const env = (parts[1] || "DEV").toUpperCase();
      return { view: "env", env: OC.ENV_ORDER.includes(env) ? env : "DEV", query: {} };
    }
    if (parts[0] === "database") {
      const env = (parts[1] || "DEV").toUpperCase();
      return { view: "database", env: OC.ENV_ORDER.includes(env) ? env : "DEV", query: {} };
    }
    if (parts[0] === "monitoring") {
      const mon = OC.parseMonitoringRoute(parts, query);
      return {
        view: "monitoring",
        env: mon.env || null,
        tab: mon.tab,
        query: mon.query,
      };
    }
    return { view: "deploy", env: "DEV", query: {} };
  };

  OC.buildAppPath = function buildAppPath(view, env, opts) {
    if (view === "env") return `/env/${env || "DEV"}`;
    if (view === "database") return `/database/${env || "DEV"}`;
    if (view === "monitoring") {
      const tab = opts?.tab || OC.monitorState?.activeTab || "summary";
      const params = new URLSearchParams(opts?.query || {});
      // Pin ?env= only with explicit focusEnv. Do not default to DEV for "all envs".
      if (opts?.focusEnv && OC.ENV_ORDER.includes(String(opts.focusEnv).toUpperCase())) {
        params.set("env", String(opts.focusEnv).toUpperCase());
      }
      const qs = params.toString();
      return `/monitoring/${tab}${qs ? `?${qs}` : ""}`;
    }
    return "/";
  };

  OC.migrateLegacyHashRoute = function migrateLegacyHashRoute() {
    const hash = window.location.hash || "";
    if (!hash.startsWith("#/")) return false;
    const next = hash.slice(1) || "/";
    window.history.replaceState({ migrated: true }, "", next);
    return true;
  };

  OC.navigate = function navigate(view, env, opts) {
    const path = OC.buildAppPath(view, env, opts);
    const current = `${window.location.pathname}${window.location.search}`;
    if (current !== path) {
      window.history.pushState({ view, env }, "", path);
    } else if (window.location.hash) {
      // Drop leftover # after same-path navigate
      window.history.replaceState({ view, env }, "", path);
    }
    OC.renderRoute();
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
    OC.currentRoute = OC.parseRoute();
    if (OC.currentRoute.view === "monitoring") {
      OC.monitorState = OC.monitorState || {};
      OC.monitorState.activeTab = OC.currentRoute.tab || "summary";
      if (OC.currentRoute.query?.env && OC.ENV_ORDER.includes(OC.currentRoute.query.env.toUpperCase())) {
        const focus = OC.currentRoute.query.env.toUpperCase();
        if (!OC.monitorState.selectedEnvs.includes(focus)) {
          OC.monitorState.selectedEnvs = [focus];
        }
      }
    }

    if (!OC.authState?.locked) {
      OC.setDashboardVisible?.(true);
    }

    const deployView = document.getElementById("view-deploy");
    const envView = document.getElementById("view-env");
    const dbView = document.getElementById("view-database");
    const monitorView = document.getElementById("view-monitoring");

    deployView?.classList.toggle("hidden", OC.currentRoute.view !== "deploy");
    envView?.classList.toggle("hidden", OC.currentRoute.view !== "env");
    dbView?.classList.toggle("hidden", OC.currentRoute.view !== "database");
    monitorView?.classList.toggle("hidden", OC.currentRoute.view !== "monitoring");

    OC.stopMonitoringRefresh?.();

    const onMonitoring = OC.currentRoute.view === "monitoring";
    const wasMonitoring = OC._wasOnMonitoringView === true;
    OC._wasOnMonitoringView = onMonitoring;

    if (onMonitoring) {
      OC.stopAutoRefresh?.();
      const statusEl = document.getElementById("refresh-status");
      if (statusEl && OC.lastOverview?.generatedAt) {
        statusEl.textContent = `Última atualização: ${OC.formatDate(OC.lastOverview.generatedAt)} · monitoramento`;
      }
    } else if (!OC.authState?.locked && !OC.refreshPaused) {
      OC.startAutoRefresh?.();
    }

    if (OC.currentRoute.view === "deploy" && OC.lastOverview) {
      OC.renderDashboard(OC.lastOverview);
    } else if (OC.currentRoute.view === "env" && OC.renderEnvConfig) {
      OC.renderEnvConfig(OC.currentRoute.env);
    } else if (OC.currentRoute.view === "database" && OC.renderDatabaseExplorer) {
      OC.renderDatabaseExplorer(OC.currentRoute.env);
    } else if (onMonitoring && OC.refreshMonitoring) {
      if (!wasMonitoring) OC.showMonitoringLoading?.();
      OC.refreshMonitoring({ showLoading: !wasMonitoring });
      OC.startMonitoringRefresh?.();
    }
  };

  function isAppPath(pathname) {
    const p = (pathname || "").replace(/\/+$/, "") || "/";
    if (p === "/" || p === "/deploy") return true;
    if (p.startsWith("/env/") || p === "/env") return true;
    if (p.startsWith("/database/") || p === "/database") return true;
    if (p.startsWith("/monitoring/") || p === "/monitoring") return true;
    return false;
  }

  OC.bindRouter = function bindRouter() {
    window.addEventListener("popstate", () => OC.renderRoute());

    // Internal SPA links (/monitoring/..., legacy #/...) without full reload
    document.addEventListener("click", (e) => {
      if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) {
        return;
      }
      const a = e.target.closest?.("a[href]");
      if (!a || a.target === "_blank" || a.hasAttribute("download")) return;
      const href = a.getAttribute("href") || "";
      if (!href || href.startsWith("mailto:") || href.startsWith("http://") || href.startsWith("https://")) {
        return;
      }

      let path = null;
      if (href.startsWith("#/")) {
        path = href.slice(1);
      } else if (href.startsWith("/")) {
        try {
          const u = new URL(href, window.location.origin);
          if (u.origin !== window.location.origin) return;
          if (!isAppPath(u.pathname)) return;
          path = u.pathname + u.search;
        } catch {
          return;
        }
      } else {
        return;
      }

      e.preventDefault();
      const current = `${window.location.pathname}${window.location.search}`;
      if (current !== path) {
        window.history.pushState({}, "", path);
      }
      OC.renderRoute();
    });
  };
})();
