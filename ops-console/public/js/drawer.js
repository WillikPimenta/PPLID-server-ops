/* global window */
(function () {
  window.OpsConsole = window.OpsConsole || {};
  const OC = window.OpsConsole;

  OC.selectedRowId = null;
  OC.drawerActiveTab = "summary";
  OC.drawerMode = null;
  OC.historyDrawerEnv = null;
  OC.drawerLogsLoadSeq = 0;
  OC.detailLogsExpandedSteps = new Set(["deps"]);
  OC.drawerSelectedRow = null;

OC.openDrawer = function openDrawer(row, options) {
  if (!row) return;
  OC.drawerMode = "detail";
  OC.historyDrawerEnv = null;
  OC.selectedRowId = row.id;
  OC.drawerSelectedRow = row;
  if (!options?.keepTab) OC.drawerActiveTab = "summary";
  OC.resetDrawerBodyScroll?.();
  document.querySelectorAll(".history-row.selected").forEach((el) => el.classList.remove("selected"));
  const rowEl = document.querySelector(`.history-row[data-row-id="${row.id}"]`);
  rowEl?.classList.add("selected");

  const title = document.getElementById("drawer-title");
  if (title) title.textContent = `Detalhes — ${row.environment}`;

  const overlay = document.getElementById("drawer-overlay");
  const drawer = document.getElementById("deployment-drawer");
  overlay?.classList.remove("hidden");
  overlay?.setAttribute("aria-hidden", "false");
  drawer?.classList.remove("hidden");
  drawer?.classList.add("open");
  drawer?.setAttribute("aria-hidden", "false");
  document.body.classList.add("drawer-open");
  OC.setDrawerWide?.(OC.drawerActiveTab === "logs");

  OC.renderDrawerContent(row);
};

OC.closeDrawer = function closeDrawer() {
  if (OC.stopDeployDrawerPolling) OC.stopDeployDrawerPolling();
  OC.deployDrawerEnv = null;
  OC.selectedRowId = null;
  OC.drawerSelectedRow = null;
  OC.drawerMode = null;
  OC.historyDrawerEnv = null;
  OC.setDrawerWide?.(false);
  document.querySelectorAll(".history-row.selected").forEach((el) => el.classList.remove("selected"));

  const overlay = document.getElementById("drawer-overlay");
  const drawer = document.getElementById("deployment-drawer");
  overlay?.classList.add("hidden");
  overlay?.setAttribute("aria-hidden", "true");
  drawer?.classList.remove("open");
  drawer?.classList.add("hidden");
  drawer?.setAttribute("aria-hidden", "true");
  document.body.classList.remove("drawer-open");
};

OC.renderDrawerContent = function renderDrawerContent(row) {
  const body = document.getElementById("drawer-body");
  if (!body) return;
  OC.resetDrawerBodyScroll?.();

  const data = row.envData || {};
  const phase = data.displayPhase || data.phase || "idle";
  const runtime = data.runtime || {};
  const cc = data.currentCommit || {};
  const dc = data.deployCommit || {};
  const commitSubject = OC.resolveCommitSubject ? OC.resolveCommitSubject(data) : dc.subject || cc.subject || data.gitCommitSubject || "";
  const deployedSha = OC.resolveCommitSha ? OC.resolveCommitSha(data) : data.deployedSha || data.deployCommit?.sha || data.activeSha || "—";
  const repoSha = data.repoSha || data.currentCommit?.sha || data.gitSha || "—";
  const links = data.links || {};
  const linksDisabled = phase === "deploying" || phase === "offline";
  const lastGood = data.lastGoodSha || data.deployState?.lastGoodSha || "—";
  const previousSha = data.previousSha || data.deployState?.previousSha || "";
  const canRollback = phase !== "deploying" && (previousSha || lastGood !== "—");
  const busy = phase === "deploying";
  const homOverview = OC.lastOverview?.environments?.HOM || {};
  const homBusy = (homOverview.displayPhase || homOverview.phase) === "deploying";
  const promoteDisabled = busy || homBusy;
  const isCurrentDeploy = row.isRunning || (busy && row.runId && data.deployState?.runId === row.runId);
  const isActiveRun = OC.isHistoryRowActive ? OC.isHistoryRowActive(row, data) : false;
  const runKindLabel = isCurrentDeploy
    ? "Deploy em andamento"
    : isActiveRun
      ? "Versao ativa no ambiente"
      : "Deploy historico";

  let alerts = "";
  const lastDeploy = data.lastDeploy || {};
  const alertMsg = lastDeploy.message || data.lastDeployMessage;
  if (data.deployPending || (data.shaInSync === false && repoSha !== "—" && deployedSha !== "—")) {
    alerts += `<div class="alert alert-drift">Deploy pendente: disco <code>${OC.escapeHtml(repoSha)}</code> ≠ implantado <code>${OC.escapeHtml(deployedSha)}</code></div>`;
  }
  if (data.updatePending) {
    const origin = data.originShaShort || (data.originSha || "").slice(0, 7);
    alerts += `<div class="alert alert-pending">Atualização detectada no GitHub${origin ? ` (origin: <code>${OC.escapeHtml(origin)}</code>)` : ""}</div>`;
  }
  if (alertMsg) {
    const cls = (lastDeploy.result || data.lastDeployResult) === "warning" ? "alert alert-warn" : "alert alert-error";
    alerts += `<div class="${cls}">${OC.escapeHtml(OC.fixMojibake(alertMsg))}</div>`;
  } else if (runtime.error && phase !== "healthy") {
    alerts += `<div class="alert alert-error">${OC.escapeHtml(runtime.error)}</div>`;
  }

  const linkBtn = (key, label) => {
    const url = links[key];
    if (!url) return "";
    const cls = linksDisabled ? "btn btn-secondary btn-sm disabled" : "btn btn-secondary btn-sm";
    const href = linksDisabled ? "#" : url;
    return `<a class="${cls}" href="${href}" target="_blank" rel="noopener">${label}</a>`;
  };

  body.innerHTML = `
    <div class="drawer-meta">${OC.statusBadgeHtml(row.statusKey)} <span class="drawer-run-kind">${OC.escapeHtml(runKindLabel)}</span></div>
    ${isCurrentDeploy ? `<div class="drawer-live-banner"><p>Deploy em andamento neste ambiente.</p><button type="button" class="btn btn-primary btn-sm" data-open-deploy-drawer="${OC.escapeHtml(row.environment)}">Ver deploy ao vivo</button></div>` : ""}
    ${alerts}
    <div class="drawer-tabs" role="tablist">
      <button type="button" class="drawer-tab ${OC.drawerActiveTab === "summary" ? "is-active" : ""}" data-drawer-tab="summary">Resumo</button>
      <button type="button" class="drawer-tab ${OC.drawerActiveTab === "database" ? "is-active" : ""}" data-drawer-tab="database">Banco</button>
      <button type="button" class="drawer-tab ${OC.drawerActiveTab === "env" ? "is-active" : ""}" data-drawer-tab="env">Variáveis</button>
      <button type="button" class="drawer-tab ${OC.drawerActiveTab === "logs" ? "is-active" : ""}" data-drawer-tab="logs">Logs</button>
    </div>
    <div class="drawer-tab-panel ${OC.drawerActiveTab === "summary" ? "is-active" : ""}" data-drawer-panel="summary">
      <dl class="drawer-dl">
        <dt>Ambiente</dt><dd>${OC.escapeHtml(row.environment)}</dd>
        <dt>Branch</dt><dd><code>${OC.escapeHtml(row.branch)}</code></dd>
        <dt>Status</dt><dd>${OC.escapeHtml(OC.STATUS_META[row.statusKey]?.label || row.statusKey)}</dd>
        <dt>SHA implantado</dt><dd><code>${OC.escapeHtml(deployedSha)}</code></dd>
        <dt>Última versão estável</dt><dd><code>${OC.escapeHtml(lastGood)}</code></dd>
        <dt>SHA no disco</dt><dd><code>${OC.escapeHtml(repoSha)}</code></dd>
        <dt>Health version</dt><dd><code>${OC.escapeHtml(runtime.version || "—")}</code></dd>
        <dt>Mensagem</dt><dd class="drawer-multiline">${OC.escapeHtml(commitSubject || "Não informado")}</dd>
        <dt>Autor</dt><dd>${OC.escapeHtml(row.author || "Não informado")}</dd>
        <dt>Último sync</dt><dd>${OC.formatDate(data.lastSyncAt)}</dd>
        <dt>Deploy iniciado</dt><dd>${OC.formatDate(data.lastDeployStartedAt)}</dd>
        <dt>Deploy finalizado</dt><dd>${data.lastDeployFinishedAt ? OC.formatDate(data.lastDeployFinishedAt) : "Ainda não finalizado"}</dd>
        <dt>Duração</dt><dd>${row.isRunning ? OC.formatLiveDuration(data.lastDeployStartedAt) : OC.formatDuration(data.lastDeployDurationSeconds)}</dd>
        <dt>Resultado</dt><dd>${OC.plain(lastDeploy.result || data.lastDeployResult, "Aguardando atualização")}</dd>
        <dt>DB (runtime)</dt><dd>${OC.plain(runtime.database, "—")}</dd>
      </dl>
      ${OC.servicesHtml(data.services)}
      <p class="drawer-hint">Rollback reverte código; migrações de schema podem exigir ação manual.</p>
      <div class="drawer-actions">
        ${data.githubCommitUrl ? `<a class="btn btn-secondary btn-sm" href="${data.githubCommitUrl}" target="_blank" rel="noopener">GitHub</a>` : ""}
        ${linkBtn("frontend", "Frontend")}
        ${linkBtn("api", "API")}
        ${linkBtn("health", "Health")}
        <button type="button" class="btn btn-danger btn-sm" id="drawer-btn-rollback" ${!canRollback || busy ? "disabled" : ""}>Rollback</button>
        <button type="button" class="btn btn-primary btn-sm" id="drawer-btn-redeploy" ${busy ? "disabled" : ""}>Re-deploy</button>
        <button type="button" class="btn btn-secondary btn-sm" data-copy-sha="${OC.escapeHtml(row.sha)}">Copiar SHA</button>
      </div>
      <div class="drawer-service-actions">
        <span class="drawer-actions-label">Reiniciar serviço:</span>
        <button type="button" class="btn btn-secondary btn-sm drawer-restart" data-service="backend" ${busy ? "disabled" : ""}>Backend</button>
        <button type="button" class="btn btn-secondary btn-sm drawer-restart" data-service="frontend" ${busy ? "disabled" : ""}>Frontend</button>
        <button type="button" class="btn btn-secondary btn-sm drawer-restart" data-service="all" ${busy ? "disabled" : ""}>Todos</button>
        ${row.environment === "DEV" ? '<button type="button" class="btn btn-secondary btn-sm" id="drawer-btn-promote-hom" ' + (promoteDisabled ? "disabled" : "") + ' title="' + (homBusy ? "HOM em deploy" : "Promover SHA ativo de DEV para HOM") + '">Promover → HOM</button>' : ""}
      </div>
    </div>
    <div class="drawer-tab-panel ${OC.drawerActiveTab === "database" ? "is-active" : ""}" data-drawer-panel="database" ${OC.drawerActiveTab === "database" ? "" : "hidden"}>
      <div id="drawer-database"><p class="loading-inline">Carregando métricas do banco…</p></div>
    </div>
    <div class="drawer-tab-panel ${OC.drawerActiveTab === "env" ? "is-active" : ""}" data-drawer-panel="env" ${OC.drawerActiveTab === "env" ? "" : "hidden"}>
      <div id="drawer-env"><p class="loading-inline">Carregando variáveis…</p></div>
    </div>
    <div class="drawer-tab-panel ${OC.drawerActiveTab === "logs" ? "is-active" : ""}" data-drawer-panel="logs" ${OC.drawerActiveTab === "logs" ? "" : "hidden"}>
      <div id="drawer-logs"><p class="loading-inline">Carregando logs…</p></div>
    </div>
  `;

  body.querySelectorAll("[data-drawer-tab]").forEach((tab) => {
    tab.addEventListener("click", () => {
      OC.drawerActiveTab = tab.getAttribute("data-drawer-tab") || "summary";
      OC.setDrawerWide?.(OC.drawerActiveTab === "logs");
      body.querySelectorAll(".drawer-tab").forEach((t) => t.classList.toggle("is-active", t === tab));
      const target = OC.drawerActiveTab;
      body.querySelectorAll("[data-drawer-panel]").forEach((p) => {
        const active = p.getAttribute("data-drawer-panel") === target;
        p.classList.toggle("is-active", active);
        p.hidden = !active;
      });
      if (target === "database") OC.loadDrawerDatabase(row.environment);
      if (target === "env") OC.loadDrawerEnv(row.environment);
      if (target === "logs") OC.loadDrawerLogs(row.environment, row.sha, row.runId);
    });
  });

  body.querySelector("[data-copy-sha]")?.addEventListener("click", (e) => {
    const sha = e.currentTarget.getAttribute("data-copy-sha");
    if (sha && navigator.clipboard) navigator.clipboard.writeText(sha).catch(() => {});
  });

  document.getElementById("drawer-btn-rollback")?.addEventListener("click", async () => {
    const btn = document.getElementById("drawer-btn-rollback");
    btn.disabled = true;
    await OC.runRollback(row.environment, "", (err, result) => {
      OC.afterActionRefresh(err, result, btn);
      if (!err && result?.ok) OC.closeDrawer();
    });
  });

  document.getElementById("drawer-btn-redeploy")?.addEventListener("click", async () => {
    const btn = document.getElementById("drawer-btn-redeploy");
    btn.disabled = true;
    await OC.runRedeploy(row.environment, row.sha !== "—" ? row.sha : "", (err, result) => {
      OC.afterActionRefresh(err, result, btn);
      if (!err && result?.ok) OC.closeDrawer();
    });
  });

  document.getElementById("drawer-btn-promote-hom")?.addEventListener("click", async () => {
    const btn = document.getElementById("drawer-btn-promote-hom");
    if (!btn || btn.disabled) return;
    btn.disabled = true;
    btn.classList.add("is-loading");
    await OC.runPromote("DEV", "HOM", (err, result) => {
      OC.afterActionRefresh(err, result, btn);
    });
  });

  body.querySelectorAll(".drawer-restart").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const service = btn.getAttribute("data-service") || "all";
      btn.disabled = true;
      await OC.runRestartService(row.environment, service, (err, result) => {
        OC.afterActionRefresh(err, result, btn);
      });
    });
  });

  if (OC.drawerActiveTab === "logs") {
    OC.loadDrawerLogs(row.environment, row.sha, row.runId);
  }
};

OC.loadDrawerDatabase = async function loadDrawerDatabase(env) {
  const panel = document.getElementById("drawer-database");
  if (!panel) return;
  try {
    const data = await OC.fetchJson(`/api/v1/database/${env}`);
    if (!data.ok && data.error) {
      panel.innerHTML = `<p class="error-msg">${OC.escapeHtml(data.error)}</p>`;
      return;
    }
    const conns = data.connections || {};
    const byState = conns.byState || {};
    const locks = (data.blockingLocks || [])
      .map((l) => `<li>PID ${l.blockedPid} bloqueado por ${l.blockingPid}: ${OC.escapeHtml(l.query)}</li>`)
      .join("");
    const slow = (data.slowQueries || [])
      .map((q) => `<li>${OC.escapeHtml(q.query)} (${q.calls} calls, ${q.meanMs}ms médio)</li>`)
      .join("");
    const mig = data.migrations || {};
    const sizeLine = data.sizeHuman
      ? `<p>${OC.escapeHtml(data.sizeHuman)}</p>`
      : "";
    const connTotal =
      conns.total != null
        ? `<p>Total: ${conns.total}</p>`
        : "";
    const stateLines = Object.keys(byState).length
      ? Object.keys(byState)
          .map((k) => `<li>${OC.escapeHtml(k)}: ${byState[k]}</li>`)
          .join("")
      : "";
    const connStates = stateLines ? `<ul>${stateLines}</ul>` : "";
    const migLine =
      mig.pending != null ? `<p>Pendentes: ${mig.pending}</p>` : "";
    panel.innerHTML = `
      <div class="db-cards">
        <div class="db-card"><h4>Banco</h4><p><code>${OC.escapeHtml(data.database || "—")}</code></p>${sizeLine}</div>
        ${connTotal || connStates ? `<div class="db-card"><h4>Conexões</h4>${connTotal}${connStates}</div>` : ""}
        ${migLine ? `<div class="db-card"><h4>Migrações</h4>${migLine}</div>` : ""}
      </div>
      ${locks ? `<h4>Locks bloqueantes</h4><ul>${locks}</ul>` : ""}
      ${slow ? `<h4>Queries lentas</h4><ul>${slow}</ul>` : ""}
      ${data.slowQueriesNote ? `<p class="drawer-hint">${OC.escapeHtml(data.slowQueriesNote)}</p>` : ""}
      ${mig.output ? `<h4>showmigrations</h4><pre class="detail-pre">${OC.escapeHtml(mig.output)}</pre>` : ""}
    `;
  } catch (err) {
    panel.innerHTML = `<p class="error-msg">${OC.escapeHtml(err.message)}</p>`;
    if (err.code === 401 && OC.onUnauthorized) OC.onUnauthorized();
  }
};

OC.loadDrawerEnv = async function loadDrawerEnv(env) {
  const panel = document.getElementById("drawer-env");
  if (!panel) return;
  try {
    const data = await OC.fetchJson(`/api/v1/env/${env}`);
    const renderTable = (title, vars) => {
      const keys = Object.keys(vars || {}).sort();
      const rows = keys
        .map((key) => {
          const item = vars[key];
          const masked = item.masked ? " 🔒" : "";
          return `<tr><td><code>${OC.escapeHtml(key)}</code>${masked}</td><td><input class="env-input" data-scope="${title}" data-key="${OC.escapeHtml(key)}" value="${OC.escapeHtml(item.value)}" ${item.masked ? 'type="password"' : ""} /></td></tr>`;
        })
        .join("");
      return `<h4>${title}</h4><table class="env-table"><tbody data-env-tbody="${title}">${rows}</tbody></table>
        <button type="button" class="btn btn-secondary btn-sm env-add-var-btn" data-add-var="${title}">+ Nova variável</button>`;
    };
    panel.innerHTML = `
      ${renderTable("backend", data.backend)}
      ${renderTable("frontend", data.frontend)}
      <div class="drawer-actions">
        <button type="button" class="btn btn-primary btn-sm" id="drawer-env-save">Salvar e sincronizar</button>
      </div>
      <p class="drawer-hint">Segredos mascarados não são alterados se mantiver ••••••••</p>
    `;
    OC.bindEnvVarInputs(panel, () => {});
    document.getElementById("drawer-env-save")?.addEventListener("click", async () => {
      let payload;
      try {
        payload = OC.collectEnvPayload(panel);
      } catch (err) {
        window.alert(err.message);
        return;
      }
      if (env === "MAIN") {
        if (!window.confirm("Confirmar alterações em MAIN?")) return;
        payload.confirmMain = true;
      }
      try {
        await OC.putJson(`/api/v1/env/${env}`, payload);
        window.alert("Variáveis atualizadas.");
        OC.loadDrawerEnv(env);
      } catch (err) {
        window.alert(err.message || "Falha ao salvar");
      }
    });
  } catch (err) {
    panel.innerHTML = `<p class="error-msg">${OC.escapeHtml(err.message)}</p>`;
    if (err.code === 401 && OC.onUnauthorized) OC.onUnauthorized();
  }
};

OC.loadDrawerLogs = async function loadDrawerLogs(env, sha, runId) {
  const panel = document.getElementById("drawer-logs");
  if (!panel) return;

  const seq = ++OC.drawerLogsLoadSeq;
  panel.innerHTML = `<p class="loading-inline">Carregando logs…</p>`;

  try {
    const query = sha && sha !== "—" && sha !== "unknown" ? `?sha=${encodeURIComponent(sha)}` : "";
    const data = await OC.fetchJson(`/api/v1/commits/${env}${query}`);
    if (seq !== OC.drawerLogsLoadSeq) return;

    const gitLog = data.gitLog || "(sem dados git)";
    const gitShow = data.gitShowStat || "";
    const deployLines = (data.deployLogExcerpt || []).join("\n");
    const syncLines = (data.syncLogExcerpt || []).join("\n");

    let runBlock = "";
    let resolvedRunId = runId || "";
    if (!resolvedRunId && sha && sha !== "—") {
      try {
        const bySha = await OC.fetchJson(`/api/v1/runs/${env}?sha=${encodeURIComponent(sha)}`);
        if (bySha.found) resolvedRunId = bySha.runId;
      } catch {
        /* ignore */
      }
    }
    if (seq !== OC.drawerLogsLoadSeq) return;

    if (resolvedRunId) {
      try {
        const runData = await OC.fetchJson(`/api/v1/runs/${env}?runId=${encodeURIComponent(resolvedRunId)}`);
        if (seq !== OC.drawerLogsLoadSeq) return;
        if (runData.found) {
          const summary = runData.summary || {};
          const failureHtml = OC.deployFailureHtml ? OC.deployFailureHtml(runData.lastError, runData) : "";
          const timelineHtml = OC.renderRunLogsTimeline
            ? OC.renderRunLogsTimeline(runData, {
                expandedSteps: OC.detailLogsExpandedSteps,
                scrollKeyPrefix: "detail:",
              })
            : "";
          runBlock = `
            <div class="drawer-run-block">
              <div class="drawer-run-head">
                <h3>Run ${OC.escapeHtml(resolvedRunId)}</h3>
                <button type="button" class="btn btn-secondary btn-sm" data-copy-run-log="${OC.escapeHtml(resolvedRunId)}">Copiar log completo</button>
              </div>
              ${summary.branch ? `<p class="drawer-run-meta">Branch: <code>${OC.escapeHtml(summary.branch)}</code> · ${OC.escapeHtml(summary.fromSha || "—")} → ${OC.escapeHtml(summary.toSha || "—")}</p>` : ""}
              ${failureHtml}
              <div class="deploy-timeline drawer-run-timeline" aria-label="Logs por etapa">${timelineHtml}</div>
            </div>`;
          panel.dataset.runLogPayload = JSON.stringify(
            Object.fromEntries(
              Object.entries(runData.logs || {}).map(([k, v]) => [k, (v?.lines || v || []).join("\n")])
            )
          );
        }
      } catch {
        runBlock = "";
      }
    }

    if (seq !== OC.drawerLogsLoadSeq) return;

    const deployFallback = deployLines
      ? `<h4>Deploy log (legado)</h4>${OC.renderPlainTextLogHtml(deployLines, 200, "detail:deploy-legacy")}`
      : `<p class="empty-hint">Nenhum trecho de deploy encontrado para este commit.</p>`;

    panel.innerHTML = `
      ${runBlock}
      <details class="drawer-log-extra" open>
        <summary>Git e sync</summary>
        <h4>Git — mensagem</h4>
        ${OC.renderPlainTextLogHtml(gitLog, 200, "detail:git-msg")}
        ${gitShow ? `<h4>Git — arquivos</h4>${OC.renderPlainTextLogHtml(gitShow, 300, "detail:git-files")}` : ""}
        ${runBlock ? "" : deployFallback}
        ${syncLines ? `<h4>Sync log</h4>${OC.renderPlainTextLogHtml(syncLines, 200, "detail:sync")}` : ""}
      </details>
    `;

    if (!runBlock && !runId && !resolvedRunId) {
      panel.insertAdjacentHTML(
        "afterbegin",
        `<p class="drawer-hint">Run ID nao disponivel para este evento; exibindo apenas trechos legados por SHA.</p>`
      );
    }

    const timelineRoot = panel.querySelector(".drawer-run-timeline");
    if (timelineRoot) {
      OC.bindTimelineToggle(timelineRoot, OC.detailLogsExpandedSteps);
      OC.bindTimelineLogScroll(timelineRoot, "detail:");
    }

    panel.querySelectorAll(".detail-log-view[data-log-scroll-key]").forEach((el) => {
      const key = el.getAttribute("data-log-scroll-key");
      if (key) OC.applySmartLogScroll(el, key);
    });

    OC.resetDrawerBodyScroll?.();

    panel.querySelector("[data-copy-run-log]")?.addEventListener("click", () => {
      try {
        const payload = panel.dataset.runLogPayload ? JSON.parse(panel.dataset.runLogPayload) : {};
        const text = Object.entries(payload)
          .map(([k, v]) => `=== ${k} ===\n${v}`)
          .join("\n\n");
        if (text && navigator.clipboard) navigator.clipboard.writeText(text).catch(() => {});
      } catch {
        /* ignore */
      }
    });
  } catch (err) {
    if (seq !== OC.drawerLogsLoadSeq) return;
    panel.innerHTML = `<p class="error-msg">${OC.escapeHtml(err.message)}</p>`;
    if (err.code === 401 && OC.onUnauthorized) OC.onUnauthorized();
  }
};

OC.bindDrawer = function bindDrawer() {
  document.getElementById("drawer-close")?.addEventListener("click", OC.closeDrawer);
  document.getElementById("drawer-overlay")?.addEventListener("click", OC.closeDrawer);
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") OC.closeDrawer();
  });
};
})();
