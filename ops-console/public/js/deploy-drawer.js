/* global window */
(function () {
  window.OpsConsole = window.OpsConsole || {};
  const OC = window.OpsConsole;

  OC.deployDrawerEnv = null;
  OC.deployDrawerPollTimer = null;
  OC.deployDrawerState = {
    runData: null,
    logOffsets: {},
    expandedSteps: new Set(["deps"]),
    expandedLogs: new Set(),
    stepsSignature: "",
  };

  const TIMELINE_GROUPS = OC.DEPLOY_TIMELINE_GROUPS || [];
  const STEP_ICONS = OC.STEP_ICONS || {
    pending: "○",
    running: "●",
    success: "✓",
    warning: "!",
    error: "✗",
    skipped: "–",
  };

  function groupStatus(steps, stepIds) {
    const matched = (steps || []).filter((s) => stepIds.includes(s.id));
    if (!matched.length) return { status: "pending", steps: [] };
    if (matched.some((s) => s.status === "error")) return { status: "error", steps: matched };
    if (matched.some((s) => s.status === "running")) return { status: "running", steps: matched };
    if (matched.every((s) => s.status === "skipped")) return { status: "skipped", steps: matched };
    if (matched.every((s) => ["success", "warning", "skipped"].includes(s.status))) {
      return { status: matched.some((s) => s.status === "warning") ? "warning" : "success", steps: matched };
    }
    if (matched.some((s) => s.status === "success" || s.status === "warning")) return { status: "running", steps: matched };
    return { status: "pending", steps: matched };
  }

  function groupTiming(steps) {
    const started = steps.map((s) => s.startedAt).filter(Boolean).sort()[0];
    const finished = steps
      .map((s) => s.finishedAt)
      .filter(Boolean)
      .sort()
      .reverse()[0];
    const duration = steps.reduce((acc, s) => acc + (Number(s.durationSec) || 0), 0);
    return { started, finished, duration };
  }

  function buildTimelineSection(group, steps, cached) {
    const { status, steps: groupSteps } = groupStatus(steps, group.stepIds);
    const timing = groupTiming(groupSteps);
    const expanded = OC.deployDrawerState.expandedSteps.has(group.id);
    const groupParsed = OC.collectGroupLogs(cached.logs, group);
    const filtered = OC.filterLogsForStepGroup(groupParsed, groupSteps, steps);
    const depsSubphase =
      group.id === "deps" && status === "running"
        ? OC.parseDepsSubphase(filtered === null ? [] : filtered)
        : "";
    const commands = groupSteps
      .filter((s) => s.status === "running" || s.status === "error")
      .map((s) => s.label)
      .join(", ");
    const meta = depsSubphase
      ? OC.escapeHtml(depsSubphase)
      : timing.duration
        ? OC.formatDuration(timing.duration)
        : status === "running"
          ? "em andamento"
          : status;

    return `<section class="deploy-timeline-item deploy-timeline-${status} ${expanded ? "is-expanded" : ""}" data-timeline-group="${group.id}">
        <button type="button" class="deploy-timeline-head" data-timeline-toggle="${group.id}">
          <span class="deploy-timeline-icon">${STEP_ICONS[status] || "○"}</span>
          <span class="deploy-timeline-main">
            <strong>${OC.escapeHtml(group.label)}</strong>
            <span class="deploy-timeline-meta" data-timeline-meta="${group.id}">${meta}</span>
          </span>
          <span class="deploy-timeline-times">
            ${timing.started ? `<span>${OC.formatTimeShort(timing.started)}</span>` : ""}
            ${timing.finished ? `<span>→ ${OC.formatTimeShort(timing.finished)}</span>` : ""}
          </span>
        </button>
        <div class="deploy-timeline-body ${expanded ? "" : "hidden"}">
          <ul class="deploy-timeline-substeps">${groupSteps
            .map(
              (s) =>
                `<li class="deploy-timeline-sub deploy-sub-${s.status || "pending"}">
                  <span>${STEP_ICONS[s.status] || "○"}</span>
                  <span>${OC.escapeHtml(OC.fixMojibake(s.label || s.id))}</span>
                  ${s.durationSec != null ? `<em>${OC.formatDuration(s.durationSec)}</em>` : ""}
                  ${s.error ? `<code>${OC.escapeHtml(OC.truncate(OC.fixMojibake(s.error), 120))}</code>` : ""}
                </li>`
            )
            .join("")}</ul>
          ${commands ? `<p class="deploy-timeline-cmd"><strong>Comando:</strong> ${OC.escapeHtml(commands)}</p>` : ""}
          <div class="deploy-timeline-log" data-log-scroll-key="drawer:${group.id}">${OC.renderStepLogHtml(filtered, 800, undefined, { active: status === "running" })}</div>
        </div>
      </section>`;
  }

  OC.patchDeployTimelineLogs = function patchDeployTimelineLogs(envName, data, runData) {
    const body = document.getElementById("drawer-body");
    if (!body || !body.querySelector(".deploy-timeline")) return false;

    if (OC.isAnyLogScrollLocked("drawer:")) return true;

    const cached = runData || OC.deployDrawerState.runData || {};
    const steps = cached.steps || data.deployProgress?.steps || [];
    const sig = OC.stepsSignature(steps);
    if (sig !== OC.deployDrawerState.stepsSignature) return false;

    body.querySelectorAll(".deploy-timeline-log").forEach((el) => {
      const groupId = el.closest("[data-timeline-group]")?.getAttribute("data-timeline-group");
      if (groupId) OC.captureLogScrollState(el, `drawer:${groupId}`);
    });

    TIMELINE_GROUPS.forEach((group) => {
      const section = body.querySelector(`[data-timeline-group="${group.id}"]`);
      if (!section) return;
      const { status, steps: groupSteps } = groupStatus(steps, group.stepIds);
      const timing = groupTiming(groupSteps);
      const groupParsed = OC.collectGroupLogs(cached.logs, group);
      const filtered = OC.filterLogsForStepGroup(groupParsed, groupSteps, steps);
      const depsSubphase =
        group.id === "deps" && status === "running"
          ? OC.parseDepsSubphase(filtered === null ? [] : filtered)
          : "";
      const meta = depsSubphase
        ? OC.escapeHtml(depsSubphase)
        : timing.duration
          ? OC.formatDuration(timing.duration)
          : status === "running"
            ? "em andamento"
            : status;

      section.className = `deploy-timeline-item deploy-timeline-${status} ${OC.deployDrawerState.expandedSteps.has(group.id) ? "is-expanded" : ""}`;
      const metaEl = section.querySelector(`[data-timeline-meta="${group.id}"]`);
      if (metaEl) metaEl.innerHTML = meta;

      const logEl = section.querySelector(".deploy-timeline-log");
      if (logEl) {
        logEl.innerHTML = OC.renderStepLogHtml(filtered, 800, undefined, { active: status === "running" });
        OC.applySmartLogScroll(logEl, `drawer:${group.id}`);
      }

      const substeps = section.querySelector(".deploy-timeline-substeps");
      if (substeps) {
        substeps.innerHTML = groupSteps
          .map(
            (s) =>
              `<li class="deploy-timeline-sub deploy-sub-${s.status || "pending"}">
                <span>${STEP_ICONS[s.status] || "○"}</span>
                <span>${OC.escapeHtml(OC.fixMojibake(s.label || s.id))}</span>
                ${s.durationSec != null ? `<em>${OC.formatDuration(s.durationSec)}</em>` : ""}
                ${s.error ? `<code>${OC.escapeHtml(OC.truncate(OC.fixMojibake(s.error), 120))}</code>` : ""}
              </li>`
          )
          .join("");
      }
    });

    const progressPct = cached.progressPct ?? data.deployProgress?.progressPct ?? 0;
    const fill = body.querySelector(".deploy-progress-fill");
    const pctEl = body.querySelector(".deploy-progress-pct");
    if (fill) fill.style.width = `${Math.max(5, progressPct)}%`;
    if (pctEl) pctEl.textContent = `${progressPct}% concluido`;

    return true;
  };

  OC.openDeployDrawer = function openDeployDrawer(envName, data) {
    if (!envName || !data) return;
    OC.deployDrawerEnv = envName;
    OC.drawerMode = "deploy-live";
    OC.historyDrawerEnv = null;
    OC.deployDrawerState.expandedSteps = new Set(["deps"]);
    if (data.displayPhase === "failed" || data.pipelineStatus === "failed") {
      OC.deployDrawerState.expandedSteps.add("deps");
    }

    const title = document.getElementById("drawer-title");
    if (title) title.textContent = `Deploy — ${envName}`;

    const overlay = document.getElementById("drawer-overlay");
    const drawer = document.getElementById("deployment-drawer");
    overlay?.classList.remove("hidden");
    overlay?.setAttribute("aria-hidden", "false");
    drawer?.classList.remove("hidden");
    drawer?.classList.add("open");
    drawer?.setAttribute("aria-hidden", "false");
    document.body.classList.add("drawer-open");
    OC.setDrawerWide?.(true);

    OC.renderDeployDrawerContent(envName, data);
    OC.startDeployDrawerPolling();
  };

  OC.closeDeployDrawer = function closeDeployDrawer() {
    OC.stopDeployDrawerPolling();
    OC.deployDrawerEnv = null;
    OC.deployDrawerState.runData = null;
    OC.deployDrawerState.logOffsets = {};
    OC.deployDrawerState.stepsSignature = "";
    if (OC.closeDrawer) OC.closeDrawer();
  };

  OC.renderDeployDrawerContent = function renderDeployDrawerContent(envName, data, runData) {
    const body = document.getElementById("drawer-body");
    if (!body) return;

    const cached = runData || OC.deployDrawerState.runData || OC.deployRunCache[envName] || {};
    const steps = cached.steps || data.deployProgress?.steps || [];
    OC.deployDrawerState.stepsSignature = OC.stepsSignature(steps);
    const failure = cached.failure || null;
    const lastError = cached.lastError || data.deployProgress?.lastError;
    const runId = data.deployState?.runId || cached.runId || "";
    const branch = data.branch || "—";
    const sha = OC.resolveCommitSha ? OC.resolveCommitSha(data) : data.deployState?.targetSha || "—";
    const prevSha = data.deployState?.activeSha || data.deployedSha || "—";
    const summary = data.deploySummary || {};
    const progressPct = cached.progressPct ?? data.deployProgress?.progressPct ?? summary.progressPct ?? 0;
    const isFailed = (data.displayPhase || data.phase) === "failed" || data.pipelineStatus === "failed";

    body.querySelectorAll(".deploy-timeline-log").forEach((el) => {
      const groupId = el.closest("[data-timeline-group]")?.getAttribute("data-timeline-group");
      if (groupId) OC.captureLogScrollState(el, `drawer:${groupId}`);
    });

    const timeline = TIMELINE_GROUPS.map((group) => buildTimelineSection(group, steps, cached)).join("");

    const failurePanel = failure || lastError
      ? `<div class="deploy-drawer-failure">
          <h3>Falha na etapa: ${OC.escapeHtml((failure || lastError).stepLabel || (failure || lastError).step || "—")}</h3>
          ${failure?.rootCause ? `<p><strong>Motivo:</strong> ${OC.escapeHtml(failure.message || "")}</p>` : `<p><strong>Erro:</strong> <code>${OC.escapeHtml(OC.fixMojibake((failure || lastError).message || ""))}</code></p>`}
          ${failure?.command ? `<p><strong>Comando:</strong> <code>${OC.escapeHtml(failure.command)}</code></p>` : ""}
          ${failure?.package ? `<p><strong>Biblioteca:</strong> <code>${OC.escapeHtml(failure.package)}</code></p>` : ""}
          ${failure?.versions?.length ? `<p><strong>Versoes:</strong> ${failure.versions.map((v) => `<code>${OC.escapeHtml(v)}</code>`).join(" ")}</p>` : ""}
          ${failure?.recommendation ? `<p class="deploy-drawer-rec"><strong>Acao recomendada:</strong> ${OC.escapeHtml(failure.recommendation)}</p>` : ""}
          <div class="deploy-drawer-failure-actions">
            <button type="button" class="btn btn-secondary btn-sm" data-copy-deploy-error="${OC.escapeHtml((failure || lastError).message || "")}">Copiar erro</button>
            ${runId ? `<a class="btn btn-secondary btn-sm" href="/api/v1/runs/${envName}/download/${encodeURIComponent(runId)}" download>Baixar logs</a>` : ""}
          </div>
        </div>`
      : "";

    body.innerHTML = `
      <div class="deploy-drawer-summary">
        <p class="deploy-drawer-message">${OC.escapeHtml(summary.message || (isFailed ? "Deploy falhou." : "Deploy em andamento."))}</p>
        <div class="deploy-drawer-meta">
          <span>Branch: <code>${OC.escapeHtml(branch)}</code></span>
          <span>Commit: <code>${OC.escapeHtml(prevSha)}</code> → <code>${OC.escapeHtml(sha)}</code></span>
          ${runId ? `<span>Run: <code>${OC.escapeHtml(runId)}</code></span>` : ""}
        </div>
        <div class="deploy-progress-estimate" aria-hidden="true">
          <div class="deploy-progress-bar"><div class="deploy-progress-fill" style="width:${Math.max(5, progressPct)}%"></div></div>
          <span class="deploy-progress-pct">${progressPct}% concluido</span>
        </div>
        ${OC.deployPhaseStepperHtml ? OC.deployPhaseStepperHtml(data) : ""}
      </div>
      ${failurePanel}
      <div class="deploy-timeline" aria-label="Etapas do deploy">${timeline}</div>
    `;

    OC.bindTimelineToggle(body, OC.deployDrawerState.expandedSteps);

    body.querySelectorAll("[data-copy-deploy-error]").forEach((btn) => {
      btn.addEventListener("click", () => {
        const text = btn.getAttribute("data-copy-deploy-error");
        if (text && navigator.clipboard) navigator.clipboard.writeText(text).catch(() => {});
      });
    });

    OC.bindTimelineLogScroll(body, "drawer:");
  };

  OC.mergeRunLogData = function mergeRunLogData(prev, incoming) {
    if (!prev) return incoming;
    const logs = { ...(prev.logs || {}) };
    Object.entries(incoming.logs || {}).forEach(([key, chunk]) => {
      const prevChunk = logs[key] || { lines: [], parsed: [] };
      logs[key] = {
        ...chunk,
        lines: [...(prevChunk.lines || []), ...(chunk.lines || [])],
        parsed: [...(prevChunk.parsed || []), ...(chunk.parsed || [])],
      };
    });
    return { ...prev, ...incoming, logs };
  };

  OC.buildLogOffsetQuery = function buildLogOffsetQuery(offsets) {
    return Object.entries(offsets || {})
      .map(([k, v]) => {
        const off = OC.normalizeLogOffset ? OC.normalizeLogOffset(v) : { file: Number(v) || 0, sqlite: 0 };
        if (off.sqlite > 0) return `${k}:f${off.file}/s${off.sqlite}`;
        if (off.file > 0) return `${k}:${off.file}`;
        return null;
      })
      .filter(Boolean)
      .join(",");
  };

  OC.normalizeLogOffset = function normalizeLogOffset(offset) {
    if (offset == null) return { file: 0, sqlite: 0 };
    if (typeof offset === "object") {
      return { file: Number(offset.file) || 0, sqlite: Number(offset.sqlite) || 0 };
    }
    return { file: Number(offset) || 0, sqlite: 0 };
  };

  OC.applyLogChunkOffsets = function applyLogChunkOffsets(store, key, chunk) {
    if (!store || !key || !chunk) return;
    store[key] = OC.normalizeLogOffset(store[key]);
    if (chunk.nextFileOffset != null) store[key].file = chunk.nextFileOffset;
    else if (chunk.nextOffset != null && chunk.source === "file") store[key].file = chunk.nextOffset;
    if (chunk.nextSqliteOffset != null) store[key].sqlite = chunk.nextSqliteOffset;
  };

  OC.pollDeployDrawer = async function pollDeployDrawer() {
    const envName = OC.deployDrawerEnv;
    if (!envName) return;
    const data = OC.lastOverview?.environments?.[envName];
    const runId = data?.deployState?.runId;
    if (!runId) return;
    try {
      const offsetQuery = OC.buildLogOffsetQuery(OC.deployDrawerState.logOffsets);
      const url =
        `/api/v1/runs/${envName}?runId=${encodeURIComponent(runId)}` +
        (offsetQuery ? `&logOffset=${encodeURIComponent(offsetQuery)}` : "");
      const incoming = await OC.fetchJson(url);
      if (!incoming.found) return;
      Object.entries(incoming.logs || {}).forEach(([key, chunk]) => {
        OC.applyLogChunkOffsets(OC.deployDrawerState.logOffsets, key, chunk);
      });
      OC.deployDrawerState.runData = OC.mergeRunLogData(OC.deployDrawerState.runData, incoming);
      OC.deployRunCache[envName] = OC.deployDrawerState.runData;
      const patched = OC.patchDeployTimelineLogs(envName, data, OC.deployDrawerState.runData);
      if (!patched) {
        OC.renderDeployDrawerContent(envName, data, OC.deployDrawerState.runData);
      }
    } catch {
      /* retry on next tick */
    }
  };

  OC.startDeployDrawerPolling = function startDeployDrawerPolling() {
    OC.stopDeployDrawerPolling();
    OC.pollDeployDrawer();
    OC.deployDrawerPollTimer = setInterval(() => {
      OC.pollDeployDrawer();
    }, 1000);
  };

  OC.stopDeployDrawerPolling = function stopDeployDrawerPolling() {
    if (OC.deployDrawerPollTimer) {
      clearInterval(OC.deployDrawerPollTimer);
      OC.deployDrawerPollTimer = null;
    }
  };

  OC.bindDeployDrawerEvents = function bindDeployDrawerEvents() {
    document.addEventListener("click", (e) => {
      const btn = e.target.closest("[data-open-deploy-drawer]");
      if (!btn) return;
      e.preventDefault();
      e.stopPropagation();
      const env = btn.getAttribute("data-open-deploy-drawer");
      const data = OC.lastOverview?.environments?.[env];
      if (data) OC.openDeployDrawer(env, data);
    });
  };
})();
