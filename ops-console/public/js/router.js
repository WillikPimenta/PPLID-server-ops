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
    if (parts[0] === "host") {
      return { view: "host", env: null, query };
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
    if (view === "host") return "/host";
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
    const previousRoute = OC.currentRoute;
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
    const hostView = document.getElementById("view-host");
    const monitorView = document.getElementById("view-monitoring");

    deployView?.classList.toggle("hidden", OC.currentRoute.view !== "deploy");
    envView?.classList.toggle("hidden", OC.currentRoute.view !== "env");
    dbView?.classList.toggle("hidden", OC.currentRoute.view !== "database");
    hostView?.classList.toggle("hidden", OC.currentRoute.view !== "host");
    monitorView?.classList.toggle("hidden", OC.currentRoute.view !== "monitoring");

    OC.stopMonitoringRefresh?.();
    OC.stopHostRefresh?.();

    const onMonitoring = OC.currentRoute.view === "monitoring";
    const onHost = OC.currentRoute.view === "host";
    const wasMonitoring = OC._wasOnMonitoringView === true;
    const wasHost = OC._wasOnHostView === true;
    const monitoringRouteChanged =
      previousRoute?.view === "monitoring" &&
      onMonitoring &&
      ((previousRoute.tab || "summary") !== (OC.currentRoute.tab || "summary") ||
        (previousRoute.env || "") !== (OC.currentRoute.env || "") ||
        JSON.stringify(previousRoute.query || {}) !== JSON.stringify(OC.currentRoute.query || {}));
    OC._wasOnMonitoringView = onMonitoring;
    OC._wasOnHostView = onHost;

    if (onMonitoring || onHost) {
      OC.stopAutoRefresh?.();
      if (OC._refreshAbort) {
        try {
          OC._refreshAbort.abort();
        } catch {
          /* ignore */
        }
      }
      const statusEl = document.getElementById("refresh-status");
      if (statusEl && OC.lastOverview?.generatedAt) {
        statusEl.textContent = `Última atualização: ${OC.formatDate(OC.lastOverview.generatedAt)} · ${onHost ? "host" : "monitoramento"}`;
      }
    } else if (!OC.authState?.locked && !OC.refreshPaused) {
      OC.startAutoRefresh?.();
    }

    if (OC.currentRoute.view === "deploy" && OC.lastOverview) {
      OC.renderDashboard(OC.lastOverview);
    } else if (OC.currentRoute.view === "env") {
      if (OC.renderEnvConfig) OC.renderEnvConfig(OC.currentRoute.env);
      else OC.ensureFeature?.("env").then(() => {
        if (OC.currentRoute?.view === "env") OC.renderEnvConfig?.(OC.currentRoute.env);
      }).catch((err) => OC.setGlobalError?.(err.message));
    } else if (OC.currentRoute.view === "database") {
      if (OC.renderDatabaseExplorer) OC.renderDatabaseExplorer(OC.currentRoute.env);
      else OC.ensureFeature?.("database").then(() => {
        if (OC.currentRoute?.view === "database") OC.renderDatabaseExplorer?.(OC.currentRoute.env);
      }).catch((err) => OC.setGlobalError?.(err.message));
    } else if (onHost) {
      const startHost = () => {
        if (OC.currentRoute?.view !== "host") return;
        if (!wasHost) OC.showHostLoading?.();
        OC.refreshHost?.({ force: !wasHost });
        OC.startHostRefresh?.();
      };
      if (OC.refreshHost) startHost();
      else OC.ensureFeature?.("host").then(startHost).catch((err) => OC.setGlobalError?.(err.message));
    } else if (onMonitoring) {
      const startMonitoring = () => {
        if (OC.currentRoute?.view !== "monitoring") return;
        if (!wasMonitoring) OC.showMonitoringLoading?.();
        OC.refreshMonitoring?.({
          showLoading: !wasMonitoring,
          force: monitoringRouteChanged,
          showFeedback: wasMonitoring && monitoringRouteChanged,
        });
        OC.startMonitoringRefresh?.();
      };
      if (OC.refreshMonitoring) startMonitoring();
      else OC.ensureFeature?.("monitoring").then(startMonitoring).catch((err) => OC.setGlobalError?.(err.message));
    }
    OC.updateSidebarActive?.();
  };

  function isAppPath(pathname) {
    const p = (pathname || "").replace(/\/+$/, "") || "/";
    if (p === "/" || p === "/deploy") return true;
    if (p.startsWith("/env/") || p === "/env") return true;
    if (p.startsWith("/database/") || p === "/database") return true;
    if (p === "/host") return true;
    if (p.startsWith("/monitoring/") || p === "/monitoring") return true;
    return false;
  }

  OC.bindRouter = function bindRouter() {
    window.addEventListener("popstate", () => OC.renderRoute());

    const sidebar = document.getElementById("app-sidebar");
    const toggle = document.getElementById("sidebar-toggle");
    const scrim = document.getElementById("sidebar-scrim");
    const layout = document.querySelector(".app-body-layout");
    const collapseToggle = document.getElementById("sidebar-collapse");
    const collapseIcon = document.getElementById("sidebar-collapse-icon");
    const sidebarPreferenceKey = "pplid-sidebar-collapsed";
    const setSidebarCollapsed = (collapsed, persist = false) => {
      layout?.classList.toggle("is-sidebar-collapsed", collapsed);
      sidebar?.classList.toggle("is-collapsed", collapsed);
      collapseToggle?.setAttribute("aria-expanded", collapsed ? "false" : "true");
      if (collapseToggle) {
        const label = collapsed ? "Expandir navegação" : "Recolher navegação";
        collapseToggle.setAttribute("aria-label", label);
        collapseToggle.title = label;
      }
      collapseIcon?.classList.toggle("is-reversed", collapsed);
      if (persist) {
        try {
          window.localStorage.setItem(sidebarPreferenceKey, collapsed ? "1" : "0");
        } catch {
          // A navegação continua funcional quando o armazenamento está indisponível.
        }
      }
    };
    let sidebarCollapsed = false;
    try {
      sidebarCollapsed = window.localStorage.getItem(sidebarPreferenceKey) === "1";
    } catch {
      sidebarCollapsed = false;
    }
    setSidebarCollapsed(sidebarCollapsed);
    collapseToggle?.addEventListener("click", () => {
      setSidebarCollapsed(!sidebar?.classList.contains("is-collapsed"), true);
    });
    const closeSidebar = () => {
      sidebar?.classList.remove("is-open");
      scrim?.classList.add("hidden");
      toggle?.setAttribute("aria-expanded", "false");
    };
    toggle?.addEventListener("click", () => {
      const open = !sidebar?.classList.contains("is-open");
      sidebar?.classList.toggle("is-open", open);
      scrim?.classList.toggle("hidden", !open);
      toggle.setAttribute("aria-expanded", open ? "true" : "false");
    });
    scrim?.addEventListener("click", closeSidebar);

    OC.updateSidebarActive = function updateSidebarActive() {
      let active = OC.currentRoute?.view || "deploy";
      if (active === "monitoring") {
        if (OC.currentRoute?.tab === "incidents") active = "incidents";
        else if (OC.currentRoute?.tab === "logs") active = "logs";
        else active = "performance";
      }
      document.querySelectorAll("[data-sidebar-view]").forEach((link) => {
        const selected = link.getAttribute("data-sidebar-view") === active;
        link.classList.toggle("is-active", selected);
        if (selected) link.setAttribute("aria-current", "page");
        else link.removeAttribute("aria-current");
      });
    };

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
      closeSidebar();
    });
    OC.updateSidebarActive();
  };
})();
