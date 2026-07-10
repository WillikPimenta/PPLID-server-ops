/* global window, document */
(function () {
  window.OpsConsole = window.OpsConsole || {};
  const OC = window.OpsConsole;

  OC.authState = {
  authenticated: false,
  locked: true,
  user: null,
  authSource: null,
  idleLockMinutes: 15,
};

OC.refreshTimer = null;
OC.idleTimer = null;
OC.clockTimer = null;
OC.durationTimer = null;
OC.refreshPaused = false;
OC.lastOverview = null;

OC.fetchJson = async function fetchJson(url, options = {}) {
  const response = await fetch(url, {
    credentials: "include",
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...(options.headers || {}),
    },
  });
  if (response.status === 401) {
    const err = new Error("Nao autorizado");
    err.code = 401;
    throw err;
  }
  const contentType = (response.headers.get("Content-Type") || "").toLowerCase();
  const isJson = contentType.includes("application/json") || contentType.includes("+json");
  if (!response.ok) {
    let detail = `API retornou ${response.status}`;
    if (isJson) {
      try {
        const data = await response.json();
        if (data.error) detail = data.error;
      } catch {
        /* ignore */
      }
    } else {
      const text = await response.text();
      if (text.trimStart().startsWith("<")) {
        detail = `API retornou HTML em vez de JSON (${response.status}). Reinicie o ops-console se rotas novas nao carregaram.`;
      }
    }
    throw new Error(detail);
  }
  if (!isJson) {
    const text = await response.text();
    if (text.trimStart().startsWith("<")) {
      throw new Error(
        "Resposta HTML em vez de JSON. Reinicie o ops-console ou verifique se a rota da API existe."
      );
    }
    throw new Error("Resposta da API nao e JSON");
  }
  return response.json();
};

OC.onUnauthorized = function onUnauthorized() {
  OC.applyAuthState({ locked: true });
};

/* Theme */
function getStoredTheme() {
  return localStorage.getItem(OC.THEME_STORAGE_KEY) === "dark" ? "dark" : "light";
}

function applyTheme(theme) {
  document.documentElement.setAttribute("data-theme", theme);
  document.documentElement.style.colorScheme = theme;
  const icon = document.getElementById("theme-toggle-icon");
  const btn = document.getElementById("btn-theme");
  if (icon) icon.textContent = theme === "dark" ? "☀" : "☽";
  if (btn) {
    btn.setAttribute("aria-label", theme === "dark" ? "Ativar tema claro" : "Ativar tema escuro");
    btn.title = theme === "dark" ? "Tema claro" : "Tema escuro";
  }
}

function toggleTheme() {
  const next = getStoredTheme() === "dark" ? "light" : "dark";
  localStorage.setItem(OC.THEME_STORAGE_KEY, next);
  applyTheme(next);
}

function getRefreshInterval() {
  if (!OC.lastOverview || !OC.getRunningEnvironments) return OC.LITE_REFRESH_MS || OC.REFRESH_MS;
  return OC.getRunningEnvironments(OC.lastOverview).length > 0
    ? OC.DEPLOY_REFRESH_MS
    : OC.LITE_REFRESH_MS || OC.REFRESH_MS;
}

function updateRefreshIntervalLabel() {
  const serverInfo = document.getElementById("server-info");
  if (!serverInfo || !OC.lastOverview) return;
  const server = OC.lastOverview.server || {};
  const sec = Math.round(getRefreshInterval() / 1000);
  serverInfo.textContent = `${server.hostname || "—"} · ${server.lanIp || "—"} · refresh ${sec}s`;
}

OC.restartAutoRefreshIfNeeded = function restartAutoRefreshIfNeeded() {
  if (OC.authState.locked || OC.refreshPaused) return;
  const next = getRefreshInterval();
  if (OC._refreshIntervalMs !== next) startAutoRefresh();
};

/* Lock screen */
function updateLockClock() {
  const el = document.getElementById("lock-clock");
  if (!el) return;
  el.textContent = new Date().toLocaleString("pt-BR", {
    weekday: "long",
    day: "numeric",
    month: "long",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function setLockError(message) {
  const el = document.getElementById("lock-error");
  if (!el) return;
  if (message) {
    el.textContent = message;
    el.hidden = false;
  } else {
    el.textContent = "";
    el.hidden = true;
  }
}

function configureLockForm() {
  const usernameWrap = document.getElementById("lock-username-wrap");
  const usernameInput = document.getElementById("lock-username");
  const passwordInput = document.getElementById("lock-password");
  const showUsernameOnly =
    OC.authState.authenticated && OC.authState.user?.username && OC.authState.locked;

  if (showUsernameOnly) {
    usernameWrap?.classList.add("hidden");
    if (usernameInput) {
      usernameInput.value = OC.authState.user.username;
      usernameInput.required = false;
    }
  } else {
    usernameWrap?.classList.remove("hidden");
    if (usernameInput) {
      usernameInput.required = true;
      if (!usernameInput.value && OC.authState.user?.username) {
        usernameInput.value = OC.authState.user.username;
      }
    }
  }
  if (passwordInput) passwordInput.value = "";
}

function showLockScreen() {
  document.getElementById("lock-screen")?.classList.remove("hidden");
  document.getElementById("lock-screen")?.setAttribute("aria-hidden", "false");
  document.body.classList.add("is-locked");
  configureLockForm();
  updateLockClock();
  document.getElementById("lock-password")?.focus();
  stopAutoRefresh();
  stopDurationTicker();
}

function hideLockScreen() {
  document.getElementById("lock-screen")?.classList.add("hidden");
  document.getElementById("lock-screen")?.setAttribute("aria-hidden", "true");
  document.body.classList.remove("is-locked");
  setLockError("");
  updateAuthBanner();
  resetIdleTimer();
  if (!OC.refreshPaused) startAutoRefresh();
  startDurationTicker();
}

function updateAuthBanner() {
  const banner = document.getElementById("auth-mode-banner");
  if (!banner) return;
  if (OC.authState.authSource === "bootstrap" && !OC.authState.locked) {
    banner.textContent = "Modo teste (bootstrap)";
    banner.classList.remove("hidden");
  } else {
    banner.classList.add("hidden");
  }
}

OC.applyAuthState = function applyAuthState(state) {
  OC.authState = { ...OC.authState, ...state };
  if (OC.authState.locked) showLockScreen();
  else hideLockScreen();
};

async function checkAuthStatus() {
  const status = await OC.fetchJson("/api/v1/auth/status");
  OC.applyAuthState(status);
  return status;
}

async function unlockWithCredentials(username, password) {
  setLockError("");
  try {
    const status = await OC.fetchJson("/api/v1/auth/unlock", {
      method: "POST",
      body: JSON.stringify({ username, password }),
    });
    OC.applyAuthState(status);
    if (!status.locked) {
      await OC.refresh({ full: true });
      tryEnterKioskFullscreen();
    }
    return true;
  } catch (err) {
    setLockError(err.message || "Falha ao desbloquear");
    return false;
  }
}

async function lockConsole() {
  try {
    const status = await OC.fetchJson("/api/v1/auth/lock", {
      method: "POST",
      body: JSON.stringify({}),
    });
    OC.applyAuthState(status);
  } catch (err) {
    if (err.code === 401) OC.applyAuthState({ locked: true, authenticated: false });
  }
}

function resetIdleTimer() {
  if (OC.idleTimer) clearTimeout(OC.idleTimer);
  if (OC.authState.locked || !OC.authState.authenticated) return;
  const minutes = OC.authState.idleLockMinutes || 15;
  if (minutes <= 0) return;
  OC.idleTimer = setTimeout(() => lockConsole(), minutes * 60 * 1000);
}

function bindIdleActivity() {
  ["mousemove", "mousedown", "keydown", "touchstart", "scroll"].forEach((name) => {
    document.addEventListener(
      name,
      () => {
        if (!OC.authState.locked) resetIdleTimer();
      },
      { passive: true }
    );
  });
}

function tryEnterKioskFullscreen() {
  if (!new URLSearchParams(window.location.search).has("kiosk")) return;
  if (document.documentElement.requestFullscreen && !document.fullscreenElement) {
    document.documentElement.requestFullscreen().catch(() => {});
  }
}

/* Refresh */
function updatePauseButton() {
  const btn = document.getElementById("btn-pause-refresh");
  const header = document.getElementById("app-header");
  if (!btn) return;
  if (OC.refreshPaused) {
    btn.textContent = "Retomar";
    btn.title = "Retomar atualização automática";
    header?.classList.add("refresh-paused");
  } else {
    btn.textContent = "Pausar";
    btn.title = "Pausar atualização automática";
    header?.classList.remove("refresh-paused");
  }
}

function togglePauseRefresh() {
  OC.refreshPaused = !OC.refreshPaused;
  updatePauseButton();
  if (OC.refreshPaused) stopAutoRefresh();
  else if (!OC.authState.locked) startAutoRefresh();
}

function stopAutoRefresh() {
  if (OC.refreshTimer) clearInterval(OC.refreshTimer);
  OC.refreshTimer = null;
  OC._refreshIntervalMs = null;
  OC.stopDeployLogPolling?.();
}

function startAutoRefresh() {
  stopAutoRefresh();
  if (OC.authState.locked || OC.refreshPaused) return;
  const ms = getRefreshInterval();
  OC._refreshIntervalMs = ms;
  OC.refreshTimer = setInterval(() => OC.refresh(), ms);
}

function startDurationTicker() {
  stopDurationTicker();
  OC.durationTimer = setInterval(() => {
    if (!OC.authState.locked) OC.updateLiveDurations();
  }, 1000);
}

function stopDurationTicker() {
  if (OC.durationTimer) clearInterval(OC.durationTimer);
  OC.durationTimer = null;
}

OC.refresh = async function refresh(options = {}) {
  if (OC.authState.locked) return;

  const full = options.full === true;
  const statusEl = document.getElementById("refresh-status");
  try {
    if (statusEl) statusEl.textContent = "Atualizando…";
    OC.setGlobalError(null);

    const endpoint = full ? "/api/v1/overview" : "/api/v1/overview-lite";
    const data = await OC.fetchJson(endpoint);
    if (!full && OC.lastOverview) {
      const merged = { ...OC.lastOverview, ...data, environments: {} };
      for (const name of OC.ENV_ORDER) {
        merged.environments[name] = {
          ...(OC.lastOverview.environments?.[name] || {}),
          ...(data.environments?.[name] || {}),
        };
      }
      OC.lastOverview = merged;
    } else {
      OC.lastOverview = data;
    }
    OC.lastDeploymentRows = OC.buildDeploymentRows(OC.lastOverview);

    updateRefreshIntervalLabel();

    OC.setDashboardVisible(true);
    const view = OC.currentRoute?.view || "deploy";

    if (view === "deploy") {
      await OC.refreshDeployView(OC.lastOverview, { incremental: !full });
    } else if (view === "database" && OC.refreshDatabasePartial) {
      if (full) {
        await OC.refreshDatabasePartial();
      }
    } else if (view === "env" && OC.envConfigState?.saved === false) {
      /* form com alterações pendentes — não re-renderizar */
    } else if (view === "env" && full) {
      /* variáveis — reload completo apenas em refresh manual */
    }

    if (statusEl) {
      const pausedNote = OC.refreshPaused ? " · auto-refresh pausado" : "";
      const modeNote = full ? "" : " · lite";
      statusEl.textContent = `Última atualização: ${OC.formatDate(data.generatedAt)}${modeNote}${pausedNote}`;
    }
    resetIdleTimer();
    OC.updateLiveDurations();
    OC.syncDeployProgress?.(OC.lastOverview);
    OC.restartAutoRefreshIfNeeded?.();
  } catch (err) {
    if (err.code === 401) {
      OC.applyAuthState({ locked: true });
      return;
    }
    OC.setGlobalError(`Erro ao carregar: ${err.message}`);
    if (statusEl) statusEl.textContent = "Falha na atualização";
  }
};

function onFilterChange() {
  /* Histórico movido para drawer por ambiente; filtros globais removidos do dashboard. */
}

function bindUi() {
  document.getElementById("lock-form")?.addEventListener("submit", async (e) => {
    e.preventDefault();
    const username = document.getElementById("lock-username")?.value?.trim() || "";
    const password = document.getElementById("lock-password")?.value || "";
    await unlockWithCredentials(username, password);
  });

  document.getElementById("btn-lock")?.addEventListener("click", lockConsole);
  document.getElementById("btn-refresh")?.addEventListener("click", () => OC.refresh({ full: true }));
  document.getElementById("btn-pause-refresh")?.addEventListener("click", togglePauseRefresh);
  document.getElementById("btn-theme")?.addEventListener("click", toggleTheme);
  document.getElementById("btn-open-monitoring")?.addEventListener("click", () => OC.navigate("monitoring"));

  OC.bindFilters(onFilterChange);
  OC.bindDrawer();
  OC.bindRouter();

  if (!window.location.hash) {
    window.location.hash = "#/";
  }
  OC.currentRoute = OC.parseRoute(window.location.hash);

  document.getElementById("filter-technical")?.addEventListener("change", (e) => {
    OC.showTechnicalActivity = !!e.target.checked;
    if (OC.lastOverview) {
      OC.lastDeploymentRows = OC.buildDeploymentRows(OC.lastOverview);
    }
  });

  document.addEventListener("click", () => {
    document.querySelectorAll(".action-menu").forEach((m) => m.classList.add("hidden"));
  });
}

async function bootstrap() {
  applyTheme(getStoredTheme());
  updatePauseButton();
  bindUi();
  bindIdleActivity();
  OC.clockTimer = setInterval(updateLockClock, 1000);

  try {
    await checkAuthStatus();
    if (!OC.authState.locked) {
      OC.currentRoute = OC.parseRoute(window.location.hash || "#/");
      await OC.refresh({ full: true });
      if (OC.currentRoute.view !== "deploy") {
        OC.renderRoute();
      }
      startAutoRefresh();
      startDurationTicker();
      tryEnterKioskFullscreen();
    } else {
      OC.setDashboardVisible(false);
    }
  } catch {
    showLockScreen();
    setLockError("Nao foi possivel verificar autenticacao");
    OC.setDashboardVisible(false);
  }
}

  bootstrap();
})();