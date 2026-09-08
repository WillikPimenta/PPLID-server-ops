/* global window, document, performance */
(function () {
  window.OpsConsole = window.OpsConsole || {};
  const OC = window.OpsConsole;

  const HOST_REFRESH_MS = 15000;
  const HOST_WINDOWS = [
    { value: "1", label: "1h" },
    { value: "6", label: "6h" },
    { value: "24", label: "24h" },
    { value: "168", label: "7d" },
  ];
  OC.hostState = OC.hostState || { hours: "24", rendered: false };
  OC.hostTimer = null;
  OC._hostInFlight = null;
  OC._hostAbort = null;

  function formatBytes(value, rate) {
    const n = Number(value);
    if (!Number.isFinite(n)) return "—";
    const units = ["B", "KB", "MB", "GB", "TB"];
    let amount = Math.max(0, n);
    let index = 0;
    while (amount >= 1024 && index < units.length - 1) {
      amount /= 1024;
      index += 1;
    }
    const digits = amount >= 100 || index === 0 ? 0 : amount >= 10 ? 1 : 2;
    return `${amount.toFixed(digits)} ${units[index]}${rate ? "/s" : ""}`;
  }

  function formatDuration(seconds) {
    const total = Number(seconds);
    if (!Number.isFinite(total)) return "—";
    const days = Math.floor(total / 86400);
    const hours = Math.floor((total % 86400) / 3600);
    const minutes = Math.floor((total % 3600) / 60);
    if (days) return `${days}d ${hours}h`;
    if (hours) return `${hours}h ${minutes}min`;
    return `${minutes}min`;
  }

  function percent(value) {
    const n = Number(value);
    return Number.isFinite(n) ? `${n.toFixed(n >= 10 ? 0 : 1)}%` : "—";
  }

  function pressureTone(value, warn, critical) {
    const n = Number(value);
    if (Number.isFinite(n) && n >= Number(critical)) return "critical";
    if (Number.isFinite(n) && n >= Number(warn)) return "warn";
    return "ok";
  }

  function svgChart(series, label, unit, color) {
    const points = series?.points || [];
    if (!points.length) {
      return `<div class="host-chart-empty">Ainda não há amostras para ${OC.escapeHtml(label)}.</div>`;
    }
    const values = points.map((p) => Number(p.v)).filter(Number.isFinite);
    const max = Math.max(...values, unit === "%" ? 100 : 1);
    const min = unit === "%" ? 0 : Math.min(...values, 0);
    const span = Math.max(max - min, 1);
    const coords = points
      .map((point, index) => {
        const x = points.length === 1 ? 0 : (index / (points.length - 1)) * 100;
        const y = 36 - ((Number(point.v) - min) / span) * 32;
        return `${x.toFixed(2)},${Math.max(2, Math.min(36, y)).toFixed(2)}`;
      })
      .join(" ");
    const latest = values[values.length - 1];
    const latestText = unit === "%" ? percent(latest) : formatBytes(latest, true);
    return `<article class="host-chart-card">
      <div class="host-chart-head"><div><h4>${OC.escapeHtml(label)}</h4><span>${points.length} amostras</span></div><strong>${OC.escapeHtml(latestText)}</strong></div>
      <svg class="host-chart" viewBox="0 0 100 40" preserveAspectRatio="none" role="img" aria-label="Tendência de ${OC.escapeHtml(label)}">
        <line x1="0" y1="36" x2="100" y2="36" class="host-chart-axis"></line>
        <polyline points="${coords}" fill="none" stroke="${color}" stroke-width="1.8" vector-effect="non-scaling-stroke"></polyline>
      </svg>
    </article>`;
  }

  function renderDisks(disks) {
    if (!disks?.length) return '<p class="host-empty">Informações de disco indisponíveis.</p>';
    return `<div class="host-resource-grid">${disks
      .map((disk) => {
        const used = Number(disk.usedPct) || 0;
        const tone = used >= 92 ? "critical" : used >= 85 ? "warn" : "ok";
        return `<article class="host-resource-card is-${tone}">
          <div class="host-resource-head"><strong>${OC.escapeHtml(disk.mount || disk.device || "Disco")}</strong><span>${percent(used)} usado</span></div>
          <div class="host-meter"><span style="width:${Math.max(0, Math.min(100, used))}%"></span></div>
          <p>${formatBytes(disk.freeBytes)} livres de ${formatBytes(disk.totalBytes)}</p>
          ${disk.fileSystem ? `<small>${OC.escapeHtml(disk.fileSystem)}</small>` : ""}
        </article>`;
      })
      .join("")}</div>`;
  }

  function renderGpus(gpus) {
    if (!gpus?.length) {
      return '<p class="host-empty">Nenhuma GPU foi identificada ou o driver não disponibilizou inventário.</p>';
    }
    return `<div class="host-resource-grid">${gpus
      .map((gpu) => {
        const available = gpu.metricsAvailable !== false;
        return `<article class="host-resource-card is-${available ? "info" : "muted"}">
          <div class="host-resource-head"><strong>${OC.escapeHtml(gpu.name || `GPU ${gpu.index || 0}`)}</strong><span>${available ? percent(gpu.utilizationPct) : "inventário"}</span></div>
          <dl class="host-detail-list">
            <div><dt>VRAM</dt><dd>${gpu.memoryUsedPct == null ? formatBytes(gpu.memoryTotalBytes) : `${percent(gpu.memoryUsedPct)} · ${formatBytes(gpu.memoryUsedBytes)} / ${formatBytes(gpu.memoryTotalBytes)}`}</dd></div>
            <div><dt>Temperatura</dt><dd>${gpu.temperatureC == null ? "não disponibilizada" : `${gpu.temperatureC} °C`}</dd></div>
            <div><dt>Potência</dt><dd>${gpu.powerWatts == null ? "não disponibilizada" : `${gpu.powerWatts} W`}</dd></div>
            <div><dt>Driver</dt><dd>${OC.escapeHtml(gpu.driverVersion || "—")}</dd></div>
          </dl>
        </article>`;
      })
      .join("")}</div>`;
  }

  function processTable(processes, empty) {
    if (!processes?.length) return `<p class="host-empty">${OC.escapeHtml(empty)}</p>`;
    return `<div class="ops-table-wrap"><table class="host-process-table">
      <thead><tr><th>Serviço</th><th>Ambiente</th><th>PID</th><th>CPU</th><th>RAM</th><th>Uptime</th><th>Estado</th></tr></thead>
      <tbody>${processes
        .map(
          (proc) => `<tr>
            <td><strong>${OC.escapeHtml(proc.service || proc.name || "processo")}</strong><small>${proc.service ? OC.escapeHtml(proc.name || "") : ""}</small></td>
            <td>${OC.escapeHtml(proc.environment || "—")}</td><td><code>${Number(proc.pid) || "—"}</code></td>
            <td>${percent(proc.cpuPct)}</td><td>${formatBytes(proc.memoryBytes)}</td><td>${formatDuration(proc.uptimeSec)}</td>
            <td><span class="host-process-status">${OC.escapeHtml(proc.status || "—")}</span></td>
          </tr>`
        )
        .join("")}</tbody></table></div>`;
  }

  function commitProcessTable(processes) {
    if (!processes?.length) return '<p class="host-empty">Consumo por processo indisponível.</p>';
    return `<div class="ops-table-wrap"><table class="host-process-table">
      <thead><tr><th>Processo</th><th>PID</th><th>Memória comprometida</th><th>RAM residente</th><th>Uptime</th><th>Estado</th></tr></thead>
      <tbody>${processes.map((proc) => `<tr>
        <td><strong>${OC.escapeHtml(proc.name || "processo")}</strong>${proc.service ? `<small>${OC.escapeHtml(`${proc.environment || "HOST"} · ${proc.service}`)}</small>` : ""}</td>
        <td><code>${Number(proc.pid) || "—"}</code></td><td><strong>${formatBytes(proc.commitBytes)}</strong></td>
        <td>${formatBytes(proc.memoryBytes)}</td><td>${formatDuration(proc.uptimeSec)}</td>
        <td><span class="host-process-status">${OC.escapeHtml(proc.status || "—")}</span></td>
      </tr>`).join("")}</tbody></table></div>`;
  }

  function renderOrphanBots(data) {
    const bots = data.orphanBots || {};
    const failed = Number(bots.failed) || (bots.ok === false ? 1 : 0);
    const items = (bots.items || []).filter(
      (item) => item?.classification === "orphan" && !["stopped", "already-stopped"].includes(item.result)
    );
    const detected = items.length;
    const stopped = Number(bots.stopped) || 0;
    const tone = failed ? "critical" : detected ? "warn" : "ok";
    const rows = items.map((item) => `<tr>
      <td><strong>${OC.escapeHtml(item.mode || "robot_runner")}</strong><small>${OC.escapeHtml(item.reason || "Sem vínculo com a release ativa")}</small></td>
      <td>${OC.escapeHtml(item.environment || "Não identificado")}</td>
      <td><code>${OC.escapeHtml(item.release || "desconhecida")}</code><small>ativa: ${OC.escapeHtml(item.activeRelease || "desconhecida")}</small></td>
      <td><code>${Number(item.pid) || "—"}</code></td>
      <td>${OC.escapeHtml(OC.formatDate(item.creationDate))}</td>
      <td><span class="host-process-status is-orphan">Órfão</span></td>
    </tr>`).join("");
    const detail = failed
      ? `<p class="host-orphan-error">${OC.escapeHtml(bots.error || `${failed} falha(s) na última operação`)}</p>`
      : "";
    const content = items.length
      ? `<div class="ops-table-wrap"><table class="host-process-table host-orphan-table"><thead><tr><th>Bot</th><th>Ambiente</th><th>Release</th><th>PID</th><th>Iniciado</th><th>Estado</th></tr></thead><tbody>${rows}</tbody></table></div>`
      : `<p class="host-empty">Nenhum bot órfão está em execução.</p>`;
    return `<article class="host-resource-card host-orphan-card is-${tone}">
      <div class="host-resource-head host-orphan-head"><div><strong>Varredura atual</strong><span>${detected} órfão(s) ativo(s)${stopped ? ` · ${stopped} encerrado(s) na última ação` : ""}</span></div>
        <button type="button" class="btn btn-danger btn-sm" data-host-stop-orphans ${detected && !OC.hostState.orphanCleanupBusy ? "" : "disabled"}>${OC.hostState.orphanCleanupBusy ? "Encerrando…" : "Parar todos os órfãos"}</button>
      </div>
      <p>Atualizado ${bots.scannedAt ? OC.escapeHtml(OC.formatRelativeTime?.(bots.scannedAt) || OC.formatDate(bots.scannedAt)) : "—"}</p>
      ${detail}${content}
    </article>`;
  }

  function bindOrphanActions(root) {
    if (root.dataset.orphanActionsBound === "true") return;
    root.dataset.orphanActionsBound = "true";
    root.addEventListener("click", async (event) => {
      const button = event.target.closest("[data-host-stop-orphans]");
      if (!button || OC.hostState.orphanCleanupBusy) return;
      const count = Number(OC.lastHostSummary?.orphanBots?.items?.length) || 0;
      if (!count || !OC.confirmAction(`Parar todos os ${count} bot(s) órfão(s) detectados no host?`)) return;
      OC.hostState.orphanCleanupBusy = true;
      button.disabled = true;
      button.classList.add("is-loading");
      button.textContent = "Encerrando…";
      try {
        const result = await OC.fetchJson("/api/v1/actions/orphan-bots/cleanup", {
          method: "POST",
          body: "{}",
          timeoutMs: 130000,
        });
        OC.showToast(`${Number(result.stopped) || 0} bot(s) órfão(s) encerrado(s)`, result.ok ? "success" : "warn");
        if (!result.ok && result.error) OC.showToast(result.error, "warn");
      } catch (err) {
        OC.showToast(err.message || "Falha ao encerrar bots órfãos", "error");
      } finally {
        OC.hostState.orphanCleanupBusy = false;
        await OC.refreshHost({ force: true });
      }
    });
  }

  function hostKpis(data) {
    const thresholds = data.thresholds || {};
    const cpu = data.cpu?.usedPct;
    const memory = data.memory?.usedPct;
    const commit = data.commit?.usedPct;
    const disk = (data.disks || []).reduce((max, item) => Math.max(max, Number(item.usedPct) || 0), 0);
    const gpu = (data.gpus || []).reduce((max, item) => Math.max(max, Number(item.utilizationPct) || 0), 0);
    return [
      { key: "cpu", label: "CPU", value: percent(cpu), hint: `${data.cpu?.physicalCores || "—"} núcleos físicos`, tone: pressureTone(cpu, thresholds.cpuWarnPct, thresholds.cpuCriticalPct), icon: "pulse" },
      { key: "memory", label: "Memória RAM", value: percent(memory), hint: `${formatBytes(data.memory?.usedBytes)} de ${formatBytes(data.memory?.totalBytes)}`, tone: pressureTone(memory, thresholds.memoryWarnPct, thresholds.memoryCriticalPct), icon: "pulse" },
      { key: "commit", label: "Memória comprometida", value: percent(commit), hint: data.commit?.supported ? `${formatBytes(data.commit?.usedBytes)} de ${formatBytes(data.commit?.limitBytes)}` : "não disponibilizada", tone: data.commit?.supported ? pressureTone(commit, thresholds.commitWarnPct, thresholds.commitCriticalPct) : "info", icon: "pulse" },
      { key: "disk", label: "Maior uso de disco", value: percent(disk), hint: `${data.disks?.length || 0} volume(s)`, tone: disk >= 92 ? "critical" : disk >= 85 ? "warn" : "ok", icon: "pulse" },
      { key: "gpu", label: "GPU", value: data.gpus?.length ? percent(gpu) : "—", hint: data.gpus?.length ? `${data.gpus.length} adaptador(es)` : "não identificada", tone: "info", icon: "pulse" },
    ];
  }

  function kpiMarkup(items) {
    return `<div class="ops-kpi-grid monitor-kpi-grid">${items
      .map((item) => `<article class="ops-kpi-card monitor-kpi-card is-${item.tone}" data-host-kpi="${item.key}">
        <div class="ops-kpi-label monitor-kpi-label"><span class="ops-kpi-icon monitor-kpi-icon">${OC.opsSvgIcon?.(item.icon) || ""}</span>${OC.escapeHtml(item.label)}</div>
        <p class="ops-kpi-value monitor-kpi-value" data-host-value>${OC.escapeHtml(item.value)}</p>
        <p class="ops-kpi-hint monitor-kpi-hint" data-host-hint>${OC.escapeHtml(item.hint)}</p>
      </article>`).join("")}</div>`;
  }

  function renderHost(data, series, incremental) {
    const root = document.getElementById("view-host");
    if (!root) return;
    const kpis = hostKpis(data);
    const collector = data.collector || {};
    const stale = collector.status !== "ok";
    const charts = `${svgChart(series.cpu, "CPU", "%", "#2a5595")}${svgChart(series.memory, "Memória RAM", "%", "#77127b")}${svgChart(series.commit, "Memória comprometida", "%", "#c26b18")}${svgChart(series.network, "Recebimento de rede", "B/s", "#1087aa")}`;
    const dynamic = {
      disks: renderDisks(data.disks),
      gpus: renderGpus(data.gpus),
      processes: processTable(data.pplidProcesses, "Nenhum processo PPLID foi identificado com as permissões atuais."),
      commitProcesses: commitProcessTable(data.topCommitProcesses),
      charts,
      orphanBots: renderOrphanBots(data),
      network: `<div class="host-network"><div><span>Recebendo</span><strong>${formatBytes(data.network?.rxBps, true)}</strong></div><div><span>Enviando</span><strong>${formatBytes(data.network?.txBps, true)}</strong></div></div>`,
    };

    if (incremental && OC.hostState.rendered && root.querySelector("[data-host-shell]")) {
      kpis.forEach((item) => {
        const card = root.querySelector(`[data-host-kpi="${item.key}"]`);
        if (!card) return;
        card.className = `ops-kpi-card monitor-kpi-card is-${item.tone}`;
        const value = card.querySelector("[data-host-value]");
        const hint = card.querySelector("[data-host-hint]");
        if (value) value.textContent = item.value;
        if (hint) hint.textContent = item.hint;
      });
      Object.entries(dynamic).forEach(([key, html]) => {
        const el = root.querySelector(`[data-host-dynamic="${key}"]`);
        if (el) el.innerHTML = html;
      });
      const freshness = root.querySelector("[data-host-freshness]");
      if (freshness) freshness.textContent = stale ? "Coleta desatualizada" : `Atualizado ${OC.formatRelativeTime?.(data.generatedAt) || OC.formatDate(data.generatedAt)}`;
      return;
    }

    root.innerHTML = `<div data-host-shell class="host-view">
      ${OC.renderOpsHero({
        title: "Desempenho do computador",
        subtitle: "Recursos do host Windows e consumo dos serviços PPLID.",
        stats: [
          { label: "Host", value: data.hostname || "—" },
          { label: "Uptime", value: formatDuration(data.uptimeSec) },
          { label: "Coletor", value: stale ? "Desatualizado" : collector.mode === "degraded" ? "Limitado" : "Ativo" },
          { label: "Intervalo", value: `${collector.intervalSec || 15}s` },
        ],
      })}
      <div class="host-toolbar">
        <div>${OC.renderOpsChipToolbar({ id: "host-window", label: "Janela", options: HOST_WINDOWS, value: OC.hostState.hours, attr: "data-host-chip" })}</div>
        <div class="host-toolbar-actions"><span class="host-freshness ${stale ? "is-stale" : ""}" data-host-freshness>${stale ? "Coleta desatualizada" : `Atualizado ${OC.formatRelativeTime?.(data.generatedAt) || OC.formatDate(data.generatedAt)}`}</span><a class="btn btn-secondary btn-sm" href="/api/v1/diagnostics/snapshot" download>Baixar diagnóstico</a></div>
      </div>
      ${kpiMarkup(kpis)}
      ${OC.renderOpsSection({ title: "Tendências", hint: `Últimas ${OC.hostState.hours}h`, body: `<div class="host-chart-grid" data-host-dynamic="charts">${dynamic.charts}</div>` })}
      <div class="host-two-column">
        ${OC.renderOpsSection({ title: "Discos", hint: `${formatBytes(data.diskIo?.readBps, true)} leitura · ${formatBytes(data.diskIo?.writeBps, true)} gravação`, body: `<div data-host-dynamic="disks">${dynamic.disks}</div>` })}
        ${OC.renderOpsSection({ title: "Rede", hint: `${data.network?.interfaces?.length || 0} interface(s) ativa(s)`, body: `<div data-host-dynamic="network">${dynamic.network}</div>` })}
      </div>
      ${OC.renderOpsSection({ title: "GPU", hint: "Utilização depende do suporte do driver", body: `<div data-host-dynamic="gpus">${dynamic.gpus}</div>` })}
      ${OC.renderOpsSection({ title: "Maiores consumidores de memória comprometida", hint: "Commit privado; capacidade combinada de RAM e arquivo de paginação", body: `<div data-host-dynamic="commitProcesses">${dynamic.commitProcesses}</div>` })}
      ${OC.renderOpsSection({ title: "Processos e serviços PPLID", hint: "Sem linhas de comando ou variáveis sensíveis", body: `<div data-host-dynamic="processes">${dynamic.processes}</div>` })}
    </div>`;
    const orphanSection = OC.renderOpsSection({ title: "Bots órfãos", hint: "Verificação global no host; falhas não bloqueiam deploy", body: `<div data-host-dynamic="orphanBots">${dynamic.orphanBots}</div>` });
    root.insertAdjacentHTML("beforeend", orphanSection);
    bindOrphanActions(root);
    OC.hostState.rendered = true;
    OC.bindOpsChipToolbar(root, "data-host-chip", (_id, value) => {
      OC.hostState.hours = String(value || "24");
      OC.hostState.rendered = false;
      OC.refreshHost({ force: true });
    });
  }

  OC.showHostLoading = function showHostLoading() {
    const root = document.getElementById("view-host");
    if (root && !OC.hostState.rendered) {
      root.innerHTML = '<div class="loading-panel"><div class="loading-spinner"></div><p>Coletando recursos do computador…</p></div>';
    }
  };

  OC.refreshHost = async function refreshHost(options = {}) {
    if (OC._hostInFlight && !options.force) return OC._hostInFlight;
    if (OC._hostAbort) OC._hostAbort.abort();
    OC._hostAbort = new AbortController();
    const signal = OC._hostAbort.signal;
    const started = typeof performance !== "undefined" ? performance.now() : Date.now();
    const run = async () => {
      try {
        const hours = Number(OC.hostState.hours || 24);
        const optionalSeries = async (metric) => {
          try {
            return await OC.fetchJson(`/api/v1/host/series?metric=${metric}&hours=${hours}`, { signal });
          } catch (err) {
            if (err.code === "aborted") throw err;
            return { metricKey: metric, points: [], unavailable: true };
          }
        };
        const [summary, orphanBots, cpu, memory, commit, network] = await Promise.all([
          OC.fetchJson("/api/v1/host/summary", { signal }),
          OC.fetchJson("/api/v1/host/orphan-bots", { signal, timeoutMs: 65000 }),
          OC.fetchJson(`/api/v1/host/series?metric=host_cpu_pct&hours=${hours}`, { signal }),
          OC.fetchJson(`/api/v1/host/series?metric=host_memory_used_pct&hours=${hours}`, { signal }),
          optionalSeries("host_commit_used_pct"),
          OC.fetchJson(`/api/v1/host/series?metric=host_net_rx_bps&hours=${hours}`, { signal }),
        ]);
        summary.orphanBots = orphanBots;
        OC.lastHostSummary = summary;
        renderHost(summary, { cpu, memory, commit, network }, !options.force);
        // Host view is refreshed by its own timer, so keep the active session
        // alive just like the main dashboard refresh does.
        OC.resetIdleTimer?.();
        const elapsed = (typeof performance !== "undefined" ? performance.now() : Date.now()) - started;
        OC.perfRecord?.({ name: "render:host", ms: Math.round(elapsed * 10) / 10 });
      } catch (err) {
        if (err.code === "aborted") return;
        const root = document.getElementById("view-host");
        if (root) root.innerHTML = `<p class="global-error" role="alert">Erro ao carregar recursos do computador: ${OC.escapeHtml(err.message)}</p>`;
      }
    };
    OC._hostInFlight = run().finally(() => {
      OC._hostInFlight = null;
    });
    return OC._hostInFlight;
  };

  OC.startHostRefresh = function startHostRefresh() {
    OC.stopHostRefresh();
    OC.hostTimer = setInterval(() => {
      if (!document.hidden && !OC._hostInFlight) OC.refreshHost();
    }, HOST_REFRESH_MS);
  };

  OC.stopHostRefresh = function stopHostRefresh() {
    if (OC.hostTimer) clearInterval(OC.hostTimer);
    OC.hostTimer = null;
    if (OC._hostAbort) OC._hostAbort.abort();
  };

  OC.refreshHomeHostSummary = async function refreshHomeHostSummary() {
    const age = Date.now() - Number(OC._homeHostFetchedAt || 0);
    if (OC._homeHostInFlight || (OC.lastHostSummary && age < 10000)) return;
    OC._homeHostInFlight = OC.fetchJson("/api/v1/host/summary")
      .then((data) => {
        OC.lastHostSummary = data;
        OC._homeHostFetchedAt = Date.now();
        if (OC.currentRoute?.view === "deploy" && OC.lastOverview) OC.renderDeployHomeShell?.(OC.lastOverview);
      })
      .catch(() => {})
      .finally(() => {
        OC._homeHostInFlight = null;
      });
    return OC._homeHostInFlight;
  };
})();
