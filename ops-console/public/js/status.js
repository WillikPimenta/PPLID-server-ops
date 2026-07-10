/* global window */
window.OpsConsole = window.OpsConsole || {};

const STATUS_META = {
  success: { label: "Sucesso", icon: "✓", badgeClass: "success" },
  running: { label: "Implantando", icon: "◐", badgeClass: "running" },
  deploying: { label: "Implantando", icon: "◐", badgeClass: "running" },
  building: { label: "Buildando", icon: "◐", badgeClass: "running" },
  validating: { label: "Validando", icon: "◐", badgeClass: "running" },
  promoting: { label: "Publicando", icon: "◐", badgeClass: "running" },
  deploy_pending: { label: "Deploy pendente", icon: "!", badgeClass: "waiting" },
  rolled_back: { label: "Rollback", icon: "!", badgeClass: "failed" },
  degraded: { label: "Degradado", icon: "!", badgeClass: "waiting" },
  warning: { label: "Aviso", icon: "!", badgeClass: "waiting" },
  failed: { label: "Falha", icon: "!", badgeClass: "failed" },
  online: { label: "Online", icon: "●", badgeClass: "online" },
  offline: { label: "Offline", icon: "○", badgeClass: "offline" },
  unhealthy: { label: "Degradado", icon: "!", badgeClass: "failed" },
  waiting: { label: "Aguardando", icon: "○", badgeClass: "waiting" },
  idle: { label: "Ocioso", icon: "○", badgeClass: "idle" },
  cancelled: { label: "Cancelado", icon: "–", badgeClass: "idle" },
  unknown: { label: "Sem dados", icon: "?", badgeClass: "idle" },
};

window.OpsConsole.STATUS_META = STATUS_META;

window.OpsConsole.phaseToStatusKey = function phaseToStatusKey(phase, envData) {
  const p = phase || "idle";
  if (p === "deploying" || p === "building" || p === "validating" || p === "promoting") return "deploying";
  if (p === "deploy_pending") return "deploy_pending";
  if (p === "rolled_back") return "rolled_back";
  if (p === "degraded") return "degraded";
  if (p === "idle" && envData?.pipelineStatus) {
    const ps = envData.pipelineStatus;
    if (ps === "building" || ps === "validating" || ps === "promoting") return "deploying";
    if (ps === "rolled_back") return "rolled_back";
    if (ps === "failed") return "failed";
  }
  if (p === "failed") return "failed";
  if (p === "offline") return "offline";
  if (p === "unhealthy") return "unhealthy";
  if (p === "degraded") return "degraded";
  if (p === "online") return "online";
  if (p === "healthy") {
    const ld = envData?.lastDeploy?.result || envData?.lastDeployResult;
    if (ld === "warning") return "warning";
    return ld === "success" ? "success" : "online";
  }
  if (p === "idle") {
    if (envData?.deployPending) return "deploy_pending";
    const ld = envData?.lastDeploy?.result || envData?.lastDeployResult;
    if (ld === "failed") return "failed";
    if (ld === "warning") return "warning";
    if (ld === "success") return "success";
    return "online";
  }
  return "unknown";
};

window.OpsConsole.eventTypeToStatusKey = function eventTypeToStatusKey(type) {
  if (!type) return "unknown";
  if (type === "deploy_success" || type === "phase_healthy") return "success";
  if (type === "rollback_success") return "rolled_back";
  if (type === "rollback_failed") return "failed";
  if (type === "rollback_started") return "deploying";
  if (type === "deploy_failed" || type === "phase_failed") return "failed";
  if (type === "deploy_started" || type === "phase_deploying") return "deploying";
  if (type === "deploy_pending" || type === "update_detected") return "deploy_pending";
  if (type.startsWith("sync_") || type === "phase_syncing") return "idle";
  if (type === "sync_idle" || type === "phase_idle") return "idle";
  return "unknown";
};

window.OpsConsole.statusBadgeHtml = function statusBadgeHtml(statusKey, extraClass) {
  const meta = STATUS_META[statusKey] || STATUS_META.unknown;
  const cls = ["status-badge", `status-${meta.badgeClass}`, extraClass].filter(Boolean).join(" ");
  return `<span class="${cls}"><span class="status-icon" aria-hidden="true">${meta.icon}</span><span class="status-text">${window.OpsConsole.escapeHtml(meta.label)}</span></span>`;
};

window.OpsConsole.summaryStatusKey = function summaryStatusKey(envData) {
  const ps = envData?.pipelineStatus;
  if (ps === "building" || ps === "validating" || ps === "promoting") {
    return ps;
  }
  const phase = envData?.displayPhase || envData?.phase || "idle";
  return window.OpsConsole.phaseToStatusKey(phase, envData);
};

window.OpsConsole.availabilityLineHtml = function availabilityLineHtml(data) {
  const avail = data.availability || {};
  const item = (key, label) => {
    const val = avail[key];
    let cls = "avail-bad";
    let icon = "✗";
    if (val === true || val === "ok") {
      cls = "avail-ok";
      icon = "✓";
    } else if (val === "warn" || val === "skip") {
      cls = "avail-warn";
      icon = "!";
    }
    return `<span class="avail-item ${cls}">${label} ${icon}</span>`;
  };
  return `${item("frontend", "Frontend")} ${item("backend", "Backend")} ${item("database", "DB")}`;
};

window.OpsConsole.serviceStatusClass = function serviceStatusClass(status) {
  if (status === "ok") return "service-ok";
  if (status === "fail") return "service-fail";
  return "service-warn";
};

window.OpsConsole.serviceStatusIcon = function serviceStatusIcon(status) {
  if (status === "ok") return "✓";
  if (status === "fail") return "✗";
  return "!";
};

window.OpsConsole.servicesHtml = function servicesHtml(services) {
  const OC = window.OpsConsole;
  if (!services?.length) return "";
  return `<dl class="service-list">${services
    .map((svc) => {
      const cls = OC.serviceStatusClass(svc.status);
      const icon = OC.serviceStatusIcon(svc.status);
      let value = "";
      if (svc.id === "postgres") {
        value = svc.database || "—";
        if (svc.connections != null) value += ` · ${svc.connections} conn`;
        if (svc.sizeHuman) value += ` · ${svc.sizeHuman}`;
      } else {
        value = svc.port ? `:${svc.port}` : "—";
      }
      return `<div class="service-row ${cls}">
        <dt class="service-label">${OC.escapeHtml(svc.name)}</dt>
        <dd class="service-value">${OC.escapeHtml(value)}</dd>
        <dd class="service-status" aria-label="${svc.status === "ok" ? "Saudável" : svc.status === "fail" ? "Erro" : "Atenção"}">${icon}</dd>
      </div>`;
    })
    .join("")}</dl>`;
};

window.OpsConsole.availabilityDotsHtml = window.OpsConsole.availabilityLineHtml;
