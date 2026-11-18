/* global window, document */
(function () {
  const OC = window.OpsConsole = window.OpsConsole || {};
  let controller = null;
  let generation = 0;
  let returnFocus = null;

  const escape = value => OC.escapeHtml(String(value ?? ''));
  const date = value => value ? OC.formatDate(value) : 'Sem registro';
  const duration = seconds => seconds == null ? 'Sem dados' : seconds < 60 ? `${Math.round(seconds)}s` : seconds < 3600 ? `${Math.floor(seconds / 60)}min ${Math.round(seconds % 60)}s` : `${Math.floor(seconds / 3600)}h ${Math.round(seconds % 3600 / 60)}min`;

  OC.renderAvailabilityDetail = function renderAvailabilityDetail(selection) {
    const data = selection.data;
    const health = data?.health || {};
    const impact = data?.impact || {};
    const latency = data?.latency || {};
    const timeline = data?.timeline || [];
    const api = selection.api;
    const verified = api && !api.error && api.periodVerified === true;
    const cached = [data, api].filter(item => item?._cache?.stale).map(item => `Última resposta de ${date(item._cache.savedAt)}: ${item._cache.error}`).join('; ');
    const list = timeline.slice(0, 30).map(item => `<li><time>${escape(date(item.startedAt))}</time><strong>${escape(item.kind === 'deploy' ? 'Deploy' : item.kind === 'outage' ? 'Episódio de falha' : item.title || 'Evento')}</strong><small>${escape(item.kind === 'outage' ? `${item.ongoing ? 'Em andamento; ' : ''}${item.uncertain ? 'Continuidade incerta; ' : ''}tempo observado ${duration(item.observedDurationSec ?? item.durationSec)}` : item.result || item.status || 'Registrado')}</small></li>`).join('');
    const healthPoints = (data?.history || []).map(point => `<span class="av-detail-sample ${!point.samples ? 'is-missing' : point.failures ? 'is-failed' : ''}" title="${escape(`${date(point.since)}: ${point.samples} amostras, ${point.failures} falhas`)}"></span>`).join('');
    const latencyPoints = (latency.points || []).filter(point => Number.isFinite(point.avgMs) && Number.isFinite(Date.parse(point.since)));
    const max = Math.max(1, ...latencyPoints.map(point => point.avgMs));
    const span = Math.max(1, Date.parse(selection.until) - Date.parse(selection.since));
    const pointPosition = point => `${8 + (Date.parse(point.since) - Date.parse(selection.since)) * 584 / span},${92 - point.avgMs / max * 80}`;
    const groups = [];
    latencyPoints.forEach((point, index) => {
      if (!index || Date.parse(point.since) - Date.parse(latencyPoints[index - 1].since) > span / 60 * 1.5) groups.push([]);
      groups[groups.length - 1].push(point);
    });
    const chart = latencyPoints.length ? `<svg class="av-detail-latency" viewBox="0 0 600 100" role="img" aria-label="Latência média das sondagens por intervalo">${groups.map(group => group.length === 1 ? `<circle cx="${pointPosition(group[0]).split(',')[0]}" cy="${pointPosition(group[0]).split(',')[1]}" r="3" fill="currentColor" />` : `<polyline points="${group.map(pointPosition).join(' ')}" fill="none" stroke="currentColor" stroke-width="2.5" />`).join('')}</svg>` : '<p>Sem amostras de latência neste intervalo.</p>';
    const apiMarkup = selection.apiLoading ? '<p role="status">Consultando métricas do intervalo…</p>' : !verified ? `<p>${escape(api?.error || 'A fonte não confirmou os limites deste intervalo. Não há totais confiáveis para exibir.')}</p>` : `<p><strong>${escape(api.totals?.requests ?? api.totals?.count ?? 'Sem dados')}</strong> requisições <span> / </span><strong>${escape(api.totals?.errors5xx ?? api.totals?.errors_5xx ?? api.totals?.status5xx ?? 'Sem dados')}</strong> erros 5xx</p><ul class="av-detail-routes">${(api.slowRoutes || api.routeStats || []).slice(0, 5).map(route => `<li>${escape(route.method)} ${escape(route.route)} <small>${escape(route.avgMs ?? route.avg_ms ?? '—')} ms em média</small></li>`).join('')}</ul>`;
    const query = new URLSearchParams({ env: selection.env, envs: selection.env, since: selection.since, until: selection.until, period: 'custom' });
    const apiQuery = new URLSearchParams(query);
    apiQuery.set('section', 'overview');
    const links = selection.demo ? '<p class="av-detail-notice">Demonstração: a investigação usa apenas dados simulados. As fontes reais não são consultadas.</p>' : `<nav class="av-detail-links"><a class="btn btn-secondary btn-sm" href="/monitoring/apis?${apiQuery}" data-av-detail-link>Investigar APIs</a><a class="btn btn-secondary btn-sm" href="/monitoring/logs?${query}" data-av-detail-link>Ver logs do intervalo</a></nav>`;
    return `<header class="av-detail-header"><div><span>Investigação de disponibilidade${selection.demo ? ' · Dados simulados' : ''}</span><h2 id="av-detail-title">${escape(selection.env)}</h2><p>${escape(date(selection.since))} → ${escape(date(selection.until))}</p></div><button type="button" class="av-detail-close" data-av-detail-close aria-label="Fechar investigação">×</button></header>
      <div class="av-detail-body">${selection.loading ? '<p role="status">Carregando sondagens e eventos locais…</p>' : ''}${selection.error || cached ? `<p class="av-detail-notice" role="alert">${escape(selection.error || cached)}</p>` : ''}
      ${data ? `<section><h3>Impacto observado</h3><dl class="av-detail-kpis"><div><dt>Episódios de falha</dt><dd>${escape(impact.incidentCount ?? 'Sem dados')}</dd></div><div><dt>Indisponibilidade estimada</dt><dd>${escape(duration(impact.estimatedDowntimeSec))}</dd></div><div><dt>Sem cobertura</dt><dd>${escape(duration(impact.unknownSec))}</dd></div><div><dt>Cobertura da coleta</dt><dd>${health.coveragePct == null ? 'Sem dados' : `${escape(health.coveragePct)}%`}</dd></div></dl></section>
      <section><h3>Sondagens do intervalo</h3><p>${escape(health.samples ?? 0)} amostras; ${escape(health.successful ?? 0)} bem-sucedidas. ${health.lastSampleAt ? `Última: ${escape(date(health.lastSampleAt))}.` : ''}</p><div class="av-detail-samples">${healthPoints}</div><p class="av-detail-caption">Verde: sem falhas observadas · vermelho: com falhas · cinza: sem coleta.</p></section>
      <section><h3>Latência do health check</h3><p>Média ${escape(latency.avgMs ?? '—')} ms / máxima ${escape(latency.maxMs ?? '—')} ms</p>${chart}<p class="av-detail-caption">Medições da sonda; não representa a latência de todas as requisições da aplicação.</p></section>
      <section><h3>Incidentes e deploys</h3>${list ? `<ol class="av-detail-events">${list}</ol>` : '<p>Sem eventos registrados neste intervalo.</p>'}${data.timelineTruncated || timeline.length > 30 ? '<p>Lista limitada aos registros mais recentes.</p>' : ''}</section>` : ''}
      <section><h3>Métricas de API</h3>${apiMarkup}</section>${links}</div>`;
  };

  function ensurePanel() {
    let overlay = document.getElementById('av-detail-overlay');
    if (!overlay) {
      overlay = document.createElement('div');
      overlay.id = 'av-detail-overlay';
      overlay.className = 'av-detail-overlay';
      overlay.innerHTML = '<aside id="av-detail-panel" class="av-detail-panel" role="dialog" aria-modal="true" aria-labelledby="av-detail-title" tabindex="-1"></aside>';
      document.body.appendChild(overlay);
      overlay.addEventListener('click', event => { if (event.target === overlay) OC.closeAvailabilityDetail(); });
      overlay.addEventListener('keydown', event => {
        if (event.key === 'Escape') { event.preventDefault(); OC.closeAvailabilityDetail(); }
        if (event.key !== 'Tab') return;
        const nodes = [...overlay.querySelectorAll('button, a[href], select, [tabindex="0"]')];
        if (!nodes.length) { event.preventDefault(); return; }
        const first = nodes[0], last = nodes[nodes.length - 1];
        if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
        else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
      });
    }
    return overlay;
  }

  function paint() {
    const selection = OC.monitorState.availabilitySelection;
    if (!selection) return;
    const overlay = ensurePanel();
    const focusedClose = document.activeElement?.hasAttribute?.('data-av-detail-close');
    const focusedHref = document.activeElement?.closest?.('[data-av-detail-link]')?.getAttribute('href');
    const panel = overlay.querySelector('aside');
    const scroll = panel.querySelector('.av-detail-body')?.scrollTop || 0;
    panel.innerHTML = OC.renderAvailabilityDetail(selection);
    panel.querySelector('.av-detail-body').scrollTop = scroll;
    panel.querySelector('[data-av-detail-close]').onclick = () => OC.closeAvailabilityDetail();
    panel.querySelectorAll('[data-av-detail-link]').forEach(link => link.addEventListener('click', () => OC.closeAvailabilityDetail(false)));
    overlay.hidden = false;
    document.body.classList.add('av-investigation-open');
    if (focusedClose) panel.querySelector('[data-av-detail-close]').focus();
    else if (focusedHref) [...panel.querySelectorAll('[data-av-detail-link]')].find(link => link.getAttribute('href') === focusedHref)?.focus();
  }

  function markSelection() {
    const selection = OC.monitorState.availabilitySelection;
    document.querySelectorAll('[data-availability-since]').forEach(button => {
      const center = selection && (Date.parse(selection.since) + Date.parse(selection.until)) / 2;
      const active = selection && button.dataset.availabilityEnv === selection.env && Date.parse(button.dataset.availabilitySince) <= center && Date.parse(button.dataset.availabilityUntil) > center;
      button.classList.toggle('is-selected', Boolean(active));
      button.setAttribute('aria-pressed', String(Boolean(active)));
    });
  }

  OC.closeAvailabilityDetail = function closeAvailabilityDetail(restoreFocus = true) {
    const selectedButton = document.querySelector('[data-availability-since].is-selected');
    generation += 1;
    controller?.abort();
    controller = null;
    if (OC.monitorState) OC.monitorState.availabilitySelection = null;
    const overlay = document.getElementById('av-detail-overlay');
    if (overlay) overlay.hidden = true;
    document.body.classList.remove('av-investigation-open');
    markSelection();
    if (restoreFocus && returnFocus?.isConnected) returnFocus.focus();
    else if (restoreFocus) selectedButton?.focus();
  };

  OC.openAvailabilityDetail = async function openAvailabilityDetail(env, since, until, trigger) {
    const start = new Date(since), end = new Date(until);
    if (!OC.ENV_ORDER.includes(env) || !Number.isFinite(start.getTime()) || end <= start || !Number.isFinite(end.getTime())) return;
    controller?.abort();
    controller = new AbortController();
    const signal = controller.signal;
    const token = ++generation;
    returnFocus = trigger || document.activeElement;
    const demo = OC.monitorState.availabilityDemo === true;
    const selection = { env, since, until, demo, period: OC.monitorState.availabilityWindow || '24h', loading: true, apiLoading: true };
    OC.monitorState.availabilitySelection = selection;
    markSelection();
    paint();
    document.querySelector('#av-detail-panel [data-av-detail-close]')?.focus();
    const stillCurrent = () => token === generation && OC.monitorState.availabilitySelection === selection;
    if (demo) {
      const fixture = OC.buildAvailabilityDemo(selection.period, OC.monitorState.availabilityScenario)[env];
      const buckets = fixture.history.filter(bucket => new Date(bucket.since) < end && new Date(bucket.until) > start);
      const samples = buckets.reduce((sum, bucket) => sum + bucket.samples, 0);
      const failures = buckets.reduce((sum, bucket) => sum + bucket.failures, 0);
      const timeline = fixture.timeline.filter(item => new Date(item.startedAt) < end && (item.kind === 'outage' ? (!item.finishedAt || new Date(item.finishedAt) >= start) : new Date(item.startedAt) >= start));
      selection.data = { ...fixture, since, until, history: buckets, timeline,
        health: { samples, successful: samples - failures, coveragePct: samples ? 100 : 0, lastSampleAt: samples ? until : null },
        impact: { incidentCount: timeline.filter(item => item.kind === 'outage').length, estimatedDowntimeSec: failures * 60, unknownSec: samples ? 0 : (end - start) / 1000 },
        latency: { avgMs: samples ? 145 : null, maxMs: samples ? 820 : null, points: buckets.filter(bucket => bucket.samples).map((bucket, i) => ({ since: bucket.since, avgMs: 100 + i % 5 * 32 })) } };
      selection.api = samples ? { periodVerified: true, totals: { requests: samples * 12, errors5xx: failures * 2 }, slowRoutes: [{ method: 'GET', route: '/api/v1/items/', avgMs: 220 }] } : { error: 'Sem dados simulados neste intervalo' };
      selection.loading = selection.apiLoading = false;
      paint();
      return;
    }
    const params = new URLSearchParams({ since, until });
    const request = async (url, target) => {
      try {
        const data = await OC.fetchMonitoringJson(url, {}, { signal, timeoutMs: 30000 });
        if (!stillCurrent()) return;
        if (target === 'data') {
          if (data.error) selection.error = data.error;
          else selection.data = data;
        } else {
          const actualStart = new Date(data.since), actualEnd = new Date(data.until);
          const tolerance = Math.min(1000, (end - start) / 100);
          selection.api = { ...data, periodVerified: !data.error && Math.abs(actualStart - start) <= tolerance && Math.abs(actualEnd - end) <= tolerance };
        }
      } catch (error) {
        if (!stillCurrent() || signal.aborted) return;
        if (target === 'data') selection.error = error.message;
        else selection.api = { error: error.message };
      } finally {
        if (stillCurrent()) {
          selection[target === 'data' ? 'loading' : 'apiLoading'] = false;
          paint();
        }
      }
    };
    await Promise.all([
      request(`/api/v1/monitoring/${env}/availability-detail?${params}`, 'data'),
      request(`/api/v1/monitoring/${env}/api-routes?window=custom&${params}`, 'api'),
    ]);
  };

  OC.bindAvailabilityDetails = function bindAvailabilityDetails(root) {
    const selection = OC.monitorState.availabilitySelection;
    if (selection && (OC.currentRoute?.tab !== 'availability' || selection.period !== OC.monitorState.availabilityWindow || selection.demo !== OC.monitorState.availabilityDemo || (!selection.demo && !OC.monitorState.selectedEnvs.includes(selection.env)))) {
      OC.closeAvailabilityDetail(false);
    }
    root.querySelectorAll('[data-availability-since]').forEach(button => {
      button.addEventListener('click', () => OC.openAvailabilityDetail(button.dataset.availabilityEnv, button.dataset.availabilitySince, button.dataset.availabilityUntil, button));
    });
    markSelection();
  };
})();
