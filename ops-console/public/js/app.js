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
  let timedOut = false;
  let externallyAborted = false;
  const onExternalAbort = () => {
    externallyAborted = true;
    controller.abort();
  };
  if (externalSignal) {
    if (externalSignal.aborted) controller.abort();
    else externalSignal.addEventListener("abort", onExternalAbort, { once: true });
  }
  if (timeoutMs > 0) {
    timer = setTimeout(() => {
      timedOut = true;
      controller.abort();
    }, timeoutMs);
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
      const seconds = timeoutMs > 0 ? Math.round(timeoutMs / 1000) : 0;
      const abortErr = new Error(
        timedOut
          ? `A requisição expirou após ${seconds}s sem resposta. O servidor pode ainda estar processando a operação.`
          : externallyAborted
            ? "A requisição foi cancelada porque uma consulta mais recente assumiu o controle."
            : "A requisição foi cancelada pelo navegador."
      );
      abortErr.code = "aborted";
      abortErr.timedOut = timedOut;
      abortErr.url = url;
      abortErr.timeoutMs = timeoutMs;
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
  if (OC.authState.locked || OC.refreshPaused || document.hidden || !OC.isDeployView?.()) return;
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
  if (!OC.refreshPaused && OC.isDeployView?.()) startAutoRefresh();
  startDurationTicker();
}

function updateAuthBanner() {
  const banner = document.getElementById("auth-mode-banner");
  if (!banner) return;
  if (["recovery", "teams_recovery"].includes(OC.authState.authSource) && !OC.authState.locked) {
    banner.textContent = "Acesso emergencial temporario";
    banner.classList.remove("hidden");
  } else {
    banner.classList.add("hidden");
  }
}

OC.applyAuthState = function applyAuthState(state) {
  OC.authState = { ...OC.authState, ...state };
  document.getElementById("btn-teams-recovery")?.classList.toggle("hidden", !OC.authState.teamsRecoveryEnabled);
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
  // A TV panel is a passive display and must remain available without input.
  if (window.location.pathname === "/monitoring/tv") return;
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
    else if (OC.isDeployView?.()) startAutoRefresh();
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
  if (OC.authState.locked || OC.refreshPaused || document.hidden || !OC.isDeployView?.()) return;
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
  // Restart the overview timer without interrupting the independent deploy-log poller.
  if (OC.refreshTimer) clearTimeout(OC.refreshTimer);
  OC.refreshTimer = null;
  if (OC.authState.locked || OC.refreshPaused || document.hidden || !OC.isDeployView?.()) return;
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
      const data = await OC.fetchJson(endpoint, { signal, timeoutMs: full ? 45000 : 20000 });
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
      if (err.code === "aborted" && !err.timedOut) return;
      if (err.code === 401) {
        OC.applyAuthState({ locked: true });
        return;
      }
      const last = OC.lastOverview?.generatedAt ? OC.formatDate(OC.lastOverview.generatedAt) : null;
      OC.setGlobalError(`Falha na atualização: ${err.message}${last ? ` · Exibindo dados de ${last}.` : ""}`, { preserveDashboard: Boolean(OC.lastOverview) });
      if (OC.lastOverview && OC.isDeployView?.()) OC.setDashboardVisible?.(true);
      if (statusEl) statusEl.textContent = last ? `Falha na atualização · últimos dados: ${last}` : "Falha na atualização";
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
  let recoveryRequest = null;
  const recoveryMessage = document.getElementById("teams-recovery-status");
  const recoveryForm = document.getElementById("teams-recovery-form");
  document.getElementById("btn-teams-recovery")?.addEventListener("click", async (event) => {
    const button = event.currentTarget;
    const username = document.getElementById("lock-username")?.value?.trim();
    if (!username) { setLockError("Informe seu usuario antes de solicitar recuperacao."); return; }
    button.disabled = true;
    recoveryRequest = null;
    recoveryForm.classList.add("hidden");
    try {
      recoveryRequest = await OC.fetchJson("/api/v1/auth/recovery/request", { method: "POST", timeoutMs: 20000, body: JSON.stringify({ username }) });
      recoveryMessage.textContent = "Pedido " + recoveryRequest.requestId + ": solicite o codigo aos responsaveis no Recovery OP. Validade: 10 minutos. Mantenha esta pagina aberta.";
      recoveryForm.reset();
      recoveryForm.classList.remove("hidden");
      document.getElementById("teams-recovery-code").focus();
    } catch (error) { recoveryMessage.textContent = error.message; }
    finally { button.disabled = false; }
  });
  recoveryForm?.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!recoveryRequest) return;
    const button = recoveryForm.querySelector("button");
    button.disabled = true;
    try {
      const code = document.getElementById("teams-recovery-code").value.trim();
      const status = await OC.fetchJson("/api/v1/auth/recovery/verify", { method: "POST", body: JSON.stringify({ ...recoveryRequest, code }) });
      if (status.authenticated) { recoveryRequest = null; window.location.reload(); }
    } catch (error) { recoveryMessage.textContent = "Codigo incorreto, pedido encerrado ou expirado. Sao permitidas ate 5 tentativas."; }
    finally { button.disabled = false; }
  });
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
    if (OC.isDeployView?.()) {
      // Resume with jitter so many tabs don't align.
      const jitter = 200 + Math.floor(Math.random() * 800);
      if (OC._hiddenResumeTimer) clearTimeout(OC._hiddenResumeTimer);
      OC._hiddenResumeTimer = setTimeout(() => {
        OC._hiddenResumeTimer = null;
        OC.refresh().finally(() => startAutoRefresh());
      }, jitter);
    }
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
      if (OC.isDeployView?.()) startAutoRefresh();
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
