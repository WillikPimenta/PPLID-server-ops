/* global window, document */
(function () {
  window.OpsConsole = window.OpsConsole || {};
  const OC = window.OpsConsole;

  const STATUS_URL = "/api/v1/console/update/status";
  const APPLY_URL = "/api/v1/console/update/apply";
  const POLL_INTERVAL_MS = 2000;
  const POLL_TIMEOUT_MS = 60000;

  OC.consoleUpdateState = {
    status: null,
    busy: false,
    uiState: "idle",
    lastCheckedAt: null,
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

  function shouldShowDetail(status, uiState, presentation) {
    if (uiState !== "idle") return true;
    if (status?.inProgress || status?.dirty || status?.updateAvailable) return true;
    if (status?.supported === false || !status?.ok) return true;
    return presentation.tone !== "success";
  }

  function resolveStatusPresentation(status, uiState) {
    if (uiState === "checking") {
      return { label: "Verificando", tone: "running", detail: "Consultando o repositório remoto…" };
    }
    if (uiState === "applying") {
      return { label: "Atualizando", tone: "running", detail: "Baixando a nova versão e preparando reinício…" };
    }
    if (uiState === "reconnecting") {
      return { label: "Reiniciando", tone: "running", detail: "Aguardando o console voltar…" };
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
    const checked =
      uiState === "checking"
        ? "agora…"
        : formatCheckedAt(status?.checkedAt || OC.consoleUpdateState.lastCheckedAt, true);
    if (checked !== "—") {
      chips.push({ label: "verificado", value: checked });
    }
    return chips;
  }

  function bindConsoleUpdateButton() {
    const root = getBarRoot();
    if (!root || root.dataset.updateBound === "1") return;
    root.dataset.updateBound = "1";
    root.addEventListener("click", (event) => {
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
    const buttonLabel =
      uiState === "checking"
        ? "Verificando…"
        : uiState === "applying"
          ? "Aplicando…"
          : uiState === "reconnecting"
            ? "Reiniciando…"
            : hasUpdate
              ? "Atualizar agora"
              : "Verificar atualização";

    root.classList.toggle("is-update-available", hasUpdate);
    root.classList.toggle("is-updating", uiState !== "idle" || Boolean(status?.inProgress));

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
        </div>
        <button
          type="button"
          class="btn ${hasUpdate ? "btn-primary" : "btn-secondary"} btn-sm ops-console-update-btn"
          id="btn-console-update"
          ${disabled ? "disabled" : ""}
          title="${OC.escapeHtml(
            supported
              ? hasUpdate
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

  OC.fetchConsoleUpdateStatus = async function fetchConsoleUpdateStatus() {
    const data = await OC.fetchJson(STATUS_URL, { timeoutMs: 120000 });
    OC.consoleUpdateState.status = data;
    OC.consoleUpdateState.lastCheckedAt = data.checkedAt || new Date().toISOString();
    return data;
  };

  async function waitForConsoleRestart() {
    const started = Date.now();
    renderConsoleUpdateBar(OC.consoleUpdateState.status, "reconnecting");
    while (Date.now() - started < POLL_TIMEOUT_MS) {
      await new Promise((resolve) => setTimeout(resolve, POLL_INTERVAL_MS));
      try {
        await OC.fetchJson("/api/v1/auth/status", { timeoutMs: 3000 });
        return true;
      } catch {
        /* server still restarting */
      }
    }
    return false;
  }

  OC.refreshConsoleUpdateBar = async function refreshConsoleUpdateBar(options = {}) {
    const silent = options.silent === true;
    const root = getBarRoot();
    if (!root) return null;

    if (OC.currentRoute?.view !== "deploy") {
      setBarVisible(false);
      return null;
    }

    setBarVisible(true);
    renderConsoleUpdateBar(OC.consoleUpdateState.status, silent ? "idle" : "checking");

    try {
      const status = await OC.fetchConsoleUpdateStatus();
      renderConsoleUpdateBar(status, "idle");
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
      renderConsoleUpdateBar(fallback, "idle");
      if (!silent) {
        OC.showToast?.(err.message || "Falha ao verificar atualização do console", "error");
      }
      return null;
    }
  };

  OC.runConsoleUpdateCheck = async function runConsoleUpdateCheck() {
    if (OC.consoleUpdateState.busy) return;

    OC.consoleUpdateState.busy = true;
    try {
      const status = await OC.refreshConsoleUpdateBar();
      if (!status) {
        if (OC.consoleUpdateState.status?.reason) {
          OC.showToast?.(OC.consoleUpdateState.status.reason, "error");
        }
        return;
      }

      if (status.supported === false) {
        OC.showToast?.(status.reason || "Atualização indisponível neste ambiente.", "warn");
        return;
      }
      if (!status.ok && !status.updateAvailable) {
        OC.showToast?.(status.error || status.reason || "Falha ao verificar atualização.", "error");
        return;
      }
      if (!status.updateAvailable) {
        OC.showToast?.("Console já está atualizado.", "success");
        return;
      }

      const fromSha = status.currentSha || "?";
      const toSha = status.remoteSha || "?";
      const confirmed = OC.confirmAction?.(
        `Nova versão ${fromSha} → ${toSha}.\n\nBaixar e reiniciar o console?`
      );
      if (!confirmed) return;

      renderConsoleUpdateBar(status, "applying");
      const result = await OC.postAction(APPLY_URL, {});
      if (!result?.ok) {
        OC.showToast?.(result?.error || result?.reason || "Falha ao aplicar atualização.", "error");
        renderConsoleUpdateBar(status, "idle");
        return;
      }
      if (!result.restarting) {
        OC.showToast?.(result.message || "Console já está atualizado.", "success");
        renderConsoleUpdateBar(status, "idle");
        return;
      }

      OC.showToast?.("Reiniciando console…", "info");
      const back = await waitForConsoleRestart();
      if (back) {
        window.location.reload();
        return;
      }
      OC.showToast?.("O console não respondeu após o reinício. Verifique manualmente.", "error");
      renderConsoleUpdateBar(status, "idle");
    } catch (err) {
      OC.showToast?.(err.message || "Falha ao atualizar o console.", "error");
      renderConsoleUpdateBar(OC.consoleUpdateState.status, "idle");
    } finally {
      OC.consoleUpdateState.busy = false;
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
