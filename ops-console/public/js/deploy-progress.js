/* global window */
(function () {
  window.OpsConsole = window.OpsConsole || {};
  const OC = window.OpsConsole;

  OC.deployLogPollTimer = null;
  OC.deployLogState = OC.deployLogState || {};
  OC.expandedDeployDetails = OC.expandedDeployDetails || new Set();
  OC.expandedDeployLogs = OC.expandedDeployLogs || new Set();
  OC.deployRunCache = OC.deployRunCache || {};

  const PIPELINE_PHASES = [
    { id: "building", label: "Build" },
    { id: "validating", label: "Validação" },
    { id: "promoting", label: "Publicação" },
  ];

  const PHASE_ORDER = { building: 0, validating: 1, promoting: 2 };
  const PHASE_LOG_MAP = {
    building: "build.log",
    validating: "validate.log",
    promoting: "promote.log",
    failed: "promote.log",
  };
  const PHASE_PROGRESS = { building: 70, validating: 80, promoting: 92, deploying: 50 };
  const DEPS_SUBPHASE_PATTERNS = [
    { pattern: /pip_progress:\s*(?:collecting|downloading|installing)\s+(\S+)/i, label: (m) => `pip: ${m[1]}` },
    { pattern: /^(?:Collecting|Downloading|Installing)\s+(\S+)/i, label: (m) => `pip: ${m[1]}` },
    { pattern: /deps_skip|deps inalteradas/i, label: "Dependencias inalteradas (skip)" },
    { pattern: /venv_clone/i, label: "Clonando venv" },
    { pattern: /Criando venv/i, label: "Criando venv" },
    { pattern: /pip_requirements/i, label: "pip install -r requirements" },
    { pattern: /pip_automacoes/i, label: "pip install -e automacoes" },
    { pattern: /frontend_skip/i, label: "Frontend inalterado (skip)" },
  ];
  const DEPLOY_STEP_ORDER = [
    "prepare",
    "git_fetch",
    "deps_backend",
    "build_backend",
    "build_frontend",
    "validate",
    "restart_services",
    "health_check",
    "publish_done",
  ];
  const LOG_SCROLL_THRESHOLD = 40;
  OC.DEPLOY_TIMELINE_GROUPS = [
    {
      id: "checkout",
      label: "Checkout do codigo",
      stepIds: ["prepare", "git_fetch"],
      logFiles: ["pipeline.log", "build.log"],
    },
    { id: "deps", label: "Instalacao de dependencias", stepIds: ["deps_backend"], logFile: "build.log" },
    { id: "build", label: "Build", stepIds: ["build_backend", "build_frontend"], logFile: "build.log" },
    { id: "validate", label: "Validacoes", stepIds: ["validate"], logFile: "validate.log" },
    {
      id: "publish",
      label: "Publicacao",
      stepIds: ["restart_services", "health_check", "publish_done"],
      logFile: "promote.log",
    },
  ];
  const STEP_ICONS = {
    pending: "○",
    running: "●",
    success: "✓",
    warning: "!",
    error: "✗",
    skipped: "–",
  };

  OC.parseDepsSubphase = function parseDepsSubphase(parsed) {
    if (!parsed?.length) return "";
    for (let i = parsed.length - 1; i >= 0; i -= 1) {
      const text = String(parsed[i].text || parsed[i].raw || "");
      for (const item of DEPS_SUBPHASE_PATTERNS) {
        const match = text.match(item.pattern);
        if (match) {
          return typeof item.label === "function" ? item.label(match) : item.label;
        }
      }
    }
    return "";
  };

  OC.deployLogScrollState = OC.deployLogScrollState || {};

  OC.parseLogTimestamp = function parseLogTimestamp(timeStr) {
    if (!timeStr) return NaN;
    const normalized = String(timeStr).trim().replace(" ", "T");
    const parsed = Date.parse(normalized);
    return Number.isNaN(parsed) ? NaN : parsed;
  };

  OC.parseStepTimestamp = function parseStepTimestamp(isoStr) {
    if (!isoStr) return NaN;
    const parsed = Date.parse(isoStr);
    return Number.isNaN(parsed) ? NaN : parsed;
  };

  OC.findNextStepOnLogFile = function findNextStepOnLogFile(step, allSteps) {
    const stepMap = Object.fromEntries((allSteps || []).map((s) => [s.id, s]));
    const idx = DEPLOY_STEP_ORDER.indexOf(step.id);
    if (idx < 0) return null;
    for (let i = idx + 1; i < DEPLOY_STEP_ORDER.length; i += 1) {
      const candidate = stepMap[DEPLOY_STEP_ORDER[i]];
      if (candidate && candidate.logFile === step.logFile) return candidate;
    }
    return null;
  };

  OC.getStepLogWindow = function getStepLogWindow(step, allSteps) {
    if (!step?.startedAt) return null;
    const start = OC.parseStepTimestamp(step.startedAt);
    if (Number.isNaN(start)) return null;
    const next = OC.findNextStepOnLogFile(step, allSteps);
    let endExclusive;
    if (next?.startedAt) {
      endExclusive = OC.parseStepTimestamp(next.startedAt);
    } else if (step.finishedAt) {
      endExclusive = OC.parseStepTimestamp(step.finishedAt) + 1000;
    } else {
      endExclusive = Date.now() + 1000;
    }
    if (Number.isNaN(endExclusive) || endExclusive <= start) {
      endExclusive = start + 1000;
    }
    return { start, endExclusive };
  };

  OC.filterLogsForStepGroup = function filterLogsForStepGroup(parsed, groupSteps, allSteps) {
    if (!groupSteps?.length) return [];
    if (groupSteps.every((s) => !s.startedAt)) return null;
    const toleranceMs = 1000;
    const seen = new Set();
    const filtered = [];
    groupSteps.forEach((step) => {
      const window = OC.getStepLogWindow(step, allSteps);
      if (!window) return;
      (parsed || []).forEach((row, idx) => {
        const key = `${row.time || ""}|${row.text || row.raw || ""}|${idx}`;
        if (seen.has(key)) return;
        const ts = OC.parseLogTimestamp(row.time);
        if (Number.isNaN(ts)) return;
        if (ts >= window.start - toleranceMs && ts < window.endExclusive + toleranceMs) {
          seen.add(key);
          filtered.push(row);
        }
      });
    });
    filtered.sort((a, b) => OC.parseLogTimestamp(a.time) - OC.parseLogTimestamp(b.time));
    return filtered;
  };

  OC.collectGroupLogs = function collectGroupLogs(logs, group) {
    const logFiles = group.logFiles || (group.logFile ? [group.logFile] : []);
    const combined = [];
    logFiles.forEach((name) => {
      (logs?.[name]?.parsed || []).forEach((line) => combined.push(line));
    });
    combined.sort((a, b) => OC.parseLogTimestamp(a.time) - OC.parseLogTimestamp(b.time));
    return combined;
  };

  OC.collapseDuplicateLogLines = function collapseDuplicateLogLines(parsed) {
    if (!parsed?.length) return [];
    const groups = [];
    let current = null;

    const fingerprint = (row) => {
      const level = String(row.level || "INFO").toUpperCase();
      const text = String(row.text || row.raw || "").trim();
      return `${level}|${text}`;
    };

    parsed.forEach((row) => {
      const key = fingerprint(row);
      if (current && current.key === key) {
        current.count += 1;
        current.lastTime = row.time || current.lastTime;
      } else {
        if (current) groups.push(current);
        current = {
          key,
          time: row.time || "",
          firstTime: row.time || "",
          lastTime: row.time || "",
          level: row.level || "INFO",
          text: row.text || row.raw || "",
          count: 1,
        };
      }
    });
    if (current) groups.push(current);
    return groups;
  };

  OC.renderStepLogHtml = function renderStepLogHtml(parsed, limit, emptyMessage, options) {
    if (parsed === null) {
      return `<span class="deploy-log-empty">${OC.escapeHtml(emptyMessage || "(aguardando inicio da etapa…)")}</span>`;
    }
    if (!parsed.length) {
      return `<span class="deploy-log-empty">${OC.escapeHtml(emptyMessage || "(sem linhas nesta etapa ainda…)")}</span>`;
    }
    return OC.renderLogLinesHtml(parsed, limit, options);
  };

  OC.getLogScrollState = function getLogScrollState(stateKey) {
    if (!stateKey) return null;
    if (!OC.deployLogScrollState[stateKey]) {
      OC.deployLogScrollState[stateKey] = {
        atBottom: true,
        scrollTop: 0,
        userLocked: false,
        interacting: false,
      };
    }
    return OC.deployLogScrollState[stateKey];
  };

  OC.isScrollAtBottom = function isScrollAtBottom(container) {
    if (!container) return true;
    return container.scrollHeight - container.scrollTop - container.clientHeight < LOG_SCROLL_THRESHOLD;
  };

  OC.captureLogScrollState = function captureLogScrollState(container, stateKey) {
    if (!container || !stateKey) return;
    const state = OC.getLogScrollState(stateKey);
    const atBottom = OC.isScrollAtBottom(container);
    state.scrollTop = container.scrollTop;
    state.atBottom = atBottom;
    if (!atBottom) state.userLocked = true;
  };

  OC.shouldFollowLogScroll = function shouldFollowLogScroll(stateKey) {
    const state = stateKey ? OC.deployLogScrollState[stateKey] : null;
    if (!state) return true;
    if (state.userLocked || state.interacting) return false;
    return state.atBottom !== false;
  };

  OC.unlockLogScroll = function unlockLogScroll(stateKey) {
    const state = OC.getLogScrollState(stateKey);
    state.userLocked = false;
    state.interacting = false;
    state.atBottom = true;
  };

  OC.bindLogScrollContainer = function bindLogScrollContainer(container, stateKey) {
    if (!container || !stateKey || container._logScrollKey === stateKey) return;
    container._logScrollKey = stateKey;

    const syncState = () => {
      const state = OC.getLogScrollState(stateKey);
      const atBottom = OC.isScrollAtBottom(container);
      state.scrollTop = container.scrollTop;
      state.atBottom = atBottom;
      if (!atBottom) state.userLocked = true;
      else if (!state.interacting) state.userLocked = false;
    };

    const onInteractStart = () => {
      const state = OC.getLogScrollState(stateKey);
      state.interacting = true;
      state.userLocked = true;
    };

    const onInteractEnd = () => {
      const state = OC.getLogScrollState(stateKey);
      state.interacting = false;
      if (OC.isScrollAtBottom(container)) {
        state.userLocked = false;
        state.atBottom = true;
      }
    };

    container.addEventListener("scroll", syncState, { passive: true });
    container.addEventListener("wheel", onInteractStart, { passive: true });
    container.addEventListener("touchstart", onInteractStart, { passive: true });
    container.addEventListener("pointerdown", onInteractStart);
    container.addEventListener("pointerup", onInteractEnd);
    container.addEventListener("pointercancel", onInteractEnd);
    container.addEventListener("touchend", onInteractEnd);
  };

  OC.applySmartLogScroll = function applySmartLogScroll(container, stateKey) {
    if (!container) return;
    if (OC.shouldFollowLogScroll(stateKey)) {
      container.scrollTop = container.scrollHeight;
      const state = stateKey ? OC.getLogScrollState(stateKey) : null;
      if (state) {
        state.atBottom = true;
        state.scrollTop = container.scrollTop;
      }
    } else {
      const state = stateKey ? OC.getLogScrollState(stateKey) : null;
      if (state?.scrollTop != null) container.scrollTop = state.scrollTop;
    }
    OC.bindLogScrollContainer(container, stateKey);
  };

  OC.isAnyLogScrollLocked = function isAnyLogScrollLocked(prefix) {
    return Object.entries(OC.deployLogScrollState || {}).some(([key, state]) => {
      if (prefix && !key.startsWith(prefix)) return false;
      return !!(state?.userLocked || state?.interacting);
    });
  };

  OC.setDrawerWide = function setDrawerWide(wide) {
    document.getElementById("deployment-drawer")?.classList.toggle("drawer--wide", !!wide);
  };

  function deployGroupStatus(steps, stepIds) {
    const matched = (steps || []).filter((s) => stepIds.includes(s.id));
    if (!matched.length) return { status: "pending", steps: [] };
    if (matched.some((s) => s.status === "error")) return { status: "error", steps: matched };
    if (matched.some((s) => s.status === "running")) return { status: "running", steps: matched };
    if (matched.every((s) => s.status === "skipped")) return { status: "skipped", steps: matched };
    if (matched.every((s) => ["success", "warning", "skipped"].includes(s.status))) {
      return { status: matched.some((s) => s.status === "warning") ? "warning" : "success", steps: matched };
    }
    if (matched.some((s) => s.status === "success" || s.status === "warning")) {
      return { status: "running", steps: matched };
    }
    return { status: "pending", steps: matched };
  }

  function deployGroupTiming(steps) {
    const started = steps.map((s) => s.startedAt).filter(Boolean).sort()[0];
    const finished = steps
      .map((s) => s.finishedAt)
      .filter(Boolean)
      .sort()
      .reverse()[0];
    const duration = steps.reduce((acc, s) => acc + (Number(s.durationSec) || 0), 0);
    return { started, finished, duration };
  }

  OC.renderRunLogsTimeline = function renderRunLogsTimeline(runData, options) {
    const steps = runData?.steps || [];
    const logs = runData?.logs || {};
    const expanded = options?.expandedSteps || new Set(["deps"]);
    const scrollKeyPrefix = options?.scrollKeyPrefix || "detail:";
    const icons = OC.STEP_ICONS || STEP_ICONS;

    return OC.DEPLOY_TIMELINE_GROUPS.map((group) => {
      const { status, steps: groupSteps } = deployGroupStatus(steps, group.stepIds);
      const timing = deployGroupTiming(groupSteps);
      const isExpanded = expanded.has(group.id);
      const groupParsed = OC.collectGroupLogs(logs, group);
      const filtered = OC.filterLogsForStepGroup(groupParsed, groupSteps, steps);
      const depsSubphase =
        group.id === "deps" && status === "running" ? OC.parseDepsSubphase(filtered === null ? [] : filtered) : "";
      const meta = depsSubphase
        ? OC.escapeHtml(depsSubphase)
        : timing.duration
          ? OC.formatDuration(timing.duration)
          : status === "running"
            ? "em andamento"
            : status;

      return `<section class="deploy-timeline-item deploy-timeline-${status} ${isExpanded ? "is-expanded" : ""}" data-timeline-group="${group.id}">
        <button type="button" class="deploy-timeline-head" data-timeline-toggle="${group.id}">
          <span class="deploy-timeline-icon">${icons[status] || "○"}</span>
          <span class="deploy-timeline-main">
            <strong>${OC.escapeHtml(group.label)}</strong>
            <span class="deploy-timeline-meta" data-timeline-meta="${group.id}">${meta}</span>
          </span>
          <span class="deploy-timeline-times">
            ${timing.started ? `<span>${OC.formatTimeShort(timing.started)}</span>` : ""}
            ${timing.finished ? `<span>→ ${OC.formatTimeShort(timing.finished)}</span>` : ""}
          </span>
        </button>
        <div class="deploy-timeline-body ${isExpanded ? "" : "hidden"}">
          <div class="deploy-timeline-log detail-log-view" data-log-scroll-key="${scrollKeyPrefix}${group.id}">${OC.renderStepLogHtml(filtered, 800, undefined, { active: status === "running" })}</div>
        </div>
      </section>`;
    }).join("");
  };

  OC.bindTimelineToggle = function bindTimelineToggle(root, expandedSteps) {
    const store = expandedSteps || new Set();
    root?.querySelectorAll("[data-timeline-toggle]").forEach((btn) => {
      btn.addEventListener("click", () => {
        const id = btn.getAttribute("data-timeline-toggle");
        if (store.has(id)) store.delete(id);
        else store.add(id);
        const section = btn.closest(".deploy-timeline-item");
        section?.classList.toggle("is-expanded");
        section?.querySelector(".deploy-timeline-body")?.classList.toggle("hidden");
      });
    });
    return store;
  };

  OC.bindTimelineLogScroll = function bindTimelineLogScroll(root, scrollKeyPrefix) {
    root?.querySelectorAll("[data-log-scroll-key]").forEach((el) => {
      const key = el.getAttribute("data-log-scroll-key") || `${scrollKeyPrefix}${el.closest("[data-timeline-group]")?.getAttribute("data-timeline-group")}`;
      OC.applySmartLogScroll(el, key);
    });
  };

  OC.stepsSignature = function stepsSignature(steps) {
    return JSON.stringify(
      (steps || []).map((s) => ({
        id: s.id,
        status: s.status,
        startedAt: s.startedAt,
        finishedAt: s.finishedAt,
        durationSec: s.durationSec,
        error: s.error,
      }))
    );
  };

  OC.activeLogKeyForData = function activeLogKeyForData(data, progress) {
    const pipelineStatus = data.pipelineStatus || "";
    if (progress?.activeLogKey) return progress.activeLogKey;
    return PHASE_LOG_MAP[pipelineStatus] || "pipeline.log";
  };

  OC.deployProgressEstimateHtml = function deployProgressEstimateHtml(pipelineStatus) {
    const pct = PHASE_PROGRESS[pipelineStatus] ?? 15;
    return `<div class="deploy-progress-estimate" aria-hidden="true">
      <div class="deploy-progress-bar"><div class="deploy-progress-fill" style="width:${pct}%"></div></div>
      <span class="deploy-progress-pct">~${pct}% estimado</span>
    </div>`;
  };

  OC.deployNowPanelHtml = function deployNowPanelHtml(envName, data, progress) {
    const pipelineStatus = data.pipelineStatus || "building";
    const currentStep = progress?.currentStep;
    const phaseLabel = OC.STATUS_META[pipelineStatus]?.label || pipelineStatus;
    let stepLabel = OC.fixMojibake(currentStep?.label || currentStep?.id || "Preparando");
    const stepStatus = currentStep?.status || "running";
    const activeLog = OC.activeLogKeyForData(data, progress);
    const startedAt = currentStep?.startedAt || data.deployState?.startedAt || data.lastDeployStartedAt;
    const elapsed = startedAt ? OC.formatLiveDuration(startedAt) : "—";
    if (currentStep?.id === "deps_backend" || /dependenc/i.test(stepLabel)) {
      const cached = envName ? OC.deployRunCache[envName] : null;
      let parsed = OC.resolveLogPreviewParsed(data, progress, cached);
      if (currentStep?.id === "deps_backend" && cached?.steps?.length) {
        const depsSteps = cached.steps.filter((s) => s.id === "deps_backend");
        const buildParsed = cached.logs?.["build.log"]?.parsed || parsed;
        const filtered = OC.filterLogsForStepGroup(buildParsed, depsSteps, cached.steps);
        if (filtered !== null) parsed = filtered;
      }
      const sub = OC.parseDepsSubphase(parsed);
      if (sub) stepLabel = `${stepLabel} — ${sub}`;
    }
    const promoteNotice =
      pipelineStatus === "promoting"
        ? `<p class="deploy-promote-notice">Reiniciando serviços — frontend/backend podem ficar offline temporariamente durante a publicação.</p>`
        : "";

    return `<div class="deploy-now-panel">
      <div class="deploy-now-row">
        <span class="deploy-now-label">Fase</span>
        <strong class="deploy-now-value">${OC.escapeHtml(phaseLabel)}</strong>
      </div>
      <div class="deploy-now-row">
        <span class="deploy-now-label">Etapa</span>
        <strong class="deploy-now-value">${OC.escapeHtml(stepLabel)}</strong>
        <span class="deploy-now-meta">${OC.escapeHtml(elapsed)} · ${stepStatus === "running" ? "em andamento" : stepStatus}</span>
      </div>
      <div class="deploy-now-row">
        <span class="deploy-now-label">Log ativo</span>
        <code class="deploy-now-logfile">${OC.escapeHtml(activeLog)}</code>
      </div>
      ${promoteNotice}
      ${OC.deployProgressEstimateHtml(pipelineStatus)}
    </div>`;
  };

  OC.deployRetryBannerHtml = function deployRetryBannerHtml(progress) {
    const prev = progress?.previousFailedRun;
    if (!prev?.runId) return "";
    const step = prev.failedStep || "—";
    return `<div class="deploy-retry-banner" role="status">
      Tentativa anterior falhou em <strong>${OC.escapeHtml(step)}</strong>
      (run <code>${OC.escapeHtml(prev.runId)}</code>).
      ${prev.lastError ? `<span class="deploy-retry-err">${OC.escapeHtml(OC.truncate(OC.fixMojibake(prev.lastError), 120))}</span>` : ""}
    </div>`;
  };

  OC.resolveLogPreviewParsed = function resolveLogPreviewParsed(data, progress, cached) {
    if (cached?.logs) {
      const activeKey = OC.activeLogKeyForData(data, progress);
      if (cached.logs[activeKey]?.parsed?.length) return cached.logs[activeKey].parsed;
      return OC.collectParsedLogs(cached);
    }
    if (progress?.logPreview?.parsed?.length) return progress.logPreview.parsed;
    return [];
  };

  OC.focusEnvCard = function focusEnvCard(envName) {
    const card = document.querySelector(`.summary-card[data-env="${envName}"]`);
    if (!card) return;
    OC.expandedDeployDetails.add(envName);
    card.scrollIntoView({ behavior: "smooth", block: "nearest" });
    card.classList.add("is-deploy-focus");
    setTimeout(() => card.classList.remove("is-deploy-focus"), 4000);
  };

  OC.deployPhaseStepperHtml = function deployPhaseStepperHtml(data) {
    const current = data.pipelineStatus || "building";
    const failed = current === "failed";
    const failedStep =
      data.deployState?.blockedReason ||
      data.deployProgress?.lastError?.step ||
      data.deployState?.lastError?.step ||
      "";
    let failedPhaseIdx = -1;
    if (failed) {
      if (String(failedStep).startsWith("valid")) failedPhaseIdx = 1;
      else if (["restart_services", "health_check", "publish_done"].includes(String(failedStep))) failedPhaseIdx = 2;
      else failedPhaseIdx = 0;
    }
    const currentIdx = failed ? failedPhaseIdx : PHASE_ORDER[current] ?? -1;
    const steps = PIPELINE_PHASES.map((step, idx) => {
      let state = "pending";
      if (failed && idx === failedPhaseIdx) state = "error";
      else if (failed && idx < failedPhaseIdx) state = "success";
      else if (!failed && idx < currentIdx) state = "success";
      else if (!failed && idx === currentIdx) state = "running";
      else if (!failed && currentIdx < 0 && step.id === "building") state = "running";
      const icon = STEP_ICONS[state] || "○";
      return `<li class="deploy-step deploy-step-${state}" data-step="${step.id}">
        <span class="deploy-step-icon" aria-hidden="true">${icon}</span>
        <span class="deploy-step-label">${OC.escapeHtml(step.label)}</span>
      </li>`;
    }).join("");
    return `<ol class="deploy-stepper deploy-stepper-phases" aria-label="Fases do deploy">${steps}</ol>`;
  };

  OC.deployFineStepsHtml = function deployFineStepsHtml(steps) {
    if (!steps?.length) return "";
    return `<ul class="deploy-fine-steps">${steps
      .map((s) => {
        const st = s.status || "pending";
        const icon = STEP_ICONS[st] || "○";
        const dur =
          s.durationSec != null ? `<span class="deploy-step-dur">${OC.formatDuration(s.durationSec)}</span>` : "";
        const err = s.error ? `<span class="deploy-step-err" title="${OC.escapeHtml(s.error)}">!</span>` : "";
        return `<li class="deploy-fine-step deploy-fine-step-${st}">
          <span class="deploy-fine-icon">${icon}</span>
          <span class="deploy-fine-label">${OC.escapeHtml(OC.fixMojibake(s.label || s.id))}</span>
          ${dur}${err}
        </li>`;
      })
      .join("")}</ul>`;
  };

  OC.renderLogLinesHtml = function renderLogLinesHtml(parsed, limit, options) {
    const opts = options || {};
    const active = !!opts.active;
    const collapsed = OC.collapseDuplicateLogLines(parsed);
    const rows = collapsed.slice(-(limit || 8));
    if (!rows.length) return `<span class="deploy-log-empty">(aguardando logs…)</span>`;
    return rows
      .map((row, idx) => {
        const isLast = idx === rows.length - 1;
        const ongoing = active && isLast;
        const repeatTitle =
          row.count > 1
            ? `Repetido ${row.count} vezes (${row.firstTime || "—"} → ${row.lastTime || "—"})`
            : "";
        const repeatBadge =
          row.count > 1 && !ongoing
            ? `<span class="log-repeat-badge" title="${OC.escapeHtml(repeatTitle)}">×${row.count}</span>`
            : "";
        const ongoingBadge = ongoing
          ? `<span class="log-ongoing-badge" title="${OC.escapeHtml(repeatTitle || "Etapa em andamento")}">
              <span class="loading-spinner loading-spinner-sm log-line-spinner" aria-hidden="true"></span>
              <span class="log-ongoing-text">${row.count > 1 ? `em andamento · ${row.count}×` : "em andamento"}</span>
            </span>`
          : "";
        const displayTime = row.lastTime || row.time || "";
        return (
          `<div class="log-line log-${OC.escapeHtml(row.level || "INFO")}${ongoing ? " log-line-ongoing" : ""}">` +
          `<span class="log-time">${OC.escapeHtml(displayTime)}</span> ` +
          `<span class="log-level">[${OC.escapeHtml(row.level || "INFO")}]</span> ` +
          `<span class="log-text">${OC.escapeHtml(OC.fixMojibake(row.text || ""))}</span>` +
          repeatBadge +
          ongoingBadge +
          `</div>`
        );
      })
      .join("");
  };

  OC.classifyPlainLogLine = function classifyPlainLogLine(line) {
    const text = String(line || "");
    const pipeMatch = text.match(/^\[([\d\-: ]+)\]\s*\[(INFO|OK|WARN|ERROR|SUCCESS)\]\s*(.+)$/);
    if (pipeMatch) {
      return { time: pipeMatch[1].trim(), level: pipeMatch[2], text: OC.fixMojibake(pipeMatch[3].trim()) };
    }
    if (/(?:^|\s)(?:error|falhou|failed|fatal)(?:\s|:|$)/i.test(text)) {
      return { level: "ERROR", text: OC.fixMojibake(text) };
    }
    if (/(?:^|\s)(?:warn|aviso|warning)(?:\s|:|$)/i.test(text)) {
      return { level: "WARN", text: OC.fixMojibake(text) };
    }
    if (/\|\s*\d+\s*\+/.test(text) || /^\s*\+/.test(text)) {
      return { level: "OK", text: OC.fixMojibake(text) };
    }
    if (/\|\s*\d+\s*\-/.test(text) || /^\s*\-/.test(text)) {
      return { level: "WARN", text: OC.fixMojibake(text) };
    }
    if (/^(commit|author|date|co-authored-by):/i.test(text.trim())) {
      return { level: "INFO", text: OC.fixMojibake(text) };
    }
    return { level: "INFO", text: OC.fixMojibake(text) };
  };

  OC.renderPlainTextLogHtml = function renderPlainTextLogHtml(text, limit, scrollKey) {
    const lines = String(text || "")
      .split(/\r?\n/)
      .filter((line) => line.length > 0);
    if (!lines.length) {
      return `<div class="detail-log-view"><span class="deploy-log-empty">(vazio)</span></div>`;
    }
    const parsed = lines.map((line) => OC.classifyPlainLogLine(line));
    const keyAttr = scrollKey ? ` data-log-scroll-key="${OC.escapeHtml(scrollKey)}"` : "";
    return `<div class="detail-log-view"${keyAttr}>${OC.renderLogLinesHtml(parsed, limit || 800)}</div>`;
  };

  OC.collectParsedLogs = function collectParsedLogs(runData, logKey) {
    const logs = runData?.logs || {};
    if (logKey && logs[logKey]?.parsed) return logs[logKey].parsed;
    const order = ["pipeline.log", "build.log", "validate.log", "promote.log", "rollback.log"];
    const all = [];
    order.forEach((name) => {
      (logs[name]?.parsed || []).forEach((line) => all.push(line));
    });
    return all;
  };

  OC.STEP_ICONS = STEP_ICONS;

  OC.deployFailureHtml = function deployFailureHtml(lastError, runData) {
    const err = lastError || runData?.lastError || runData?.failure;
    if (!err?.message) return "";
    const stepLabel = err.stepLabel || err.step || "—";
    const copyText = [
      `Falha na etapa: ${stepLabel}`,
      `Erro: ${OC.fixMojibake(err.message)}`,
      err.command ? `Comando: ${err.command}` : "",
      err.recommendation ? `Acao: ${err.recommendation}` : "",
    ]
      .filter(Boolean)
      .join("\n");
    return `<div class="env-deploy-failure">
      <strong>Deploy falhou</strong>
      <dl class="deploy-failure-grid">
        <dt>Etapa</dt><dd>${OC.escapeHtml(stepLabel)}</dd>
        <dt>Erro</dt><dd><code class="deploy-failure-msg">${OC.escapeHtml(OC.fixMojibake(err.message))}</code></dd>
        ${err.at ? `<dt>Horario</dt><dd>${OC.escapeHtml(OC.formatTimeShort(err.at))}</dd>` : ""}
      </dl>
      <div class="deploy-failure-actions">
        <button type="button" class="btn btn-secondary btn-sm" data-copy-deploy-error="${OC.escapeHtml(copyText)}">Copiar erro</button>
        <button type="button" class="btn btn-primary btn-sm" data-open-deploy-drawer="${OC.escapeHtml(runData?.environment || "")}">Ver detalhes do deploy</button>
      </div>
    </div>`;
  };

  OC.deployProgressBannerHtml = function deployProgressBannerHtml(name, data) {
    const phase = data.displayPhase || data.phase;
    const pipelineStatus = data.pipelineStatus || "";
    const isFailed = phase === "failed" || pipelineStatus === "failed";
    const isDeploying = phase === "deploying" || ["building", "validating", "promoting"].includes(pipelineStatus);
    if (!isDeploying && !isFailed) return "";

    const sha = OC.resolveCommitSha(data);
    const prevSha = OC.resolveDeployedSha ? OC.resolveDeployedSha(data) : data.activeSha;
    const subject = OC.truncate(OC.resolveCommitSubject(data), 72);
    const runId = data.deployState?.runId || "";
    const branch = data.branch || "—";
    const startedAt = data.lastDeployStartedAt || data.deployState?.startedAt || "";
    const finishedAt = data.lastDeployFinishedAt || data.deployState?.finishedAt || "";
    const summary = data.deploySummary || {};
    const progress = data.deployProgress || OC.deployRunCache[name] || {};
    const progressPct = progress.progressPct ?? summary.progressPct ?? 0;
    const lastError = progress.lastError || progress.failure || (isFailed ? { message: data.deployState?.lastError || data.lastDeployMessage } : null);
    const statusKey = isFailed
      ? "failed"
      : ["building", "validating", "promoting"].includes(pipelineStatus)
        ? pipelineStatus
        : "deploying";
    const title = isFailed ? "Deploy falhou" : "Deploy em andamento";
    const contextMessage = summary.message || (isFailed ? "O deploy encontrou um erro." : `Processando commit na branch ${branch}.`);

    return `
      <div class="env-deploy-banner env-deploy-banner-live env-deploy-banner-compact ${isFailed ? "is-failed" : ""}" data-deploy-env="${name}">
        <div class="env-deploy-banner-head">
          ${OC.statusBadgeHtml(statusKey)}
          <span class="env-deploy-label">${OC.escapeHtml(title)}</span>
        </div>
        <p class="env-deploy-context">${OC.escapeHtml(contextMessage)}</p>
        <div class="env-deploy-meta env-deploy-meta-compact">
          <span>Branch: <code>${OC.escapeHtml(branch)}</code></span>
          <span>Commit: <code>${OC.escapeHtml(prevSha || "—")}</code> → <code>${OC.escapeHtml(sha)}</code></span>
          ${runId ? `<span>Run: <code>${OC.escapeHtml(runId)}</code></span>` : ""}
          ${isFailed && finishedAt && startedAt
            ? `<span>Tempo: ${OC.formatDuration(Math.max(0, Math.floor((new Date(finishedAt).getTime() - new Date(startedAt).getTime()) / 1000)))}</span>`
            : `<span>Tempo: <span class="live-duration" data-started="${OC.escapeHtml(startedAt)}">${OC.formatLiveDuration(startedAt)}</span></span>`}
        </div>
        ${subject ? `<p class="env-deploy-subject-line">${OC.escapeHtml(subject)}</p>` : ""}
        <div class="deploy-progress-estimate" aria-hidden="true">
          <div class="deploy-progress-bar"><div class="deploy-progress-fill" style="width:${Math.max(5, progressPct)}%"></div></div>
          <span class="deploy-progress-pct">${progressPct}% concluido</span>
        </div>
        ${OC.deployPhaseStepperHtml(data)}
        ${isFailed ? OC.deployFailureHtml(lastError, { ...progress, environment: name }) : ""}
        <div class="env-deploy-actions">
          ${!isFailed ? `<button type="button" class="btn btn-danger btn-sm" data-cancel-deploy="${OC.escapeHtml(name)}">Cancelar deploy</button>` : ""}
          <button type="button" class="btn btn-primary btn-sm" data-open-deploy-drawer="${OC.escapeHtml(name)}">Ver detalhes do deploy</button>
        </div>
      </div>`;
  };

  OC.deployStepperHtml = OC.deployPhaseStepperHtml;

  OC.patchDeployProgressDom = function patchDeployProgressDom(envName, runData, overviewEnv) {
    OC.deployRunCache[envName] = runData;
    const fineHost = document.querySelector(`[data-deploy-fine-steps="${envName}"]`);
    if (fineHost && runData.steps) {
      fineHost.innerHTML = OC.deployFineStepsHtml(runData.steps);
    }

    const logHost = document.querySelector(`[data-deploy-log="${envName}"]`);
    if (logHost) {
      OC.captureLogScrollState(logHost, `env:${envName}`);
      const expanded = OC.expandedDeployLogs.has(envName);
      const envData = overviewEnv || OC.lastOverview?.environments?.[envName] || {};
      const parsed = OC.resolveLogPreviewParsed(envData, envData.deployProgress, runData);
      const isActive =
        runData.currentStep?.status === "running" ||
        envData.displayPhase === "deploying" ||
        envData.phase === "deploying";
      logHost.innerHTML = OC.renderLogLinesHtml(parsed, expanded ? 30 : 5, { active: isActive });
      OC.applySmartLogScroll(logHost, `env:${envName}`);
    }

    const activeKey = runData.activeLogKey || OC.activeLogKeyForData(overviewEnv || {}, runData);
    const isDeployActive =
      runData.currentStep?.status === "running" ||
      (overviewEnv || OC.lastOverview?.environments?.[envName] || {}).displayPhase === "deploying";
    ["build.log", "validate.log", "promote.log"].forEach((key) => {
      const tab = document.querySelector(`[data-deploy-log-tab="${envName}"][data-log-key="${key}"]`);
      const tabWrap = tab?.closest(".deploy-log-tab");
      tabWrap?.classList.toggle("is-active", key === activeKey);
      if (tab && runData.logs?.[key]?.parsed) {
        tab.innerHTML = OC.renderLogLinesHtml(runData.logs[key].parsed, 15, {
          active: isDeployActive && key === activeKey,
        });
      } else if (tab && key === activeKey && runData.logPreview?.parsed?.length) {
        tab.innerHTML = OC.renderLogLinesHtml(runData.logPreview.parsed, 15, { active: isDeployActive });
      }
    });

    const currentStep = runData.currentStep;
    const heartbeat = document.querySelector(`[data-deploy-heartbeat="${envName}"]`);
    if (heartbeat) {
      const parsed = OC.collectParsedLogs(runData);
      const last = parsed[parsed.length - 1];
      const lastLine = last ? `[${last.level}] ${last.text}` : "";
      const now = Date.now();
      const prev = OC.deployLogState[envName] || {};
      if (lastLine !== prev.lastLine) {
        OC.deployLogState[envName] = { ...prev, lastLine, lastChangeAt: now, pollError: false };
      }
      const state = OC.deployLogState[envName] || {};
      const stepRunning = currentStep?.status === "running";
      const staleMs = now - (state.lastChangeAt || now);
      const stale = !stepRunning && state.lastChangeAt && staleMs > 120000;
      let heartbeatText = lastLine || currentStep?.label || "—";
      if (state.pollError) {
        heartbeatText = "Falha ao buscar logs — tentando novamente";
      } else if (stepRunning && !lastLine && staleMs > 60000) {
        heartbeatText = `${currentStep.label} · sem novas linhas há ${Math.floor(staleMs / 60000)} min`;
      } else if (stale) {
        heartbeatText = "Sem atividade recente";
      }
      heartbeat.textContent = heartbeatText;
      heartbeat.classList.toggle("is-stale", stale || !!state.pollError);
    }
  };

  OC.updateDeployLiveLog = function updateDeployLiveLog(envName, runData) {
    const overviewEnv = OC.lastOverview?.environments?.[envName];
    OC.patchDeployProgressDom(envName, runData, overviewEnv);
  };

  OC.bindDeployProgressEvents = function bindDeployProgressEvents() {
    document.querySelectorAll("[data-deploy-details-toggle]").forEach((btn) => {
      btn.addEventListener("click", (e) => {
        e.stopPropagation();
        const env = btn.getAttribute("data-deploy-details-toggle");
        if (OC.expandedDeployDetails.has(env)) OC.expandedDeployDetails.delete(env);
        else OC.expandedDeployDetails.add(env);
        const panel = document.querySelector(`[data-deploy-details="${env}"]`);
        panel?.classList.toggle("hidden", !OC.expandedDeployDetails.has(env));
        btn.textContent = OC.expandedDeployDetails.has(env)
          ? "Ocultar detalhes do deploy"
          : "Ver detalhes do deploy";
      });
    });

    document.querySelectorAll("[data-deploy-log-toggle]").forEach((btn) => {
      btn.addEventListener("click", (e) => {
        e.stopPropagation();
        const env = btn.getAttribute("data-deploy-log-toggle");
        if (OC.expandedDeployLogs.has(env)) OC.expandedDeployLogs.delete(env);
        else OC.expandedDeployLogs.add(env);
        const wrap = btn.closest(".env-deploy-log-wrap");
        wrap?.classList.toggle("is-expanded", OC.expandedDeployLogs.has(env));
        wrap?.classList.toggle("is-collapsed", !OC.expandedDeployLogs.has(env));
        btn.textContent = OC.expandedDeployLogs.has(env) ? "Ocultar logs" : "Ver detalhes do build";
        const cached = OC.deployRunCache[env];
        if (cached) OC.patchDeployProgressDom(env, cached);
      });
    });

    document.querySelectorAll("[data-copy-deploy-error]").forEach((btn) => {
      btn.addEventListener("click", (e) => {
        e.stopPropagation();
        const text = btn.getAttribute("data-copy-deploy-error");
        if (text && navigator.clipboard) navigator.clipboard.writeText(text).catch(() => {});
      });
    });

    document.querySelectorAll("[data-cancel-deploy]").forEach((btn) => {
      btn.addEventListener("click", async (e) => {
        e.stopPropagation();
        const env = btn.getAttribute("data-cancel-deploy");
        if (!env || !OC.runCancelDeploy) return;
        btn.disabled = true;
        await OC.runCancelDeploy(env, (err, result) => {
          OC.afterActionRefresh?.(err, result, btn);
          if (err || !result?.ok) btn.disabled = false;
        });
      });
    });
  };

  OC.deployLogOffsets = OC.deployLogOffsets || {};

  OC.pollDeployRunLogs = async function pollDeployRunLogs() {
    const overview = OC.lastOverview;
    if (!overview) return;
    const running = OC.getRunningEnvironments(overview);
    const failed = OC.ENV_ORDER.filter((name) => {
      const d = overview.environments?.[name];
      if (!d) return false;
      const phase = d.displayPhase || d.phase;
      return phase === "failed" && d.deployState?.runId;
    }).map((name) => ({ name, data: overview.environments[name] }));

    const targets = [...running, ...failed];
    if (!targets.length) return;

    await Promise.all(
      targets.map(async ({ name, data }) => {
        const runId = data.deployState?.runId;
        if (!runId) return;
        try {
          const offsets = OC.deployLogOffsets[name] || {};
          const offsetQuery = OC.buildLogOffsetQuery
            ? OC.buildLogOffsetQuery(offsets)
            : Object.entries(offsets)
                .map(([k, v]) => `${k}:${v}`)
                .join(",");
          const url =
            `/api/v1/runs/${name}?runId=${encodeURIComponent(runId)}` +
            (offsetQuery ? `&logOffset=${encodeURIComponent(offsetQuery)}` : "");
          const runData = await OC.fetchJson(url);
          if (runData.found) {
            OC.deployLogState[name] = { ...(OC.deployLogState[name] || {}), pollError: false };
            const prev = OC.deployRunCache[name];
            const merged = OC.mergeRunLogData ? OC.mergeRunLogData(prev, runData) : runData;
            Object.entries(runData.logs || {}).forEach(([key, chunk]) => {
              if (OC.applyLogChunkOffsets) {
                OC.applyLogChunkOffsets(OC.deployLogOffsets[name], key, chunk);
              } else if (chunk?.nextOffset != null) {
                OC.deployLogOffsets[name] = OC.deployLogOffsets[name] || {};
                OC.deployLogOffsets[name][key] = chunk.nextOffset;
              }
            });
            OC.updateDeployLiveLog(name, merged);
            if (OC.deployDrawerEnv === name && OC.renderDeployDrawerContent) {
              OC.deployDrawerState.runData = merged;
              OC.renderDeployDrawerContent(name, data, merged);
            }
          }
        } catch {
          OC.deployLogState[name] = {
            ...(OC.deployLogState[name] || {}),
            pollError: true,
            lastChangeAt: OC.deployLogState[name]?.lastChangeAt || Date.now(),
          };
        }
      })
    );
  };

  OC.syncDeployProgress = function syncDeployProgress(overview) {
    const envs = overview?.environments || {};
    const active = OC.ENV_ORDER.some((name) => {
      const d = envs[name];
      if (!d) return false;
      const phase = d.displayPhase || d.phase;
      if (phase === "deploying") return true;
      if (phase !== "failed") return false;
      const finishedAt = d.lastDeployFinishedAt || d.deployState?.finishedAt;
      const runId = d.deployState?.runId;
      return !!(runId && !finishedAt);
    });
    if (active) {
      OC.startDeployLogPolling();
      OC.restartAutoRefreshIfNeeded?.();
    } else {
      OC.stopDeployLogPolling();
      OC.restartAutoRefreshIfNeeded?.();
    }
  };

  OC.startDeployLogPolling = function startDeployLogPolling() {
    if (OC.deployLogPollTimer) return;
    OC.pollDeployRunLogs();
    OC.deployLogPollTimer = setInterval(() => {
      if (OC.authState?.locked) return;
      OC.pollDeployRunLogs();
    }, OC.DEPLOY_REFRESH_MS);
  };

  OC.stopDeployLogPolling = function stopDeployLogPolling() {
    if (OC.deployLogPollTimer) clearInterval(OC.deployLogPollTimer);
    OC.deployLogPollTimer = null;
  };
})();
