/* global window */
(function () {
  window.OpsConsole = window.OpsConsole || {};
  const OC = window.OpsConsole;

  OC.postAction = async function postAction(url, body = {}) {
    return OC.fetchJson(url, { method: "POST", body: JSON.stringify(body) });
  };

  OC.putJson = async function putJson(url, body = {}) {
    return OC.fetchJson(url, { method: "PUT", body: JSON.stringify(body) });
  };

  OC.confirmAction = function confirmAction(message) {
    return window.confirm(message);
  };

  OC.runRollback = async function runRollback(env, sha, onDone) {
    const msg = sha
      ? `Reverter ${env} para o commit ${sha}? O schema do banco pode nao ser revertido automaticamente.`
      : `Reverter ${env} para a versao anterior? O schema do banco pode nao ser revertido automaticamente.`;
    if (!OC.confirmAction(msg)) return;
    try {
      const body = { reason: "console" };
      if (sha) body.sha = sha;
      const result = await OC.postAction(`/api/v1/actions/rollback/${env}`, body);
      if (onDone) onDone(null, result);
      return result;
    } catch (err) {
      if (onDone) onDone(err);
      throw err;
    }
  };

  OC.runRedeploy = async function runRedeploy(env, sha, onDone) {
    const msg = sha
      ? `Re-implantar ${env} com o commit ${sha}?`
      : `Re-implantar ${env} com o commit mais recente de origin/${env.toLowerCase()}?`;
    if (!OC.confirmAction(msg)) return;
    try {
      const body = sha ? { sha, async: true } : { async: true };
      const result = await OC.postAction(`/api/v1/actions/redeploy/${env}`, body);
      if (onDone) onDone(null, result);
      return result;
    } catch (err) {
      if (onDone) onDone(err);
      throw err;
    }
  };

  OC.runClearBlockRedeploy = async function runClearBlockRedeploy(env, onDone) {
    const msg = `Limpar bloqueio de deploy em ${env} e publicar o commit mais recente do remoto?`;
    if (!OC.confirmAction(msg)) return;
    try {
      const result = await OC.postAction(`/api/v1/actions/clear-block/${env}`, {});
      if (onDone) onDone(null, result);
      return result;
    } catch (err) {
      if (onDone) onDone(err);
      throw err;
    }
  };

  OC.runRestartService = async function runRestartService(env, service, onDone) {
    if (!OC.confirmAction(`Reiniciar ${service} em ${env}?`)) return;
    try {
      const result = await OC.postAction(`/api/v1/actions/restart/${env}`, { service });
      if (onDone) onDone(null, result);
      return result;
    } catch (err) {
      if (onDone) onDone(err);
      throw err;
    }
  };

  OC.showToast = function showToast(message, type = "info") {
    let host = document.getElementById("toast-host");
    if (!host) {
      host = document.createElement("div");
      host.id = "toast-host";
      host.className = "toast-host";
      host.setAttribute("aria-live", "polite");
      document.body.appendChild(host);
    }
    const el = document.createElement("div");
    el.className = `toast toast-${type}`;
    el.textContent = message;
    host.appendChild(el);
    setTimeout(() => el.remove(), 6000);
  };

  OC.runPromote = async function runPromote(source, target, onDone) {
    const overview = OC.lastOverview;
    const sourceData = overview?.environments?.[source] || {};
    const targetData = overview?.environments?.[target] || {};
    const sha =
      sourceData.deployState?.activeSha ||
      sourceData.activeSha ||
      sourceData.deployedSha ||
      "—";
    const homBusy = (targetData.displayPhase || targetData.phase) === "deploying";
    let msg = `Promover SHA ${sha} de ${source} para ${target}?`;
    if (homBusy) msg += `\n\nAtenção: ${target} já está em deploy.`;
    if (!OC.confirmAction(msg)) return;
    try {
      const result = await OC.postAction("/api/v1/actions/promote", { source, target });
      if (onDone) onDone(null, result);
      return result;
    } catch (err) {
      if (onDone) onDone(err);
      throw err;
    }
  };

  OC.afterActionRefresh = function afterActionRefresh(err, result, btn, context) {
    if (btn) {
      btn.disabled = false;
      btn.classList.remove("is-loading");
    }
    if (err) {
      window.alert(err.message || "Falha na acao");
      return;
    }
    if (result && !result.ok) {
      const detail = result.logTail ? `\n\n${result.logTail.slice(-800)}` : "";
      window.alert((result.error || "Falha na acao") + detail);
      return;
    }
    if (result?.accepted && result.environment) {
      OC.showToast(
        `Deploy ${result.environment} iniciado — SHA ${result.targetSha || "?"}, run ${result.runId || "?"}`,
        "success"
      );
      OC.focusEnvCard?.(result.environment);
    } else if (result?.ok) {
      const svc = context?.service || result.service;
      const env = context?.env;
      let portOk = true;
      if (svc === "frontend") portOk = result.portListening !== false;
      else if (svc === "backend") portOk = result.portListening !== false;
      else if (svc === "all" && result.portListening) {
        portOk = result.portListening.frontend !== false && result.portListening.backend !== false;
      }
      if (portOk) {
        OC.showToast("Serviço reiniciado e porta respondendo.", "success");
      } else {
        OC.showToast("Restart concluído, mas a porta ainda não responde — veja logs de runtime.", "warn");
        if (env && svc && svc !== "all") {
          OC.drawerServiceLog = svc;
          OC.drawerActiveTab = "logs";
        }
      }
    }
    OC.refresh?.();
  };

  OC.bindActionButton = function bindActionButton(btn, handler) {
    btn.addEventListener("click", async () => {
      btn.disabled = true;
      btn.classList.add("is-loading");
      try {
        await handler();
      } finally {
        btn.disabled = false;
        btn.classList.remove("is-loading");
      }
    });
  };
})();
