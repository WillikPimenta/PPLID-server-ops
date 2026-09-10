/* global window, document */
(function () {
  "use strict";

  window.OpsConsole = window.OpsConsole || {};
  const OC = window.OpsConsole;
  const CFG = OC.automationConfig || {};
  const MODES = ["production", "rotina"];
  const LABELS = { production: "Produção (H/H)", rotina: "Rotina diária" };
  const DESCRIPTIONS = {
    production: "Executa o fluxo de produção hora a hora.",
    rotina: "Executa os processamentos da rotina diária.",
  };

  let data = null;
  let timer = null;
  let pendingAction = "";
  let configModalMode = "";
  const credentialDraft = { matricula: "", senha: "", validated: false, error: "" };

  function request(url, method = "GET", body = null, timeoutMs = 15000) {
    return OC.fetchJson(url, {
      method,
      body: body == null ? undefined : JSON.stringify(body),
      timeoutMs,
    });
  }

  function runtimeReadiness() {
    return data?.runtimeReadiness || {};
  }

  function runtimeReady() {
    return runtimeReadiness().ready === true;
  }

  function runtimeStatusLabel() {
    const readiness = runtimeReadiness();
    if (pendingAction === "validate") return "Validando Okta…";
    if (readiness.installing) return "Preparando dependências…";
    if (readiness.ready) return "Runtime pronto";
    return "Runtime pendente";
  }

  function runtimeStatusTone() {
    const readiness = runtimeReadiness();
    if (pendingAction === "validate") return "is-busy";
    if (readiness.installing) return "is-busy";
    if (readiness.ready) return "is-ready";
    return "is-pending";
  }

  function tone(status) {
    if (["running", "starting"].includes(status)) return "ok";
    if (["error", "interrupted"].includes(status)) return "error";
    if (status === "stopping") return "warn";
    return "idle";
  }

  function statusLabel(status) {
    return ({ running: "Em execução", starting: "Iniciando", stopping: "Parando", error: "Com falha", interrupted: "Interrompido", idle: "Parado", scheduled: "Agendado" })[status] || "Parado";
  }

  function opsRunning(bot) {
    return bot?.controller === "ops" && Boolean(bot?.running);
  }

  function botPresentation(mode, bot, botConfig) {
    const status = bot?.status || "idle";
    const running = Boolean(bot?.running);
    const agendada = mode === "rotina" && CFG.isRotinaAgendada?.(botConfig) && running && ["starting", "running"].includes(status);
    if (agendada) return { status: "scheduled", tone: "scheduled" };
    return { status, tone: tone(status) };
  }

  function selectedTargets(settings) {
    const fromSettings = Array.isArray(settings?.targetEnvironments) ? settings.targetEnvironments : [];
    const cleaned = fromSettings.filter((env) => OC.ENV_ORDER.includes(env));
    if (cleaned.length) return cleaned;
    const primary = settings?.targetEnvironment;
    return primary && OC.ENV_ORDER.includes(primary) ? [primary] : ["MAIN"];
  }

  function targetAvailability(env) {
    const probe = data?.targetAvailability?.[env];
    if (probe && typeof probe.available === "boolean") return probe;
    const info = data?.environments?.[env] || {};
    if (typeof info.available === "boolean") {
      return { available: info.available, reason: info.reason || "", database: info.database };
    }
    return {};
  }

  function targetReason(probe, info) {
    const reason = String(probe?.reason || info?.reason || "").trim();
    if (reason) return reason.length > 140 ? `${reason.slice(0, 137)}…` : reason;
    if (info?.enabled === false) return "ambiente desativado";
    return "indisponível";
  }

  function formatTargetLabel(targets) {
    return targets.map((env) => {
      const info = data?.environments?.[env] || {};
      return `${env} · ${info.database || "não configurado"}`;
    }).join(" | ");
  }

  function botBankLabel(bot) {
    const active = (bot?.instances || []).filter((item) => item?.environment).map((item) => item.environment);
    const configured = bot?.targetEnvironments || active;
    const list = active.length ? active : configured;
    if (Array.isArray(list) && list.length) return list.join(" · ");
    return bot?.targetEnvironment || "Não definido";
  }

  function botProcessLabel(bot) {
    const instances = (bot?.instances || []).filter((item) => item?.running || item?.runnerPid || item?.supervisorPid);
    if (!instances.length) return "Sem processo";
    return instances.map((item) => {
      const pid = item.runnerPid || item.supervisorPid;
      return pid ? `${item.environment}: PID ${pid}` : `${item.environment}: iniciando`;
    }).join(" · ");
  }

  function renderTargetChips(selected) {
    const count = selected.length;
    return `<div class="auto-target-picker" role="group" aria-label="Bancos de destino">
      <div class="auto-target-chip-row">${OC.ENV_ORDER.map((env) => {
        const info = data?.environments?.[env] || {};
        const probe = targetAvailability(env);
        const available = probe.available === true;
        const isSelected = selected.includes(env);
        const disabled = !available || Boolean(data?.summary?.running);
        const reason = targetReason(probe, info);
        const classes = [
          "auto-target-chip",
          isSelected && available ? "is-selected" : "",
          isSelected && !available ? "is-stale-selected" : "",
          !available ? "is-unavailable" : "",
        ].filter(Boolean).join(" ");
        return `<button type="button" class="${classes}" data-auto-target-chip="${env}" ${!available ? "disabled" : ""} aria-pressed="${isSelected ? "true" : "false"}" title="${OC.escapeHtml(available ? `${env} · ${info.database || ""}` : reason)}">
          <span class="auto-target-chip-env">${OC.escapeHtml(env)}</span>
          <small>${OC.escapeHtml(available ? (info.database || "não configurado") : reason)}</small>
        </button>`;
      }).join("")}</div>
      <p class="auto-target-hint">Selecione até 2 bancos disponíveis. Ao iniciar, o bot sobe em paralelo em cada um selecionado. <span class="auto-target-count">${count}/2</span></p>
    </div>`;
  }

  function configModalBody(mode, bot) {
    const botConfig = data?.configs?.[mode] || {};
    const configJson = JSON.stringify(botConfig, null, 2);
    const name = LABELS[mode];
    const configDisabled = opsRunning(bot) || Boolean(pendingAction);
    const formHtml = CFG.renderBotConfigForm?.(mode, botConfig, { disabled: configDisabled }) || "";
    return `<div class="auto-config-modal-inner" data-auto-config-modal-inner="${mode}">
      ${configDisabled && opsRunning(bot) ? `<p class="auto-config-hint auto-config-locked">Pare o bot Ops antes de alterar a configuração.</p>` : ""}
      ${formHtml}
      <details class="auto-config auto-config-advanced">
        <summary><span>JSON avançado</span><small>fallback para campos extras</small></summary>
        <div class="auto-config-body">
          <textarea data-auto-config-json="${mode}" spellcheck="false" aria-label="JSON avançado de ${OC.escapeHtml(name)}">${OC.escapeHtml(configJson)}</textarea>
          <button type="button" class="btn btn-secondary" data-auto-apply-json="${mode}" ${configDisabled ? "disabled" : ""}>Aplicar JSON</button>
        </div>
      </details>
    </div>`;
  }

  function configModalMarkup() {
    const mode = configModalMode;
    const bot = mode ? data?.bots?.[mode] : null;
    const open = Boolean(mode);
    const title = mode ? LABELS[mode] : "Configuração";
    const configDisabled = mode ? opsRunning(bot) || Boolean(pendingAction) : true;
    return `<div id="auto-config-modal" class="auto-config-modal ${open ? "" : "hidden"}" aria-hidden="${open ? "false" : "true"}">
      <div class="auto-config-modal-backdrop" data-auto-config-close></div>
      <div class="modal-card auto-config-modal-card" role="dialog" aria-modal="true" aria-labelledby="auto-config-modal-title">
        <header class="auto-config-modal-header">
          <div>
            <span class="auto-eyebrow">Configuração do bot</span>
            <h2 id="auto-config-modal-title">${OC.escapeHtml(title)}</h2>
          </div>
          <button type="button" class="drawer-close auto-config-modal-close" data-auto-config-close aria-label="Fechar">×</button>
        </header>
        <div class="auto-config-modal-body" id="auto-config-modal-body">${open ? configModalBody(mode, bot) : ""}</div>
        <footer class="auto-config-modal-footer">
          <button type="button" class="btn btn-secondary" data-auto-config-close>Cancelar</button>
          <button type="button" class="btn btn-primary" data-auto-save-config="${mode || ""}" ${!open || configDisabled ? "disabled" : ""}>Salvar configuração</button>
        </footer>
      </div>
    </div>`;
  }

  function botCard(mode, bot) {
    const running = Boolean(bot?.running);
    const catalog = data?.botsCatalog?.find((item) => item.id === mode) || {};
    const isStarting = pendingAction === `start:${mode}`;
    const isStopping = pendingAction === `stop:${mode}`;
    const botConfig = data?.configs?.[mode] || {};
    const name = catalog.name || LABELS[mode];
    const description = catalog.description || DESCRIPTIONS[mode];
    const configSummary = CFG.renderConfigSummary?.(mode, botConfig) || "";
    const presentation = botPresentation(mode, bot, botConfig);
    const status = presentation.status;
    const statusTone = presentation.tone;

    return `<article class="auto-bot is-${statusTone}">
      <div class="auto-bot-accent" aria-hidden="true"></div>
      <header class="auto-bot-header">
        <div class="auto-bot-identity">
          <span class="auto-bot-mark" aria-hidden="true">${mode === "production" ? "P" : "R"}</span>
          <div><span class="auto-eyebrow">${OC.escapeHtml(catalog.type || "Automação")}</span><h3>${OC.escapeHtml(name)}</h3></div>
        </div>
        <div class="auto-bot-header-actions">
          <button type="button" class="auto-config-gear" data-auto-open-config="${mode}" aria-label="Configurar ${OC.escapeHtml(name)}" title="Configuração">
            <span aria-hidden="true">⚙</span>
          </button>
          <span class="auto-status-pill is-${statusTone}"><i aria-hidden="true"></i>${OC.escapeHtml(statusLabel(status))}</span>
        </div>
      </header>
      <p class="auto-bot-description">${OC.escapeHtml(description)}</p>
      <dl class="auto-bot-meta">
        <div><dt>Banco</dt><dd>${OC.escapeHtml(botBankLabel(bot))}</dd></div>
        <div><dt>Processo</dt><dd>${OC.escapeHtml(botProcessLabel(bot))}</dd></div>
        <div><dt>Runtime</dt><dd>${OC.escapeHtml(bot?.bundle?.id || "Ops nativo")}</dd></div>
        <div><dt>Controle</dt><dd>${OC.escapeHtml(bot?.controller || "Ops Console")}</dd></div>
        ${configSummary ? `<div class="auto-bot-meta-wide"><dt>Configuração</dt><dd>${OC.escapeHtml(configSummary)}</dd></div>` : ""}
      </dl>
      ${bot?.error ? `<div class="auto-error" role="alert"><strong>Não foi possível executar</strong><span>${OC.escapeHtml(bot.error)}</span></div>` : ""}
      ${(bot?.instances || []).filter((item) => item?.error).map((item) => `<div class="auto-error" role="alert"><strong>Falha em ${OC.escapeHtml(item.environment || "banco")}</strong><span>${OC.escapeHtml(item.error)}</span></div>`).join("")}
      <footer class="auto-bot-footer">
        <div class="auto-primary-actions">
          <button class="btn btn-primary auto-action-main" data-auto-start="${mode}" ${running || !credentialDraft.validated || pendingAction ? "disabled" : ""}><span aria-hidden="true">▶</span>${isStarting ? "Iniciando…" : "Iniciar bot"}</button>
          <button class="btn btn-danger auto-action-main" data-auto-stop="${mode}" ${!running || pendingAction ? "disabled" : ""}><span aria-hidden="true">■</span>${isStopping ? "Parando…" : "Parar bot"}</button>
        </div>
        <button class="btn btn-secondary auto-icon-action" data-auto-logs="${mode}">Logs</button>
      </footer>
      <pre class="auto-log hidden" data-auto-log-panel="${mode}"></pre>
    </article>`;
  }

  function markup() {
    const settings = data?.settings || {};
    const summary = data?.summary || {};
    const targets = selectedTargets(settings);
    const targetLabel = targets.join(" · ");
    const targetDatabase = formatTargetLabel(targets);

    return `<section class="auto-hero">
        <div class="auto-hero-copy">
          <span class="auto-engine-badge"><i aria-hidden="true"></i>Engine Ops nativa</span>
          <h2>Central de automações</h2>
          <p>Controle os bots, credenciais e destino dos dados em um único lugar.</p>
        </div>
        <div class="auto-kpis" aria-label="Resumo das automações">
          <div class="auto-kpi"><span>Em execução</span><strong>${summary.running || 0}</strong><small>de ${summary.total || 0} bots</small></div>
          <div class="auto-kpi ${summary.errors ? "is-error" : ""}"><span>Falhas</span><strong>${summary.errors || 0}</strong><small>${summary.errors ? "requer atenção" : "tudo certo"}</small></div>
          <div class="auto-kpi is-database"><span>Destino atual</span><strong>${OC.escapeHtml(targetLabel)}</strong><small>${OC.escapeHtml(targetDatabase)}</small></div>
        </div>
      </section>
      <section class="auto-control-center">
        <div class="auto-control-heading">
          <div><span class="auto-eyebrow">Antes de iniciar</span><h2>Preparar execução</h2><p>Os bancos selecionados (até 2) são fixados no momento em que cada bot começa.</p></div>
          <button class="btn btn-secondary auto-refresh" data-auto-refresh title="Atualização manual; o polling automático está desativado"><span aria-hidden="true">↻</span> Atualizar estados</button>
        </div>
        <div class="auto-control-grid">
          <div class="auto-target-panel">
            <div class="auto-step-number">1</div>
            <div class="auto-control-content"><label>Banco de destino</label>${renderTargetChips(targets)}</div>
          </div>
          <div class="auto-okta-panel ${credentialDraft.validated ? "is-valid" : ""} ${!runtimeReady() ? "is-runtime-pending" : ""}">
            <div class="auto-step-number">2</div>
            <div class="auto-control-content">
              <div class="auto-okta-title">
                <div>
                  <label>Credencial global Okta</label>
                  <small>Válida para todos os bots por 15 minutos</small>
                </div>
                <div class="auto-okta-title-status">
                  <span class="auto-runtime-pill ${runtimeStatusTone()}" title="${OC.escapeHtml(runtimeReadiness().reason || "")}">${OC.escapeHtml(runtimeStatusLabel())}</span>
                  <span class="auto-okta-state"><i aria-hidden="true"></i>${credentialDraft.validated ? "Validada" : "Pendente"}</span>
                </div>
              </div>
              <div class="auto-okta-fields">
                <label><span>Matrícula</span><input type="text" autocomplete="username" data-auto-global-user placeholder="Sua matrícula"></label>
                <label><span>Senha</span><input type="password" autocomplete="current-password" data-auto-global-pass placeholder="Sua senha"></label>
                <button class="btn ${credentialDraft.validated ? "btn-secondary" : "btn-primary"}" data-auto-global-validate ${pendingAction === "validate" || !runtimeReady() ? "disabled" : ""} title="${OC.escapeHtml(!runtimeReady() ? (runtimeReadiness().reason || "Aguarde o runtime de automações") : "")}">${pendingAction === "validate" ? "Validando…" : credentialDraft.validated ? "Validar novamente" : "Validar Okta"}</button>
              </div>
              ${credentialDraft.error ? `<p class="auto-okta-error" role="alert">${OC.escapeHtml(credentialDraft.error)}</p>` : ""}
              ${!runtimeReady() && runtimeReadiness().reason ? `<p class="auto-okta-hint">${OC.escapeHtml(runtimeReadiness().reason)}</p>` : ""}
              ${pendingAction === "validate" ? `<p class="auto-okta-hint">Consultando Okta… isso pode levar até 3 minutos na primeira validação.</p>` : ""}
            </div>
          </div>
        </div>
      </section>
      <section class="auto-bots-section">
        <div class="auto-section-heading"><div><span class="auto-eyebrow">Operação</span><h2>Seus bots</h2></div><span class="auto-bot-count">${summary.total || MODES.length} automações nativas</span></div>
        ${!credentialDraft.validated ? `<div class="auto-guidance"><span aria-hidden="true">i</span><p>Valide a credencial global acima para liberar o início dos bots.</p></div>` : ""}
        <div class="auto-bot-grid">${MODES.map((mode) => botCard(mode, data?.bots?.[mode])).join("")}</div>
      </section>`;
  }

  function configModalHost() {
    let host = document.getElementById("auto-config-modal-host");
    if (!host) {
      host = document.createElement("div");
      host.id = "auto-config-modal-host";
      host.setAttribute("aria-live", "polite");
      document.body.appendChild(host);
    }
    return host;
  }

  function getConfigModal() {
    return document.getElementById("auto-config-modal");
  }

  function renderConfigModal() {
    const host = configModalHost();
    host.innerHTML = configModalMarkup();
    bindConfigModal();
  }

  function resetConfigModalState() {
    configModalMode = "";
    document.body.classList.remove("auto-config-modal-open");
  }

  function openConfigModal(mode) {
    configModalMode = mode;
    document.body.classList.add("auto-config-modal-open");
    try {
      renderConfigModal();
    } catch (err) {
      resetConfigModalState();
      try { renderConfigModal(); } catch (_) { /* ignore */ }
      OC.showToast?.(err.message || "Falha ao abrir configuração", "error");
    }
  }

  function closeConfigModal() {
    resetConfigModalState();
    renderConfigModal();
  }

  async function saveConfig(mode, config, root) {
    await request(`/api/v1/automations/config/${mode}`, "PATCH", { config });
    OC.showToast?.("Configuração salva", "success");
    closeConfigModal();
    await refresh();
  }

  function setCredentialInputs(root) {
    const user = root.querySelector("[data-auto-global-user]");
    const pass = root.querySelector("[data-auto-global-pass]");
    if (user) user.value = credentialDraft.matricula;
    if (pass) pass.value = credentialDraft.senha;
  }

  function syncCredentialsFromInputs(root) {
    const user = root.querySelector("[data-auto-global-user]");
    const pass = root.querySelector("[data-auto-global-pass]");
    if (user) credentialDraft.matricula = String(user.value || "").trim();
    if (pass) credentialDraft.senha = String(pass.value || "");
    return { matricula: credentialDraft.matricula, senha: credentialDraft.senha };
  }

  function setCredentialError(root, message) {
    credentialDraft.error = message || "";
    const existing = root.querySelector(".auto-okta-error");
    if (!message) {
      existing?.remove();
      return;
    }
    if (existing) {
      existing.textContent = message;
      return;
    }
    const panel = root.querySelector(".auto-okta-panel .auto-control-content");
    if (!panel) return;
    const error = document.createElement("p");
    error.className = "auto-okta-error";
    error.setAttribute("role", "alert");
    error.textContent = message;
    panel.appendChild(error);
  }

  function invalidateCredentials(root) {
    credentialDraft.validated = false;
    credentialDraft.error = "";
    const state = root.querySelector(".auto-okta-state");
    state && (state.innerHTML = '<i aria-hidden="true"></i>Pendente');
    root.querySelector(".auto-okta-panel")?.classList.remove("is-valid");
    root.querySelector(".auto-okta-error")?.remove();
    root.querySelectorAll("[data-auto-start]").forEach((button) => { button.disabled = true; });
  }

  async function runAction(key, action) {
    if (pendingAction) {
      OC.showToast?.("Aguarde a ação em andamento terminar", "warn");
      return;
    }
    pendingAction = key;
    try {
      render();
      await action();
    } finally {
      pendingAction = "";
      try { await refresh(); }
      catch (err) { OC.showToast?.(err.message || "Falha ao atualizar estado", "error"); }
    }
  }

  function bindConfigModal() {
    const modal = getConfigModal();
    if (!modal) return;
    modal.querySelector(".auto-config-modal-card")?.addEventListener("click", (event) => event.stopPropagation());
    modal.querySelectorAll("[data-auto-config-close]").forEach((node) => {
      node.addEventListener("click", closeConfigModal);
    });
    modal.querySelectorAll("[data-auto-save-config]").forEach((button) => button.addEventListener("click", async () => {
      const mode = button.dataset.autoSaveConfig;
      if (!mode) return;
      try {
        const config = CFG.readBotConfigForm?.(mode, modal) || {};
        await saveConfig(mode, config, modal);
      } catch (err) { OC.showToast?.(err.message, "error"); }
    }));
    modal.querySelectorAll("[data-auto-apply-json]").forEach((button) => button.addEventListener("click", async () => {
      const mode = button.dataset.autoApplyJson;
      const raw = modal.querySelector(`[data-auto-config-json="${mode}"]`)?.value || "{}";
      try {
        const config = JSON.parse(raw);
        await saveConfig(mode, config, modal);
      } catch (err) { OC.showToast?.(err.message, "error"); }
    }));
    CFG.bindRotinaImmediateToggle?.(modal);
  }

  async function saveTargetSelection(root, nextTargets) {
    const chips = root.querySelectorAll("[data-auto-target-chip]");
    chips.forEach((chip) => { chip.disabled = true; });
    try {
      await request("/api/v1/automations/settings", "PATCH", { targetEnvironments: nextTargets });
      OC.showToast?.(`Destino atualizado: ${nextTargets.join(" · ")}`, "success");
      await refresh();
    } catch (err) {
      chips.forEach((chip) => {
        const env = chip.dataset.autoTargetChip;
        const probe = targetAvailability(env);
        chip.disabled = probe.available !== true;
      });
      OC.showToast?.(err.message, "error");
    }
  }

  function bind(root) {
    setCredentialInputs(root);
    const globalUser = root.querySelector("[data-auto-global-user]");
    const globalPass = root.querySelector("[data-auto-global-pass]");
    globalUser?.addEventListener("input", () => { credentialDraft.matricula = globalUser.value; invalidateCredentials(root); });
    globalPass?.addEventListener("input", () => { credentialDraft.senha = globalPass.value; invalidateCredentials(root); });
    root.querySelector("[data-auto-refresh]")?.addEventListener("click", refresh);

    root.querySelectorAll("[data-auto-open-config]").forEach((button) => {
      button.addEventListener("click", () => openConfigModal(button.dataset.autoOpenConfig));
    });

    root.querySelectorAll("[data-auto-target-chip]").forEach((button) => {
      button.addEventListener("click", async () => {
        const env = button.dataset.autoTargetChip;
        if (!env || button.disabled) return;
        const current = selectedTargets(data?.settings || {});
        let next = current.includes(env) ? current.filter((item) => item !== env) : [...current, env];
        if (!next.length) {
          OC.showToast?.("Selecione ao menos um banco de destino", "warn");
          return;
        }
        if (next.length > 2) {
          OC.showToast?.("Selecione no máximo 2 bancos de destino", "warn");
          return;
        }
        if (next.length === current.length && next.every((item, index) => item === current[index])) return;
        await saveTargetSelection(root, next);
      });
    });

    root.querySelector("[data-auto-global-validate]")?.addEventListener("click", async () => {
      const creds = syncCredentialsFromInputs(root);
      setCredentialError(root, "");
      if (!runtimeReady()) {
        const message = runtimeReadiness().reason || "Runtime de automações ainda não está pronto";
        setCredentialError(root, message);
        OC.showToast?.(message, "warn");
        return;
      }
      if (!creds.matricula || !creds.senha) {
        const message = "Preencha matrícula e senha antes de validar no Okta";
        setCredentialError(root, message);
        OC.showToast?.(message, "warn");
        return;
      }
      try {
        await runAction("validate", async () => {
          const result = await request(
            "/api/v1/automations/credentials/validate",
            "POST",
            { matricula: creds.matricula, senha: creds.senha },
            210000
          );
          credentialDraft.validated = true;
          credentialDraft.error = "";
          OC.showToast?.(result.message || "Credencial global validada", "success");
        });
      } catch (err) {
        credentialDraft.validated = false;
        const message = err.message || "Falha na validação Okta";
        credentialDraft.error = message;
        setCredentialError(root, message);
        OC.showToast?.(message, "error");
      }
    });

    root.querySelectorAll("[data-auto-start]").forEach((button) => button.addEventListener("click", async () => {
      const mode = button.dataset.autoStart;
      try {
        await runAction(`start:${mode}`, async () => {
          await request(`/api/v1/automations/bots/${mode}/start`, "POST", { matricula: credentialDraft.matricula, senha: credentialDraft.senha }, 30000);
          OC.showToast?.(`${LABELS[mode]} iniciado`, "success");
        });
      } catch (err) { OC.showToast?.(err.message, "error"); }
    }));

    root.querySelectorAll("[data-auto-stop]").forEach((button) => button.addEventListener("click", async () => {
      const mode = button.dataset.autoStop;
      if (!window.confirm(`Parar ${LABELS[mode]}?`)) return;
      try {
        await runAction(`stop:${mode}`, async () => {
          await request(`/api/v1/automations/bots/${mode}/stop`, "POST", {});
          OC.showToast?.(`${LABELS[mode]} parado`, "success");
        });
      } catch (err) { OC.showToast?.(err.message, "error"); }
    }));

    root.querySelectorAll("[data-auto-logs]").forEach((button) => button.addEventListener("click", async () => {
      const mode = button.dataset.autoLogs;
      const panel = root.querySelector(`[data-auto-log-panel="${mode}"]`);
      try {
        const logs = await request(`/api/v1/automations/bots/${mode}/logs?tail=120`);
        panel.textContent = (logs.lines || []).join("\n") || "Nenhum log disponível.";
        panel.classList.toggle("hidden");
        button.textContent = panel.classList.contains("hidden") ? "Logs" : "Ocultar logs";
      } catch (err) { OC.showToast?.(err.message, "error"); }
    }));

    if (!document.body.dataset.autoEscBound) {
      document.body.dataset.autoEscBound = "true";
      document.addEventListener("keydown", (event) => {
        if (event.key === "Escape" && configModalMode) closeConfigModal();
      });
    }
  }

  function render() {
    const root = document.getElementById("view-automations");
    if (!root || !data) return;
    root.innerHTML = markup();
    bind(root);
    try {
      renderConfigModal();
    } catch (err) {
      // Modal half-open (ex.: falha anterior) não pode travar start/stop.
      resetConfigModalState();
      try { renderConfigModal(); } catch (_) { /* ignore */ }
      OC.showToast?.(err.message || "Falha ao atualizar configuração", "error");
    }
  }

  async function refresh() {
    if (OC.currentRoute?.view !== "automations") return;
    try {
      data = await request("/api/v1/automations/overview");
      render();
    } catch (err) { OC.setGlobalError?.(err.message); }
  }

  OC.renderAutomations = refresh;
  OC.startAutomationsRefresh = function () { OC.stopAutomationsRefresh(); };
  OC.stopAutomationsRefresh = function () {
    if (timer) window.clearInterval(timer);
    timer = null;
    closeConfigModal();
  };
})();
