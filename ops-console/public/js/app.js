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
OC._refreshInFlight = null;
OC._refreshQueued = false;
OC._refreshGeneration = 0;
OC._refreshAbort = null;
OC._hiddenResumeTimer = null;

OC.fetchJson = async function fetchJson(url, options = {}) {
  const timeoutMs = options.timeoutMs ?? OC.FETCH_TIMEOUT_MS ?? 8000;
  const externalSignal = options.signal;
  const controller = new AbortController();
  let timer = null;
  const onExternalAbort = () => controller.abort();
  if (externalSignal) {
    if (externalSignal.aborted) controller.abort();
    else externalSignal.addEventListener("abort", onExternalAbort, { once: true });
  }
  if (timeoutMs > 0) {
    timer = setTimeout(() => controller.abort(), timeoutMs);
  }
  try {
    const { timeoutMs: _t, signal: _s, ...rest } = options;
    const response = await fetch(url, {
      credentials: "include",
      ...rest,
      signal: controller.signal,
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
  } catch (err) {
    if (err?.name === "AbortError") {
      const abortErr = new Error("Requisicao cancelada ou expirou");
      abortErr.code = "aborted";
      throw abortErr;
    }
    throw err;
  } finally {
    if (timer) clearTimeout(timer);
    if (externalSignal) externalSignal.removeEventListener("abort", onExternalAbort);
  }
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
  if (OC.authState.locked || OC.refreshPaused || document.hidden) return;
  // Interval is re-read on each scheduled tick — no need to restart unless stopped.
  if (!OC.refreshTimer) startAutoRefresh();
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
      OC.migrateLegacyHashRoute?.();
      OC.currentRoute = OC.parseRoute();
      await OC.refresh({ full: true });
      OC.setDashboardVisible(true);
      OC.renderRoute();
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

// Exposed for views that refresh through their own polling loop.
OC.resetIdleTimer = resetIdleTimer;

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
  if (OC.refreshPaused) {
    stopAutoRefresh();
    OC.stopHostRefresh?.();
    OC.stopMonitoringRefresh?.();
  } else if (!OC.authState.locked) {
    if (OC.currentRoute?.view === "host") OC.startHostRefresh?.();
    else if (OC.currentRoute?.view === "monitoring") OC.startMonitoringRefresh?.();
    else startAutoRefresh();
  }
}

function stopAutoRefresh() {
  if (OC.refreshTimer) {
    clearTimeout(OC.refreshTimer);
    OC.refreshTimer = null;
  }
  OC._refreshIntervalMs = null;
  OC.stopDeployLogPolling?.();
}

function scheduleNextRefresh(ms) {
  if (OC.refreshTimer) {
    clearTimeout(OC.refreshTimer);
    OC.refreshTimer = null;
  }
  if (OC.authState.locked || OC.refreshPaused || document.hidden) return;
  if (["monitoring", "host"].includes(OC.currentRoute?.view)) return;
  const delay = Math.max(500, Number(ms) || getRefreshInterval());
  OC._refreshIntervalMs = delay;
  OC.refreshTimer = setTimeout(() => {
    OC.refreshTimer = null;
    OC.refresh().finally(() => {
      scheduleNextRefresh(getRefreshInterval());
    });
  }, delay);
}

function startAutoRefresh() {
  stopAutoRefresh();
  if (OC.authState.locked || OC.refreshPaused || document.hidden) return;
  if (["monitoring", "host"].includes(OC.currentRoute?.view)) return;
  scheduleNextRefresh(getRefreshInterval());
}

OC.stopAutoRefresh = stopAutoRefresh;
OC.startAutoRefresh = startAutoRefresh;

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

  // Coalesce: reuse in-flight promise; at most one follow-up after it finishes.
  if (OC._refreshInFlight) {
    OC._refreshQueued = true;
    OC._refreshQueuedOptions = { ...OC._refreshQueuedOptions, ...options };
    return OC._refreshInFlight;
  }

  const run = async () => {
    const full = options.full === true;
    const generation = ++OC._refreshGeneration;
    const statusEl = document.getElementById("refresh-status");

    if (OC._refreshAbort) {
      try {
        OC._refreshAbort.abort();
      } catch {
        /* ignore */
      }
    }
    OC._refreshAbort = new AbortController();
    const signal = OC._refreshAbort.signal;

    if (OC.currentRoute?.view === "monitoring") {
      OC.setDashboardVisible(true);
      if (statusEl && OC.lastOverview?.generatedAt) {
        statusEl.textContent = `Última atualização: ${OC.formatDate(OC.lastOverview.generatedAt)} · monitoramento`;
      }
      if (full && !OC.lastOverview) {
        try {
          OC.lastOverview = await OC.fetchJson("/api/v1/overview-lite", { signal });
        } catch {
          /* ignore — monitoring tab can still load */
        }
      }
      // Monitoring has its own polling loop; count a successful cycle as activity
      // so an active dashboard is not locked while it is being updated.
      resetIdleTimer();
      return;
    }
    try {
      if (statusEl) statusEl.textContent = "Atualizando…";
      OC.setGlobalError(null);

      // Always prefer lite for auto/boot; full only when explicitly requested.
      const endpoint = full ? "/api/v1/overview" : "/api/v1/overview-lite";
      const data = await OC.fetchJson(endpoint, { signal });
      if (generation !== OC._refreshGeneration) return; // stale response

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
      if (err.code === "aborted") return;
      if (err.code === 401) {
        OC.applyAuthState({ locked: true });
        return;
      }
      OC.setGlobalError(`Erro ao carregar: ${err.message}`);
      if (statusEl) statusEl.textContent = "Falha na atualização";
    }
  };

  OC._refreshInFlight = run().finally(() => {
    OC._refreshInFlight = null;
    if (OC._refreshQueued) {
      const queued = OC._refreshQueuedOptions || {};
      OC._refreshQueued = false;
      OC._refreshQueuedOptions = null;
      // Schedule a single coalesced follow-up (do not stack).
      Promise.resolve().then(() => OC.refresh(queued));
    }
  });
  return OC._refreshInFlight;
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
  document.getElementById("btn-refresh")?.addEventListener("click", () => {
    if (OC.currentRoute?.view === "host") OC.refreshHost?.({ force: true });
    else if (OC.currentRoute?.view === "monitoring") OC.refreshMonitoring?.({ showLoading: false });
    else OC.refresh({ full: true });
  });
  document.getElementById("btn-pause-refresh")?.addEventListener("click", togglePauseRefresh);
  document.getElementById("btn-theme")?.addEventListener("click", toggleTheme);

  OC.bindFilters?.(onFilterChange);
  OC.bindDrawer?.();
  OC.bindRouter();

  OC.migrateLegacyHashRoute?.();
  OC.currentRoute = OC.parseRoute();

  document.getElementById("filter-technical")?.addEventListener("change", (e) => {
    OC.showTechnicalActivity = !!e.target.checked;
    if (OC.lastOverview) {
      OC.lastDeploymentRows = OC.buildDeploymentRows(OC.lastOverview);
    }
  });

  document.addEventListener("click", () => {
    document.querySelectorAll(".action-menu").forEach((m) => m.classList.add("hidden"));
  });

  document.addEventListener("visibilitychange", () => {
    if (document.hidden) {
      stopAutoRefresh();
      OC.stopHostRefresh?.();
      OC.stopMonitoringRefresh?.();
      if (OC._refreshAbort) {
        try {
          OC._refreshAbort.abort();
        } catch {
          /* ignore */
        }
      }
      return;
    }
    if (OC.authState.locked || OC.refreshPaused) return;
    if (OC.currentRoute?.view === "host") {
      const refreshHost = OC.refreshHost?.();
      if (refreshHost?.finally) refreshHost.finally(() => OC.startHostRefresh?.());
      return;
    }
    if (OC.currentRoute?.view === "monitoring") {
      const refreshMonitoring = OC.refreshMonitoring?.({ showLoading: false });
      if (refreshMonitoring?.finally) refreshMonitoring.finally(() => OC.startMonitoringRefresh?.());
      return;
    }
    // Resume with jitter so many tabs don't align.
    const jitter = 200 + Math.floor(Math.random() * 800);
    if (OC._hiddenResumeTimer) clearTimeout(OC._hiddenResumeTimer);
    OC._hiddenResumeTimer = setTimeout(() => {
      OC._hiddenResumeTimer = null;
      OC.refresh().finally(() => startAutoRefresh());
    }, jitter);
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
      OC.migrateLegacyHashRoute?.();
      OC.currentRoute = OC.parseRoute();
      // Fast first paint via overview-lite (snapshots); progressive full load later if needed.
      await OC.refresh({ full: false });
      OC.setDashboardVisible(true);
      OC.renderRoute();
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
