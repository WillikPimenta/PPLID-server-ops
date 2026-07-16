/* global window */
(function () {
  window.OpsConsole = window.OpsConsole || {};
  const OC = window.OpsConsole;

  OC.showTechnicalActivity = false;
  OC.commitSubjectCache = OC.commitSubjectCache || {};

  const DEFAULT_EVENT_TYPES = new Set([
    "deploy_started",
    "deploy_success",
    "deploy_failed",
    "deploy_cancelled",
    "rollback_started",
    "rollback_success",
    "rollback_failed",
    "update_detected",
    "deploy_pending",
  ]);

  function parseTime(iso) {
    if (!iso) return 0;
    const t = new Date(iso).getTime();
    return Number.isNaN(t) ? 0 : t;
  }

  OC.historyRowsNeedEnrich = function historyRowsNeedEnrich(rows) {
    return rows.some((row) => {
      if (row.isPrimary) return false;
      const sha = row.sha && row.sha !== "—" ? row.sha : OC.parseEventSha({ message: row.message });
      if (!sha) return false;
      const key = `${row.environment}:${sha}`;
      if (OC.commitSubjectCache[key]) return false;
      const subj = row.subject || row.message || "";
      if (subj && !/^Pipeline\s/i.test(subj)) return false;
      return true;
    });
  };

  OC.isDeployInProgress = function isDeployInProgress(data) {
    if (!data) return false;
    const phase = data.displayPhase || data.phase;
    const ps = data.pipelineStatus;
    if (phase === "deploying") return true;
    return ps === "building" || ps === "validating" || ps === "promoting";
  };

  OC.resolveDeployedSha = function resolveDeployedSha(data) {
    if (!data) return "—";
    return data.deployedSha || data.deployCommit?.sha || data.activeSha || "—";
  };

  OC.isHistoryRowActive = function isHistoryRowActive(row, envData) {
    const data = envData || row?.envData || {};
    const activeSha = OC.resolveDeployedSha(data);
    if (!activeSha || activeSha === "—" || !row?.sha || row.sha === "—") return false;
    const shaMatch =
      row.sha === activeSha || row.sha.startsWith(activeSha) || activeSha.startsWith(row.sha);
    if (!shaMatch) return false;
    if (row.isRunning || row.statusKey === "failed" || row.result === "failed") return false;
    if (["failed", "error", "building", "validating", "promoting", "deploying"].includes(row.statusKey)) {
      return false;
    }
    if (row.isPrimary) {
      const phase = data.displayPhase || data.phase;
      return phase !== "failed" && data.lastDeployResult !== "failed";
    }
    return row.statusKey === "success" || row.result === "success";
  };

  OC.resetDrawerBodyScroll = function resetDrawerBodyScroll() {
    const body = document.getElementById("drawer-body");
    if (body) body.scrollTop = 0;
  };

  OC.resolveCommitSubject = function resolveCommitSubject(data) {
    if (!data) return "";
    let raw;
    if (OC.isDeployInProgress(data)) {
      raw =
        data.gitCommitSubject ||
        data.targetCommit?.subject ||
        data.deployCommit?.subject ||
        "";
    } else {
      raw =
        data.deployCommit?.subject ||
        data.currentCommit?.subject ||
        data.gitCommitSubject ||
        "";
    }
    return OC.fixMojibake(raw);
  };

  OC.resolveCommitSha = function resolveCommitSha(data) {
    if (!data) return "—";
    if (OC.isDeployInProgress(data)) {
      return (
        data.deployState?.targetSha ||
        data.gitSha ||
        data.targetCommit?.sha ||
        "—"
      );
    }
    return data.deployedSha || data.deployCommit?.sha || data.activeSha || data.gitSha || "—";
  };

  OC.parseEventRunId = function parseEventRunId(evt) {
    if (!evt) return null;
    return evt.runId || null;
  };

  OC.parseEventSha = function parseEventSha(evt) {
    if (!evt) return null;
    if (evt.sha) return evt.sha;
    const msg = evt.message || "";
    const paren = msg.match(/\(([a-f0-9]{7,40})\)/i);
    if (paren) return paren[1];
    const bare = msg.match(/\b([a-f0-9]{7,40})\b/i);
    return bare ? bare[1] : null;
  };

  function isPipelineMessage(msg) {
    return /^Pipeline\s/i.test(msg || "");
  }

  OC.resolveEventSubject = function resolveEventSubject(evt, envName) {
    if (evt.subject) return OC.fixMojibake(evt.subject);
    const msg = evt.message || "";
    if (msg && !isPipelineMessage(msg)) return OC.fixMojibake(msg);
    const sha = OC.parseEventSha(evt);
    if (sha && OC.commitSubjectCache[`${envName}:${sha}`]) {
      return OC.commitSubjectCache[`${envName}:${sha}`];
    }
    return msg;
  };

  OC.enrichEventSubjects = async function enrichEventSubjects(rows) {
    const pending = rows.filter((row) => {
      if (row.isPrimary) return false;
      const sha = row.sha && row.sha !== "—" ? row.sha : OC.parseEventSha({ message: row.message });
      if (!sha) return false;
      const key = `${row.environment}:${sha}`;
      if (OC.commitSubjectCache[key]) {
        row.subject = OC.commitSubjectCache[key];
        return false;
      }
      const subj = row.subject || row.message || "";
      return !subj || isPipelineMessage(subj);
    });

    await Promise.all(
      pending.map(async (row) => {
        const sha = row.sha || OC.parseEventSha({ message: row.message });
        if (!sha || sha === "—") return;
        const key = `${row.environment}:${sha}`;
        try {
          const data = await OC.fetchJson(`/api/v1/commits/${row.environment}?sha=${encodeURIComponent(sha)}`);
          if (data.subject) {
            OC.commitSubjectCache[key] = data.subject;
            row.subject = OC.fixMojibake(data.subject);
            if (data.author && !row.author) row.author = data.author;
          }
        } catch {
          /* ignore */
        }
      })
    );
    return rows;
  };

  function envRow(envName, data) {
    const phase = data.displayPhase || data.phase || "idle";
    const isRunning = phase === "deploying";
    const deployedSha = OC.resolveCommitSha(data);
    const repoSha = data.repoSha || data.currentCommit?.sha || data.gitSha || "—";
    const statusKey =
      OC.isDeployInProgress(data) &&
      ["building", "validating", "promoting"].includes(data.pipelineStatus)
        ? data.pipelineStatus
        : OC.phaseToStatusKey(phase, data);

    return {
      id: `env-${envName}`,
      environment: envName,
      branch: data.branch || "—",
      sha: deployedSha,
      repoSha,
      subject: OC.resolveCommitSubject(data),
      author: data.deployCommit?.author || data.gitCommitAuthor || data.currentCommit?.author || "",
      startedAt: data.lastDeployStartedAt || null,
      finishedAt: data.lastDeployFinishedAt || null,
      durationSeconds: data.lastDeployDurationSeconds,
      result: data.lastDeployResult || null,
      statusKey,
      isRunning,
      isPrimary: true,
      envData: data,
      message: "",
      eventType: null,
      sortAt: parseTime(isRunning ? data.lastDeployStartedAt : data.lastDeployFinishedAt || data.deployedAt),
    };
  }

  function eventRow(evt, environments) {
    const envName = evt.environment || "?";
    const envData = environments[envName] || {};
    const statusKey = OC.eventTypeToStatusKey(evt.type);
    const sha = OC.parseEventSha(evt) || "—";
    const runId = OC.parseEventRunId(evt);

    return {
      id: `evt-${evt.at}-${envName}-${evt.type}`,
      environment: envName,
      branch: envData.branch || "—",
      sha,
      repoSha: envData.repoSha || envData.gitSha || "—",
      subject: OC.resolveEventSubject(evt, envName),
      author: evt.author || envData.gitCommitAuthor || "",
      startedAt: evt.startedAt || evt.at,
      finishedAt: evt.finishedAt || null,
      durationSeconds: evt.durationSeconds != null ? evt.durationSeconds : null,
      result: evt.result || null,
      runId,
      failedStep: evt.failedStep || null,
      previousSha: evt.previousSha || null,
      statusKey,
      isRunning: statusKey === "deploying",
      isPrimary: false,
      envData,
      message: evt.message || "",
      eventType: evt.type,
      sortAt: parseTime(evt.at),
    };
  }

  function isDefaultEvent(type) {
    if (!type) return false;
    if (DEFAULT_EVENT_TYPES.has(type)) return true;
    if (OC.showTechnicalActivity) {
      return (
        type.startsWith("deploy_") ||
        type.startsWith("sync_") ||
        type.startsWith("phase_") ||
        type === "update_detected"
      );
    }
    return false;
  }

  function hasClosingDeployEvent(evt, events) {
    if (evt.type !== "deploy_started") return false;
    const env = evt.environment;
    const startedAt = parseTime(evt.at);
    if (!startedAt) return false;
    return events.some(
      (e) =>
        e.environment === env &&
        (e.type === "deploy_success" || e.type === "deploy_failed") &&
        parseTime(e.at) > startedAt
    );
  }

  function isDuplicateEvent(row, envRows, events) {
    const envRowMatch = envRows.find((r) => r.environment === row.environment);
    if (!envRowMatch) return false;
    if (!row.eventType) return true;
    if (row.eventType === "deploy_success" && envRowMatch.result === "success") {
      const delta = Math.abs(row.sortAt - parseTime(envRowMatch.finishedAt));
      return delta < 120000;
    }
    if (row.eventType === "deploy_started") {
      if (envRowMatch.isRunning) return true;
      if (hasClosingDeployEvent({ type: "deploy_started", environment: row.environment, at: row.startedAt }, events)) {
        return true;
      }
      if (
        envRowMatch.result === "success" &&
        envRowMatch.finishedAt &&
        row.sortAt < parseTime(envRowMatch.finishedAt)
      ) {
        return true;
      }
    }
    return false;
  }

  OC.buildEnvRows = function buildEnvRows(overview) {
    const environments = overview?.environments || {};
    return OC.ENV_ORDER.filter((name) => environments[name]).map((name) => envRow(name, environments[name]));
  };

  OC.buildHistoryEvents = function buildHistoryEvents(overview, envRows) {
    const environments = overview?.environments || {};
    const events = overview?.events || [];
    return events
      .filter((evt) => isDefaultEvent(evt.type))
      .map((evt) => eventRow(evt, environments))
      .filter((row) => !isDuplicateEvent(row, envRows, events))
      .sort((a, b) => (b.sortAt || 0) - (a.sortAt || 0));
  };

  OC.buildDeploymentRows = function buildDeploymentRows(overview) {
    const envRows = OC.buildEnvRows(overview);
    const eventRows = OC.buildHistoryEvents(overview, envRows);
    const all = [...envRows, ...eventRows];

    all.sort((a, b) => {
      if (a.isRunning && !b.isRunning) return -1;
      if (!a.isRunning && b.isRunning) return 1;
      return (b.sortAt || 0) - (a.sortAt || 0);
    });

    return all;
  };

  OC.getHistoryForEnv = function getHistoryForEnv(overview, envName) {
    const envRows = OC.buildEnvRows(overview);
    return OC.buildHistoryEvents(overview, envRows).filter((row) => row.environment === envName);
  };

  OC.getRunningEnvironments = function getRunningEnvironments(overview) {
    const environments = overview?.environments || {};
    return OC.ENV_ORDER.filter((name) => {
      const d = environments[name];
      if (!d) return false;
      const phase = d.displayPhase || d.phase;
      return phase === "deploying";
    }).map((name) => ({ name, data: environments[name] }));
  };

  OC.getAvailabilitySummary = function getAvailabilitySummary(overview) {
    const environments = overview?.environments || {};
    return OC.ENV_ORDER.filter((name) => environments[name]).map((name) => {
      const data = environments[name];
      const phase = data.displayPhase || data.phase;
      const online = phase === "online" || phase === "healthy" || (data.runtime?.reachable && data.runtime?.database === "ok");
      return { name, online, data };
    });
  };
})();
