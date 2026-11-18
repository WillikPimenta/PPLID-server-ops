/* global window */
(function () {
  window.OpsConsole = window.OpsConsole || {};
  const OC = window.OpsConsole;

  const inFlightActions = new Set();
  const actionTimeouts = { rollback: 315000, redeploy: 240000, 'clear-block': 240000,
    cancel: 135000, restart: 90000, disable: 150000, enable: 330000, promote: 240000 };
  OC.postAction = async function postAction(url, body = {}, options = {}) {
    if (inFlightActions.has(url)) throw new Error('Esta ação já está em andamento. Aguarde o resultado.');
    const action = String(url).match(/^\/api\/v1\/actions\/([^/]+)/)?.[1];
    inFlightActions.add(url);
    try {
      return await OC.fetchJson(url, {
        method: "POST",
        body: JSON.stringify(body),
        timeoutMs: actionTimeouts[action] || OC.FETCH_TIMEOUT_MS || 8000,
        ...options,
      });
    } finally {
      inFlightActions.delete(url);
    }
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
      if (onDone) { onDone(err); return; }
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
      if (onDone) { onDone(err); return; }
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
      if (onDone) { onDone(err); return; }
      throw err;
    }
  };

  OC.runCancelDeploy = async function runCancelDeploy(env, onDone) {
    const msg =
      `Cancelar o deploy em andamento em ${env}?\n\n` +
      `O pipeline sera interrompido e o ambiente liberado para um novo redeploy.\n` +
      `Se o cancelamento ocorrer durante a publicacao (promote), o ambiente pode ficar inconsistente — use Rollback se necessario.`;
    if (!OC.confirmAction(msg)) return;
    try {
      const result = await OC.postAction(`/api/v1/actions/cancel/${env}`, {});
      if (onDone) onDone(null, result);
      return result;
    } catch (err) {
      if (onDone) { onDone(err); return; }
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
      if (onDone) { onDone(err); return; }
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

  const deployTrackers = new Map();
  OC.trackAcceptedDeploy = function trackAcceptedDeploy(env, runId) {
    if (!env || !runId || deployTrackers.has(runId)) return;
    const started = Date.now();
    let warned = false;
    const schedule = delay => deployTrackers.set(runId, setTimeout(poll, delay));
    const finish = () => deployTrackers.delete(runId);
    const poll = async () => {
      if (Date.now() - started > 45 * 60 * 1000) {
        finish();
        OC.showToast('Acompanhamento do deploy expirou. Consulte o histórico antes de tentar novamente.', 'warn');
        return;
      }
      if (OC.authState?.locked) { schedule(15000); return; }
      try {
        const status = await OC.fetchJson(`/api/v1/actions/deploy-status/${encodeURIComponent(env)}?runId=${encodeURIComponent(runId)}`, { timeoutMs: 10000 });
        warned = false;
        if (status.state === 'failed') {
          finish();
          OC.showToast(`Deploy ${env} não concluiu: ${status.error || 'verifique os logs.'}`, 'error');
          OC.refresh?.();
          return;
        }
        if (status.state === 'completed') {
          finish();
          OC.showToast(`Pipeline ${env} concluído.`, 'success');
          OC.refresh?.();
          return;
        }
        if (status.state === 'unknown') {
          finish();
          OC.showToast(`Não foi possível confirmar o pipeline ${env}. Consulte o histórico de deploy.`, 'warn');
          return;
        }
        schedule(5000);
      } catch (error) {
        if (!warned) {
          OC.showToast(`Acompanhamento do deploy ${env} indisponível; tentando novamente.`, 'warn');
          warned = true;
        }
        schedule(15000);
      }
    };
    schedule(5000);
  };

  OC.runDisableEnv = async function runDisableEnv(env, onDone) {
    const strong =
      env === "MAIN"
        ? `\n\nAtenção: MAIN é o ambiente de produção. Processos serão parados e watch/deploy/probes ficam pausados.`
        : `\n\nProcessos serão parados e watch/deploy/probes deste ambiente ficam pausados.`;
    if (!OC.confirmAction(`Desativar o ambiente ${env}?${strong}`)) return;
    try {
      const result = await OC.postAction(`/api/v1/actions/disable/${env}`, {});
      if (onDone) onDone(null, result);
      return result;
    } catch (err) {
      if (onDone) { onDone(err); return; }
      throw err;
    }
  };

  OC.runEnableEnv = async function runEnableEnv(env, onDone) {
    if (!OC.confirmAction(`Ativar o ambiente ${env} e iniciar os serviços?`)) return;
    try {
      const result = await OC.postAction(`/api/v1/actions/enable/${env}`, {});
      if (onDone) onDone(null, result);
      return result;
    } catch (err) {
      if (onDone) { onDone(err); return; }
      throw err;
    }
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
      if (onDone) { onDone(err); return; }
      throw err;
    }
  };

  OC.afterActionRefresh = function afterActionRefresh(err, result, btn, context) {
    if (btn) {
      btn.disabled = false;
      btn.classList.remove("is-loading");
    }
    if (err) {
      const detail = err.timedOut ? '\n\nA operação pode continuar no servidor. Confira o status antes de tentar novamente.' : '';
      window.alert((err.message || "Falha na acao") + detail);
      return;
    }
    if (result && !result.ok) {
      const detail = result.logTail ? `\n\n${result.logTail.slice(-800)}` : "";
      window.alert((result.error || "Falha na acao") + detail);
      return;
    }
    if (result?.accepted && result.environment) {
      OC.showToast(
        `Solicitação de deploy ${result.environment} aceita — SHA ${result.targetSha || "?"}, run ${result.runId || "?"}. Acompanhe o pipeline.`,
        "success"
      );
      OC.focusEnvCard?.(result.environment);
      OC.trackAcceptedDeploy(result.environment, result.runId);
    } else if (result?.ok && result.action === "cancel") {
      OC.showToast(result.message || `Deploy ${result.environment || ""} cancelado.`, "success");
      if (result.environment) OC.focusEnvCard?.(result.environment);
    } else if (result?.ok && result.action === "disable") {
      OC.showToast(result.message || `Ambiente ${result.environment || ""} desativado.`, "success");
      if (result.environment) OC.focusEnvCard?.(result.environment);
    } else if (result?.ok && result.action === "enable") {
      OC.showToast(result.message || `Ambiente ${result.environment || ""} ativado.`, "success");
      if (result.environment) OC.focusEnvCard?.(result.environment);
    } else if (result?.ok) {
      const svc = context?.service || result.service;
      const env = context?.env;
      let portOk = true;
      if (svc === "frontend") portOk = result.portListening !== false;
      else if (svc === "backend") portOk = result.portListening !== false;
      else if (svc === "all" && result.portListening) {
        portOk = result.portListening.frontend !== false && result.portListening.backend !== false;
      }
      if (!svc) {
        OC.showToast(result.message || "Ação concluída.", "success");
      } else if (portOk) {
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

  OC.withActionButton = async function withActionButton(btn, handler) {
    if (!btn || btn.disabled) return;
    btn.disabled = true;
    btn.classList.add("is-loading");
    try {
      return await handler();
    } catch (err) {
      OC.afterActionRefresh(err, null, btn);
    } finally {
      btn.disabled = false;
      btn.classList.remove("is-loading");
    }
  };

  OC.bindActionButton = function bindActionButton(btn, handler) {
    btn.addEventListener("click", () => OC.withActionButton(btn, handler));
  };
})();
