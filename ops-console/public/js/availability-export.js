/* global window, document, Blob, URL, fetch */
(function () {
  const OC = window.OpsConsole = window.OpsConsole || {};
  const eventLogRequests = new Set();

  function snapshot() {
    const state = OC.monitorState;
    const period = state.availabilityWindow || "24h";
    const demo = state.availabilityDemo === true;
    const payload = state.payload || {};
    const source = demo ? OC.buildAvailabilityDemo(period, state.availabilityScenario) : payload.availabilityByEnv;
    const envs = demo ? ["MAIN", "DEV", "HOM"] : (state.selectedEnvs || OC.ENV_ORDER);
    return {
      exportedAt: new Date().toISOString(), window: period, demo,
      timestampFormat: "ISO 8601 UTC", durationUnit: "seconds",
      note: "Exportação dos dados carregados na tela. Eventos respeitam o limite informado por timelineTruncated.",
      environments: envs.map(environment => {
        const entry = source?.[environment];
        const valid = entry?.window === period && !entry.error;
        const data = valid ? entry : null;
        const current = (demo ? data?.current : payload.availabilityCurrentByEnv?.[environment]) || {};
        const stale = current._cache?.stale || (current.checkedAt && Date.now() - Date.parse(current.checkedAt) > (current.maxAgeMs || 120000));
        return {
          environment, status: !valid ? "unavailable" : entry._cache?.stale ? "stale" : "available",
          error: valid ? null : entry?.window === period && entry.error ? entry.error : "Dados deste período ainda não foram carregados.",
          data,
          current: { ...current, displayedState: current.state === "disabled" ? "disabled" : stale ? "stale" : current.state || "unknown" },
        };
      }),
    };
  }

  const iso = value => value && Number.isFinite(Date.parse(value)) ? new Date(value).toISOString() : null;
  const commonHeaders = ["Ambiente", "Período", "Dados simulados", "Situação dos dados", "Erro", "Exportado em (UTC)"];
  const common = (report, entry) => [entry.environment, report.window, report.demo, entry.status, entry.error, report.exportedAt];

  function csvTable(report, kind) {
    if (kind === "summary") return {
      headers: [...commonHeaders, "Início do período (UTC)", "Fim do período (UTC)", "Estado atual", "Verificação atual (UTC)",
        "API disponibilidade (%)", "API requisições", "API erros 5xx", "API período confirmado", "API observação",
        "Health disponibilidade (%)", "Amostras", "Amostras bem-sucedidas", "Cobertura (%)", "Última amostra (UTC)",
        "Frequência prevista (s)", "Intervalo médio real (s)", "Maior lacuna (s)", "Episódios de falha",
        "Indisponibilidade estimada (s)", "Tempo observado (s)", "Tempo sem cobertura (s)", "Eventos limitados"],
      rows: report.environments.map(entry => {
        const data = entry.data || {}, api = data.api || {}, health = data.health || {}, impact = data.impact || {};
        const apiVisible = api.availabilityPct != null || (api.requests === 0 && api.periodVerified);
        return [...common(report, entry), iso(data.since), iso(data.until), entry.current.displayedState, iso(entry.current.checkedAt),
          api.availabilityPct, apiVisible ? api.requests : null, apiVisible ? api.errors5xx : null, api.periodVerified,
          api.unavailableReason || api.error, health.uptimePct, health.samples, health.successful, health.coveragePct, iso(health.lastSampleAt),
          health.intervalSec, health.actualIntervalSec, health.largestGapSec, impact.incidentCount, impact.estimatedDowntimeSec,
          impact.observedSec, impact.unknownSec, data.timelineTruncated];
      }),
    };
    if (kind === "history") return {
      headers: [...commonHeaders, "Início do intervalo (UTC)", "Fim do intervalo (UTC)", "Amostras", "Falhas",
        "Tempo observado (s)", "Indisponibilidade observada (s)", "Cobertura (%)"],
      rows: report.environments.flatMap(entry => (entry.data?.history || []).map(point => [
        ...common(report, entry), iso(point.since), iso(point.until), point.samples, point.failures,
        point.observedSec, point.downSec, point.coveragePct,
      ])),
    };
    if (kind === "events") return {
      headers: [...commonHeaders, "Início do evento (UTC)", "Fim do evento (UTC)", "Tipo", "Título", "Severidade",
        "Estado", "Resultado", "Em andamento", "Continuidade incerta", "Duração estimada (s)", "Duração observada (s)", "Eventos limitados",
        "Deploy associado", "Episódios consolidados", "Sinais correlacionados"],
      rows: report.environments.flatMap(entry => (entry.data?.timeline || []).map(event => [
        ...common(report, entry), iso(event.startedAt), iso(event.finishedAt), event.kind, event.title, event.severity,
        event.status, event.result, event.ongoing, event.uncertain, event.durationSec, event.observedDurationSec, entry.data.timelineTruncated,
        event.deploy?.runId, event.fragmentCount, event.relatedSignals?.length,
      ])),
    };
    throw new Error("Tipo de exportação inválido");
  }

  function csvCell(value) {
    if (value == null) return '""';
    let text = typeof value === "boolean" ? value ? "Sim" : "Não"
      : typeof value === "number" && Number.isFinite(value) ? String(value).replace(".", ",") : String(value);
    // Prevent event titles and source errors from becoming spreadsheet formulas.
    if (typeof value === "string" && /^[\s\uFEFF]*[=+@-]/.test(text)) text = "'" + text;
    return `"${text.replace(/"/g, '""')}"`;
  }

  OC.buildAvailabilityExport = function buildAvailabilityExport(kind) {
    const report = snapshot();
    const tag = `${report.demo ? "simulacao-" : ""}${report.environments.map(entry => entry.environment).join("-")}-${report.window}-${report.exportedAt.replace(/[:.]/g, "-")}`;
    if (kind === "json") return {
      filename: `disponibilidade-completo-${tag}.json`, type: "application/json;charset=utf-8",
      content: JSON.stringify(report, null, 2) + "\n",
    };
    const table = csvTable(report, kind);
    return {
      filename: `disponibilidade-${{ summary: "resumo", history: "historico", events: "eventos" }[kind]}-${tag}.csv`,
      type: "text/csv;charset=utf-8",
      content: "\uFEFF" + [table.headers, ...table.rows].map(row => row.map(csvCell).join(";")).join("\r\n") + "\r\n",
      rows: table.rows.length,
    };
  };

  OC.bindAvailabilityExports = function bindAvailabilityExports(root) {
    root.querySelectorAll("[data-availability-export]").forEach(button => {
      button.addEventListener("click", () => {
        let url;
        try {
          const file = OC.buildAvailabilityExport(button.dataset.availabilityExport);
          if (file.rows === 0) {
            OC.toast?.("Não há registros carregados para esta exportação no período selecionado.", "warning");
            return;
          }
          url = URL.createObjectURL(new Blob([file.content], { type: file.type }));
          const link = document.createElement("a");
          link.href = url;
          link.download = file.filename;
          link.hidden = true;
          document.body.appendChild(link);
          try { link.click(); } finally { link.remove(); }
          button.closest("details")?.removeAttribute("open");
        } catch (error) {
          OC.toast?.(`Não foi possível exportar: ${error.message}`, "error");
        } finally {
          if (url) window.setTimeout(() => URL.revokeObjectURL(url), 1000);
        }
      });
    });
  };

  OC.bindAvailabilityEventLogs = function bindAvailabilityEventLogs(root) {
    root.querySelectorAll('[data-availability-event-log]').forEach(button => {
      if (button.dataset.logBound) return;
      button.dataset.logBound = '1';
      button.addEventListener('click', async () => {
        if (button.disabled || (OC.monitorState.availabilityDemo && OC.monitorState.activeTab !== "congestion")) return;
        const requestKey = JSON.stringify([button.dataset.logEnv, button.dataset.logAt, button.dataset.logSince, button.dataset.logUntil]);
        if (eventLogRequests.has(requestKey)) return;
        eventLogRequests.add(requestKey);
        const label = button.textContent;
        button.disabled = true;
        button.textContent = 'Extraindo…';
        let url;
        try {
          const { logEnv: env, logAt: at, logSince: since, logUntil: until } = button.dataset;
          if (!OC.ENV_ORDER.includes(env)) throw new Error('Ambiente inválido.');
          const query = new URLSearchParams({ at, since, until });
          const response = await fetch(`/api/v1/monitoring/${encodeURIComponent(env)}/availability-event-log?${query}`, { credentials: 'same-origin' });
          if (!response.ok) {
            const failure = await response.json().catch(() => ({}));
            throw new Error(failure.error || `Falha ao extrair log (HTTP ${response.status}).`);
          }
          const file = await response.blob();
          url = URL.createObjectURL(file);
          const link = document.createElement('a');
          link.href = url;
          link.download = `pplid-${env.toLowerCase()}-evento-${at.replace(/[:.]/g, '-')}.log`;
          link.hidden = true;
          document.body.appendChild(link);
          try { link.click(); } finally { link.remove(); }
          OC.toast?.('Log do evento extraído com sondagens e fontes disponíveis.', 'success');
        } catch (error) {
          OC.toast?.(`Não foi possível extrair o log: ${error.message}`, 'error');
        } finally {
          eventLogRequests.delete(requestKey);
          button.disabled = false;
          button.textContent = label;
          if (url) window.setTimeout(() => URL.revokeObjectURL(url), 1000);
        }
      });
    });
  };
})();
