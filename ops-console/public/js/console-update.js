/* global window, document, navigator */
(function () {
  window.OpsConsole = window.OpsConsole || {};
  const OC = window.OpsConsole;

  const STATUS_URL = "/api/v1/console/update/status";
  const LOG_URL = "/api/v1/console/update/log?limit=200";
  const APPLY_URL = "/api/v1/console/update/apply";
  const CANCEL_URL = "/api/v1/console/update/cancel";
  const POLL_INTERVAL_MS = 2000;
  // Dependencias/bootstrap podem ultrapassar dois minutos em máquinas mais
  // lentas. Enquanto o lock estiver ativo, não tratar a atualização como erro.
  const POLL_TIMEOUT_MS = 900000;
  const RESTART_TIMEOUT_MS = 180000;
  // O endpoint executa a verificacao Git antes de iniciar o worker. Em PCs
  // com rede/DNS/credencial mais lentos, o timeout global de 8s cancela a
  // requisicao embora o servidor ainda esteja processando a atualizacao.
  const APPLY_REQUEST_TIMEOUT_MS = 150000;

  OC.consoleUpdateState = {
    status: null,
    busy: false,
    uiState: "idle",
    lastCheckedAt: null,
    lastStatusAttemptAt: 0,
    lastError: null,
    dismissedErrorKey: null,
    activityStartedAt: null,
    activityLabel: null,
    applyStartedAt: null,
    elapsedTimer: null,
    logOpen: false,
    logLoading: false,
    logLines: [],
    logError: null,
    statusInFlight: null,
    progressInFlight: null,
    cancelPending: false,
    logInFlight: null,
    logUpdatedAt: null,
    logTimer: null,
  };

  const SILENT_STATUS_MIN_INTERVAL_MS = 15000;

  function getBarRoot() {
    return document.getElementById("deploy-console-update");
  }

  function setBarVisible(visible) {
    const root = getBarRoot();
    if (!root) return;
    root.classList.toggle("hidden", !visible);
  }

  function formatCheckedAt(value, compact = false) {
    if (!value) return "—";
    try {
      const date = new Date(value);
      if (compact) {
        return date.toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit" });
      }
      if (OC.formatDate) return OC.formatDate(value);
      return date.toLocaleString("pt-BR");
    } catch {
      return "—";
    }
  }

  function formatElapsed(startedAt) {
    if (!startedAt) return "";
    const seconds = Math.max(0, Math.floor((Date.now() - startedAt) / 1000));
    const mm = String(Math.floor(seconds / 60)).padStart(2, "0");
    const ss = String(seconds % 60).padStart(2, "0");
    return `${mm}:${ss}`;
  }

  function stopElapsedTimer() {
    if (OC.consoleUpdateState.elapsedTimer) {
      clearInterval(OC.consoleUpdateState.elapsedTimer);
      OC.consoleUpdateState.elapsedTimer = null;
    }
  }

  function startElapsedTimer() {
    stopElapsedTimer();
    OC.consoleUpdateState.elapsedTimer = setInterval(() => {
      if (!OC.consoleUpdateState.busy && OC.consoleUpdateState.uiState === "idle") {
        stopElapsedTimer();
        return;
      }
      renderConsoleUpdateBar(OC.consoleUpdateState.status, OC.consoleUpdateState.uiState);
    }, 1000);
  }

  function clearLastError(options = {}) {
    if (options.dismiss && OC.consoleUpdateState.lastError) {
      OC.consoleUpdateState.dismissedErrorKey = errorKey(OC.consoleUpdateState.lastError, OC.consoleUpdateState.status);
    }
    OC.consoleUpdateState.lastError = null;
  }

  function errorKey(error, status) {
    const result = status?.lastResult || {};
    return [
      error?.message || result.error || result.reason || "",
      result.finishedAt || error?.at || "",
      result.phase || "",
    ].join("|");
  }

  function setLastError(message, extras = {}) {
    const text = String(message || "Falha na atualização do console.").trim();
    const next = {
      message: text,
      at: new Date().toISOString(),
      ...extras,
    };
    if (OC.consoleUpdateState.dismissedErrorKey === errorKey(next, OC.consoleUpdateState.status)) {
      return;
    }
    OC.consoleUpdateState.lastError = next;
  }

  function extractErrorMessage(payload) {
    if (!payload) return null;
    if (typeof payload === "string") return payload;
    return (
      payload.error ||
      payload.reason ||
      payload.lastError ||
      payload.message ||
      payload.lastResult?.error ||
      payload.lastResult?.reason ||
      null
    );
  }

  function explainUpdateError(error, status = null) {
    const raw = String(error?.message || error || "Falha desconhecida").trim();
    const phase = String(error?.phase || status?.lastResult?.phase || "");

    if (error?.code === "aborted" || /expirou|cancelada/i.test(raw)) {
      const phaseText = phase && phase !== "started" ? ` Etapa registrada: ${phase}.` : "";
      return `${raw}${phaseText}\n\nO que isso indica: o navegador não recebeu a resposta dentro do prazo.\nO que verificar: aguarde alguns segundos e consulte o status novamente; se persistir, verifique a conexão com o GitHub e os logs do Console.`;
    }
    if (error?.code === 401 || /nao autorizado|não autorizado|401/i.test(raw)) {
      return `${raw}\n\nO que isso indica: a sessão do Console expirou ou não tem autorização.\nO que fazer: desbloqueie o Console novamente e repita a atualização.`;
    }
    if (/network|failed to fetch|fetch|conex[aã]o|conectar|econn|offline/i.test(raw)) {
      return `${raw}\n\nO que isso indica: o navegador não conseguiu alcançar o servidor do Console.\nO que verificar: confirme se o serviço está ativo, se a porta do Console está acessível e se não houve reinício durante a atualização.`;
    }
    return raw;
  }

  function shouldShowDetail(status, uiState, presentation) {
    if (uiState !== "idle") return true;
    if (OC.consoleUpdateState.lastError) return true;
    if (status?.inProgress || status?.dirty || status?.updateAvailable) return true;
    if (status?.supported === false || !status?.ok) return true;
    return presentation.tone !== "success";
  }

  function resolveStatusPresentation(status, uiState) {
    const elapsed = formatElapsed(OC.consoleUpdateState.activityStartedAt);
    const elapsedSuffix = elapsed ? ` (${elapsed})` : "";
    if (status?.inProgress && (status.cancelRequested || OC.consoleUpdateState.cancelPending)) {
      return { label: "Cancelando", tone: "running", detail: "Aguardando a etapa atual terminar. Alterações já aplicadas serão preservadas." };
    }
    if (!status?.inProgress && status?.lastResult?.cancelled) {
      return { label: "Cancelado", tone: "info", detail: "Deploy cancelado. Alterações já aplicadas foram preservadas; o console não foi reiniciado." };
    }

    const applyPhaseLabels = {
      started: "Iniciando worker de atualização",
      pulling: "Baixando código do remoto",
      dependencies: "Instalando dependências",
      automation_runtime: "Atualizando runtime das automações",
      restarting: "Preparando reinício do console",
    };
    const applyPhase = applyPhaseLabels[String(status?.lastResult?.phase || "")] || "";

    if (uiState === "checking") {
      return {
        label: "Verificando",
        tone: "running",
        detail: `Consultando o repositório remoto…${elapsedSuffix}`,
      };
    }
    if (uiState === "applying") {
      const lock = status?.lockDetail ? ` (${status.lockDetail})` : "";
      return {
        label: "Atualizando",
        tone: "running",
        detail:
          OC.consoleUpdateState.activityLabel ||
          `${applyPhase || "Aplicando alterações"}${lock}…${elapsedSuffix}`,
      };
    }
    if (uiState === "reconnecting") {
      return {
        label: "Reiniciando",
        tone: "running",
        detail:
          OC.consoleUpdateState.activityLabel ||
          `Aguardando o console voltar…${elapsedSuffix}`,
      };
    }
    if (status?.inProgress) {
      return {
        label: "Atualizando",
        tone: "running",
        detail: status.lockDetail
          ? `${applyPhase || "Atualização em andamento"} (${status.lockDetail}).`
          : `${applyPhase || "Atualização do console em andamento"}.`,
      };
    }
    if (OC.consoleUpdateState.lastError) {
      return {
        label: "Falhou",
        tone: "unhealthy",
        detail: "A atualização não concluiu. Veja o erro abaixo.",
      };
    }
    if (status?.supported === false) {
      return {
        label: "Indisponível",
        tone: "unhealthy",
        detail: status.reason || "Este ambiente não suporta auto-atualização.",
      };
    }
    if (status?.dirty) {
      return {
        label: "Alterações locais",
        tone: "running",
        detail: status.reason || "Há mudanças locais no repositório ops.",
      };
    }
    if (status?.updateAvailable) {
      const behind = Number(status.commitsBehind) > 0
        ? `${status.commitsBehind} commit(s) atrás do remoto.`
        : "Há uma versão mais recente no GitHub.";
      return { label: "Atrasado", tone: "unhealthy", detail: behind };
    }
    if (status?.ok) {
      return { label: "Atualizado", tone: "success", detail: "O console está na versão mais recente do remoto." };
    }
    if (status?.reason || status?.error) {
      return {
        label: "Atenção",
        tone: "running",
        detail: status.reason || status.error,
      };
    }
    return { label: "Aguardando", tone: "info", detail: "Carregando informações da versão…" };
  }

  function buildMetaChips(status, uiState) {
    const chips = [];
    if (status?.currentSha) {
      chips.push({ label: "versão", value: status.currentSha, mono: true });
    }
    if (status?.branch) {
      chips.push({ label: "branch", value: status.branch, mono: true });
    }
    if (status?.remoteSha && status?.updateAvailable) {
      chips.push({ label: "remoto", value: status.remoteSha, mono: true, warn: true });
    }
    if (Number(status?.commitsBehind) > 0) {
      chips.push({ label: "atrás", value: `${status.commitsBehind} commit(s)`, warn: true });
    }
    const elapsed = formatElapsed(OC.consoleUpdateState.activityStartedAt);
    if (uiState !== "idle" && elapsed) {
      chips.push({ label: "tempo", value: elapsed, warn: true });
    }
    const phaseLabels = {
      started: "iniciando atualização",
      pulling: "baixando código",
      dependencies: "instalando dependências",
      automation_runtime: "atualizando automações",
      restarting: "preparando reinício",
    };
    const phaseLabel = phaseLabels[String(status?.lastResult?.phase || "")];
    if (uiState === "applying" && phaseLabel) {
      chips.push({ label: "etapa", value: phaseLabel, warn: true });
    }
    const checked =
      uiState === "checking"
        ? "agora…"
        : formatCheckedAt(status?.checkedAt || OC.consoleUpdateState.lastCheckedAt, true);
    if (checked !== "—") {
      chips.push({ label: "verificado", value: checked });
    }
    return chips;
  }

  function buildErrorCopyText(error, status) {
    const lines = [
      "Erro na atualização do Console ops",
      error?.at ? `Horário: ${error.at}` : "",
      error?.message ? `Erro: ${error.message}` : "",
      error?.detail ? `Detalhe: ${error.detail}` : "",
      status?.currentSha ? `Versão local: ${status.currentSha}` : "",
      status?.remoteSha ? `Versão remota: ${status.remoteSha}` : "",
      status?.branch ? `Branch: ${status.branch}` : "",
      status?.lastResult?.phase ? `Fase: ${status.lastResult.phase}` : "",
      status?.lastResult?.previousSha
        ? `De: ${status.lastResult.previousSha} → ${status.lastResult.targetSha || "?"}`
        : "",
    ].filter(Boolean);
    return lines.join("\n");
  }

  function buildErrorPanelHtml(status) {
    const error = OC.consoleUpdateState.lastError;
    if (!error?.message) return "";
    const copyText = buildErrorCopyText(error, status);
    return `
      <div class="ops-console-update-error" role="alert">
        <div class="ops-console-update-error-head">
          <strong>Erro na atualização</strong>
          <div class="ops-console-update-error-actions">
            <button type="button" class="btn btn-secondary btn-sm" id="btn-console-update-copy-error">Copiar erro</button>
            <button type="button" class="btn btn-ghost btn-sm" id="btn-console-update-dismiss-error">Dispensar</button>
          </div>
        </div>
        <pre class="ops-console-update-error-body" id="console-update-error-text">${OC.escapeHtml(error.message)}</pre>
        <textarea class="ops-console-update-error-raw" id="console-update-error-raw" readonly hidden>${OC.escapeHtml(copyText)}</textarea>
      </div>
    `;
  }

  function buildUpdateLogPanel() {
    if (!OC.consoleUpdateState.logOpen) return "";
    const state = OC.consoleUpdateState;
    let body = "";
    if (state.logLoading && !state.logLines.length) {
      body = "Carregando eventos da atualização…";
    } else if (state.logError) {
      body = `Não foi possível carregar o log: ${state.logError}`;
    } else if (state.logLines.length) {
      body = state.logLines.join("\n");
    } else {
      body = "Nenhum evento registrado ainda.";
    }
    return `
      <div class="ops-console-update-log" role="region" aria-label="Eventos da atualização">
        <div class="ops-console-update-log-head">
          <div><strong>Eventos da atualização</strong><span class="ops-console-update-log-meta">Últimos ${state.logLines.length} eventos${state.logUpdatedAt ? ` · ${formatCheckedAt(state.logUpdatedAt, true)}` : ""}</span></div>
          <div class="ops-console-update-log-actions">
            <button type="button" class="btn btn-secondary btn-sm" id="btn-console-update-copy-log" ${!state.logLines.length ? "disabled" : ""}>Copiar log</button>
            <button type="button" class="btn btn-ghost btn-sm" id="btn-console-update-refresh-log" ${state.logLoading ? "disabled" : ""}>${state.logLoading ? "Carregando…" : "Atualizar log"}</button>
          </div>
        </div>
        <pre class="ops-console-update-log-body" id="console-update-log-text" tabindex="0" aria-label="Log da atualização">${body.split("\n").map(line => `<span class="ops-console-update-log-line${/\[ERROR\]|\[failed\]/i.test(line) ? " is-error" : ""}">${OC.escapeHtml(line)}</span>`).join("\n")}</pre>
      </div>
    `;
  }

  async function loadConsoleUpdateLog() {
    if (OC.consoleUpdateState.logInFlight) return OC.consoleUpdateState.logInFlight;
    const request = fetchUpdateLog();
    OC.consoleUpdateState.logInFlight = request;
    try { await request; } finally { OC.consoleUpdateState.logInFlight = null; }
  }

  async function fetchUpdateLog() {
    OC.consoleUpdateState.logLoading = true;
    OC.consoleUpdateState.logError = null;
    renderConsoleUpdateBar(OC.consoleUpdateState.status, OC.consoleUpdateState.uiState);
    try {
      const payload = await OC.fetchJson(LOG_URL, { timeoutMs: 10000 });
      OC.consoleUpdateState.logLines = Array.isArray(payload?.lines) ? payload.lines : [];
      OC.consoleUpdateState.logUpdatedAt = new Date().toISOString();
    } catch (err) {
      OC.consoleUpdateState.logError = err.message || "erro desconhecido";
    } finally {
      OC.consoleUpdateState.logLoading = false;
      renderConsoleUpdateBar(OC.consoleUpdateState.status, OC.consoleUpdateState.uiState);
    }
  }

  async function cancelConsoleUpdate() {
    const state = OC.consoleUpdateState;
    if (state.cancelPending || state.status?.cancelRequested) return;
    state.cancelPending = true;
    renderConsoleUpdateBar(state.status, state.uiState);
    try {
      await OC.postAction(CANCEL_URL, {}, { timeoutMs: 15000 });
      const status = await OC.fetchConsoleUpdateStatus({ progressOnly: true });
      renderConsoleUpdateBar(status, state.uiState);
      OC.showToast?.("Cancelamento solicitado. Aguardando a etapa atual terminar.", "info");
    } catch (err) {
      OC.showToast?.(err.message || "Não foi possível cancelar o deploy.", "error");
    } finally {
      state.cancelPending = false;
      renderConsoleUpdateBar(state.status, state.uiState);
    }
  }

  function buildUpdateSteps(status, uiState) {
    if (!status?.inProgress && uiState !== "applying" && uiState !== "reconnecting") return "";
    const steps = [["started", "Início"], ["pulling", "Código"], ["dependencies", "Dependências"], ["automation_runtime", "Automações"], ["restarting", "Reinício"]];
    const phase = uiState === "reconnecting" ? "restarting" : status?.lastResult?.phase || "started";
    return `<ol class="ops-console-update-steps" aria-label="Etapas do deploy">${steps.map(([key, label]) => `<li${key === phase ? ' aria-current="step"' : ""}>${label}</li>`).join("")}</ol>`;
  }

  async function copyUpdateError(button) {
    const raw = document.getElementById("console-update-error-raw");
    const text = raw?.value || OC.consoleUpdateState.lastError?.message || "";
    await OC.copyText(text, { button, successMessage: "Erro copiado." });
  }

  function bindConsoleUpdateButton() {
    const root = getBarRoot();
    if (!root || root.dataset.updateBound === "1") return;
    root.dataset.updateBound = "1";
    root.addEventListener("click", (event) => {
      if (event.target.closest("#btn-console-update-copy-log")) {
        OC.copyText(OC.consoleUpdateState.logLines.join("\n"), { button: event.target.closest("button"), successMessage: "Log copiado." });
        return;
      }
      if (event.target.closest("#btn-console-update-cancel")) {
        cancelConsoleUpdate();
        return;
      }
      if (event.target.closest("#btn-console-update-copy-error")) {
        event.preventDefault();
        copyUpdateError(event.target.closest("#btn-console-update-copy-error"));
        return;
      }
      if (event.target.closest("#btn-console-update-dismiss-error")) {
        event.preventDefault();
        clearLastError({ dismiss: true });
        renderConsoleUpdateBar(OC.consoleUpdateState.status, "idle");
        return;
      }
      if (event.target.closest("#btn-console-update-log")) {
        event.preventDefault();
        OC.consoleUpdateState.logOpen = !OC.consoleUpdateState.logOpen;
        renderConsoleUpdateBar(OC.consoleUpdateState.status, OC.consoleUpdateState.uiState);
        if (OC.consoleUpdateState.logOpen) loadConsoleUpdateLog();
        return;
      }
      if (event.target.closest("#btn-console-update-refresh-log")) {
        event.preventDefault();
        loadConsoleUpdateLog();
        return;
      }
      if (event.target.closest("#btn-console-update")) {
        OC.runConsoleUpdateCheck?.();
      }
    });
  }

  function renderConsoleUpdateBar(status, uiState = "idle") {
    const root = getBarRoot();
    if (!root) return;

    OC.consoleUpdateState.uiState = uiState;
    const supported = status?.supported !== false;
    const presentation = resolveStatusPresentation(status, uiState);
    const metaChips = buildMetaChips(status, uiState);
    const showDetail = shouldShowDetail(status, uiState, presentation);
    const disabled = uiState !== "idle" || OC.consoleUpdateState.busy || status?.inProgress;
    const hasUpdate = Boolean(status?.updateAvailable);
    const hasError = Boolean(OC.consoleUpdateState.lastError);
    const hasLog = Boolean(status?.inProgress || status?.lastResult);
    const canCancel = status?.inProgress && status?.lastResult?.phase !== "restarting" && uiState !== "reconnecting";
    const cancelRequested = OC.consoleUpdateState.cancelPending || status?.cancelRequested;
    if (OC.consoleUpdateState.logOpen && status?.inProgress && !OC.consoleUpdateState.logTimer) {
      OC.consoleUpdateState.logTimer = setInterval(() => {
        if (OC.currentRoute?.view !== "deploy") {
          clearInterval(OC.consoleUpdateState.logTimer);
          OC.consoleUpdateState.logTimer = null;
          return;
        }
        loadConsoleUpdateLog();
      }, 5000);
    } else if ((!OC.consoleUpdateState.logOpen || !status?.inProgress) && OC.consoleUpdateState.logTimer) {
      clearInterval(OC.consoleUpdateState.logTimer);
      OC.consoleUpdateState.logTimer = null;
    }
    const buttonLabel =
      uiState === "checking"
        ? "Verificando…"
        : uiState === "applying" || status?.inProgress
          ? "Aplicando…"
          : uiState === "reconnecting"
            ? "Reiniciando…"
            : hasError
              ? "Tentar novamente"
              : hasUpdate
                ? "Atualizar agora"
                : "Verificar atualização";

    root.classList.toggle("is-update-available", hasUpdate && !hasError);
    root.classList.toggle("is-updating", uiState !== "idle" || Boolean(status?.inProgress));
    root.classList.toggle("is-update-error", hasError && uiState === "idle");

    const chipsHtml = metaChips.length
      ? metaChips
          .map((chip) => {
            const value = chip.mono
              ? `<code>${OC.escapeHtml(chip.value)}</code>`
              : OC.escapeHtml(chip.value);
            return `<span class="ops-console-update-chip${chip.warn ? " is-warn" : ""}">
              <span class="ops-console-update-chip-label">${OC.escapeHtml(chip.label)}</span>
              <span class="ops-console-update-chip-value">${value}</span>
            </span>`;
          })
          .join("")
      : `<span class="ops-console-update-chip is-muted"><span class="ops-console-update-chip-value">Carregando versão…</span></span>`;

    const oldLog = root.querySelector(".ops-console-update-log-body");
    const logScroll = oldLog?.scrollTop || 0;
    const followLog = !oldLog || oldLog.scrollHeight - oldLog.clientHeight - logScroll < 24;
    const focusedId = root.contains(document.activeElement) ? document.activeElement?.id : null;
    root.innerHTML = `
      <div class="ops-console-update-inner is-tone-${OC.escapeHtml(presentation.tone)}">
        <div class="ops-console-update-icon" aria-hidden="true">
          ${OC.opsSvgIcon?.("rocket") || "↻"}
        </div>
        <div class="ops-console-update-body">
          <div class="ops-console-update-row">
            <div class="ops-console-update-title-wrap">
              <span class="ops-console-update-title">Console ops</span>
              <span class="status-badge status-${OC.escapeHtml(presentation.tone)} ops-console-update-badge">
                ${OC.escapeHtml(presentation.label)}
              </span>
            </div>
            <div class="ops-console-update-chips" aria-label="Detalhes da versão">${chipsHtml}</div>
          </div>
          ${
            showDetail
              ? `<p class="ops-console-update-detail">${OC.escapeHtml(presentation.detail)}</p>`
              : ""
          }
          ${buildUpdateSteps(status, uiState)}
          ${hasLog ? `<button type="button" class="btn btn-ghost btn-sm ops-console-update-log-btn" id="btn-console-update-log" aria-expanded="${OC.consoleUpdateState.logOpen}">${OC.consoleUpdateState.logOpen ? "Ocultar eventos" : "Ver eventos"}</button>` : ""}
          ${buildUpdateLogPanel()}
          ${uiState === "idle" ? buildErrorPanelHtml(status) : ""}
        </div>
        <div class="ops-console-update-actions">
        ${canCancel ? `<button type="button" class="btn btn-danger btn-sm" id="btn-console-update-cancel" title="Parar após a etapa atual, preservando alterações já aplicadas" ${cancelRequested ? "disabled" : ""}>${cancelRequested ? "Cancelando…" : "Cancelar deploy"}</button>` : ""}
        <button
          type="button"
          class="btn ${hasUpdate || hasError ? "btn-primary" : "btn-secondary"} btn-sm ops-console-update-btn"
          id="btn-console-update"
          ${disabled ? "disabled" : ""}
          title="${OC.escapeHtml(
            supported
              ? hasError
                ? "Tentar a atualização novamente"
                : hasUpdate
                  ? "Baixar a nova versão e reiniciar o console"
                  : "Buscar atualização no Git e reiniciar o console"
              : status?.reason || "Atualização indisponível"
          )}"
        >
          ${OC.escapeHtml(buttonLabel)}
        </button>
        </div>
      </div>
    `;

    const nextLog = root.querySelector(".ops-console-update-log-body");
    if (nextLog) nextLog.scrollTop = followLog ? nextLog.scrollHeight : logScroll;
    if (focusedId) document.getElementById(focusedId)?.focus({ preventScroll: true });
    bindConsoleUpdateButton();
  }

  function beginActivity(uiState, label) {
    OC.consoleUpdateState.activityStartedAt = Date.now();
    OC.consoleUpdateState.activityLabel = label || null;
    startElapsedTimer();
    renderConsoleUpdateBar(OC.consoleUpdateState.status, uiState);
  }

  function endActivity() {
    stopElapsedTimer();
    OC.consoleUpdateState.activityStartedAt = null;
    OC.consoleUpdateState.activityLabel = null;
  }

  function isFinalApplyResult(result, applyStartedAt) {
    if (!result || typeof result !== "object") return false;
    const phase = String(result.phase || "");
    if (phase === "started") return false;
    if (phase === "failed" || phase === "restarting" || phase === "done" || phase === "cancelled") return true;
    if (result.ok === false) return true;
    if (!applyStartedAt) return Boolean(result.applied || result.restarting || result.error);
    const finishedMs = Date.parse(result.finishedAt || "");
    const startedMs = Date.parse(applyStartedAt);
    if (Number.isFinite(finishedMs) && Number.isFinite(startedMs)) {
      return finishedMs >= startedMs - 1000 && phase !== "started";
    }
    return Boolean(result.error || result.restarting || result.applied);
  }

  OC.fetchConsoleUpdateStatus = async function fetchConsoleUpdateStatus(options = {}) {
    const progressOnly = options.progressOnly === true;
    const inflightKey = progressOnly ? "progressInFlight" : "statusInFlight";
    const existing = OC.consoleUpdateState[inflightKey];
    if (existing) {
      return existing;
    }

    const url = progressOnly ? `${STATUS_URL}?progress=1` : STATUS_URL;
    const timeoutMs = progressOnly ? 15000 : 150000;
    if (!progressOnly) OC.consoleUpdateState.lastStatusAttemptAt = Date.now();
    const request = OC.fetchJson(url, { timeoutMs })
      .then((data) => {
        // Progress polls must not wipe updateAvailable from a prior full check.
        if (progressOnly && OC.consoleUpdateState.status) {
          const merged = {
            ...OC.consoleUpdateState.status,
            ...data,
            updateAvailable:
              data.updateAvailable == null
                ? OC.consoleUpdateState.status.updateAvailable
                : data.updateAvailable,
            remoteSha: data.remoteSha || OC.consoleUpdateState.status.remoteSha,
            commitsBehind:
              data.commitsBehind == null
                ? OC.consoleUpdateState.status.commitsBehind
                : data.commitsBehind,
          };
          OC.consoleUpdateState.status = merged;
          OC.consoleUpdateState.lastCheckedAt =
            data.checkedAt || OC.consoleUpdateState.lastCheckedAt || new Date().toISOString();
          return merged;
        }
        OC.consoleUpdateState.status = data;
        OC.consoleUpdateState.lastCheckedAt = data.checkedAt || new Date().toISOString();
        return data;
      })
      .finally(() => {
        if (OC.consoleUpdateState[inflightKey] === request) {
          OC.consoleUpdateState[inflightKey] = null;
        }
      });

    OC.consoleUpdateState[inflightKey] = request;
    return request;
  };

  async function waitForApplyResult(applyStartedAt, targetSha) {
    const started = Date.now();
    beginActivity(
      "applying",
      `Aplicando atualização${targetSha ? ` → ${targetSha}` : ""}…`
    );

    while (Date.now() - started < POLL_TIMEOUT_MS) {
      await new Promise((resolve) => setTimeout(resolve, POLL_INTERVAL_MS));
      let status = null;
      try {
        // Progress-only: lock/result/local SHA, no git fetch — keeps polls serial
        // and avoids stacking pending /status calls while apply runs.
        status = await OC.fetchConsoleUpdateStatus({ progressOnly: true });
      } catch (err) {
        // Servidor pode já ter caído para reinício — continuar até ter resultado ou timeout.
        OC.consoleUpdateState.activityLabel =
          `Servidor indisponível temporariamente (${err.message || "rede"}). Aguardando…`;
        renderConsoleUpdateBar(OC.consoleUpdateState.status, "applying");
        continue;
      }

      const lockDetail = status.lockDetail ? ` (${status.lockDetail})` : "";
      if (status.inProgress) {
        // Quando o worker já gravou uma fase, deixa a apresentação usar esse
        // progresso em vez de escondê-lo atrás do texto genérico do lock.
        OC.consoleUpdateState.activityLabel = status.lastResult?.phase && status.lastResult.phase !== "started"
          ? null
          : `Atualização em andamento${lockDetail}…`;
        renderConsoleUpdateBar(status, "applying");
        continue;
      }

      const result = status.lastResult || null;
      if (isFinalApplyResult(result, applyStartedAt)) {
        return { status, result };
      }

      OC.consoleUpdateState.activityLabel =
        "Aguardando conclusão do worker de atualização…";
      renderConsoleUpdateBar(status, "applying");
    }

    let status = OC.consoleUpdateState.status;
    try {
      status = await OC.fetchConsoleUpdateStatus({ progressOnly: true });
    } catch {
      /* keep last */
    }
    return {
      status,
      result: status?.lastResult || null,
      timedOut: true,
    };
  }

  async function waitForConsoleRestart(previousInstanceId = null) {
    const started = Date.now();
    let sawDown = false;
    beginActivity("reconnecting", "Reiniciando o console…");

    while (Date.now() - started < RESTART_TIMEOUT_MS) {
      await new Promise((resolve) => setTimeout(resolve, POLL_INTERVAL_MS));
      try {
        const health = await OC.fetchJson("/api/v1/auth/status", { timeoutMs: 3000 });
        if (sawDown) return true;
        if (previousInstanceId && health?.serverInstanceId && health.serverInstanceId !== previousInstanceId) return true;
        // Ainda não reiniciou: servidor continua respondendo.
        OC.consoleUpdateState.activityLabel =
          "Aguardando reinício do processo (servidor ainda ativo)…";
        renderConsoleUpdateBar(OC.consoleUpdateState.status, "reconnecting");
      } catch {
        sawDown = true;
        OC.consoleUpdateState.activityLabel = "Console fora do ar; aguardando voltar…";
        renderConsoleUpdateBar(OC.consoleUpdateState.status, "reconnecting");
      }
    }

    if (sawDown) {
      try {
        await OC.fetchJson("/api/v1/auth/status", { timeoutMs: 3000 });
        return true;
      } catch {
        return false;
      }
    }
    // Nunca caiu: reinício pode ter falhado ou sido muito rápido — tenta status.
    try {
      await OC.fetchJson("/api/v1/auth/status", { timeoutMs: 3000 });
      return "still-up";
    } catch {
      return false;
    }
  }

  OC.refreshConsoleUpdateBar = async function refreshConsoleUpdateBar(options = {}) {
    const silent = options.silent === true;
    const keepError = options.keepError === true;
    const root = getBarRoot();
    if (!root) return null;

    if (OC.currentRoute?.view !== "deploy") {
      setBarVisible(false);
      return null;
    }

    setBarVisible(true);
    if (silent) {
      // Dashboard auto-refresh must not stack full git-fetch status calls.
      if (OC.consoleUpdateState.statusInFlight) {
        return OC.consoleUpdateState.statusInFlight;
      }
      const lastMs = Number(OC.consoleUpdateState.lastStatusAttemptAt || 0);
      if (
        Number.isFinite(lastMs) &&
        Date.now() - lastMs < SILENT_STATUS_MIN_INTERVAL_MS &&
        OC.consoleUpdateState.status
      ) {
        if (!OC.consoleUpdateState.busy) {
          renderConsoleUpdateBar(OC.consoleUpdateState.status, "idle");
        }
        return OC.consoleUpdateState.status;
      }
    }

    if (!silent) {
      clearLastError();
      beginActivity("checking", "Consultando o repositório remoto…");
    } else if (!OC.consoleUpdateState.busy) {
      renderConsoleUpdateBar(OC.consoleUpdateState.status, "idle");
    }

    try {
      const status = await OC.fetchConsoleUpdateStatus();
      const recentFail =
        status?.lastResult &&
        status.lastResult.ok === false &&
        (status.lastResult.error || status.lastResult.reason);
      if (recentFail && !OC.consoleUpdateState.lastError && (!silent || keepError !== false)) {
        // Em silent (boot), ainda mostra falha recente para não voltar “cego” ao Atrasado.
        setLastError(recentFail, { phase: status.lastResult.phase || "failed" });
      }
      // Em checagem explícita, sempre mostra o resultado; silent durante apply não sobrescreve.
      if (!silent) {
        endActivity();
        renderConsoleUpdateBar(status, "idle");
      } else if (!OC.consoleUpdateState.busy) {
        renderConsoleUpdateBar(status, "idle");
      }
      return status;
    } catch (err) {
      const local = OC.consoleUpdateState.status || {};
      const fallback = {
        ...local,
        supported: local.supported ?? true,
        ok: false,
        reason:
          err.message ||
          "Não foi possível consultar atualizações. Reinicie o console se a rota for nova.",
      };
      OC.consoleUpdateState.status = fallback;
      if (!silent) {
        setLastError(fallback.reason);
        endActivity();
        renderConsoleUpdateBar(fallback, "idle");
        OC.showToast?.(err.message || "Falha ao verificar atualização do console", "error");
      } else if (!OC.consoleUpdateState.busy) {
        renderConsoleUpdateBar(fallback, "idle");
      }
      return null;
    }
  };

  OC.runConsoleUpdateCheck = async function runConsoleUpdateCheck() {
    if (OC.consoleUpdateState.busy) return;

    OC.consoleUpdateState.busy = true;
    clearLastError();
    OC.consoleUpdateState.dismissedErrorKey = null;
    try {
      const status = await OC.refreshConsoleUpdateBar();
      if (!status) {
        const msg =
          OC.consoleUpdateState.lastError?.message ||
          OC.consoleUpdateState.status?.reason ||
          "Falha ao verificar atualização.";
        setLastError(msg);
        endActivity();
        renderConsoleUpdateBar(OC.consoleUpdateState.status, "idle");
        return;
      }

      if (status.supported === false) {
        setLastError(status.reason || "Atualização indisponível neste ambiente.");
        endActivity();
        renderConsoleUpdateBar(status, "idle");
        return;
      }
      if (status.inProgress) {
        setLastError(
          status.error ||
            status.lockDetail ||
            "Já existe uma atualização em andamento. Aguarde e tente de novo."
        );
        endActivity();
        renderConsoleUpdateBar(status, "idle");
        return;
      }
      if (!status.ok && !status.updateAvailable) {
        setLastError(status.error || status.reason || "Falha ao verificar atualização.");
        endActivity();
        renderConsoleUpdateBar(status, "idle");
        return;
      }
      if (!status.updateAvailable) {
        clearLastError();
        endActivity();
        renderConsoleUpdateBar(status, "idle");
        OC.showToast?.("Console já está atualizado.", "success");
        return;
      }

      const fromSha = status.currentSha || "?";
      const toSha = status.remoteSha || "?";
      const confirmed = OC.confirmAction?.(
        `Nova versão ${fromSha} → ${toSha}.\n\nBaixar e reiniciar o console?`
      );
      if (!confirmed) {
        endActivity();
        renderConsoleUpdateBar(status, "idle");
        return;
      }

      beginActivity("applying", `Solicitando atualização ${fromSha} → ${toSha}…`);
      const applyStartedAt = new Date().toISOString();
      OC.consoleUpdateState.applyStartedAt = applyStartedAt;
      let previousInstanceId = null;
      try {
        previousInstanceId = (await OC.fetchJson('/api/v1/auth/status', { timeoutMs: 3000 }))?.serverInstanceId || null;
      } catch { /* A lost connection is handled by the apply request. */ }

      let result;
      try {
        result = await OC.postAction(APPLY_URL, {}, {
          timeoutMs: APPLY_REQUEST_TIMEOUT_MS,
        });
      } catch (err) {
        // The worker may have started even when its acceptance response was lost.
        let progress = null;
        if (err.timedOut || err.code === "aborted" || /fetch|network/i.test(err.message || "")) {
          try { progress = await OC.fetchConsoleUpdateStatus({ progressOnly: true }); } catch { /* Keep the original error. */ }
        }
        const workerStartedAt = progress?.lastResult?.startedAt;
        const isCurrent = workerStartedAt && Date.parse(workerStartedAt) >= Date.parse(applyStartedAt) - 1000;
        if (progress?.inProgress && isCurrent) {
          result = { ok: true, accepted: true, restarting: true, startedAt: workerStartedAt };
        } else {
          setLastError(explainUpdateError(err, progress || OC.consoleUpdateState.status), {
            code: err.code, timedOut: err.timedOut, detail: err.message,
          });
          endActivity();
          renderConsoleUpdateBar(progress || OC.consoleUpdateState.status, "idle");
          return;
        }
      }

      if (!result?.ok) {
        setLastError(
          explainUpdateError(
            { ...result, message: extractErrorMessage(result) || "Falha ao aplicar atualização." },
            status
          ),
          { phase: result?.phase || "failed", detail: extractErrorMessage(result) }
        );
        endActivity();
        renderConsoleUpdateBar({ ...status, ...result, lastResult: result }, "idle");
        return;
      }
      if (!result.restarting) {
        clearLastError();
        endActivity();
        const refreshed = await OC.refreshConsoleUpdateBar({ silent: true, keepError: true });
        renderConsoleUpdateBar(refreshed || status, "idle");
        OC.showToast?.(result.message || "Console já está atualizado.", "success");
        return;
      }

      OC.consoleUpdateState.logOpen = true;
      loadConsoleUpdateLog();
      let wait = await waitForApplyResult(result.startedAt || applyStartedAt, toSha);
      while (wait.timedOut && wait.status?.inProgress) {
        wait = await waitForApplyResult(result.startedAt || applyStartedAt, toSha);
      }
      if (wait.timedOut && !isFinalApplyResult(wait.result, applyStartedAt)) {
        setLastError(
          "A atualização não confirmou a conclusão dentro do tempo esperado. Verifique o status antes de tentar novamente.",
          {
            phase: "timeout",
            detail: "O worker não informou um resultado final e não há lock ativo no momento.",
          }
        );
        endActivity();
        renderConsoleUpdateBar(wait.status || status, "idle");
        return;
      }

      if (wait.result?.cancelled) {
        clearLastError();
        endActivity();
        if (OC.consoleUpdateState.logOpen) await loadConsoleUpdateLog();
        renderConsoleUpdateBar(wait.status || status, "idle");
        OC.showToast?.("Deploy cancelado. Alterações já aplicadas foram preservadas.", "info");
        return;
      }

      if (wait.result && wait.result.ok === false) {
        setLastError(
          extractErrorMessage(wait.result) || "Worker de atualização falhou.",
          { phase: wait.result.phase || "failed" }
        );
        endActivity();
        renderConsoleUpdateBar(wait.status || status, "idle");
        return;
      }

      const restartState = await waitForConsoleRestart(previousInstanceId);
      if (restartState === true) {
        OC.showToast?.("Console reiniciado. Recarregando…", "success");
        window.location.reload();
        return;
      }

      // still-up ou falha: reconsulta status para não voltar “cego” ao Atrasado
      let after = null;
      try {
        after = await OC.fetchConsoleUpdateStatus();
      } catch (err) {
        setLastError(
          `Reinício não confirmado e status indisponível: ${err.message || err}`,
          { phase: "restart" }
        );
        endActivity();
        renderConsoleUpdateBar(wait.status || status, "idle");
        return;
      }

      if (!after.updateAvailable && after.ok) {
        clearLastError();
        endActivity();
        renderConsoleUpdateBar(after, "idle");
        OC.showToast?.("Atualização aplicada. Recarregando…", "success");
        window.location.reload();
        return;
      }

      const failMsg =
        extractErrorMessage(after) ||
        (restartState === "still-up"
          ? "O processo de atualização terminou, mas o console não reiniciou e a versão continua atrasada."
          : "O console não respondeu após o reinício e a versão continua atrasada.");
      setLastError(failMsg, { phase: "restart" });
      endActivity();
      renderConsoleUpdateBar(after, "idle");
    } catch (err) {
      setLastError(err.message || "Falha ao atualizar o console.");
      endActivity();
      renderConsoleUpdateBar(OC.consoleUpdateState.status, "idle");
    } finally {
      OC.consoleUpdateState.busy = false;
      stopElapsedTimer();
    }
  };

  OC.initConsoleUpdate = function initConsoleUpdate() {
    if (OC.currentRoute?.view !== "deploy") {
      setBarVisible(false);
      return;
    }
    setBarVisible(true);
    // O dashboard é redesenhado a cada auto-refresh. Durante uma atualização
    // isso não pode resetar o banner para idle com o SHA anterior: o worker
    // ainda está aplicando o pull/reinício e o polling abaixo é quem deve
    // finalizar o estado visual.
    const activeState = OC.consoleUpdateState.busy || OC.consoleUpdateState.uiState !== "idle";
    renderConsoleUpdateBar(
      OC.consoleUpdateState.status,
      activeState ? OC.consoleUpdateState.uiState : "idle"
    );
    if (activeState) return;
    bindConsoleUpdateButton();
    OC.refreshConsoleUpdateBar({ silent: true });
  };

  bindConsoleUpdateButton();
})();
