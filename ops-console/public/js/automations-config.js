/* global window, document */
(function () {
  "use strict";

  window.OpsConsole = window.OpsConsole || {};
  const OC = window.OpsConsole;

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

  const ROTINA_HORARIO_AGENDADO = "08:00";

  const PRODUCTION_CHECKBOXES = [
    { key: "executar_confer", label: "Executar Confer" },
    { key: "executar_brflow", label: "Executar BRFlow" },
    { key: "baixar_monitor_com_producao", label: "Baixar monitor com produção" },
    { key: "baixar_log_eventos_com_producao", label: "Baixar log de eventos com produção" },
    { key: "executar_ged_irregularidade", label: "Executar GED irregularidade" },
    { key: "executar_produtividade_case", label: "Executar produtividade Case Manager" },
  ];

  function esc(text) {
    return OC.escapeHtml ? OC.escapeHtml(text) : String(text ?? "");
  }

  function boolVal(value, fallback) {
    if (typeof value === "string") return ["1", "true", "yes", "on"].includes(value.trim().toLowerCase());
    if (value == null) return fallback;
    return Boolean(value);
  }

  function renderProductionForm(mode, config, options) {
    const disabled = options?.disabled ? "disabled" : "";
    const cfg = config || {};
    const checks = PRODUCTION_CHECKBOXES.map((item) => {
      const checked = boolVal(cfg[item.key], item.key !== "executar_produtividade_case") ? "checked" : "";
      return `<label class="auto-checkline"><input type="checkbox" data-auto-field="${item.key}" ${checked} ${disabled}><span>${esc(item.label)}</span></label>`;
    }).join("");
    const headless = boolVal(cfg.headless, false) ? "checked" : "";
    return `<div class="auto-config-form-panel" data-auto-config-form="${mode}">
      <div class="auto-config-grid-2">
        <label class="auto-field"><span>Intervalo entre ciclos (min)</span>
          <input type="number" min="5" max="180" step="1" data-auto-field="tempo_espera_minutos" value="${esc(cfg.tempo_espera_minutos ?? 60)}" ${disabled}></label>
        <label class="auto-field"><span>Dias para baixar do BRFlow</span>
          <input type="number" min="1" max="31" step="1" data-auto-field="dias_download_brflow" value="${esc(cfg.dias_download_brflow ?? 3)}" ${disabled}></label>
      </div>
      <p class="auto-config-hint">Quantidade de dias incluídos em cada ciclo da Produção H/H, contando hoje. Padrão: 3 dias.</p>
      <div class="auto-config-checkgrid">${checks}</div>
      <label class="auto-checkline"><input type="checkbox" data-auto-field="headless" ${headless} ${disabled}><span>Executar em modo headless (sem janela)</span></label>
    </div>`;
  }

  function renderRotinaForm(mode, config, options) {
    const disabled = options?.disabled ? "disabled" : "";
    const cfg = config || {};
    const imediata = boolVal(cfg.executar_imediatamente, false);
    const selected = new Set(Array.isArray(cfg.tarefas) ? cfg.tarefas.map((t) => String(t).toLowerCase()) : []);
    const taskRows = Object.entries(TAREFAS_DISPONIVEIS).map(([id, info]) => {
      const deps = info.deps?.length ? ` <small>requer: ${esc(info.deps.join(", "))}</small>` : "";
      const checked = selected.has(id) ? "checked" : "";
      return `<label class="auto-checkline auto-task-line"><input type="checkbox" data-auto-task="${id}" ${checked} ${disabled}><span><strong>${esc(info.label)}</strong>${deps}<br><small>${esc(info.descricao)}</small></span></label>`;
    }).join("");
    const headless = boolVal(cfg.headless, false) ? "checked" : "";
    return `<div class="auto-config-form-panel" data-auto-config-form="${mode}">
      <label class="auto-checkline"><input type="checkbox" data-auto-field="executar_imediatamente" data-auto-rotina-imediata ${imediata ? "checked" : ""} ${disabled}><span>Executar rotina imediatamente</span></label>
      <div class="auto-config-grid-2">
        <label class="auto-field"><span>Período (início)</span>
          <input type="date" data-auto-field="rotina_data_inicio" value="${esc(cfg.rotina_data_inicio || "")}" ${imediata && !disabled ? "" : "disabled"} ${disabled}></label>
        <label class="auto-field"><span>Período (fim)</span>
          <input type="date" data-auto-field="rotina_data_fim" value="${esc(cfg.rotina_data_fim || "")}" ${imediata && !disabled ? "" : "disabled"} ${disabled}></label>
      </div>
      <p class="auto-config-hint">Desmarcado: o robô permanece ativo aguardando a execução diária às ${ROTINA_HORARIO_AGENDADO}. Marcado: processa o período informado e encerra.</p>
      <div class="auto-config-subhead">Tarefas da rotina</div>
      <div class="auto-config-taskgrid">${taskRows}</div>
      <label class="auto-checkline"><input type="checkbox" data-auto-field="headless" ${headless} ${disabled}><span>Executar em modo headless (sem janela)</span></label>
    </div>`;
  }

  function renderBotConfigForm(mode, config, options) {
    if (mode === "production") return renderProductionForm(mode, config, options);
    if (mode === "rotina") return renderRotinaForm(mode, config, options);
    return "";
  }

  function readField(root, name) {
    const node = root.querySelector(`[data-auto-field="${name}"]`);
    if (!node) return undefined;
    if (node.type === "checkbox") return node.checked;
    return node.value;
  }

  function readBotConfigForm(mode, root) {
    const panel = root.querySelector(`[data-auto-config-form="${mode}"]`);
    if (!panel) return {};
    if (mode === "production") {
      const out = { headless: Boolean(readField(panel, "headless")) };
      out.tempo_espera_minutos = Number(readField(panel, "tempo_espera_minutos"));
      out.dias_download_brflow = Number(readField(panel, "dias_download_brflow"));
      PRODUCTION_CHECKBOXES.forEach((item) => { out[item.key] = Boolean(readField(panel, item.key)); });
      return out;
    }
    if (mode === "rotina") {
      const tarefas = [];
      panel.querySelectorAll("[data-auto-task]:checked").forEach((node) => tarefas.push(node.dataset.autoTask));
      return {
        headless: Boolean(readField(panel, "headless")),
        executar_imediatamente: Boolean(readField(panel, "executar_imediatamente")),
        rotina_data_inicio: String(readField(panel, "rotina_data_inicio") || "").trim(),
        rotina_data_fim: String(readField(panel, "rotina_data_fim") || "").trim(),
        tarefas,
        ged_execucao_imediata: "",
      };
    }
    return {};
  }

  function isRotinaAgendada(config) {
    return !boolVal(config?.executar_imediatamente, false);
  }

  function renderConfigSummary(mode, config) {
    const cfg = config || {};
    if (mode === "production") {
      const parts = [`BRFlow: ${cfg.dias_download_brflow ?? 3} dia(s)`, `intervalo: ${cfg.tempo_espera_minutos ?? 60} min`];
      if (cfg.baixar_monitor_com_producao !== false) parts.push("monitor: sim");
      if (cfg.executar_produtividade_case) parts.push("case: sim");
      if (cfg.headless) parts.push("headless");
      return parts.join(" · ");
    }
    if (mode === "rotina") {
      const parts = [];
      if (cfg.executar_imediatamente) {
        parts.push(`imediata: ${cfg.rotina_data_inicio || "-"} a ${cfg.rotina_data_fim || "-"}`);
      } else {
        parts.push(`agendada: diária às ${ROTINA_HORARIO_AGENDADO}`);
      }
      const count = Array.isArray(cfg.tarefas) ? cfg.tarefas.length : 0;
      parts.push(count ? `${count} tarefa(s)` : "sem tarefas selecionadas");
      if (cfg.headless) parts.push("headless");
      return parts.join(" · ");
    }
    return "";
  }

  function bindRotinaImmediateToggle(root) {
    root.querySelectorAll("[data-auto-rotina-imediata]").forEach((checkbox) => {
      checkbox.addEventListener("change", () => {
        const panel = checkbox.closest("[data-auto-config-form]");
        if (!panel) return;
        const enabled = checkbox.checked;
        panel.querySelectorAll('[data-auto-field="rotina_data_inicio"], [data-auto-field="rotina_data_fim"]').forEach((input) => {
          if (input.disabled && checkbox.disabled) return;
          input.disabled = !enabled || checkbox.disabled;
        });
      });
    });
  }

  OC.automationConfig = {
    TAREFAS_DISPONIVEIS,
    ROTINA_HORARIO_AGENDADO,
    isRotinaAgendada,
    renderBotConfigForm,
    readBotConfigForm,
    renderConfigSummary,
    bindRotinaImmediateToggle,
  };
})();
