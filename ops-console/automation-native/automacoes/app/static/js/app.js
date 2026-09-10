function readAppModes() {
  const raw = document.getElementById("app-modes-data")?.textContent || "[]";
  try {
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

const robotModes = readAppModes();

function readModeLabels() {
  const raw = document.getElementById("app-mode-labels-data")?.textContent || "{}";
  try {
    const parsed = JSON.parse(raw);
    return parsed && typeof parsed === "object" ? parsed : {};
  } catch {
    return {};
  }
}

const modeLabels = readModeLabels();

function escapeHtml(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

function getModeLabel(mode) {
  return modeLabels[mode] || mode;
}

function hasModeSpecificOptions(mode) {
  return isRotinaPeriodMode(mode) || isGedMode(mode) || isReplicacaoAudMode(mode) || mode === productionMode;
}

const CONFIG_TAB_IDS = ["execucao", "pastas", "opcoes", "logs"];

function getDefaultConfigTab(mode) {
  return hasModeSpecificOptions(mode) ? "opcoes" : "execucao";
}

function isConfigTabVisible(tabId, mode, cfg) {
  if (tabId === "pastas") {
    const folders = Array.isArray(cfg?.sharepoint_folders) ? cfg.sharepoint_folders : [];
    return mode !== "onedrive" && folders.length > 0;
  }
  if (tabId === "opcoes") {
    return hasModeSpecificOptions(mode);
  }
  return true;
}

function getActiveConfigTabId() {
  const activeBtn = document.querySelector(".config-tab.is-active");
  return activeBtn?.dataset?.tab || "execucao";
}

function activateConfigTab(tabId, { force = false } = {}) {
  const normalized = CONFIG_TAB_IDS.includes(tabId) ? tabId : "execucao";
  const mode = activeConfigMode;
  const cfg = mode ? getRobotConfig(mode) : {};

  if (!force && mode && !isConfigTabVisible(normalized, mode, cfg)) {
    activateConfigTab(getDefaultConfigTab(mode), { force: true });
    return;
  }

  document.querySelectorAll(".config-tab").forEach((btn) => {
    const isActive = btn.dataset.tab === normalized;
    btn.classList.toggle("is-active", isActive);
    btn.setAttribute("aria-selected", isActive ? "true" : "false");
  });

  document.querySelectorAll(".config-tab-panel").forEach((panel) => {
    const panelId = panel.id.replace("robot-config-tab-", "");
    const isActive = panelId === normalized;
    panel.classList.toggle("is-active", isActive);
    panel.hidden = !isActive;
  });
}

function wireConfigTabs() {
  document.querySelectorAll(".config-tab").forEach((btn) => {
    if (btn.dataset.wired === "1") return;
    btn.dataset.wired = "1";
    btn.addEventListener("click", () => {
      activateConfigTab(btn.dataset.tab || "execucao", { force: true });
    });
  });
}

function syncConfigModalSections(mode, cfg) {
  const { wrapper: rotinaWrapper } = getRotinaPeriodNodes();
  const { wrapper: gedWrapper } = getGedImmediateNodes();
  const { wrapper: replicacaoWrapper } = getReplicacaoAudNodes(replicacaoAudMode);
  const { wrapper: replicacaoD1Wrapper } = getReplicacaoAudNodes(replicacaoAudD1Mode);
  const productionWrapper = document.getElementById("robot-config-production-options");
  const productionDaysInput = document.getElementById("robot-config-production-dias-brflow");
  const noOptions = document.getElementById("robot-config-no-options");
  const sharepointBlock = document.getElementById("robot-config-sharepoint-block");
  const pastasTabBtn = document.getElementById("robot-config-tab-pastas-btn");
  const opcoesTabBtn = document.getElementById("robot-config-tab-opcoes-btn");

  if (rotinaWrapper) rotinaWrapper.hidden = !isRotinaPeriodMode(mode);
  if (gedWrapper) gedWrapper.hidden = !isGedMode(mode);
  if (replicacaoWrapper) replicacaoWrapper.hidden = mode !== replicacaoAudMode;
  if (replicacaoD1Wrapper) replicacaoD1Wrapper.hidden = mode !== replicacaoAudD1Mode;
  if (productionWrapper) productionWrapper.hidden = mode !== productionMode;
  if (productionDaysInput && mode === productionMode) {
    productionDaysInput.value = String(cfg?.dias_download_brflow || 3);
  }

  if (noOptions) {
    noOptions.hidden = hasModeSpecificOptions(mode);
  }

  const folders = Array.isArray(cfg?.sharepoint_folders) ? cfg.sharepoint_folders : [];
  const showPastas = mode !== "onedrive" && folders.length > 0;
  if (sharepointBlock) sharepointBlock.hidden = !showPastas;
  if (pastasTabBtn) pastasTabBtn.hidden = !showPastas;

  const showOpcoes = hasModeSpecificOptions(mode);
  if (opcoesTabBtn) opcoesTabBtn.hidden = !showOpcoes;
}

function expandConfigSectionForMode(mode) {
  activateConfigTab(getDefaultConfigTab(mode), { force: true });
}

const credentiallessRobotModes = ["onedrive"];
const rotinaPeriodModes = ["rotina"];
const replicacaoAudMode = "replicacao_auditoria";
const replicacaoAudD1Mode = "replicacao_auditoria_d1";
const productionMode = "production";
const pendingActionByMode = {};
const robotConfigState = {};
// Tarefas disponíveis para o modo 'rotina' (id -> label/desc/deps)
const TAREFAS_DISPONIVEIS = {
  rotinas_1d: { label: "BRBR-4467 (1 dia atrás)", descricao: "Detalhado de registros 1D", deps: [] },
  rotinas_2d: { label: "BRBR-4467 (2 dias atrás)", descricao: "Detalhado de registros 2D", deps: [] },
  rotinas_3d: { label: "BRBR-4467 (3 dias atrás)", descricao: "Detalhado de registros 3D", deps: [] },
  produtividade_d1: { label: "Produtividade D-1", descricao: "BRBR-5336 > Detalhado de Produtividade", deps: [] },
  auditoria_replicados_d1: { label: "BRBR6121 Replicados D1", descricao: "G AUDITORIA Replicados D1 - PARTE 1", deps: [] },
  auditoria_etapas: { label: "Auditoria com Etapas", descricao: "G Auditoria com etapas", deps: [] },
  irregularidade: { label: "Irregularidade GED", descricao: "Relatório de Irregularidades (GED, 15 dias)", deps: [] },
  monitor_eventos: { label: "Monitor de Eventos", descricao: "Monitor de eventos D-1", deps: ["produtividade_d1"] },
  confer_producao: { label: "Confer/Produção", descricao: "Relatório Produção Confer", deps: [] },
  log_eventos: { label: "Log de Eventos", descricao: "Log de eventos", deps: ["confer_producao"] },
  prod_unificado: { label: "Prod Unificado", descricao: "Produtividade D-1 + Confer (parquet unificado)", deps: ["produtividade_d1", "confer_producao"] },
  monitor_unificado: { label: "Monitor Unificado", descricao: "Monitor (tratado) + Log/Confer (unificado)", deps: ["monitor_eventos", "log_eventos"] },
};
const defaultReplicacaoAudConfig = {
  apenas_planejamento: false,
  run_id: "",
  gerar_novo_plano: false,
  forcar_reexecucao: false,
  apenas_pendentes: true,
  excluir_historico: true,
  dias_historico: 30,
  sobrescrever: false,
  fallback_ultimo_parquet: false,
  replicacao_aud_seed: 42,
  replicacao_aud_data_ref: "",
  usar_escala_auditores: true,
  auditores_ativos: "",
  meta_produ: "300",
  escala_auditores_csv: "",
  replicacao_cliente_destino: "GAQ",
  replicacao_cliente_cod: "751",
  replicacao_workflow_destino: "G Auditoria - G Auditoria",
  replicacao_workflow_cod: "17047",
  replicacao_workflows_amostra_100: [],
};

const defaultReplicacaoAudD1Config = {
  ...defaultReplicacaoAudConfig,
  fallback_ultimo_parquet: true,
  limpar_planos_automatico: false,
  manter_planos_ultimos_n: 5,
  dias_retencao_planos: 0,
  limpar_planos_ao_gerar: false,
  limpar_planos_apos_conclusao: false,
};

const defaultRobotConfig = {
  output_dir: "",
  headless: false,
  max_workers: null,
  executar_imediatamente: false,
  rotina_data_inicio: "",
  rotina_data_fim: "",
  ged_execucao_imediata: "",
  sharepoint_url: "",
  sharepoint_folders: [],
  ...defaultReplicacaoAudConfig,
};
let activeConfigMode = null;
const replicacaoRunningPrev = { replicacao_auditoria: false, replicacao_auditoria_d1: false };

const OKTA_VALIDATOR_VISIBLE_KEY = "ui_okta_validator_visible";
const OKTA_VALIDATE_SHOW_BROWSER_KEY = "ui_okta_validate_show_browser";
const OKTA_VALIDATE_HEADLESS_KEY_LEGACY = "ui_okta_validate_headless";

const credentialsValidationState = {
  validated: false,
  message: "Credenciais não verificadas",
  checking: false,
  last_checked_at: null,
};
let credentialsDirty = true;

function isOktaValidatorVisible() {
  const checkbox = document.getElementById("show-okta-validator");
  if (checkbox) return Boolean(checkbox.checked);
  const stored = localStorage.getItem(OKTA_VALIDATOR_VISIBLE_KEY);
  return stored !== "0";
}

function applyOktaValidatorVisibility() {
  const visible = isOktaValidatorVisible();
  const block = document.getElementById("okta-validator-block");
  const help = document.getElementById("credentials-help");
  const overlay = document.getElementById("credentials-loading-overlay");

  if (block) block.hidden = !visible;
  if (help) {
    help.textContent = visible
      ? "Preencha matrícula e senha, valide no Okta e então os botões de início serão liberados. Com \"Exibir navegador\" marcado, o Chrome abre na sua sessão para acompanhar."
      : "Preencha matrícula e senha para iniciar os robôs (validação Okta desativada).";
  }
  if (!visible && overlay) overlay.hidden = true;

  syncStartButtonsAvailability();
}

function bindOktaValidatorToggle() {
  const checkbox = document.getElementById("show-okta-validator");
  if (!checkbox) return;

  const stored = localStorage.getItem(OKTA_VALIDATOR_VISIBLE_KEY);
  if (stored === "0") checkbox.checked = false;
  else if (stored === "1") checkbox.checked = true;

  applyOktaValidatorVisibility();
  checkbox.addEventListener("change", () => {
    localStorage.setItem(OKTA_VALIDATOR_VISIBLE_KEY, checkbox.checked ? "1" : "0");
    applyOktaValidatorVisibility();
  });
}

function isOktaValidateBrowserVisible() {
  const checkbox = document.getElementById("okta-validate-show-browser");
  if (checkbox) return Boolean(checkbox.checked);

  const stored = localStorage.getItem(OKTA_VALIDATE_SHOW_BROWSER_KEY);
  if (stored === "1") return true;
  if (stored === "0") return false;

  const legacyHeadless = localStorage.getItem(OKTA_VALIDATE_HEADLESS_KEY_LEGACY);
  if (legacyHeadless === "1") return false;
  if (legacyHeadless === "0") return true;

  return true;
}

function bindOktaValidateBrowserToggle() {
  const checkbox = document.getElementById("okta-validate-show-browser");
  if (!checkbox) return;

  const stored = localStorage.getItem(OKTA_VALIDATE_SHOW_BROWSER_KEY);
  if (stored === "1") checkbox.checked = true;
  else if (stored === "0") checkbox.checked = false;
  else {
    const legacyHeadless = localStorage.getItem(OKTA_VALIDATE_HEADLESS_KEY_LEGACY);
    if (legacyHeadless === "1") checkbox.checked = false;
    else if (legacyHeadless === "0") checkbox.checked = true;
  }

  checkbox.addEventListener("change", () => {
    localStorage.setItem(OKTA_VALIDATE_SHOW_BROWSER_KEY, checkbox.checked ? "1" : "0");
  });
}

function setCredentialsLoadingOverlay(active) {
  const overlay = document.getElementById("credentials-loading-overlay");
  if (!overlay) return;
  overlay.hidden = !active;
  document.body.classList.toggle("loading-active", active);
}

function hasRequiredCredentials() {
  const { matricula, senha } = getCredentials();
  return Boolean(matricula && senha);
}

function modeRequiresCredentials(mode) {
  return !credentiallessRobotModes.includes(String(mode || "").trim().toLowerCase());
}

function canStartRobots(mode) {
  if (!modeRequiresCredentials(mode)) {
    return true;
  }
  if (!hasRequiredCredentials()) {
    return false;
  }
  if (!isOktaValidatorVisible()) {
    return true;
  }
  return credentialsValidationState.validated && !credentialsValidationState.checking && !credentialsDirty;
}

function getStartDisableReason(mode) {
  if (!modeRequiresCredentials(mode)) {
    return "";
  }
  if (!hasRequiredCredentials()) {
    return "Preencha matrícula e senha para iniciar o robô.";
  }
  if (!isOktaValidatorVisible()) {
    return "";
  }
  if (credentialsValidationState.checking) {
    return "Aguarde a validação das credenciais no Okta.";
  }
  if (credentialsDirty || !credentialsValidationState.validated) {
    return "Valide as credenciais no Okta para liberar o início.";
  }
  return "";
}

function applyCredentialsStatus(status, { respectDirty = true } = {}) {
  if (!status || typeof status !== "object") return;
  if (respectDirty && credentialsDirty && !status.checking) return;

  credentialsValidationState.validated = Boolean(status.validated);
  credentialsValidationState.message = status.message || (status.validated ? "Credenciais validadas" : "Credenciais não validadas");
  credentialsValidationState.checking = Boolean(status.checking);
  credentialsValidationState.last_checked_at = status.last_checked_at || null;
  updateCredentialsStatusUI();
}

function markCredentialsAsDirty(message = "Credenciais alteradas. Valide novamente no Okta.") {
  credentialsDirty = true;
  credentialsValidationState.validated = false;
  credentialsValidationState.checking = false;
  credentialsValidationState.message = message;
  credentialsValidationState.last_checked_at = null;
  updateCredentialsStatusUI();
}

function updateCredentialsStatusUI() {
  const node = document.getElementById("credentials-status");
  if (!node) return;

  let text = credentialsValidationState.message || "Credenciais não verificadas";
  if (credentialsValidationState.checking) {
    text = "Validando credenciais no Okta...";
  }

  node.textContent = `Status: ${text}`;
  node.classList.toggle("valid", !credentialsDirty && credentialsValidationState.validated && !credentialsValidationState.checking);
  node.classList.toggle("invalid", credentialsDirty || !credentialsValidationState.validated);
}

function syncValidateButtonAvailability() {
  const validateBtn = document.getElementById("validate-credentials-btn");
  if (!validateBtn) return;

  const credentialsFilled = hasRequiredCredentials();
  validateBtn.disabled = !credentialsFilled || credentialsValidationState.checking;
  validateBtn.title = credentialsFilled ? "" : "Preencha matrícula e senha para validar no Okta.";
}

function syncStartButtonsAvailability() {
  robotModes.forEach((mode) => {
    if (pendingActionByMode[mode]) return;
    const { startBtn, card } = getRobotButtons(mode);
    const isRunning = card?.dataset?.running === "1";
    const startEnabled = canStartRobots(mode);
    const disabledReason = getStartDisableReason(mode);

    if (startBtn && !isRunning) {
      startBtn.disabled = !startEnabled;
      startBtn.title = startEnabled ? "" : disabledReason;
    }
  });

  syncValidateButtonAvailability();
}

function getRobotButtons(mode) {
  const card = document.querySelector(`.robot-card[data-mode="${mode}"]`);
  return {
    card,
    startBtn: card?.querySelector('button[data-action="start"]') || null,
    stopBtn: card?.querySelector('button[data-action="stop"]') || null,
  };
}

function setButtonLoading(button, active, loadingText) {
  if (!button) return;
  if (!button.dataset.defaultText) {
    button.dataset.defaultText = button.textContent;
  }
  button.disabled = active;
  button.classList.toggle("is-loading", active);
  button.textContent = active ? loadingText : button.dataset.defaultText;
}

function getCredentials() {
  return {
    matricula: document.getElementById("matricula").value.trim(),
    senha: document.getElementById("senha").value,
  };
}

function toLocalISODate(date) {
  const local = new Date(date.getTime() - (date.getTimezoneOffset() * 60000));
  return local.toISOString().slice(0, 10);
}

function getRotinaPeriodNodes() {
  return {
    wrapper: document.getElementById("robot-config-rotina-options"),
    checkbox: document.getElementById("robot-config-imediata"),
    startInput: document.getElementById("robot-config-data-inicio"),
    endInput: document.getElementById("robot-config-data-fim"),
  };
}

function isRotinaPeriodMode(mode) {
  return rotinaPeriodModes.includes(mode);
}

function isTarefasMode(mode) {
  return isRotinaPeriodMode(mode);
}

function getTarefasContainer() {
  return document.getElementById("robot-config-tarefas-container");
}

function renderTarefasCheckboxes() {
  if (!activeConfigMode || !isTarefasMode(activeConfigMode)) {
    const container = getTarefasContainer();
    if (container) container.style.display = "none";
    return;
  }

  const container = getTarefasContainer();
  if (!container) return;

  const cfg = getRobotConfig(activeConfigMode) || {};
  const explicitSelecionadas = Array.isArray(cfg.tarefas) ? cfg.tarefas.slice() : [];

  let html = `
    <div class="tarefas-section">
      <label class="tarefas-title">
        <input type="checkbox" id="tarefas-select-all" class="tarefas-checkbox-all">
        <strong>Tarefas a Executar</strong>
      </label>
      <div class="tarefas-list">
  `;

  Object.entries(TAREFAS_DISPONIVEIS).forEach(([id, info]) => {
    const depsText = info.deps.length ? ` (requer: ${info.deps.join(", ")})` : "";
    const depsClass = info.deps.length ? "has-deps" : "";

    // data-explicit será '1' se usuário explicitamente configurou esta tarefa
    const explicitAttr = explicitSelecionadas.includes(id) ? ' data-explicit="1"' : '';

    html += `
      <label class="tarefa-item ${depsClass}">
        <input type="checkbox" class="tarefas-checkbox" value="${id}"${explicitAttr}>
        <span class="tarefa-label">${info.label}</span>
        <span class="tarefa-desc">${info.descricao}</span>
        ${depsText ? `<span class="tarefa-deps">${depsText}</span>` : ""}
      </label>
    `;
  });

  html += `
    </div>
    <div class="tarefas-preview" id="tarefas-preview">
      <strong>Será executado:</strong> <span id="tarefas-preview-text">Nenhuma tarefa selecionada</span>
    </div>
  </div>
  `;

  container.innerHTML = html;
  container.style.display = "block";

  // Bind eventos
  const selectAllCheckbox = document.getElementById("tarefas-select-all");
  const tarefasCheckboxes = Array.from(document.querySelectorAll(".tarefas-checkbox"));

  // Gerenciamento de seleção explícita vs dependências automáticas
  let explicitSet = new Set(explicitSelecionadas.map((t) => String(t).trim().toLowerCase()));

  function computeImplied(explicitArr) {
    const resultado = new Set();
    const pend = Array.from(explicitArr);
    while (pend.length) {
      const t = pend.shift();
      const info = TAREFAS_DISPONIVEIS[t];
      if (!info || !info.deps) continue;
      info.deps.forEach((d) => {
        if (!resultado.has(d) && !explicitSet.has(d)) {
          resultado.add(d);
          pend.push(d);
        }
      });
    }
    return resultado;
  }

  function applyStateFromExplicit() {
    const implied = computeImplied(Array.from(explicitSet));
    tarefasCheckboxes.forEach((cb) => {
      const id = cb.value;
      if (explicitSet.has(id)) {
        cb.checked = true;
        cb.disabled = false;
        cb.dataset.explicit = '1';
      } else if (implied.has(id)) {
        cb.checked = true;
        cb.disabled = true;
        cb.dataset.explicit = '0';
      } else {
        cb.checked = false;
        cb.disabled = false;
        cb.dataset.explicit = '0';
      }
    });

    // atualizar select-all estado
    if (selectAllCheckbox) {
      const allChecked = tarefasCheckboxes.every((cb) => cb.checked);
      selectAllCheckbox.checked = allChecked;
    }

    updateTarefasPreview();
  }

  // Clique no select-all torna todas explícitas
  selectAllCheckbox?.addEventListener("change", (e) => {
    if (e.target.checked) {
      explicitSet = new Set(Object.keys(TAREFAS_DISPONIVEIS));
    } else {
      explicitSet = new Set();
    }
    applyStateFromExplicit();
  });

  // Handler para mudanças manuais (usuário)
  tarefasCheckboxes.forEach((cb) => {
    cb.addEventListener("change", (ev) => {
      // somente processar quando habilitado (se disabled, evento não ocorrerá)
      const id = cb.value;
      if (cb.disabled) return;

      if (cb.checked) {
        explicitSet.add(id);
      } else {
        explicitSet.delete(id);
      }
      applyStateFromExplicit();
    });
  });

  // Aplicar estado inicial
  applyStateFromExplicit();
}

function updateTarefasPreview() {
  // Mostrar preview baseado em checkboxes atuais (inclui dependências automáticas)
  const tarefasCheckboxes = Array.from(document.querySelectorAll(".tarefas-checkbox"));
  const selecionadas = tarefasCheckboxes.filter((cb) => cb.checked).map((cb) => cb.value);
  const comDeps = new Set(selecionadas);

  // ordenar e mostrar rótulos
  const preview = document.getElementById("tarefas-preview-text");
  if (preview) {
    if (comDeps.size === 0) {
      preview.textContent = "Nenhuma tarefa selecionada (padrão: TODAS)";
    } else {
      const labels = Array.from(comDeps)
        .sort((a, b) => {
          const ordem = Object.keys(TAREFAS_DISPONIVEIS);
          return ordem.indexOf(a) - ordem.indexOf(b);
        })
        .map((id) => TAREFAS_DISPONIVEIS[id].label)
        .join(", ");
      preview.textContent = labels;
    }
  }
}

function getTarefasSelecionadas() {
  // Retornar apenas SELEÇÕES EXPLÍCITAS do usuário (dataset.explicit === '1')
  const checkboxes = Array.from(document.querySelectorAll(".tarefas-checkbox"));
  return checkboxes.filter((cb) => cb.dataset && cb.dataset.explicit === '1').map((cb) => cb.value);
}

function isGedMode(mode) {
  return mode === "ged";
}

function isReplicacaoAudMode(mode) {
  return mode === replicacaoAudMode || mode === replicacaoAudD1Mode;
}

function isReplicacaoAudD1Mode(mode) {
  return mode === replicacaoAudD1Mode;
}

function replicacaoPanelPrefix(mode) {
  return isReplicacaoAudD1Mode(mode) ? "robot-config-replicacao-d1" : "robot-config-replicacao";
}

function getReplicacaoAudNodes(mode = activeConfigMode || replicacaoAudMode) {
  const p = replicacaoPanelPrefix(mode);
  return {
    wrapper: document.getElementById(`${p}-options`),
    apenasPlanejamento: document.getElementById(`${p}-apenas-planejamento`),
    runId: document.getElementById(`${p}-run-id`),
    gerarNovoPlano: document.getElementById(`${p}-gerar-novo-plano`),
    forcarReexecucao: document.getElementById(`${p}-forcar-reexecucao`),
    apenasPendentes: document.getElementById(`${p}-apenas-pendentes`),
    excluirHistorico: document.getElementById(`${p}-excluir-historico`),
    diasHistorico: document.getElementById(`${p}-dias-historico`),
    sobrescrever: document.getElementById(`${p}-sobrescrever`),
    fallbackParquet: document.getElementById(`${p}-fallback-parquet`),
    seed: document.getElementById(`${p}-seed`),
    dataRef: document.getElementById(`${p}-data-ref`),
    usarEscala: document.getElementById(`${p}-usar-escala`),
    auditoresAtivos: document.getElementById(`${p}-auditores-ativos`),
    metaProdu: document.getElementById(`${p}-meta-produ`),
    clienteCod: document.getElementById(`${p}-cliente-cod`),
    workflowCod: document.getElementById(`${p}-workflow-cod`),
    workflowsAmostra100: document.getElementById(`${p}-workflows-amostra-100`),
    workflowsAmostra100Search: document.getElementById(`${p}-workflows-amostra-100-search`),
    validarPlanoBtn: document.getElementById(`${p}-validar-plano`),
    validarResultado: document.getElementById(`${p}-validar-resultado`),
    runsList: document.getElementById(`${p}-runs-list`),
    limparPlanosAutomatico: document.getElementById(`${p}-limpar-planos-automatico`),
    limparPlanosAoGerar: document.getElementById(`${p}-limpar-planos-ao-gerar`),
    limparPlanosAposConclusao: document.getElementById(`${p}-limpar-planos-apos-conclusao`),
    manterPlanosN: document.getElementById(`${p}-manter-planos-n`),
    diasRetencaoPlanos: document.getElementById(`${p}-dias-retencao-planos`),
    limparPlanosBtn: document.getElementById(`${p}-limpar-planos-btn`),
    runsSelectAll: document.getElementById(`${p}-runs-select-all`),
    apagarSelecionadosBtn: document.getElementById(`${p}-apagar-selecionados-btn`),
    limparForcar: document.getElementById(`${p}-limpar-forcar`),
    removerLedger: document.getElementById(`${p}-remover-ledger`),
    metaAnoMes: document.getElementById(`${p}-meta-ano-mes`),
    metaCarregarBtn: document.getElementById(`${p}-meta-carregar-btn`),
    metaRecalcularBtn: document.getElementById(`${p}-meta-recalcular-btn`),
    metaTabela: document.getElementById(`${p}-meta-tabela`),
    metaAjusteWorkflow: document.getElementById(`${p}-meta-ajuste-workflow`),
    metaAjusteConsumo: document.getElementById(`${p}-meta-ajuste-consumo`),
    metaAjusteBtn: document.getElementById(`${p}-meta-ajuste-btn`),
  };
}

const replicacaoWorkflowsCache = {
  replicacao_auditoria: [],
  replicacao_auditoria_d1: [],
};

function parseReplicacaoWorkflows100(raw) {
  if (Array.isArray(raw)) {
    return [...new Set(raw.map((item) => String(item).trim()).filter(Boolean))];
  }
  const text = String(raw || "").trim();
  if (!text) return [];
  return [...new Set(text.split(/[,;\n\r]+/).map((item) => item.trim()).filter(Boolean))];
}

function readWorkflowsAmostra100FromSelect(selectEl) {
  if (!selectEl) return [];
  return Array.from(selectEl.selectedOptions)
    .map((option) => String(option.value || "").trim())
    .filter(Boolean);
}

function fillWorkflowsAmostra100Select(selectEl, cfg) {
  if (!selectEl) return;
  const selected = new Set(parseReplicacaoWorkflows100(cfg.replicacao_workflows_amostra_100));
  Array.from(selectEl.options).forEach((option) => {
    option.selected = selected.has(option.value);
  });
}

function renderWorkflowsAmostra100Select(mode = activeConfigMode || replicacaoAudMode) {
  const nodes = getReplicacaoAudNodes(mode);
  const selectEl = nodes.workflowsAmostra100;
  if (!selectEl) return;

  const workflows = replicacaoWorkflowsCache[mode] || [];
  const query = String(nodes.workflowsAmostra100Search?.value || "").trim().toLowerCase();
  const cfg = getRobotConfig(mode);
  const selected = new Set([
    ...parseReplicacaoWorkflows100(cfg.replicacao_workflows_amostra_100),
    ...readWorkflowsAmostra100FromSelect(selectEl),
  ]);

  const filtered = !query
    ? workflows
    : workflows.filter((item) => {
        const workflow = String(item.workflow || "").trim();
        const cliente = String(item.cliente || "").trim();
        if (selected.has(workflow)) return true;
        const label = cliente ? `${workflow} (${cliente})` : workflow;
        return label.toLowerCase().includes(query);
      });

  selectEl.innerHTML = "";
  filtered.forEach((item) => {
    const workflow = String(item.workflow || "").trim();
    if (!workflow) return;
    const option = document.createElement("option");
    option.value = workflow;
    const cliente = String(item.cliente || "").trim();
    option.textContent = cliente ? `${workflow} (${cliente})` : workflow;
    option.selected = selected.has(workflow);
    selectEl.appendChild(option);
  });
}

function wireWorkflowsAmostra100Search(mode = activeConfigMode || replicacaoAudMode) {
  const nodes = getReplicacaoAudNodes(mode);
  const searchEl = nodes.workflowsAmostra100Search;
  if (!searchEl || searchEl.dataset.wired === "1") return;
  searchEl.dataset.wired = "1";
  searchEl.addEventListener("input", () => {
    renderWorkflowsAmostra100Select(mode);
  });
}

async function loadReplicacaoWorkflowsList(mode = activeConfigMode || replicacaoAudMode) {
  const nodes = getReplicacaoAudNodes(mode);
  const selectEl = nodes.workflowsAmostra100;
  if (!selectEl) return;
  const d1 = isReplicacaoAudD1Mode(mode);
  const path = d1 ? "/api/robots/replicacao-d1/workflows" : "/api/robots/replicacao/workflows";
  selectEl.disabled = true;
  if (nodes.workflowsAmostra100Search) nodes.workflowsAmostra100Search.disabled = true;
  try {
    const resp = await fetch(path);
    const data = await resp.json();
    replicacaoWorkflowsCache[mode] = Array.isArray(data.workflows) ? data.workflows : [];
    if (nodes.workflowsAmostra100Search) nodes.workflowsAmostra100Search.value = "";
    renderWorkflowsAmostra100Select(mode);
    wireWorkflowsAmostra100Search(mode);
  } catch (err) {
    console.warn("Falha ao carregar workflows de replicação:", err);
  } finally {
    selectEl.disabled = false;
    if (nodes.workflowsAmostra100Search) nodes.workflowsAmostra100Search.disabled = false;
  }
}

function getReplicacaoEscalaDataEsperada(cfg) {
  const dataRef = String(cfg?.replicacao_aud_data_ref || "").trim();
  const base = dataRef && /^\d{8}$/.test(dataRef)
    ? new Date(
        Number.parseInt(dataRef.slice(0, 4), 10),
        Number.parseInt(dataRef.slice(4, 6), 10) - 1,
        Number.parseInt(dataRef.slice(6, 8), 10),
      )
    : new Date();
  if (dataRef && /^\d{8}$/.test(dataRef)) {
    base.setDate(base.getDate() + 1);
  } else {
    const hoje = new Date();
    hoje.setHours(0, 0, 0, 0);
    base.setTime(hoje.getTime());
    base.setDate(base.getDate() + 1);
  }
  const y = base.getFullYear();
  const m = String(base.getMonth() + 1).padStart(2, "0");
  const d = String(base.getDate()).padStart(2, "0");
  return `${y}${m}${d}`;
}

function getReplicacaoMetaProduEfetiva(cfg) {
  const raw = String(cfg?.meta_produ ?? "").trim();
  if (raw) return raw;
  return String(defaultReplicacaoAudConfig.meta_produ || "300");
}

function readReplicacaoAudConfigFromNodes(mode = activeConfigMode || replicacaoAudMode) {
  const nodes = getReplicacaoAudNodes(mode);
  let diasHistorico = Number.parseInt(nodes.diasHistorico?.value, 10);
  if (!Number.isFinite(diasHistorico)) diasHistorico = defaultReplicacaoAudConfig.dias_historico;
  diasHistorico = Math.min(365, Math.max(1, diasHistorico));

  let seed = Number.parseInt(nodes.seed?.value, 10);
  if (!Number.isFinite(seed)) seed = defaultReplicacaoAudConfig.replicacao_aud_seed;

  const base = {
    apenas_planejamento: Boolean(nodes.apenasPlanejamento?.checked),
    run_id: String(nodes.runId?.value || "").trim(),
    gerar_novo_plano: Boolean(nodes.gerarNovoPlano?.checked),
    forcar_reexecucao: Boolean(nodes.forcarReexecucao?.checked),
    apenas_pendentes: Boolean(nodes.apenasPendentes?.checked),
    excluir_historico: Boolean(nodes.excluirHistorico?.checked),
    dias_historico: diasHistorico,
    sobrescrever: Boolean(nodes.sobrescrever?.checked),
    fallback_ultimo_parquet: Boolean(nodes.fallbackParquet?.checked),
    replicacao_aud_seed: seed,
    replicacao_aud_data_ref: String(nodes.dataRef?.value || "").trim(),
    usar_escala_auditores: Boolean(nodes.usarEscala?.checked),
    auditores_ativos: String(nodes.auditoresAtivos?.value || "").trim(),
    meta_produ: String(nodes.metaProdu?.value || "").trim() || defaultReplicacaoAudConfig.meta_produ,
    replicacao_cliente_cod: String(nodes.clienteCod?.value || "").trim() || defaultReplicacaoAudConfig.replicacao_cliente_cod,
    replicacao_workflow_cod: String(nodes.workflowCod?.value || "").trim() || defaultReplicacaoAudConfig.replicacao_workflow_cod,
    replicacao_cliente_destino: defaultReplicacaoAudConfig.replicacao_cliente_destino,
    replicacao_workflow_destino: defaultReplicacaoAudConfig.replicacao_workflow_destino,
    replicacao_workflows_amostra_100: readWorkflowsAmostra100FromSelect(nodes.workflowsAmostra100),
  };

  if (!isReplicacaoAudD1Mode(mode)) {
    return base;
  }

  let manterPlanosN = Number.parseInt(nodes.manterPlanosN?.value, 10);
  if (!Number.isFinite(manterPlanosN)) manterPlanosN = defaultReplicacaoAudD1Config.manter_planos_ultimos_n;
  manterPlanosN = Math.min(50, Math.max(1, manterPlanosN));

  let diasRetencaoPlanos = Number.parseInt(nodes.diasRetencaoPlanos?.value, 10);
  if (!Number.isFinite(diasRetencaoPlanos)) diasRetencaoPlanos = defaultReplicacaoAudD1Config.dias_retencao_planos;
  diasRetencaoPlanos = Math.min(365, Math.max(0, diasRetencaoPlanos));

  return {
    ...base,
    limpar_planos_automatico: Boolean(nodes.limparPlanosAutomatico?.checked),
    limpar_planos_ao_gerar: Boolean(nodes.limparPlanosAoGerar?.checked),
    limpar_planos_apos_conclusao: Boolean(nodes.limparPlanosAposConclusao?.checked),
    manter_planos_ultimos_n: manterPlanosN,
    dias_retencao_planos: diasRetencaoPlanos,
  };
}

function fillReplicacaoAudConfigNodes(cfg, mode = activeConfigMode || replicacaoAudMode) {
  const nodes = getReplicacaoAudNodes(mode);
  if (nodes.apenasPlanejamento) nodes.apenasPlanejamento.checked = Boolean(cfg.apenas_planejamento);
  if (nodes.runId) nodes.runId.value = cfg.run_id || "";
  if (nodes.gerarNovoPlano) nodes.gerarNovoPlano.checked = Boolean(cfg.gerar_novo_plano);
  if (nodes.forcarReexecucao) nodes.forcarReexecucao.checked = Boolean(cfg.forcar_reexecucao);
  if (nodes.apenasPendentes) nodes.apenasPendentes.checked = cfg.apenas_pendentes !== false;
  if (nodes.excluirHistorico) nodes.excluirHistorico.checked = cfg.excluir_historico !== false;
  if (nodes.diasHistorico) nodes.diasHistorico.value = String(cfg.dias_historico ?? defaultReplicacaoAudConfig.dias_historico);
  if (nodes.sobrescrever) nodes.sobrescrever.checked = Boolean(cfg.sobrescrever);
  if (nodes.fallbackParquet) nodes.fallbackParquet.checked = Boolean(cfg.fallback_ultimo_parquet);
  if (nodes.seed) nodes.seed.value = String(cfg.replicacao_aud_seed ?? defaultReplicacaoAudConfig.replicacao_aud_seed);
  if (nodes.dataRef) nodes.dataRef.value = cfg.replicacao_aud_data_ref || "";
  if (nodes.usarEscala) nodes.usarEscala.checked = cfg.usar_escala_auditores !== false;
  if (nodes.auditoresAtivos) nodes.auditoresAtivos.value = cfg.auditores_ativos ? String(cfg.auditores_ativos) : "";
  if (nodes.metaProdu) {
    nodes.metaProdu.value = getReplicacaoMetaProduEfetiva(cfg);
  }
  if (nodes.clienteCod) {
    nodes.clienteCod.value = cfg.replicacao_cliente_cod || defaultReplicacaoAudConfig.replicacao_cliente_cod;
  }
  if (nodes.workflowCod) {
    nodes.workflowCod.value = cfg.replicacao_workflow_cod || defaultReplicacaoAudConfig.replicacao_workflow_cod;
  }
  if (nodes.workflowsAmostra100Search) {
    nodes.workflowsAmostra100Search.value = "";
  }
  if (nodes.workflowsAmostra100) {
    renderWorkflowsAmostra100Select(mode);
  }
  if (isReplicacaoAudD1Mode(mode)) {
    if (nodes.limparPlanosAutomatico) {
      nodes.limparPlanosAutomatico.checked = Boolean(cfg.limpar_planos_automatico);
    }
    if (nodes.limparPlanosAoGerar) {
      nodes.limparPlanosAoGerar.checked = Boolean(cfg.limpar_planos_ao_gerar);
    }
    if (nodes.limparPlanosAposConclusao) {
      nodes.limparPlanosAposConclusao.checked = Boolean(cfg.limpar_planos_apos_conclusao);
    }
    if (nodes.manterPlanosN) {
      nodes.manterPlanosN.value = String(
        cfg.manter_planos_ultimos_n ?? defaultReplicacaoAudD1Config.manter_planos_ultimos_n,
      );
    }
    if (nodes.diasRetencaoPlanos) {
      nodes.diasRetencaoPlanos.value = String(
        cfg.dias_retencao_planos ?? defaultReplicacaoAudD1Config.dias_retencao_planos,
      );
    }
  }
}

async function loadReplicacaoRunsList(mode = activeConfigMode || replicacaoAudMode) {
  const nodes = getReplicacaoAudNodes(mode);
  const { runsList } = nodes;
  if (!runsList) return;
  const apiPath = isReplicacaoAudD1Mode(mode) ? "/api/robots/replicacao-d1/runs" : "/api/robots/replicacao/runs";
  const limite = isReplicacaoAudD1Mode(mode) ? 30 : 12;
  try {
    const response = await fetch(`${apiPath}?limite=${limite}`);
    const data = await response.json();
    if (!data.ok || !Array.isArray(data.runs)) {
      runsList.innerHTML = "";
      return;
    }
    if (!data.runs.length) {
      runsList.innerHTML = "<li>Nenhuma execução recente encontrada.</li>";
      if (nodes.runsSelectAll) nodes.runsSelectAll.checked = false;
      return;
    }

    if (isReplicacaoAudD1Mode(mode)) {
      const visibleRuns = data.runs.filter((run) => Number(run.workflows_total || 0) > 0);
      if (!visibleRuns.length) {
        runsList.innerHTML = '<li class="config-runs-empty">Nenhum plano com protocolos para replicar.</li>';
        if (nodes.runsSelectAll) nodes.runsSelectAll.checked = false;
        return;
      }
      runsList.innerHTML =
        `<li class="config-runs-item config-runs-item--header" aria-hidden="true">` +
        `<span></span><span>Run ID</span><span>Workflows</span><span>OK</span><span>Pend.</span><span>Erro</span>` +
        `</li>` +
        visibleRuns
        .map((run) => {
          const rid = String(run.run_id || "");
          const salvo = run.salvo_ok ?? 0;
          const pend = run.pendente ?? 0;
          const erro = run.erro ?? 0;
          const total = run.workflows_total ?? salvo + pend + erro;
          const safeRid = escapeHtml(rid);
          return (
            `<li class="config-runs-item">` +
            `<input type="checkbox" class="config-run-checkbox" value="${safeRid}" ` +
            `aria-label="Selecionar plano ${safeRid}" />` +
            `<button type="button" class="link-btn config-runs-runid">${safeRid}</button>` +
            `<strong class="config-runs-count config-runs-count--total">${total}</strong>` +
            `<strong class="config-runs-count config-runs-count--ok">${salvo}</strong>` +
            `<strong class="config-runs-count config-runs-count--pending">${pend}</strong>` +
            `<strong class="config-runs-count config-runs-count--error">${erro}</strong>` +
            `</li>`
          );
        })
        .join("");
      runsList.querySelectorAll(".config-run-checkbox").forEach((cb) => {
        cb.addEventListener("change", () => syncRunsSelectAllCheckbox(mode));
      });
      runsList.querySelectorAll(".config-runs-runid").forEach((btn) => {
        btn.addEventListener("click", (event) => {
          event.preventDefault();
          const { runId } = getReplicacaoAudNodes(mode);
          if (runId) runId.value = btn.textContent?.trim() || "";
        });
        btn.title = "Usar este Run ID";
      });
      syncRunsSelectAllCheckbox(mode);
      return;
    }

    runsList.innerHTML = data.runs
      .map(
        (run) =>
          `<li><button type="button" class="link-btn" data-run-id="${run.run_id}">${run.run_id}</button> ` +
          `— OK: ${run.upload_ok} | pend: ${run.pendente} | erro: ${run.erro}</li>`,
      )
      .join("");
    runsList.querySelectorAll("[data-run-id]").forEach((btn) => {
      btn.addEventListener("click", () => {
        const { runId } = getReplicacaoAudNodes(mode);
        if (runId) runId.value = btn.getAttribute("data-run-id") || "";
      });
    });
  } catch {
    runsList.innerHTML = "<li>Falha ao carregar execuções.</li>";
  }
}

function getSelectedD1RunIds() {
  const { runsList } = getReplicacaoAudNodes(replicacaoAudD1Mode);
  if (!runsList) return [];
  return Array.from(runsList.querySelectorAll(".config-run-checkbox:checked"))
    .map((cb) => String(cb.value || "").trim())
    .filter(Boolean);
}

function syncRunsSelectAllCheckbox(mode = replicacaoAudD1Mode) {
  const { runsList, runsSelectAll } = getReplicacaoAudNodes(mode);
  if (!runsSelectAll || !runsList) return;
  const boxes = runsList.querySelectorAll(".config-run-checkbox");
  if (!boxes.length) {
    runsSelectAll.checked = false;
    runsSelectAll.indeterminate = false;
    return;
  }
  const checked = runsList.querySelectorAll(".config-run-checkbox:checked").length;
  runsSelectAll.checked = checked === boxes.length;
  runsSelectAll.indeterminate = checked > 0 && checked < boxes.length;
}

async function fetchRunsLedgerStatus(runIds) {
  const status = {};
  await Promise.all(
    runIds.map(async (rid) => {
      try {
        const res = await fetch(`/api/robots/replicacao-d1/runs/${encodeURIComponent(rid)}/ledger`);
        const data = await res.json();
        status[rid] = Boolean(data.no_ledger);
      } catch {
        status[rid] = false;
      }
    }),
  );
  return status;
}

function renderMetaMensalTabela(clientes) {
  const { metaTabela } = getReplicacaoAudNodes(replicacaoAudD1Mode);
  if (!metaTabela) return;
  if (!Array.isArray(clientes) || !clientes.length) {
    metaTabela.innerHTML = "<p class=\"config-meta-empty\">Nenhum dado para o mês selecionado.</p>";
    return;
  }
  const rows = clientes
    .map((row) => {
      const wf = row.workflow || row.cliente || "";
      const cli = row.cliente || "";
      const meta = row.meta_mensal ?? "";
      const consumo = row.consumo_acumulado ?? "0";
      const headroom = row.headroom ?? "";
      return `<tr><td>${wf}</td><td>${cli}</td><td>${meta}</td><td>${consumo}</td><td>${headroom}</td></tr>`;
    })
    .join("");
  metaTabela.innerHTML =
    `<table class="config-meta-table"><thead><tr>` +
    `<th>Workflow</th><th>Cliente</th><th>Limite de balanceamento</th><th>Consumo operacional</th><th>Saldo para redistribuição</th>` +
    `</tr></thead><tbody>${rows}</tbody></table>`;
}

async function carregarMetaMensalD1Ui() {
  const nodes = getReplicacaoAudNodes(replicacaoAudD1Mode);
  const anoMes = String(nodes.metaAnoMes?.value || "").trim();
  if (nodes.metaCarregarBtn) nodes.metaCarregarBtn.disabled = true;
  try {
    const qs = anoMes ? `?ano_mes=${encodeURIComponent(anoMes)}` : "";
    const response = await fetch(`/api/robots/replicacao-d1/meta-mensal${qs}`);
    const data = await response.json();
    if (!data.ok) {
      setFeedback(data.message || "Falha ao carregar o balanceamento mensal.", true);
      return;
    }
    if (nodes.metaAnoMes && data.ano_mes) nodes.metaAnoMes.value = data.ano_mes;
    renderMetaMensalTabela(data.clientes || []);
  } catch (exc) {
    setFeedback(`Erro ao carregar o balanceamento mensal: ${exc?.message || exc}`, true);
  } finally {
    if (nodes.metaCarregarBtn) nodes.metaCarregarBtn.disabled = false;
  }
}

async function recalcularMetaMensalD1Ui() {
  const nodes = getReplicacaoAudNodes(replicacaoAudD1Mode);
  const cfg = readReplicacaoAudConfigFromNodes(replicacaoAudD1Mode);
  const anoMes = String(nodes.metaAnoMes?.value || "").trim();
  if (nodes.metaRecalcularBtn) nodes.metaRecalcularBtn.disabled = true;
  try {
    const response = await fetch("/api/robots/replicacao-d1/meta-mensal/recalcular", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ robot_config: cfg, ano_mes: anoMes || undefined }),
    });
    const data = await response.json();
    setFeedback(data.message || (data.ok ? "Snapshot recalculado." : "Falha ao recalcular."), !data.ok);
    if (data.ok) await carregarMetaMensalD1Ui();
  } catch (exc) {
    setFeedback(`Erro ao recalcular snapshot: ${exc?.message || exc}`, true);
  } finally {
    if (nodes.metaRecalcularBtn) nodes.metaRecalcularBtn.disabled = false;
  }
}

async function aplicarAjusteMetaMensalD1Ui() {
  const nodes = getReplicacaoAudNodes(replicacaoAudD1Mode);
  const cfg = readReplicacaoAudConfigFromNodes(replicacaoAudD1Mode);
  const anoMes = String(nodes.metaAnoMes?.value || "").trim();
  const workflow = String(nodes.metaAjusteWorkflow?.value || "").trim();
  const consumo = Number.parseInt(nodes.metaAjusteConsumo?.value, 10);
  if (!anoMes || !workflow || !Number.isFinite(consumo) || consumo < 0) {
    setFeedback("Informe mês, workflow e consumo válido para o ajuste.", true);
    return;
  }
  if (!window.confirm(`Definir consumo de "${workflow}" em ${anoMes} para ${consumo}?`)) return;
  if (nodes.metaAjusteBtn) nodes.metaAjusteBtn.disabled = true;
  try {
    const response = await fetch("/api/robots/replicacao-d1/meta-mensal/ajuste", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        robot_config: cfg,
        ano_mes: anoMes,
        workflow,
        consumo_acumulado: consumo,
      }),
    });
    const data = await response.json();
    setFeedback(data.message || (data.ok ? "Ajuste aplicado." : "Falha no ajuste."), !data.ok);
    if (data.ok) await carregarMetaMensalD1Ui();
  } catch (exc) {
    setFeedback(`Erro no ajuste manual: ${exc?.message || exc}`, true);
  } finally {
    if (nodes.metaAjusteBtn) nodes.metaAjusteBtn.disabled = false;
  }
}

async function limparPlanosReplicacaoD1Ui(options = {}) {
  const mode = replicacaoAudD1Mode;
  const nodes = getReplicacaoAudNodes(mode);
  const runIds = Array.isArray(options.run_ids) ? options.run_ids : null;
  const forcar = Boolean(options.forcar ?? nodes.limparForcar?.checked);
  const removerLedger = Boolean(options.remover_ledger ?? nodes.removerLedger?.checked);
  const triggerBtn = options.triggerBtn || nodes.limparPlanosBtn;

  if (runIds && !runIds.length) {
    setFeedback("Selecione pelo menos um plano para apagar.", true);
    return;
  }

  if (runIds && runIds.length > 0) {
    const ledgerStatus = await fetchRunsLedgerStatus(runIds);
    const comLedger = runIds.filter((rid) => ledgerStatus[rid]);
    const lista = runIds.slice(0, 5).join(", ");
    const sufixo = runIds.length > 5 ? ` e mais ${runIds.length - 5}` : "";
    let msg = `Apagar ${runIds.length} plano(s)?\n${lista}${sufixo}`;
    if (comLedger.length) {
      msg += `\n\n${comLedger.length} run(s) constam no ledger de balanceamento.`;
      if (!removerLedger) {
        msg += "\nMarque \"Remover consumo do ledger\" para reverter o consumo.";
      }
    }
    if (!window.confirm(msg)) return;
  }

  const cfg = readReplicacaoAudConfigFromNodes(mode);
  const runIdInput = nodes.runId;
  const runIdAtual = String(runIdInput?.value || "").trim();
  if (triggerBtn) triggerBtn.disabled = true;
  setFeedback(
    runIds ? `Apagando ${runIds.length} plano(s) selecionado(s)...` : "Limpando planos antigos D-1...",
    false,
  );
  try {
    const body = { robot_config: cfg, forcar, remover_ledger: removerLedger };
    if (runIds) body.run_ids = runIds;

    const response = await fetch("/api/robots/replicacao-d1/limpar-planos", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const data = await response.json();
    const removidos = Array.isArray(data.removidos) ? data.removidos : [];
    const ignorados = Array.isArray(data.ignorados) ? data.ignorados.length : 0;
    let feedback = data.message || (data.ok ? `${removidos.length} plano(s) removido(s)` : "Falha ao limpar planos.");
    if (ignorados > 0) {
      feedback += ` (${ignorados} ignorado(s) com pendências — marque Forçar para apagar)`;
    }
    setFeedback(feedback, !data.ok);
    if (runIds && runIdAtual && removidos.includes(runIdAtual) && runIdInput) {
      runIdInput.value = "";
    }
    loadReplicacaoRunsList(mode);
  } catch (exc) {
    setFeedback(`Erro ao limpar planos: ${exc?.message || exc}`, true);
  } finally {
    if (triggerBtn) triggerBtn.disabled = false;
  }
}

function apagarPlanosSelecionadosD1Ui() {
  const runIds = getSelectedD1RunIds();
  const { apagarSelecionadosBtn } = getReplicacaoAudNodes(replicacaoAudD1Mode);
  limparPlanosReplicacaoD1Ui({
    run_ids: runIds,
    triggerBtn: apagarSelecionadosBtn,
  });
}

function renderPlanoValidadoD1(node, details) {
  const d = details || {};
  const pendingCount = Number(d.workflows_pendentes_count || 0);
  const workflowCount = Number(d.workflows || 0);
  const protocolCount = Number(d.protocolos || 0);
  const partialCount = Number(d.parciais || 0);
  const warningGroups = Array.isArray(d.warning_groups) ? d.warning_groups : [];
  const pendingNames = Array.from(
    new Set([
      ...(d.workflows_pendentes_novos || []),
      ...(d.workflows_pendentes_existentes || []),
      ...(d.workflows_pendentes_falha_sync || []),
    ].map((item) => String(item || "").trim()).filter(Boolean)),
  );

  const pendingDetails = pendingCount
    ? `<details class="config-validation-group config-validation-group--warning">` +
      `<summary><span>Cadastro pendente</span><strong>${pendingCount}</strong></summary>` +
      `<p>Esses workflows possuem registros D-1, mas ficaram fora da replicação até a configuração ser concluída.</p>` +
      (pendingNames.length
        ? `<ul>${pendingNames.slice(0, 20).map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul>` +
          (pendingNames.length > 20 ? `<small>Mais ${pendingNames.length - 20} workflow(s).</small>` : "")
        : "") +
      `</details>`
    : "";

  const warningDetails = warningGroups
    .filter((group) => group?.key !== "cadastro")
    .map((group) => {
      const items = Array.isArray(group.items) ? group.items : [];
      return (
        `<details class="config-validation-group">` +
        `<summary><span>${escapeHtml(group.label || "Ponto de atenção")}</span><strong>${Number(group.count || items.length)}</strong></summary>` +
        `<ul>${items.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul>` +
        (Number(group.remaining || 0) > 0 ? `<small>Mais ${Number(group.remaining)} aviso(s).</small>` : "") +
        `</details>`
      );
    })
    .join("");

  node.classList.toggle("config-validar-resultado--pendentes", pendingCount > 0);
  node.innerHTML =
    `<div class="config-validation-head">` +
    `<div><span>Plano pronto para revisão</span><strong>${escapeHtml(d.run_id || "-")}</strong></div>` +
    `<span class="config-validation-status">Banco de dados</span>` +
    `</div>` +
    `<div class="config-validation-kpis">` +
    `<article><span>Para replicar</span><strong>${workflowCount}</strong><small>workflows com protocolos</small></article>` +
    `<article><span>Protocolos</span><strong>${protocolCount.toLocaleString("pt-BR")}</strong><small>selecionados no plano</small></article>` +
    `<article><span>Parciais</span><strong>${partialCount}</strong><small>abaixo da amostra</small></article>` +
    `<article><span>Cadastro pendente</span><strong>${pendingCount}</strong><small>fora deste plano</small></article>` +
    `</div>` +
    `<p class="config-validation-note">Somente workflows com protocolos efetivamente selecionados entram na execução.</p>` +
    pendingDetails +
    warningDetails;
}

async function validarPlanoReplicacaoUi() {
  const mode =
    activeConfigMode && isReplicacaoAudMode(activeConfigMode) ? activeConfigMode : replicacaoAudMode;
  const { validarPlanoBtn, validarResultado } = getReplicacaoAudNodes(mode);
  if (!validarPlanoBtn) {
    setFeedback("Botão Validar plano não encontrado na página.", true);
    return;
  }

  const cfg = readReplicacaoAudConfigFromNodes(mode);
  const err = validateReplicacaoAudConfig(cfg);
  if (err) {
    setFeedback(err, true);
    return;
  }

  validarPlanoBtn.disabled = true;
  setFeedback("Validando plano (gerando CSVs de teste)...", false);
  if (validarResultado) {
    validarResultado.hidden = true;
    validarResultado.textContent = "";
  }

  try {
    const apiPath = isReplicacaoAudD1Mode(mode)
      ? "/api/robots/replicacao-d1/validar-plano"
      : "/api/robots/replicacao/validar-plano";
    const response = await fetch(apiPath, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        robot_config: {
          ...getRobotConfig(mode),
          ...cfg,
        },
      }),
    });

    let data;
    const rawText = await response.text();
    try {
      data = rawText ? JSON.parse(rawText) : {};
    } catch {
      setFeedback(`Resposta inválida do servidor (${response.status}).`, true);
      if (validarResultado) {
        validarResultado.hidden = false;
        validarResultado.textContent = rawText.slice(0, 500) || "(vazio)";
      }
      return;
    }

    if (validarResultado) {
      validarResultado.hidden = false;
      if (data.ok && data.detalhes) {
        const d = data.detalhes;
        if (isReplicacaoAudD1Mode(mode)) {
          renderPlanoValidadoD1(validarResultado, d);
        } else {
          const warns = (d.warnings || []).length
            ? `\nAvisos:\n- ${(d.warnings || []).join("\n- ")}`
            : "\nSem avisos.";
          validarResultado.textContent =
            `run_id: ${d.run_id}\n` +
            `workflows: ${d.workflows} | sem D-1: ${d.workflows_sem_d1} | OK: ${d.workflows_ok ?? 0} | parciais: ${d.parciais ?? 0}` +
            warns;
        }
      } else {
        validarResultado.classList.remove("config-validar-resultado--pendentes");
        let texto = data.message || "Falha na validação.";
        if (data.detalhes?.tipo === "escala_ausente" && data.detalhes.data_escala) {
          texto +=
            ` Inclua no CSV de escala a data ${data.detalhes.data_escala}` +
            (data.detalhes.data_escala_fmt ? ` (${data.detalhes.data_escala_fmt})` : "") +
            " ou informe auditores ativos no campo acima.";
        }
        validarResultado.textContent = texto;
      }
    }

    const msgFeedback = data.message || (data.ok ? "Plano validado." : "Falha na validação.");
    setFeedback(msgFeedback, !data.ok);

    if (data.ok && data.detalhes?.run_id) {
      const { runId } = getReplicacaoAudNodes(mode);
      if (runId && !runId.value.trim()) runId.value = data.detalhes.run_id;
      loadReplicacaoRunsList(mode);
    }
  } catch (exc) {
    setFeedback(`Erro ao validar plano: ${exc?.message || exc}`, true);
  } finally {
    validarPlanoBtn.disabled = false;
  }
}

function wireReplicacaoValidarPlano() {
  [replicacaoAudMode, replicacaoAudD1Mode].forEach((mode) => {
    const { validarPlanoBtn } = getReplicacaoAudNodes(mode);
    if (!validarPlanoBtn) return;
    if (validarPlanoBtn.dataset.wired === "1") return;
    validarPlanoBtn.dataset.wired = "1";
    validarPlanoBtn.addEventListener("click", (event) => {
      event.preventDefault();
      event.stopPropagation();
      validarPlanoReplicacaoUi();
    });
  });
}

function wireReplicacaoLimparPlanosD1() {
  const nodes = getReplicacaoAudNodes(replicacaoAudD1Mode);
  if (nodes.limparPlanosBtn && nodes.limparPlanosBtn.dataset.wired !== "1") {
    nodes.limparPlanosBtn.dataset.wired = "1";
    nodes.limparPlanosBtn.addEventListener("click", (event) => {
      event.preventDefault();
      event.stopPropagation();
      limparPlanosReplicacaoD1Ui({ triggerBtn: nodes.limparPlanosBtn });
    });
  }
  if (nodes.apagarSelecionadosBtn && nodes.apagarSelecionadosBtn.dataset.wired !== "1") {
    nodes.apagarSelecionadosBtn.dataset.wired = "1";
    nodes.apagarSelecionadosBtn.addEventListener("click", (event) => {
      event.preventDefault();
      event.stopPropagation();
      apagarPlanosSelecionadosD1Ui();
    });
  }
  if (nodes.runsSelectAll && nodes.runsSelectAll.dataset.wired !== "1") {
    nodes.runsSelectAll.dataset.wired = "1";
    nodes.runsSelectAll.addEventListener("change", () => {
      const { runsList } = getReplicacaoAudNodes(replicacaoAudD1Mode);
      if (!runsList) return;
      const checked = Boolean(nodes.runsSelectAll.checked);
      runsList.querySelectorAll(".config-run-checkbox").forEach((cb) => {
        cb.checked = checked;
      });
      nodes.runsSelectAll.indeterminate = false;
    });
  }
}

function wireReplicacaoMetaMensalD1() {
  const nodes = getReplicacaoAudNodes(replicacaoAudD1Mode);
  if (nodes.metaCarregarBtn && nodes.metaCarregarBtn.dataset.wired !== "1") {
    nodes.metaCarregarBtn.dataset.wired = "1";
    nodes.metaCarregarBtn.addEventListener("click", (event) => {
      event.preventDefault();
      carregarMetaMensalD1Ui();
    });
  }
  if (nodes.metaRecalcularBtn && nodes.metaRecalcularBtn.dataset.wired !== "1") {
    nodes.metaRecalcularBtn.dataset.wired = "1";
    nodes.metaRecalcularBtn.addEventListener("click", (event) => {
      event.preventDefault();
      recalcularMetaMensalD1Ui();
    });
  }
  if (nodes.metaAjusteBtn && nodes.metaAjusteBtn.dataset.wired !== "1") {
    nodes.metaAjusteBtn.dataset.wired = "1";
    nodes.metaAjusteBtn.addEventListener("click", (event) => {
      event.preventDefault();
      aplicarAjusteMetaMensalD1Ui();
    });
  }
}

function validateReplicacaoAudConfig(cfg) {
  const dataRef = String(cfg.replicacao_aud_data_ref || "").trim();
  if (dataRef && !/^\d{8}$/.test(dataRef)) {
    return "Data referência do parquet deve estar no formato YYYYMMDD (8 dígitos).";
  }
  if (cfg.usar_escala_auditores !== false) {
    const metaRaw = String(cfg.meta_produ ?? "").trim();
    if (metaRaw) {
      const meta = Number.parseFloat(metaRaw);
      if (!Number.isFinite(meta) || meta <= 0) {
        return "Meta Produ (diária) deve ser um número maior que zero.";
      }
    }
  }
  return "";
}

function getGedImmediateNodes() {
  return {
    wrapper: document.getElementById("robot-config-ged-options"),
    autoRadio: document.getElementById("robot-config-ged-imediata-auto"),
    diurnoRadio: document.getElementById("robot-config-ged-imediata-diurno"),
    noturnoRadio: document.getElementById("robot-config-ged-imediata-noturno"),
  };
}

function getGedImmediateSelection() {
  const selected = document.querySelector('input[name="robot-config-ged-imediata"]:checked');
  const value = String(selected?.value || "").trim().toLowerCase();
  return value === "diurno" || value === "noturno" ? value : "";
}

function setGedImmediateSelection(value) {
  const { autoRadio, diurnoRadio, noturnoRadio } = getGedImmediateNodes();
  const normalized = String(value || "").trim().toLowerCase();
  if (normalized === "diurno") {
    if (diurnoRadio) diurnoRadio.checked = true;
    return;
  }
  if (normalized === "noturno") {
    if (noturnoRadio) noturnoRadio.checked = true;
    return;
  }
  if (autoRadio) autoRadio.checked = true;
}

function ensureRotinaPeriodDefaults() {
  const { startInput, endInput } = getRotinaPeriodNodes();
  if (!startInput || !endInput) return;

  const today = new Date();
  const todayIso = toLocalISODate(today);

  if (!startInput.value) startInput.value = todayIso;
  if (!endInput.value) endInput.value = todayIso;
}

function applyRotinaDateLimits() {
  const { startInput, endInput } = getRotinaPeriodNodes();
  if (!startInput || !endInput) return;

  const today = new Date();
  const minDate = new Date(2000, 0, 1); // Sem limite prático

  const minIso = toLocalISODate(minDate);
  const maxIso = toLocalISODate(today);

  startInput.min = minIso;
  startInput.max = maxIso;
  endInput.min = minIso;
  endInput.max = maxIso;
}

/**
 * Fonte de verdade: `enabled` (ou checkbox.checked).
 * Mantém checkbox.checked, input.disabled e defaults alinhados.
 */
function applyRotinaImmediateUi(enabled, { fillDefaults = false } = {}) {
  const { checkbox, startInput, endInput } = getRotinaPeriodNodes();
  const on = Boolean(enabled);

  if (checkbox) checkbox.checked = on;
  if (startInput) startInput.disabled = !on;
  if (endInput) endInput.disabled = !on;

  if (on && fillDefaults) {
    ensureRotinaPeriodDefaults();
  }
}

function syncRotinaDateAvailability() {
  const { checkbox } = getRotinaPeriodNodes();
  applyRotinaImmediateUi(Boolean(checkbox?.checked), { fillDefaults: false });
}

function bindRotinaOptions() {
  const { checkbox } = getRotinaPeriodNodes();
  if (!checkbox || checkbox.dataset.rotinaBound === "1") return;
  checkbox.dataset.rotinaBound = "1";

  const onToggle = () => {
    applyRotinaImmediateUi(Boolean(checkbox.checked), {
      fillDefaults: Boolean(checkbox.checked),
    });
  };

  // change cobre clique no input e no texto do <label> envolvente
  checkbox.addEventListener("change", onToggle);
  checkbox.addEventListener("input", onToggle);
}

function validateRotinaPeriodPayload(payload) {
  if (!payload?.executar_imediatamente) return "";

  const inicio = (payload.rotina_data_inicio || "").trim();
  const fim = (payload.rotina_data_fim || "").trim();

  if (!inicio || !fim) {
    return "Informe data inicial e final para executar a rotina por período.";
  }

  const dtInicio = new Date(`${inicio}T00:00:00`);
  const dtFim = new Date(`${fim}T00:00:00`);
  if (Number.isNaN(dtInicio.getTime()) || Number.isNaN(dtFim.getTime())) {
    return "Período inválido. Verifique as datas informadas.";
  }

  const hoje = new Date();
  hoje.setHours(0, 0, 0, 0);

  if (dtInicio > dtFim) {
    return "A data inicial não pode ser maior que a data final.";
  }
  if (dtFim > hoje) {
    return "A data final não pode ser maior que hoje.";
  }

  return "";
}

function getStartPayload(mode) {
  const payload = {
    mode,
    ...getCredentials(),
    require_okta_validation: isOktaValidatorVisible(),
  };
  const cfg = getRobotConfig(mode);
  payload.robot_config = { ...cfg };
  if (isTarefasMode(mode)) {
    const container = getTarefasContainer();
    if (container && container.querySelector(".tarefas-checkbox")) {
      payload.robot_config.tarefas = getTarefasSelecionadas();
    }
  }
  if (!isRotinaPeriodMode(mode)) return payload;

  payload.executar_imediatamente = Boolean(cfg.executar_imediatamente);
  payload.rotina_data_inicio = payload.executar_imediatamente ? (cfg.rotina_data_inicio || "") : "";
  payload.rotina_data_fim = payload.executar_imediatamente ? (cfg.rotina_data_fim || "") : "";
  return payload;
}

function setFeedback(message, isError = false) {
  const node = document.getElementById("feedback");
  node.textContent = message || "";
  node.classList.toggle("error", isError);
}

function normalizeRobotConfig(config) {
  const raw = config && typeof config === "object" ? config : {};
  const output_dir = String(raw.output_dir || "").trim();
  const headless = Boolean(raw.headless);
  let max_workers = null;
  const parsed = Number.parseInt(raw.max_workers, 10);
  if (Number.isFinite(parsed) && parsed > 0) {
    max_workers = Math.min(5, parsed);
  }

  const executar_imediatamente = Boolean(raw.executar_imediatamente);
  const rotina_data_inicio = String(raw.rotina_data_inicio || "").trim();
  const rotina_data_fim = String(raw.rotina_data_fim || "").trim();
  const gedExecucaoImediataRaw = String(raw.ged_execucao_imediata || "").trim().toLowerCase();
  const ged_execucao_imediata = (gedExecucaoImediataRaw === "diurno" || gedExecucaoImediataRaw === "noturno")
    ? gedExecucaoImediataRaw
    : "";
  const sharepoint_url = String(raw.sharepoint_url || "").trim();
  let sharepoint_folders = [];
  if (Array.isArray(raw.sharepoint_folders)) {
    sharepoint_folders = raw.sharepoint_folders
      .map((item) => {
        const entry = {
          id: String(item?.id || "").trim(),
          label: String(item?.label || "").trim(),
          url: String(item?.url || "").trim(),
        };
        if (Array.isArray(item?.subpastas)) {
          const subpastas = item.subpastas.map((s) => String(s || "").trim()).filter(Boolean);
          if (subpastas.length) entry.subpastas = subpastas;
        }
        return entry;
      })
      .filter((item) => item.url);
  }
  // tarefas (opcionais)
  let tarefas = [];
  try {
    if (Array.isArray(raw.tarefas)) {
      tarefas = raw.tarefas.map((t) => String(t || "").trim().toLowerCase()).filter(Boolean);
    }
  } catch {
    tarefas = [];
  }

  const apenas_planejamento = Boolean(raw.apenas_planejamento);
  const run_id = String(raw.run_id || "").trim();
  const gerar_novo_plano = Boolean(raw.gerar_novo_plano);
  const forcar_reexecucao = Boolean(raw.forcar_reexecucao);
  const apenas_pendentes = raw.apenas_pendentes !== false;
  const excluir_historico = raw.excluir_historico !== false;

  let dias_historico = Number.parseInt(raw.dias_historico, 10);
  if (!Number.isFinite(dias_historico)) dias_historico = defaultReplicacaoAudConfig.dias_historico;
  dias_historico = Math.min(365, Math.max(1, dias_historico));

  const sobrescrever = Boolean(raw.sobrescrever);
  const fallback_ultimo_parquet = Boolean(raw.fallback_ultimo_parquet);

  let replicacao_aud_seed = Number.parseInt(raw.replicacao_aud_seed, 10);
  if (!Number.isFinite(replicacao_aud_seed)) replicacao_aud_seed = defaultReplicacaoAudConfig.replicacao_aud_seed;

  const replicacao_aud_data_ref = String(raw.replicacao_aud_data_ref || "").trim();
  const usar_escala_auditores = raw.usar_escala_auditores !== false;
  const auditores_ativos = String(raw.auditores_ativos ?? "").trim();
  const escala_auditores_csv = String(raw.escala_auditores_csv || "").trim();
  const meta_produ_norm = String(raw.meta_produ ?? "").trim() || defaultReplicacaoAudConfig.meta_produ;
  const replicacao_cliente_cod = String(raw.replicacao_cliente_cod ?? "").trim() || defaultReplicacaoAudConfig.replicacao_cliente_cod;
  const replicacao_workflow_cod = String(raw.replicacao_workflow_cod ?? "").trim() || defaultReplicacaoAudConfig.replicacao_workflow_cod;

  let tempo_espera_minutos = Number.parseInt(raw.tempo_espera_minutos, 10);
  if (!Number.isFinite(tempo_espera_minutos)) tempo_espera_minutos = 60;
  tempo_espera_minutos = Math.min(180, Math.max(5, tempo_espera_minutos));

  let dias_download_brflow = Number.parseInt(raw.dias_download_brflow, 10);
  if (!Number.isFinite(dias_download_brflow)) dias_download_brflow = 3;
  dias_download_brflow = Math.min(31, Math.max(1, dias_download_brflow));

  return {
    output_dir,
    headless,
    max_workers,
    executar_imediatamente,
    rotina_data_inicio,
    rotina_data_fim,
    ged_execucao_imediata,
    sharepoint_url,
    sharepoint_folders,
    tarefas,
    apenas_planejamento,
    run_id,
    gerar_novo_plano,
    forcar_reexecucao,
    apenas_pendentes,
    excluir_historico,
    dias_historico,
    sobrescrever,
    fallback_ultimo_parquet,
    replicacao_aud_seed,
    replicacao_aud_data_ref,
    usar_escala_auditores,
    auditores_ativos,
    meta_produ: meta_produ_norm,
    escala_auditores_csv,
    replicacao_cliente_destino: String(raw.replicacao_cliente_destino ?? defaultReplicacaoAudConfig.replicacao_cliente_destino).trim(),
    replicacao_cliente_cod,
    replicacao_workflow_destino: String(raw.replicacao_workflow_destino ?? defaultReplicacaoAudConfig.replicacao_workflow_destino).trim(),
    replicacao_workflow_cod,
    replicacao_workflows_amostra_100: parseReplicacaoWorkflows100(
      raw.replicacao_workflows_amostra_100 ?? defaultReplicacaoAudConfig.replicacao_workflows_amostra_100,
    ),
    tempo_espera_minutos,
    dias_download_brflow,
    executar_confer: raw.executar_confer !== false,
    executar_brflow: raw.executar_brflow !== false,
    baixar_monitor_com_producao: raw.baixar_monitor_com_producao !== false,
    baixar_log_eventos_com_producao: raw.baixar_log_eventos_com_producao !== false,
    executar_ged_irregularidade: raw.executar_ged_irregularidade !== false,
    executar_produtividade_case: raw.executar_produtividade_case === true,
  };
}

function getRobotConfig(mode) {
  let base = defaultRobotConfig;
  if (isReplicacaoAudD1Mode(mode)) {
    base = { ...defaultRobotConfig, ...defaultReplicacaoAudD1Config };
  } else if (mode === replicacaoAudMode) {
    base = { ...defaultRobotConfig, ...defaultReplicacaoAudConfig };
  }
  const cfg = robotConfigState[mode] || base;
  return normalizeRobotConfig(cfg);
}

function renderRobotConfigSummary(mode) {
  const node = document.getElementById(`config-summary-${mode}`);
  if (!node) return;
  const cfg = getRobotConfig(mode);
  const parts = [];
  parts.push(cfg.headless ? "headless: ligado" : "headless: desligado");
  parts.push(cfg.output_dir ? `pasta: ${cfg.output_dir}` : "pasta: padrão");
  parts.push(cfg.max_workers ? `workers: ${cfg.max_workers}` : "workers: automático");
  if (isGedMode(mode) && cfg.ged_execucao_imediata) {
    parts.push(`imediata: ${cfg.ged_execucao_imediata}`);
  }
  if (isRotinaPeriodMode(mode) && cfg.executar_imediatamente) {
    parts.push(`imediata: ${cfg.rotina_data_inicio || "-"} a ${cfg.rotina_data_fim || "-"}`);
  }
  if (isReplicacaoAudMode(mode)) {
    if (cfg.usar_escala_auditores) {
      parts.push(cfg.auditores_ativos ? `auditores: ${cfg.auditores_ativos}` : "auditores: CSV");
      parts.push(`meta: ${getReplicacaoMetaProduEfetiva(cfg)}`);
      parts.push(`escala data: ${getReplicacaoEscalaDataEsperada(cfg)}`);
    } else {
      parts.push("escala: off");
    }
    if (cfg.apenas_planejamento) parts.push("só planejamento");
    if (cfg.run_id) parts.push(`run_id: ${cfg.run_id}`);
    parts.push(cfg.excluir_historico ? `histórico: ${cfg.dias_historico}d` : "histórico: off");
    if (cfg.fallback_ultimo_parquet) parts.push("fallback parquet");
    if (cfg.gerar_novo_plano) parts.push("novo plano");
    if (cfg.forcar_reexecucao) parts.push("forçar reexec");
    const wf100 = parseReplicacaoWorkflows100(cfg.replicacao_workflows_amostra_100);
    if (wf100.length) parts.push(`100%: ${wf100.length} wf`);
  }
  if (mode === productionMode) {
    parts.push(`BRFlow: ${cfg.dias_download_brflow} dia(s)`);
  }
  // mostrar tarefas configuradas para modo rotina
  if (isTarefasMode(mode) && Array.isArray(cfg.tarefas) && cfg.tarefas.length > 0) {
    const explicitSet = new Set(cfg.tarefas.map((t) => String(t || "").trim().toLowerCase()));
    // resolver dependências localmente para preview
    const implied = new Set();
    const pend = Array.from(explicitSet);
    while (pend.length) {
      const t = pend.shift();
      const info = TAREFAS_DISPONIVEIS[t];
      if (!info || !info.deps) continue;
      info.deps.forEach((d) => {
        if (!implied.has(d) && !explicitSet.has(d)) {
          implied.add(d);
          pend.push(d);
        }
      });
    }
    const all = Array.from(new Set([...explicitSet, ...implied]));
    const labels = all
      .sort((a, b) => Object.keys(TAREFAS_DISPONIVEIS).indexOf(a) - Object.keys(TAREFAS_DISPONIVEIS).indexOf(b))
      .map((id) => (TAREFAS_DISPONIVEIS[id] ? TAREFAS_DISPONIVEIS[id].label : id))
      .join(", ");
    parts.push(`tarefas: ${labels}`);
  }
  node.textContent = `Configuração: ${parts.join(" | ")}`;
}

function applyRobotConfigs(configs) {
  robotModes.forEach((mode) => {
    robotConfigState[mode] = normalizeRobotConfig(configs?.[mode]);
    renderRobotConfigSummary(mode);
  });
}

async function fetchRobotConfigs() {
  try {
    const response = await fetch("/api/robots/config");
    const data = await response.json();
    if (data.ok) {
      applyRobotConfigs(data.configs || {});
    }
  } catch {
    setFeedback("Falha ao carregar configurações dos robôs.", true);
  }
}

function renderSharepointFolders(cfg) {
  const container = document.getElementById("robot-config-sharepoint-folders");
  if (!container) return;

  const folders = Array.isArray(cfg?.sharepoint_folders) ? cfg.sharepoint_folders : [];
  container.replaceChildren();

  if (!folders.length) {
    const empty = document.createElement("p");
    empty.className = "config-empty-hint";
    empty.textContent = "Sem links configurados para este robô.";
    container.appendChild(empty);
    return;
  }

  folders.forEach((folder) => {
    const item = document.createElement("div");
    item.className = "sharepoint-folder-item";

    const label = document.createElement("label");
    label.textContent = folder.label || "Pasta";
    item.appendChild(label);

    const row = document.createElement("div");
    row.className = "path-picker-row";

    const input = document.createElement("input");
    input.type = "text";
    input.readOnly = true;
    input.value = folder.url || "";
    input.title = folder.url || "";
    row.appendChild(input);

    const openBtn = document.createElement("button");
    openBtn.className = "btn btn-ghost";
    openBtn.type = "button";
    openBtn.textContent = "Abrir";
    openBtn.addEventListener("click", () => openRobotSharepointLink(folder.url));
    row.appendChild(openBtn);

    const copyBtn = document.createElement("button");
    copyBtn.className = "btn btn-ghost";
    copyBtn.type = "button";
    copyBtn.textContent = "Copiar";
    copyBtn.addEventListener("click", () => copyRobotSharepointLink(folder.url));
    row.appendChild(copyBtn);

    item.appendChild(row);

    if (Array.isArray(folder.subpastas) && folder.subpastas.length) {
      const subList = document.createElement("ul");
      subList.className = "sharepoint-subpastas";
      folder.subpastas.forEach((nome) => {
        const li = document.createElement("li");
        li.textContent = nome;
        subList.appendChild(li);
      });
      item.appendChild(subList);
    }

    container.appendChild(item);
  });
}

function setConfigModalBodyLock(active) {
  document.documentElement.classList.toggle("config-modal-open", active);
  document.body.classList.toggle("config-modal-open", active);
}

function openRobotConfig(mode) {
  const modal = document.getElementById("robot-config-modal");
  if (!modal) return;
  activeConfigMode = mode;
  const cfg = getRobotConfig(mode);

  const modeNode = document.getElementById("robot-config-mode");
  const outputInput = document.getElementById("robot-config-output-dir");
  const headlessInput = document.getElementById("robot-config-headless");
  const workersInput = document.getElementById("robot-config-workers");
  const downloadLogBtn = document.getElementById("robot-config-download-log-btn");
  const clearLogBtn = document.getElementById("robot-config-clear-log-btn");
  const { startInput: rotinaStartInput, endInput: rotinaEndInput } = getRotinaPeriodNodes();

  if (modeNode) modeNode.textContent = getModeLabel(mode);
  if (outputInput) outputInput.value = cfg.output_dir || "";
  renderSharepointFolders(cfg);
  if (headlessInput) headlessInput.checked = Boolean(cfg.headless);
  if (workersInput) workersInput.value = cfg.max_workers ? String(cfg.max_workers) : "";
  if (downloadLogBtn) downloadLogBtn.disabled = false;
  if (clearLogBtn) clearLogBtn.disabled = false;

  syncConfigModalSections(mode, cfg);
  expandConfigSectionForMode(mode);
  if (isRotinaPeriodMode(mode)) {
    applyRotinaDateLimits();
    if (rotinaStartInput) rotinaStartInput.value = cfg.rotina_data_inicio || "";
    if (rotinaEndInput) rotinaEndInput.value = cfg.rotina_data_fim || "";
    // Sincroniza checked + disabled + defaults a partir da config salva
    applyRotinaImmediateUi(Boolean(cfg.executar_imediatamente), {
      fillDefaults: Boolean(cfg.executar_imediatamente),
    });
  }
  if (isGedMode(mode)) {
    setGedImmediateSelection(cfg.ged_execucao_imediata || "");
  }
  if (isReplicacaoAudMode(mode)) {
    fillReplicacaoAudConfigNodes(cfg, mode);
    loadReplicacaoRunsList(mode);
    void loadReplicacaoWorkflowsList(mode);
    wireReplicacaoValidarPlano();
wireReplicacaoLimparPlanosD1();
wireReplicacaoMetaMensalD1();
  }

  // Renderizar selecao de tarefas quando aplicável
  try {
    renderTarefasCheckboxes();
  } catch (e) {
    // silencioso
  }

  modal.hidden = false;
  setConfigModalBodyLock(true);
}

function closeRobotConfig() {
  const modal = document.getElementById("robot-config-modal");
  if (modal) modal.hidden = true;
  activeConfigMode = null;
  setConfigModalBodyLock(false);
}

function downloadActiveRobotLog() {
  if (!activeConfigMode) return;
  downloadRobotLog(activeConfigMode);
}

async function clearActiveRobotLog() {
  if (!activeConfigMode) return;
  await clearRobotLog(activeConfigMode);
}

async function browseRobotOutputDir() {
  const outputInput = document.getElementById("robot-config-output-dir");
  const currentPath = (outputInput?.value || "").trim();

  try {
    const response = await fetch("/api/system/select-folder", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ initial_path: currentPath }),
    });
    const data = await response.json();
    if (!data.ok) {
      setFeedback(data.message || "Não foi possível abrir o Explorer.", true);
      return;
    }
    if (outputInput) outputInput.value = data.path || "";
  } catch {
    setFeedback("Falha ao abrir seletor de pasta.", true);
  }
}

function clearRobotOutputDir() {
  const outputInput = document.getElementById("robot-config-output-dir");
  if (outputInput) outputInput.value = "";
}

function openRobotSharepointLink(link) {
  const url = String(link || "").trim();
  if (!url) {
    setFeedback("Nenhum link SharePoint configurado para este robô.", true);
    return;
  }
  window.open(url, "_blank", "noopener,noreferrer");
}

async function copyRobotSharepointLink(link) {
  const url = String(link || "").trim();
  if (!url) {
    setFeedback("Nenhum link SharePoint configurado para este robô.", true);
    return;
  }

  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(url);
    } else {
      const temp = document.createElement("textarea");
      temp.value = url;
      document.body.appendChild(temp);
      temp.select();
      document.execCommand("copy");
      temp.remove();
    }
    setFeedback("Link SharePoint copiado.");
  } catch {
    setFeedback("Não foi possível copiar o link SharePoint.", true);
  }
}

async function saveRobotConfig() {
  if (!activeConfigMode) return;

  const outputInput = document.getElementById("robot-config-output-dir");
  const headlessInput = document.getElementById("robot-config-headless");
  const workersInput = document.getElementById("robot-config-workers");
  const { checkbox: rotinaCheckbox, startInput: rotinaStartInput, endInput: rotinaEndInput } = getRotinaPeriodNodes();
  const saveBtn = document.getElementById("robot-config-save-btn");
  const currentCfg = getRobotConfig(activeConfigMode);
  const productionDaysInput = document.getElementById("robot-config-production-dias-brflow");

  const config = normalizeRobotConfig({
    output_dir: outputInput?.value || "",
    sharepoint_url: currentCfg.sharepoint_url || "",
    sharepoint_folders: currentCfg.sharepoint_folders || [],
    headless: Boolean(headlessInput?.checked),
    max_workers: workersInput?.value || null,
    executar_imediatamente: isRotinaPeriodMode(activeConfigMode) ? Boolean(rotinaCheckbox?.checked) : false,
    rotina_data_inicio: isRotinaPeriodMode(activeConfigMode) ? (rotinaStartInput?.value?.trim() || "") : "",
    rotina_data_fim: isRotinaPeriodMode(activeConfigMode) ? (rotinaEndInput?.value?.trim() || "") : "",
    ged_execucao_imediata: isGedMode(activeConfigMode) ? getGedImmediateSelection() : "",
    ...(isReplicacaoAudMode(activeConfigMode) ? readReplicacaoAudConfigFromNodes(activeConfigMode) : {}),
    ...(activeConfigMode === productionMode ? {
      tempo_espera_minutos: currentCfg.tempo_espera_minutos,
      dias_download_brflow: productionDaysInput?.value || currentCfg.dias_download_brflow,
      executar_confer: currentCfg.executar_confer,
      executar_brflow: currentCfg.executar_brflow,
      baixar_monitor_com_producao: currentCfg.baixar_monitor_com_producao,
      baixar_log_eventos_com_producao: currentCfg.baixar_log_eventos_com_producao,
      executar_ged_irregularidade: currentCfg.executar_ged_irregularidade,
      executar_produtividade_case: currentCfg.executar_produtividade_case,
    } : {}),
  });

  // Incluir tarefas selecionadas (quando aplicável)
  if (isTarefasMode(activeConfigMode)) {
    config.tarefas = getTarefasSelecionadas();
  } else {
    config.tarefas = [];
  }

  if (isRotinaPeriodMode(activeConfigMode)) {
    const payload = {
      executar_imediatamente: Boolean(config.executar_imediatamente),
      rotina_data_inicio: config.rotina_data_inicio,
      rotina_data_fim: config.rotina_data_fim,
    };
    const rotinaPeriodError = validateRotinaPeriodPayload(payload);
    if (rotinaPeriodError) {
      setFeedback(rotinaPeriodError, true);
      return;
    }
  }

  if (isReplicacaoAudMode(activeConfigMode)) {
    const replicacaoError = validateReplicacaoAudConfig(config);
    if (replicacaoError) {
      setFeedback(replicacaoError, true);
      return;
    }
  }

  setButtonLoading(saveBtn, true, "Salvando...");
  try {
    const response = await fetch("/api/robots/config", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mode: activeConfigMode, config }),
    });
    const data = await response.json();
    if (!data.ok) {
      setFeedback(data.message || "Falha ao salvar configuração.", true);
      return;
    }

    robotConfigState[activeConfigMode] = normalizeRobotConfig(data.configs?.[activeConfigMode] || config);
    renderRobotConfigSummary(activeConfigMode);
    setFeedback(data.message || "Configuração salva.");
    closeRobotConfig();
  } catch {
    setFeedback("Falha ao salvar configuração do robô.", true);
  } finally {
    setButtonLoading(saveBtn, false, "Salvando...");
  }
}

function bindCredentialValidation() {
  const matriculaInput = document.getElementById("matricula");
  const senhaInput = document.getElementById("senha");

  [matriculaInput, senhaInput].forEach((input) => {
    input?.addEventListener("input", () => {
      markCredentialsAsDirty();
      syncStartButtonsAvailability();
      if (hasRequiredCredentials()) {
        setFeedback("");
      }
    });
  });
}

function formatDateTime(value) {
  if (!value) return "-";
  const dt = new Date(value);
  if (Number.isNaN(dt.getTime())) return "-";
  return dt.toLocaleString("pt-BR");
}

function formatDuration(seconds) {
  if (seconds === null || seconds === undefined) return "-";
  const total = Math.max(0, Math.round(Number(seconds)));
  const mins = Math.floor(total / 60);
  const secs = total % 60;
  return mins ? `${mins}m ${secs}s` : `${secs}s`;
}

function normalizeResult(result) {
  const value = (result || "sem histórico").toLowerCase();
  if (value === "sucesso") return { label: "✅ sucesso", cls: "success" };
  if (value === "erro") return { label: "❌ erro", cls: "error" };
  if (value === "interrompido") return { label: "⏹ interrompido", cls: "stopped" };
  if (value === "em execução") return { label: "⏳ em execução", cls: "running" };
  return { label: "• sem histórico", cls: "empty" };
}

function updateStatus(robots) {
  robotModes.forEach((mode) => {
    const statusNode = document.getElementById(`status-${mode}`);
    const pidNode = document.getElementById(`pid-${mode}`);
    const execNode = document.getElementById(`exec-${mode}`);
    const updatedNode = document.getElementById(`updated-${mode}`);
    const progressBarNode = document.getElementById(`progress-bar-${mode}`);
    const progressPctNode = document.getElementById(`progress-pct-${mode}`);
    const progressTextNode = document.getElementById(`progress-text-${mode}`);
    const info = robots?.[mode];
    const { startBtn, stopBtn, card } = getRobotButtons(mode);

    if (!statusNode || !execNode || !info) return;

    const execution = info.execution || {};
    const started = formatDateTime(execution.started_at);
    const ended = formatDateTime(execution.ended_at);
    const duration = formatDuration(execution.duration_seconds);
    const result = normalizeResult(execution.result);
    const runtime = info.runtime || {};
    const pct = Number.isFinite(Number(runtime.progress)) ? Math.max(0, Math.min(100, Number(runtime.progress))) : 0;
    const statusMsg = String(runtime.status || "Aguardando execução");
    const updatedAt = formatDateTime(runtime.updated_at);
    let progressState = "idle";
    if (info.running) {
      progressState = "running";
    } else if (result.cls === "success") {
      progressState = "success";
    } else if (result.cls === "error") {
      progressState = "error";
    } else if (result.cls === "stopped") {
      progressState = "stopped";
    }

    execNode.innerHTML = `Última execução: <span class="exec-badge ${result.cls}">${result.label}</span> | início: ${started} | fim: ${ended} | duração: ${duration}`;
    if (pidNode) {
      pidNode.textContent = `PID: ${info.running ? (info.pid ?? "-") : "-"}`;
    }
    if (updatedNode) {
      updatedNode.textContent = `Atualizado: ${updatedAt}`;
    }
    if (progressBarNode) {
      progressBarNode.style.width = `${pct}%`;
      progressBarNode.classList.remove("running", "success", "error", "stopped", "idle");
      progressBarNode.classList.add(progressState);
    }
    if (progressPctNode) {
      progressPctNode.textContent = `${pct}%`;
    }
    if (progressTextNode) {
      progressTextNode.textContent = statusMsg;
    }

    if (pendingActionByMode[mode]) return;

    if (info.running) {
      statusNode.textContent = "Estado: em execução";
      statusNode.classList.add("running");
      if (card) card.dataset.running = "1";
      if (startBtn) startBtn.disabled = true;
      if (stopBtn) stopBtn.disabled = false;
    } else {
      statusNode.textContent = "Estado: parado";
      statusNode.classList.remove("running");
      if (card) card.dataset.running = "0";
      if (startBtn) startBtn.disabled = !canStartRobots(mode);
      if (stopBtn) stopBtn.disabled = true;
    }

    if (isReplicacaoAudMode(mode)) {
      const wasRunning = replicacaoRunningPrev[mode];
      if (wasRunning && !info.running) {
        const label = isReplicacaoAudD1Mode(mode) ? "Replicação D-1" : "Replicação de Auditoria";
        const msg = info.result || info.last_result || "Execução encerrada";
        setFeedback(`${label}: ${msg}`, false);
        loadReplicacaoRunsList(mode);
      }
      replicacaoRunningPrev[mode] = Boolean(info.running);
    }
  });

  syncStartButtonsAvailability();
}

function updateLogs(logs) {
  robotModes.forEach((mode) => {
    const node = document.getElementById(`log-${mode}`);
    if (!node) return;
    const lines = logs?.[mode] || [];
    node.textContent = lines.length ? lines.join("\n") : "Sem logs.";
    node.scrollTop = node.scrollHeight;
  });
}

async function fetchStatus() {
  try {
    const response = await fetch("/api/robots/status");
    const data = await response.json();
    updateStatus(data.robots);
    applyCredentialsStatus(data.credentials, { respectDirty: true });
    syncStartButtonsAvailability();
  } catch {
    setFeedback("Falha ao consultar status dos robôs.", true);
  }
}

async function fetchCredentialsStatus() {
  try {
    const response = await fetch("/api/credentials/status");
    const data = await response.json();
    if (data.ok) {
      applyCredentialsStatus(data.credentials, { respectDirty: true });
      syncStartButtonsAvailability();
    }
  } catch {
    setFeedback("Falha ao consultar validação de credenciais.", true);
  }
}

async function fetchLogs() {
  try {
    const response = await fetch("/api/robots/logs?tail=80");
    const data = await response.json();
    if (data.ok) {
      updateLogs(data.logs);
    }
  } catch {
    setFeedback("Falha ao consultar logs em tempo real.", true);
  }
}

async function startRobot(mode) {
  if (!canStartRobots(mode)) {
    setFeedback(getStartDisableReason(mode), true);
    syncStartButtonsAvailability();
    return;
  }

  const payload = getStartPayload(mode);
  if (isRotinaPeriodMode(mode)) {
    const rotinaPeriodError = validateRotinaPeriodPayload(payload);
    if (rotinaPeriodError) {
      setFeedback(rotinaPeriodError, true);
      return;
    }
  }
  if (isReplicacaoAudMode(mode)) {
    const replicacaoError = validateReplicacaoAudConfig(payload.robot_config || {});
    if (replicacaoError) {
      setFeedback(replicacaoError, true);
      return;
    }
  }

  if (pendingActionByMode[mode]) return;
  pendingActionByMode[mode] = "start";
  const { startBtn, stopBtn } = getRobotButtons(mode);
  setButtonLoading(startBtn, true, "Iniciando...");
  if (stopBtn) stopBtn.disabled = true;

  try {
    const response = await fetch("/api/robots/start", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await response.json();
    setFeedback(data.message, !data.ok);
    updateStatus(data.robots);
    applyCredentialsStatus(data.credentials, { respectDirty: false });
  } catch {
    setFeedback("Falha ao iniciar robô.", true);
  } finally {
    delete pendingActionByMode[mode];
    setButtonLoading(startBtn, false, "Iniciando...");
    await fetchStatus();
  }
}

async function stopRobot(mode) {
  if (pendingActionByMode[mode]) return;
  pendingActionByMode[mode] = "stop";
  const { startBtn, stopBtn } = getRobotButtons(mode);
  setButtonLoading(stopBtn, true, "Parando...");
  if (startBtn) startBtn.disabled = true;

  try {
    const response = await fetch("/api/robots/stop", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mode }),
    });
    const data = await response.json();
    setFeedback(data.message, !data.ok);
    updateStatus(data.robots);
    applyCredentialsStatus(data.credentials, { respectDirty: true });
  } catch {
    setFeedback("Falha ao parar robô.", true);
  } finally {
    delete pendingActionByMode[mode];
    setButtonLoading(stopBtn, false, "Parando...");
    await fetchStatus();
  }
}

async function stopAllRobots() {
  const stopAllBtn = document.getElementById("stop-all-btn");
  setButtonLoading(stopAllBtn, true, "Parando...");
  try {
    const response = await fetch("/api/robots/stop-all", { method: "POST" });
    const data = await response.json();
    setFeedback("Comando de parada enviado para todos os robôs.");
    updateStatus(data.robots);
    applyCredentialsStatus(data.credentials, { respectDirty: true });
  } catch {
    setFeedback("Falha ao parar todos os robôs.", true);
  } finally {
    setButtonLoading(stopAllBtn, false, "Parando...");
    await fetchStatus();
  }
}

function downloadRobotLog(mode) {
  const url = `/api/robots/logs/download?mode=${encodeURIComponent(mode)}`;
  window.open(url, "_blank");
}

async function clearRobotLog(mode) {
  const confirmed = window.confirm(`Deseja limpar o log do robô "${mode}"?`);
  if (!confirmed) return;
  try {
    const response = await fetch("/api/robots/logs/clear", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mode }),
    });
    const data = await response.json();
    setFeedback(data.message, !data.ok);
    if (data.ok) updateLogs(data.logs);
  } catch {
    setFeedback("Falha ao limpar log.", true);
  }
}

async function validateCredentials() {
  if (!isOktaValidatorVisible()) {
    setFeedback("O validador Okta está oculto. Marque a opção para exibi-lo.", true);
    return;
  }

  if (!hasRequiredCredentials()) {
    setFeedback("Preencha matrícula e senha para validar no Okta.", true);
    syncStartButtonsAvailability();
    return;
  }

  credentialsValidationState.checking = true;
  credentialsValidationState.message = "Validando credenciais no Okta (pode levar até 3 min)...";
  updateCredentialsStatusUI();
  syncStartButtonsAvailability();
  setCredentialsLoadingOverlay(true);
  setFeedback("Validação Okta em andamento. O navegador pode abrir em instantes — aguarde até 3 minutos.");

  try {
    const response = await fetch("/api/credentials/validate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        ...getCredentials(),
        headless: !isOktaValidateBrowserVisible(),
      }),
    });
    const data = await response.json();
    credentialsDirty = !data.ok;
    applyCredentialsStatus(data.credentials, { respectDirty: false });
    setFeedback(data.message, !data.ok);
    await fetchStatus();
  } catch {
    credentialsDirty = true;
    credentialsValidationState.checking = false;
    credentialsValidationState.validated = false;
    credentialsValidationState.message = "Falha ao validar credenciais no Okta.";
    updateCredentialsStatusUI();
    setFeedback("Falha ao validar credenciais no Okta.", true);
  } finally {
    setCredentialsLoadingOverlay(false);
    syncStartButtonsAvailability();
  }
}

window.startRobot = startRobot;
window.stopRobot = stopRobot;
window.stopAllRobots = stopAllRobots;
window.downloadRobotLog = downloadRobotLog;
window.clearRobotLog = clearRobotLog;
window.downloadActiveRobotLog = downloadActiveRobotLog;
window.clearActiveRobotLog = clearActiveRobotLog;
window.validateCredentials = validateCredentials;
window.openRobotConfig = openRobotConfig;
window.closeRobotConfig = closeRobotConfig;
window.saveRobotConfig = saveRobotConfig;
window.browseRobotOutputDir = browseRobotOutputDir;
window.clearRobotOutputDir = clearRobotOutputDir;
window.openRobotSharepointLink = openRobotSharepointLink;
window.copyRobotSharepointLink = copyRobotSharepointLink;

bindRotinaOptions();
wireReplicacaoValidarPlano();
wireReplicacaoLimparPlanosD1();
wireReplicacaoMetaMensalD1();
wireConfigTabs();
bindOktaValidatorToggle();
bindOktaValidateBrowserToggle();
bindCredentialValidation();
updateCredentialsStatusUI();
syncStartButtonsAvailability();
fetchCredentialsStatus();
fetchRobotConfigs();
fetchStatus();
setInterval(fetchStatus, 4000);
