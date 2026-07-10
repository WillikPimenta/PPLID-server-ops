/* global window */
(function () {
  window.OpsConsole = window.OpsConsole || {};
  const OC = window.OpsConsole;

  OC.expandedEnvCards = OC.expandedEnvCards || new Set();

  OC.rowDurationLabel = function rowDurationLabel(row) {
    if (row.isRunning) return `Rodando · ${OC.formatLiveDuration(row.startedAt)}`;
    if (row.durationSeconds != null) return `${OC.STATUS_META[row.statusKey]?.label || "Concluído"} · ${OC.formatDuration(row.durationSeconds)}`;
    if (row.finishedAt && row.startedAt) {
      const sec = Math.floor((parseTime(row.finishedAt) - parseTime(row.startedAt)) / 1000);
      if (sec > 0) return `Duração · ${OC.formatDuration(sec)}`;
    }
    return OC.STATUS_META[row.statusKey]?.label || "—";
  };

  function parseTime(iso) {
    if (!iso) return 0;
    const t = new Date(iso).getTime();
    return Number.isNaN(t) ? 0 : t;
  }

  function envPrimaryRow(envName, overview) {
    const rows = OC.buildEnvRows(overview);
    return rows.find((r) => r.environment === envName);
  }

  function deployingBannerHtml(name, data) {
    if (OC.deployProgressBannerHtml) return OC.deployProgressBannerHtml(name, data);
    const phase = data.displayPhase || data.phase;
    if (phase !== "deploying") return "";
    return "";
  }

  function blockedDeployBannerHtml(name, data) {
    const blockedSha = data.deployState?.blockedSha || data.blockedSha;
    if (!blockedSha) return "";
    const reason = data.deployState?.blockedReason || data.blockedReason || "deploy_failed";
    const busy = (data.displayPhase || data.phase) === "deploying";
    return `
      <div class="env-deploy-banner env-blocked-banner" data-env-blocked="${name}">
        <div class="env-deploy-banner-head">
          <span class="env-deploy-label">Deploy pausado</span>
          <code class="env-deploy-sha">${OC.escapeHtml(blockedSha)}</code>
          <span class="detail-muted">· ${OC.escapeHtml(reason)}</span>
        </div>
        <p class="detail-muted">O watcher nao repete este commit apos falha. Use o botao abaixo para limpar o bloqueio e publicar o commit mais recente do remoto.</p>
        <button type="button" class="btn btn-primary btn-sm" data-card-action="clear-block" data-env="${name}" ${busy ? "disabled" : ""}>Limpar bloqueio e deployar</button>
      </div>`;
  }

  function envExpandPanelHtml(name, data, isExpanded) {
    const deployedSha = OC.resolveCommitSha(data);
    const previousSha = OC.resolveDeployedSha ? OC.resolveDeployedSha(data) : data.activeSha;
    const repoSha = data.repoSha || data.currentCommit?.sha || data.gitSha || "—";
    const subject = OC.resolveCommitSubject(data);
    const diskMatch =
      !data.deployPending && data.shaInSync !== false && deployedSha !== "—" && repoSha !== "—";
    const phase = data.displayPhase || data.phase;
    const busy = phase === "deploying";
    const timeLabel = busy
      ? `iniciado ${OC.formatTimeShort(data.lastDeployStartedAt || data.deployState?.startedAt)}`
      : `implantado ${OC.formatRelativeTime(data.deployedAt || data.lastDeployFinishedAt)}`;
    const lastGood = data.lastGoodSha || data.deployState?.lastGoodSha || "";
    const canRollback = !busy && !!lastGood;
    const expandClass = isExpanded ? "env-card-expand" : "env-card-expand hidden";

    return `
      <div class="${expandClass}" data-env-expand="${name}">
        <div class="env-expand-panel">
          <dl class="detail-grid env-expand-grid">
            <dt class="detail-label">${busy ? "Alvo" : "Commit"}</dt>
            <dd class="detail-value"><code>${OC.escapeHtml(deployedSha)}</code>${busy && previousSha && previousSha !== "—" && previousSha !== deployedSha ? ` <span class="detail-muted">(anterior ${OC.escapeHtml(previousSha)})</span>` : ""}</dd>
            <dt class="detail-label">Mensagem</dt>
            <dd class="detail-value detail-subject">${OC.escapeHtml(subject || "—")}</dd>
            <dt class="detail-label">${busy ? "Início" : "Implantado"}</dt>
            <dd class="detail-value"><span class="detail-muted">${OC.escapeHtml(timeLabel)}</span></dd>
            <dt class="detail-label">No disco</dt>
            <dd class="detail-value"><code>${OC.escapeHtml(repoSha)}</code> ${diskMatch ? '<span class="disk-ok">✓</span>' : '<span class="disk-warn">≠</span>'}</dd>
          </dl>
          <div class="env-expand-actions">
            <a class="btn btn-secondary btn-sm" href="#/env/${name}">Variáveis</a>
            <a class="btn btn-secondary btn-sm" href="#/database/${name}">Banco</a>
            ${data.githubCommitUrl ? `<a class="btn btn-secondary btn-sm" href="${data.githubCommitUrl}" target="_blank" rel="noopener">GitHub</a>` : ""}
            <button type="button" class="btn btn-outline-danger btn-sm" data-card-action="rollback" data-env="${name}" ${!canRollback ? "disabled" : ""}>Rollback</button>
            <button type="button" class="btn btn-primary btn-sm" data-card-action="redeploy" data-env="${name}" ${busy ? "disabled" : ""}>Re-deploy</button>
          </div>
        </div>
      </div>`;
  }

  OC.envCardFingerprint = function envCardFingerprint(data) {
    if (!data) return "";
    const services = (data.services || [])
      .map((s) => `${s.id}:${s.status}`)
      .join(",");
    return [
      data.displayPhase || data.phase || "",
      data.pipelineStatus || "",
      data.deployState?.status || "",
      data.deployState?.runId || "",
      data.runtime?.reachable ? "1" : "0",
      data.runtime?.status || "",
      data.runtime?.database || "",
      services,
      data.lastDeployMessage || "",
      data.gitSha || data.deployedSha || "",
    ].join("|");
  };

  OC.patchSummaryCard = function patchSummaryCard(name, data, overview) {
    const root = document.getElementById("env-summary");
    if (!root) return false;
    let card = root.querySelector(`[data-env="${name}"]`);
    const statusKey = OC.summaryStatusKey(data);
    const isExpanded = OC.expandedEnvCards.has(name);
    const html = OC.renderSummaryCardHtml(name, data, statusKey, isExpanded);
    if (!card) {
      root.insertAdjacentHTML("beforeend", html);
      return true;
    }
    const fp = card.getAttribute("data-fingerprint") || "";
    const nextFp = OC.envCardFingerprint(data);
    if (fp === nextFp) return false;
    card.outerHTML = html;
    return true;
  };

  OC.renderSummaryCardHtml = function renderSummaryCardHtml(name, data, statusKey, isExpanded) {
    const links = data.links || {};
    const linkChip = (url, label, extraClass) =>
      url
        ? `<a class="link-chip ${extraClass || ""}" href="${url}" target="_blank" rel="noopener">${label}</a>`
        : "";
    return `
        <article class="summary-card status-border-${OC.STATUS_META[statusKey]?.badgeClass || "idle"} ${isExpanded ? "is-config-expanded" : ""}" data-env="${name}" data-fingerprint="${OC.escapeHtml(OC.envCardFingerprint(data))}">
          <header class="summary-card-header">
            <div class="summary-card-title">
              <h3 class="summary-env-name">${name}</h3>
              <span class="summary-env-branch"><code>${OC.escapeHtml(data.branch || "—")}</code></span>
            </div>
            <div class="summary-card-tools">
              ${OC.statusBadgeHtml(statusKey, "summary-badge")}
            </div>
          </header>
          ${blockedDeployBannerHtml(name, data)}
          ${deployingBannerHtml(name, data)}
          <section class="summary-card-services">
            ${OC.servicesHtml(data.services) || `<p class="summary-empty">Sem dados de serviços</p>`}
          </section>
          ${envExpandPanelHtml(name, data, isExpanded)}
          <footer class="summary-card-footer">
            <div class="summary-card-actions-row">
              ${linkChip(links.frontend, "Abrir", "link-chip-primary")}
              ${linkChip(links.health, "Health")}
              ${linkChip(links.api, "API")}
            </div>
            <div class="summary-card-actions-row summary-card-actions-meta">
              <button type="button" class="card-meta-btn ${isExpanded ? "is-active" : ""}" data-card-toggle="config" data-env="${name}" title="Configuração e deploy">
                <span class="card-meta-icon" aria-hidden="true">⚙</span> Configuração
              </button>
              <button type="button" class="card-meta-btn" data-card-toggle="history" data-env="${name}" title="Histórico de deploys">
                <span class="card-meta-icon" aria-hidden="true">🕘</span> Histórico
              </button>
            </div>
          </footer>
        </article>`;
  };

  OC.renderSummaryCards = function renderSummaryCards(overview, options = {}) {
    const root = document.getElementById("env-summary");
    if (!root) return;

    const environments = overview?.environments || {};
    const incremental = options.incremental === true;
    let rebound = false;

    if (incremental) {
      OC.ENV_ORDER.filter((name) => environments[name]).forEach((name) => {
        if (OC.patchSummaryCard(name, environments[name], overview)) {
          rebound = true;
        }
      });
    } else {
      root.innerHTML = OC.ENV_ORDER.filter((name) => environments[name])
        .map((name) => {
          const data = environments[name];
          const statusKey = OC.summaryStatusKey(data);
          const isExpanded = OC.expandedEnvCards.has(name);
          return OC.renderSummaryCardHtml(name, data, statusKey, isExpanded);
        })
        .join("");
      rebound = true;
    }

    if (rebound || !incremental) {
      OC.bindEnvCardEvents(overview);
      if (OC.bindDeployProgressEvents) OC.bindDeployProgressEvents();
      if (OC.bindDeployDrawerEvents) OC.bindDeployDrawerEvents();
      if (OC.syncDeployProgress) OC.syncDeployProgress(overview);
    }
  };

  OC.bindEnvCardEvents = function bindEnvCardEvents(overview) {
    document.querySelectorAll("[data-card-toggle]").forEach((btn) => {
      btn.addEventListener("click", (e) => {
        e.stopPropagation();
        const env = btn.getAttribute("data-env");
        const mode = btn.getAttribute("data-card-toggle");
        if (mode === "config") {
          OC.toggleEnvConfigExpand(env);
        } else if (mode === "history") {
          OC.openEnvHistoryDrawer(env, overview, btn);
        }
      });
    });

    document.querySelectorAll("[data-card-action]").forEach((btn) => {
      btn.addEventListener("click", async (e) => {
        e.stopPropagation();
        const action = btn.getAttribute("data-card-action");
        const env = btn.getAttribute("data-env");
        btn.disabled = true;
        if (action === "rollback") {
          await OC.runRollback(env, "", (err, result) => OC.afterActionRefresh(err, result, btn));
        } else if (action === "redeploy") {
          await OC.runRedeploy(env, "", (err, result) => OC.afterActionRefresh(err, result, btn));
        } else if (action === "clear-block") {
          await OC.runClearBlockRedeploy(env, (err, result) => OC.afterActionRefresh(err, result, btn));
        }
      });
    });
  };

  OC.toggleEnvConfigExpand = function toggleEnvConfigExpand(envName) {
    const panel = document.querySelector(`[data-env-expand="${envName}"]`);
    const card = document.querySelector(`.summary-card[data-env="${envName}"]`);
    const btn = document.querySelector(`[data-card-toggle="config"][data-env="${envName}"]`);
    if (!panel || !card) return;

    const willExpand = panel.classList.contains("hidden");
    if (willExpand) {
      OC.expandedEnvCards.add(envName);
      panel.classList.remove("hidden");
      card.classList.add("is-config-expanded");
      btn?.classList.add("is-active");
    } else {
      OC.expandedEnvCards.delete(envName);
      panel.classList.add("hidden");
      card.classList.remove("is-config-expanded");
      btn?.classList.remove("is-active");
    }
  };

  OC.renderHistoryDrawerSkeleton = function renderHistoryDrawerSkeleton(envName) {
    const body = document.getElementById("drawer-body");
    if (!body) return;
    body.innerHTML = `
      <div class="history-drawer-loading">
        <div class="loading-spinner"></div>
        <p>Carregando histórico de ${OC.escapeHtml(envName)}…</p>
      </div>`;
  };

  OC.openEnvHistoryDrawer = async function openEnvHistoryDrawer(envName, overview, triggerBtn) {
    overview = overview || OC.lastOverview;
    if (!overview) return;

    OC.drawerMode = "history";
    OC.historyDrawerEnv = envName;

    if (triggerBtn) {
      triggerBtn.disabled = true;
      triggerBtn.classList.add("is-loading");
    }

    const overlay = document.getElementById("drawer-overlay");
    const drawer = document.getElementById("deployment-drawer");
    const title = document.getElementById("drawer-title");
    if (title) title.textContent = `Histórico — ${envName}`;

    overlay?.classList.remove("hidden");
    overlay?.setAttribute("aria-hidden", "false");
    drawer?.classList.remove("hidden");
    drawer?.classList.add("open");
    drawer?.setAttribute("aria-hidden", "false");
    document.body.classList.add("drawer-open");
    OC.setDrawerWide?.(true);

    OC.renderHistoryDrawerSkeleton(envName);

    try {
      let rows = OC.getHistoryForEnv(overview, envName);
      if (OC.historyRowsNeedEnrich(rows)) {
        rows = await OC.enrichEventSubjects(rows);
      }
      OC.renderHistoryDrawerContent(envName, rows, overview);
    OC.resetDrawerBodyScroll?.();
    } finally {
      if (triggerBtn) {
        triggerBtn.disabled = false;
        triggerBtn.classList.remove("is-loading");
      }
    }
  };

  OC.drawerHistoryRowHtml = function drawerHistoryRowHtml(row, envData) {
    const data = envData || row.envData || {};
    const subject = OC.truncate(row.subject || row.message || "", 72);
    const time = row.startedAt ? OC.formatDate(row.startedAt) : "—";
    const isActive = OC.isHistoryRowActive ? OC.isHistoryRowActive(row, data) : false;
    const durationClass = row.isRunning ? "row-duration live-duration" : "row-duration";
    const durationAttr = row.isRunning && row.startedAt ? ` data-started="${OC.escapeHtml(row.startedAt)}"` : "";
    const logsBtn = row.runId
      ? `<button type="button" class="btn btn-secondary btn-sm" data-action="run-logs" data-run-id="${OC.escapeHtml(row.runId)}">Ver logs</button>`
      : `<button type="button" class="btn btn-secondary btn-sm" disabled title="Run ID indisponivel para este evento">Ver logs</button>`;

    return `<article class="history-row drawer-history-row ${row.isRunning ? "is-running" : ""} ${isActive ? "is-active-deploy" : ""}" data-row-id="${OC.escapeHtml(row.id)}" data-env="${OC.escapeHtml(row.environment)}">
      <div class="history-row-main">
        <div class="history-row-top">
          ${OC.statusBadgeHtml(row.statusKey)}
          ${row.isRunning ? '<span class="running-badge">Rodando</span>' : ""}
          ${isActive ? '<span class="active-badge">Ativo</span>' : ""}
        </div>
        <div class="history-commit-block">
          <code class="history-sha-prominent">${OC.escapeHtml(row.sha || "—")}</code>
          ${subject ? `<p class="history-subject">${OC.escapeHtml(subject)}</p>` : ""}
        </div>
        <p class="history-meta">
          <span>${OC.escapeHtml(row.author || "—")}</span>
          <span class="meta-sep">·</span>
          <span>${OC.escapeHtml(time)}</span>
          <span class="meta-sep">·</span>
          <span class="${durationClass}"${durationAttr}>${OC.escapeHtml(OC.rowDurationLabel(row))}</span>
        </p>
      </div>
      <div class="history-row-actions">
        ${logsBtn}
        <button type="button" class="btn btn-secondary btn-sm" data-action="details">Detalhes</button>
      </div>
    </article>`;
  };

  OC.patchHistoryDrawerRows = function patchHistoryDrawerRows(envName, rows, overview) {
    const list = document.querySelector(".drawer-history-list");
    if (!list) return false;

    rows.forEach((row) => {
      const article = list.querySelector(`[data-row-id="${row.id}"]`);
      if (!article) return;
      const badgeHost = article.querySelector(".history-row-top");
      if (badgeHost) {
        const data = overview?.environments?.[envName] || row.envData || {};
        const isActive = OC.isHistoryRowActive ? OC.isHistoryRowActive(row, data) : false;
        article.classList.toggle("is-running", !!row.isRunning);
        article.classList.toggle("is-active-deploy", !!isActive);
        badgeHost.innerHTML = `${OC.statusBadgeHtml(row.statusKey)}${row.isRunning ? '<span class="running-badge">Rodando</span>' : ""}${isActive ? '<span class="active-badge">Ativo</span>' : ""}`;
      }
      const dur = article.querySelector(".row-duration, .live-duration");
      if (dur) {
        dur.textContent = OC.rowDurationLabel(row);
        dur.className = row.isRunning ? "row-duration live-duration" : "row-duration";
        if (row.isRunning && row.startedAt) dur.setAttribute("data-started", row.startedAt);
        else dur.removeAttribute("data-started");
      }
    });

    const countEl = document.querySelector(".drawer-history-count");
    if (countEl) countEl.textContent = `${rows.length} evento(s)`;
    return true;
  };

  OC.renderHistoryDrawerContent = function renderHistoryDrawerContent(envName, rows, overview) {
    const body = document.getElementById("drawer-body");
    if (!body) return;

    const data = overview?.environments?.[envName] || {};
    const listEl = body.querySelector(".drawer-history-list");
    if (listEl && (OC.isAnyLogScrollLocked?.("history:") || OC.deployLogScrollState?.["history:list"]?.userLocked)) {
      if (OC.patchHistoryDrawerRows(envName, rows, overview)) return;
    }

    OC.captureLogScrollState?.(body, "history:body");
    OC.captureLogScrollState?.(listEl, "history:list");

    const runningRows = rows.filter((r) => r.isRunning);
    const otherRows = rows.filter((r) => !r.isRunning);
    const orderedRows = [...runningRows, ...otherRows];

    const listHtml = orderedRows.length
      ? orderedRows.map((row) => OC.drawerHistoryRowHtml(row, data)).join("")
      : `<p class="empty-hint">Nenhum evento de deploy registrado para ${OC.escapeHtml(envName)}.</p>`;

    body.innerHTML = `
      <div class="drawer-history-header">
        ${OC.statusBadgeHtml(OC.summaryStatusKey(data))}
        <span class="drawer-history-env">${OC.escapeHtml(envName)}</span>
        <span class="drawer-history-count">${rows.length} evento(s)</span>
      </div>
      ${OC.isDeployInProgress?.(data) ? `<div class="drawer-live-banner"><p>Deploy em andamento em ${OC.escapeHtml(envName)}.</p><button type="button" class="btn btn-primary btn-sm" data-open-deploy-drawer="${OC.escapeHtml(envName)}">Acompanhar ao vivo</button></div>` : ""}
      <label class="technical-toggle drawer-history-filter">
        <input type="checkbox" id="drawer-filter-technical" ${OC.showTechnicalActivity ? "checked" : ""} />
        Exibir logs técnicos
      </label>
      <div class="history-list drawer-history-list" role="list" data-log-scroll-key="history:list">${listHtml}</div>
    `;

    document.getElementById("drawer-filter-technical")?.addEventListener("change", async (e) => {
      OC.showTechnicalActivity = !!e.target.checked;
      if (OC.lastOverview) {
        OC.lastDeploymentRows = OC.buildDeploymentRows(OC.lastOverview);
        const refreshed = OC.getHistoryForEnv(OC.lastOverview, envName);
        const enriched = OC.historyRowsNeedEnrich(refreshed)
          ? await OC.enrichEventSubjects(refreshed)
          : refreshed;
        OC.renderHistoryDrawerContent(envName, enriched, OC.lastOverview);
      }
    });

    const list = body.querySelector(".drawer-history-list");
    if (list && orderedRows.length) {
      OC.bindHistoryRowEvents(list, orderedRows);
      list.querySelectorAll('[data-action="run-logs"]').forEach((btn) => {
        btn.addEventListener("click", async (e) => {
          e.stopPropagation();
          if (btn.disabled) return;
          const article = e.target.closest(".history-row");
          const id = article?.getAttribute("data-row-id");
          const row = orderedRows.find((r) => r.id === id);
          if (!row) return;
          OC.openDrawer(row, { keepTab: false });
          OC.drawerActiveTab = "logs";
          OC.setDrawerWide?.(true);
          const tab = document.querySelector('[data-drawer-tab="logs"]');
          tab?.click();
        });
      });
    }

    OC.applySmartLogScroll?.(body, "history:body");
    OC.applySmartLogScroll?.(list, "history:list");
    if (!OC.deployLogScrollState?.["history:list"]?.userLocked) {
      if (list) list.scrollTop = 0;
      body.scrollTop = 0;
    }
  };

  OC.historyRowHtml = function historyRowHtml(row) {
    const subject = OC.truncate(row.subject || row.message || "", 72);
    const data = row.envData || {};
    const isActive = OC.isHistoryRowActive ? OC.isHistoryRowActive(row, data) : false;
    const timeRange =
      row.startedAt && row.finishedAt
        ? `${OC.formatTimeShort(row.startedAt)} → ${OC.formatTimeShort(row.finishedAt)}`
        : row.startedAt
          ? OC.formatTimeShort(row.startedAt)
          : row.finishedAt
            ? OC.formatDate(row.finishedAt)
            : "—";

    const durationClass = row.isRunning ? "row-duration live-duration" : "row-duration";
    const durationAttr = row.isRunning && row.startedAt ? ` data-started="${OC.escapeHtml(row.startedAt)}"` : "";

    return `
    <article class="history-row ${row.isRunning ? "is-running" : ""} ${isActive ? "is-active-deploy" : ""}" role="listitem" data-row-id="${OC.escapeHtml(row.id)}" data-env="${OC.escapeHtml(row.environment)}">
      <div class="history-row-main">
        <div class="history-row-top">
          ${OC.statusBadgeHtml(row.statusKey)}
          ${isActive ? '<span class="active-badge">Ativo</span>' : ""}
          <span class="history-env">${OC.escapeHtml(row.environment)}</span>
          <code class="history-branch">${OC.escapeHtml(row.branch)}</code>
          <code class="history-sha">${OC.escapeHtml(row.sha)}</code>
        </div>
        ${subject ? `<p class="history-subject" title="${OC.escapeHtml(row.subject || row.message || "")}">${OC.escapeHtml(subject)}</p>` : ""}
        <p class="history-meta">
          <span>${OC.escapeHtml(row.author || "—")}</span>
          <span class="meta-sep">·</span>
          <span>${OC.escapeHtml(timeRange)}</span>
          <span class="meta-sep">·</span>
          <span class="${durationClass}"${durationAttr}>${OC.escapeHtml(OC.rowDurationLabel(row))}</span>
        </p>
      </div>
      <div class="history-row-actions">
        <button type="button" class="btn btn-secondary btn-sm" data-action="details">Detalhes</button>
        <div class="action-menu-wrap">
          <button type="button" class="btn-icon" data-action="menu" aria-label="Mais ações">⋯</button>
          <div class="action-menu hidden" role="menu">
            ${OC.actionMenuItems(row)}
          </div>
        </div>
      </div>
    </article>
  `;
  };

  OC.actionMenuItems = function actionMenuItems(row) {
    const data = row.envData || {};
    const links = data.links || {};
    const phase = data.displayPhase || data.phase;
    const disabled = phase === "deploying" || phase === "offline";
    const activeSha = data.activeSha || data.deployedSha;
    const canRollback = row.sha && row.sha !== "—" && row.sha !== activeSha;
    const item = (href, label, enabled) =>
      enabled
        ? `<a href="${href}" target="_blank" rel="noopener" role="menuitem">${label}</a>`
        : `<span class="menu-disabled" role="menuitem">${label}</span>`;
    const btn = (action, label, enabled = true) =>
      enabled
        ? `<button type="button" role="menuitem" data-quick-action="${action}" data-env="${OC.escapeHtml(row.environment)}" data-sha="${OC.escapeHtml(row.sha)}">${label}</button>`
        : `<span class="menu-disabled" role="menuitem">${label}</span>`;

    return `
    ${data.githubCommitUrl ? item(data.githubCommitUrl, "Abrir GitHub", !disabled) : ""}
    ${item(links.frontend, "Abrir Frontend", links.frontend && !disabled)}
    ${item(links.api, "Abrir API", links.api && !disabled)}
    ${item(links.health, "Abrir Health", links.health && !disabled)}
    ${btn("rollback", "Rollback para este SHA", canRollback && !disabled)}
    ${btn("redeploy", "Re-deploy deste SHA", row.sha && row.sha !== "—" && !disabled)}
    <button type="button" role="menuitem" data-copy-sha="${OC.escapeHtml(row.sha)}">Copiar SHA</button>
  `;
  };

  OC.renderHistoryList = function renderHistoryList(rows) {
    const list = document.getElementById("deployment-history");
    const empty = document.getElementById("history-empty");
    if (!list || !empty) return;

    if (!rows.length) {
      list.innerHTML = "";
      empty.classList.remove("hidden");
      return;
    }

    empty.classList.add("hidden");
    list.innerHTML = rows.map((row) => OC.historyRowHtml(row)).join("");
    OC.bindHistoryRowEvents(list, rows);
  };

  OC.bindHistoryRowEvents = function bindHistoryRowEvents(list, rows) {
    list.querySelectorAll('[data-action="details"]').forEach((btn) => {
      btn.addEventListener("click", (e) => {
        const article = e.target.closest(".history-row");
        const id = article?.getAttribute("data-row-id");
        const row = rows.find((r) => r.id === id);
        if (row) OC.openDrawer(row);
      });
    });

    list.querySelectorAll('[data-action="menu"]').forEach((btn) => {
      btn.addEventListener("click", (e) => {
        e.stopPropagation();
        const wrap = btn.closest(".action-menu-wrap");
        const menu = wrap?.querySelector(".action-menu");
        document.querySelectorAll(".action-menu").forEach((m) => {
          if (m !== menu) m.classList.add("hidden");
        });
        menu?.classList.toggle("hidden");
      });
    });

    list.querySelectorAll("[data-copy-sha]").forEach((btn) => {
      btn.addEventListener("click", () => {
        const sha = btn.getAttribute("data-copy-sha");
        if (sha && navigator.clipboard) navigator.clipboard.writeText(sha).catch(() => {});
        btn.closest(".action-menu")?.classList.add("hidden");
      });
    });

    list.querySelectorAll("[data-quick-action]").forEach((btn) => {
      btn.addEventListener("click", async () => {
        const action = btn.getAttribute("data-quick-action");
        const env = btn.getAttribute("data-env");
        const sha = btn.getAttribute("data-sha");
        btn.closest(".action-menu")?.classList.add("hidden");
        if (action === "rollback") {
          await OC.runRollback(env, sha, (err, result) => OC.afterActionRefresh(err, result));
        } else if (action === "redeploy") {
          await OC.runRedeploy(env, sha, (err, result) => OC.afterActionRefresh(err, result));
        }
      });
    });

    list.querySelectorAll(".action-menu a").forEach((a) => {
      a.addEventListener("click", () => {
        a.closest(".action-menu")?.classList.add("hidden");
      });
    });
  };

  OC.updateLiveDurations = function updateLiveDurations() {
    document.querySelectorAll(".live-duration[data-started]").forEach((el) => {
      const started = el.getAttribute("data-started");
      if (started) {
        const label = el.classList.contains("row-duration")
          ? `Rodando · ${OC.formatLiveDuration(started)}`
          : OC.formatLiveDuration(started);
        el.textContent = label;
      }
    });
  };

  OC.renderDashboard = function renderDashboard(overview, options = {}) {
    OC.lastOverview = overview;
    OC.lastDeploymentRows = OC.buildDeploymentRows(overview);

    OC.renderSummaryCards(overview, options);

    const logDirEl = document.getElementById("footer-log-dir");
    if (logDirEl && overview?.logDir) {
      logDirEl.textContent = overview.logDir.endsWith("\\") ? overview.logDir : `${overview.logDir}\\`;
    }

    if (OC.selectedRowId && OC.drawerMode === "detail") {
      const still = OC.lastDeploymentRows.find((r) => r.id === OC.selectedRowId);
      if (still) {
        document.querySelectorAll(".history-row.selected").forEach((el) => el.classList.remove("selected"));
        document.querySelector(`.history-row[data-row-id="${OC.selectedRowId}"]`)?.classList.add("selected");
      } else if (!document.getElementById("deployment-drawer")?.classList.contains("open")) {
        OC.closeDrawer();
      }
    }
  };

  OC.refreshDeployView = async function refreshDeployView(overview, options = {}) {
    OC.renderDashboard(overview, options);
    if (OC.drawerMode === "history" && OC.historyDrawerEnv && document.getElementById("deployment-drawer")?.classList.contains("open")) {
      let rows = OC.getHistoryForEnv(overview, OC.historyDrawerEnv);
      if (OC.historyRowsNeedEnrich(rows)) {
        rows = await OC.enrichEventSubjects(rows);
      }
      OC.renderHistoryDrawerContent(OC.historyDrawerEnv, rows, overview);
    }
  };

  OC.setDashboardVisible = function setDashboardVisible(visible) {
    document.getElementById("loading-panel")?.classList.toggle("hidden", visible);
    document.getElementById("dashboard")?.classList.toggle("hidden", !visible);
  };

  OC.setGlobalError = function setGlobalError(message) {
    const el = document.getElementById("global-error");
    if (!el) return;
    if (message) {
      el.textContent = message;
      el.classList.remove("hidden");
      OC.setDashboardVisible(false);
    } else {
      el.classList.add("hidden");
    }
  };
})();
