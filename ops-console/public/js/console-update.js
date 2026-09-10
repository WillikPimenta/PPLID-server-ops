/* global window, document, navigator */
(function () {
  window.OpsConsole = window.OpsConsole || {};
  const OC = window.OpsConsole;

  const STATUS_URL = "/api/v1/console/update/status";
  const APPLY_URL = "/api/v1/console/update/apply";
  const POLL_INTERVAL_MS = 2000;
  const POLL_TIMEOUT_MS = 120000;
  const RESTART_TIMEOUT_MS = 90000;

  OC.consoleUpdateState = {
    status: null,
    busy: false,
    uiState: "idle",
    lastCheckedAt: null,
    lastError: null,
    dismissedErrorKey: null,
    activityStartedAt: null,
    activityLabel: null,
    applyStartedAt: null,
    elapsedTimer: null,
  };

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
          `Baixando a nova versão e aplicando alterações${lock}…${elapsedSuffix}`,
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
          ? `Atualização em andamento (${status.lockDetail}).`
          : "Atualização do console em andamento.",
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

  async function copyUpdateError(button) {
    const raw = document.getElementById("console-update-error-raw");
    const text = raw?.value || OC.consoleUpdateState.lastError?.message || "";
    try {
      await navigator.clipboard.writeText(text);
      if (button) {
        const original = button.textContent;
        button.textContent = "Copiado";
        setTimeout(() => {
          if (button.isConnected) button.textContent = original;
        }, 1400);
      }
      OC.showToast?.("Erro copiado", "success");
    } catch {
      if (raw) {
        raw.hidden = false;
        raw.focus();
        raw.select();
      }
      OC.showToast?.("Não foi possível copiar automaticamente. Selecione o texto do erro.", "warn");
    }
  }

  function bindConsoleUpdateButton() {
    const root = getBarRoot();
    if (!root || root.dataset.updateBound === "1") return;
    root.dataset.updateBound = "1";
    root.addEventListener("click", (event) => {
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
    const buttonLabel =
      uiState === "checking"
        ? "Verificando…"
        : uiState === "applying"
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
          ${uiState === "idle" ? buildErrorPanelHtml(status) : ""}
        </div>
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
    `;

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
    if (phase === "failed" || phase === "restarting" || phase === "done") return true;
    if (result.ok === false) return true;
    if (!applyStartedAt) return Boolean(result.applied || result.restarting || result.error);
    const finishedMs = Date.parse(result.finishedAt || "");
    const startedMs = Date.parse(applyStartedAt);
    if (Number.isFinite(finishedMs) && Number.isFinite(startedMs)) {
      return finishedMs >= startedMs - 1000 && phase !== "started";
    }
    return Boolean(result.error || result.restarting || result.applied);
  }

  OC.fetchConsoleUpdateStatus = async function fetchConsoleUpdateStatus() {
    const data = await OC.fetchJson(STATUS_URL, { timeoutMs: 120000 });
    OC.consoleUpdateState.status = data;
    OC.consoleUpdateState.lastCheckedAt = data.checkedAt || new Date().toISOString();
    return data;
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
        status = await OC.fetchConsoleUpdateStatus();
      } catch (err) {
        // Servidor pode já ter caído para reinício — continuar até ter resultado ou timeout.
        OC.consoleUpdateState.activityLabel =
          `Servidor indisponível temporariamente (${err.message || "rede"}). Aguardando…`;
        renderConsoleUpdateBar(OC.consoleUpdateState.status, "applying");
        continue;
      }

      const lockDetail = status.lockDetail ? ` (${status.lockDetail})` : "";
      if (status.inProgress) {
        OC.consoleUpdateState.activityLabel = `Atualização em andamento${lockDetail}…`;
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
      status = await OC.fetchConsoleUpdateStatus();
    } catch {
      /* keep last */
    }
    return {
      status,
      result: status?.lastResult || null,
      timedOut: true,
    };
  }

  async function waitForConsoleRestart() {
    const started = Date.now();
    let sawDown = false;
    beginActivity("reconnecting", "Reiniciando o console…");

    while (Date.now() - started < RESTART_TIMEOUT_MS) {
      await new Promise((resolve) => setTimeout(resolve, POLL_INTERVAL_MS));
      try {
        await OC.fetchJson("/api/v1/auth/status", { timeoutMs: 3000 });
        if (sawDown) return true;
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

      let result;
      try {
        result = await OC.postAction(APPLY_URL, {});
      } catch (err) {
        setLastError(err.message || "Falha ao solicitar atualização.");
        endActivity();
        renderConsoleUpdateBar(OC.consoleUpdateState.status, "idle");
        return;
      }

      if (!result?.ok) {
        setLastError(
          extractErrorMessage(result) || "Falha ao aplicar atualização.",
          { phase: result?.phase || "failed" }
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

      const wait = await waitForApplyResult(result.startedAt || applyStartedAt, toSha);
      if (wait.timedOut && !isFinalApplyResult(wait.result, applyStartedAt)) {
        setLastError(
          "A atualização demorou demais e não retornou resultado. O console pode continuar atrasado.",
          { phase: "timeout" }
        );
        endActivity();
        renderConsoleUpdateBar(wait.status || status, "idle");
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

      const restartState = await waitForConsoleRestart();
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
    renderConsoleUpdateBar(OC.consoleUpdateState.status, "idle");
    bindConsoleUpdateButton();
    OC.refreshConsoleUpdateBar({ silent: true });
  };

  bindConsoleUpdateButton();
})();
