/* global window, document */
(function () {
  const OC = window.OpsConsole = window.OpsConsole || {};
  const escape = value => OC.escapeHtml(String(value ?? ''));
  const date = value => value ? escape(new Date(value).toLocaleString('pt-BR', { timeZone: 'America/Sao_Paulo', year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit',fractionalSecondDigits:3 })) : 'Sem registro';
  const duration = ms => ms == null ? 'Não confirmada' : ms < 1000 ? `${ms} ms` : `${(ms / 1000).toLocaleString('pt-BR', { maximumFractionDigits: 3 })} s`;
  const clock = value => escape(new Date(value).toLocaleTimeString('pt-BR',{timeZone:'America/Sao_Paulo',hour:'2-digit',minute:'2-digit',second:'2-digit',fractionalSecondDigits:3}));
  const labels = { enabled: 'Coleta ativada', congestion_start: 'Fila detectada', congestion_ongoing: 'Fila observada', recovered: 'Fila esvaziada' };
  const S = OC.congestionState = { env: 'MAIN', window: '24h', since: '', until: '', follow: true, overview: null,
    detail: null, episodeId: null, snapshotId: null, episodeOffset: 0, snapshotOffset: 0, error: '', generation: 0, controller: null, busy: false, routeKey: null, resolved: false,
    demo:false,scenario:'sustained',demoAnchor:Date.now() };
  const isActive = () => OC.currentRoute?.view === 'monitoring' && OC.currentRoute?.tab === 'congestion';

  function routeState() {
    const query = OC.currentRoute?.query || {};
    const key = JSON.stringify([query.env, query.since, query.until, query.episode]);
    if (S.routeKey === key) return;
    S.routeKey = key;
    const env = String(query.env || S.env).toUpperCase();
    if (['MAIN', 'DEV', 'HOM'].includes(env)) S.env = env;
    if (query.since && query.until) { S.window = 'custom'; S.since = query.since; S.until = query.until; }
    S.episodeId = query.episode || null;
    if (query.episode || (query.since && query.until)) S.follow=false;
    S.overview = S.detail = null; S.episodeOffset = S.snapshotOffset = 0; S.snapshotId = null;
  }

  OC.renderCongestion = function () {
    routeState();
    return `<link rel="stylesheet" href="/congestion.css?v=20261119-history">
      <div class="congestion-view" data-congestion-root>
        <div class="cg-toolbar">
          <label>Ambiente<select data-cg-env>${['MAIN', 'HOM', 'DEV'].map(env => `<option ${env === S.env ? 'selected' : ''}>${env}</option>`).join('')}</select></label>
          <label>Período<select data-cg-window>${[['1h','Última hora'],['6h','Últimas 6 horas'],['24h','Últimas 24 horas'],['7d','Últimos 7 dias'],['custom','Personalizado']].map(([key,text]) => `<option value="${key}" ${S.window === key ? 'selected' : ''}>${text}</option>`).join('')}</select></label>
          <label class="cg-follow"><input type="checkbox" data-cg-follow ${S.follow ? 'checked' : ''}> Acompanhar novos registros</label>
          <button type="button" class="btn btn-secondary" data-cg-refresh>Atualizar</button>
          <button type="button" class="btn btn-secondary" data-cg-demo aria-pressed="${S.demo}">${S.demo?'Voltar aos dados reais':'Dados fictícios'}</button>
          <label data-cg-scenario-control ${S.demo?'':'hidden'}>Cenário<select data-cg-scenario>${[['short','Fila curta'],['sustained','Saturação e recuperação'],['partial','Reinício e coleta incompleta']].map(([key,label])=>`<option value="${key}" ${S.scenario===key?'selected':''}>${label}</option>`).join('')}</select></label>
        </div>
        ${S.demo?'<div class="cg-demo-banner" role="status"><strong>Modo demonstração · dados fictícios</strong><span>Estes valores não refletem o ambiente selecionado. Nenhuma API de dados reais é consultada e a extração de logs de produção fica desabilitada.</span></div>':''}
        <form class="cg-custom" data-cg-custom ${S.window === 'custom' ? '' : 'hidden'}>
          <label>De<input type="datetime-local" data-cg-since value="${escape(localInput(S.since))}" required></label>
          <label>Até<input type="datetime-local" data-cg-until value="${escape(localInput(S.until))}" required></label>
          <button class="btn btn-secondary" type="submit">Aplicar período</button><span>Máximo de sete dias · horário local do navegador</span>
        </form>
        <p class="cg-caption">Histórico em Brasília. Tempos e ocupação são observações dos snapshots, não medições contínuas.</p>
        <p data-cg-error role="alert" hidden></p>
        <div data-cg-content><div class="loading-inline">Carregando evidências locais…</div></div>
      </div>`;
  };

  function localInput(value) {
    if (!value || !Number.isFinite(Date.parse(value))) return '';
    const d = new Date(value);
    return new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
  }

  function collectionMarkup(overview) {
    const status = overview.collection || {};
    const sources = status.sources || [];
    const base = sources[sources.length - 1];
    const state = S.demo ? 'Fonte simulada no navegador' : !status.enabled ? 'Coletor desativado' : status.collector_stale ? 'Coletor sem atualização recente' : status.pending_import ? 'Importando histórico disponível' : base?.status === 'unreadable' ? 'Arquivo atual sem acesso' : base?.status === 'missing' ? 'Arquivo atual não encontrado' : 'Leitura local atualizada';
    const warning = !status.enabled || status.collector_stale || base?.status === 'unreadable';
    const sourceLabels = { available: 'Leitura disponível', missing: 'Não encontrado', unreadable: 'Sem acesso', importing: 'Importando', waiting: 'Aguardando próximo ciclo' };
    return `<div class="cg-source ${warning ? 'cg-warning' : ''}"><strong>${state}</strong><span>Última verificação: ${date(status.polled_at)}</span></div>
      <details data-cg-preserve="sources"><summary>Fontes e cobertura do histórico</summary>
        <p>Retenção: sete dias, limitada aos registros disponíveis e ao orçamento de armazenamento. A leitura de um arquivo não comprova que o processo produtor está ativo.</p>
        <p>Registro mais antigo retido: ${date(overview.coverage?.oldest_event_at)}. Linhas inválidas: ${Number(status.invalid_lines || 0)}. Registros removidos por limite de armazenamento: ${Number(status.dropped_records || 0)}.</p>
        <ul>${sources.map(source => `<li>${escape(source.file)}: ${sourceLabels[source.status] || escape(source.status)}${source.remaining_bytes ? ` · ${source.remaining_bytes} bytes pendentes` : ''}${source.partial_line ? ' · aguardando conclusão de uma linha' : ''}${source.reset ? ' · arquivo reiniciado ou truncado' : ''}</li>`).join('') || '<li>Nenhuma fonte verificada.</li>'}</ul>
      </details>`;
  }

  function snapshotSummary(snapshot, overview) {
    if (!snapshot) return '<section class="cg-empty"><h3>Nenhum snapshot disponível</h3><p>Confira as fontes acima e a ativação de PPLID_QUEUE_DIAGNOSTICS no backend. Silêncio do arquivo não comprova ausência de congestionamento.</p></section>';
    const old = snapshot.state !== 'recovered' && Date.now() - Date.parse(snapshot.event_at) > Math.max(15, 3 * (overview.latest_interval_seconds || 5)) * 1000;
    return `<section class="cg-observation"><div class="cg-section-heading"><div><h3>Última observação recebida</h3><p>${date(snapshot.event_at)} · ${escape(snapshot.environment)} · PID ${snapshot.pid} · ${labels[snapshot.state] || escape(snapshot.state)}</p></div>${old ? '<span class="cg-badge cg-warning">Sem amostra recente · fim não confirmado</span>' : ''}</div>
      <div class="cg-kpis"><div><span>Threads ocupadas</span><strong>${snapshot.running_requests}<small> / ${snapshot.threads}</small></strong></div><div><span>Requisições aguardando</span><strong>${snapshot.pending_requests}</strong></div><div><span>Execuções demoradas listadas</span><strong>${snapshot.slow_executions.length}${snapshot.slow_executions_partial ? '<small> · parcial</small>' : ''}</strong></div></div>
      <progress class="cg-occupancy" aria-label="Ocupação no último snapshot" value="${snapshot.running_requests}" max="${snapshot.threads || 1}"></progress>
      <p class="cg-caption">${snapshot.state === 'recovered' ? 'A fila esvaziou neste snapshot; atendimentos ainda podem estar em execução.' : 'Esta observação não confirma o estado atual do processo.'} Ingerido em ${date(snapshot.ingested_at)}.${snapshot.request_totals_partial ? ' Totais parciais.' : ''}</p></section>`;
  }

  function episodesMarkup(data) {
    return `<section><div class="cg-section-heading"><h3>Episódios no período</h3><span>Picos observados, por processo</span></div>
      <div class="cg-table-wrap"><table class="cg-table"><thead><tr><th>Início observado</th><th>Processo</th><th>Fila máx.</th><th>Ocupação máx.</th><th>Duração / fim</th></tr></thead><tbody>${data.episodes.map(ep => `<tr ${ep.id === S.episodeId ? 'class="is-selected"' : ''}><td><button class="cg-link" data-cg-episode="${ep.id}" ${ep.id === S.episodeId ? 'aria-current="true"' : ''}>${date(ep.start_at)}</button>${!ep.start_known ? '<small>Início não registrado</small>' : ''}</td><td>PID ${ep.pid}${ep.partial ? '<small>Listas parciais</small>' : ''}</td><td>${ep.max_pending}</td><td>${ep.max_running}</td><td>${ep.end_at ? duration(ep.duration_ms) + '<small>Fila esvaziada</small>' : 'Fim não registrado'}</td></tr>`).join('') || '<tr><td colspan="5">Nenhum episódio encontrado neste período. Verifique a cobertura antes de concluir que não houve congestionamento.</td></tr>'}</tbody></table></div>
      <div class="cg-pagination"><button class="btn btn-secondary btn-sm" data-cg-ep-page="${Math.max(0,S.episodeOffset-100)}" ${S.episodeOffset ? '' : 'disabled'}>Mais recentes</button><span>Página ${Math.floor(S.episodeOffset/100)+1}</span><button class="btn btn-secondary btn-sm" data-cg-ep-page="${data.next_offset}" ${data.next_offset == null ? 'disabled' : ''}>Mais antigos</button></div></section>`;
  }

  function chart(points, key, title, expectedInterval) {
    if (!points.length) return '';
    const width = Math.max(280, Math.min(920, (document.querySelector('[data-cg-content]')?.clientWidth || 800)-20)), height = 164, left = 36, right = 12, top = 12, bottom = 30;
    const values = points.map(point => Number(point[key] || 0));
    const max = Math.max(1, ...values, ...(key === 'running' ? points.map(point => point.threads || 0) : []));
    const minTime = Date.parse(points[0].at), maxTime = Date.parse(points[points.length-1].last_at || points[points.length-1].at);
    const span = Math.max(1000, maxTime-minTime);
    const x = point => left + ((Date.parse(point.at)-minTime)+(1000 > maxTime-minTime ? (1000-(maxTime-minTime))/2 : 0)) / span * (width-left-right);
    const y = point => top + (1-Number(point[key] || 0)/max)*(height-top-bottom);
    const segments = points.map((point, index) => {
      const previous = points[index-1];
      if (!previous || point.aggregated || previous.aggregated || Date.parse(point.at)-Date.parse(previous.at) > Math.max(15000,expectedInterval*3000)) return '';
      return `<path d="M${x(previous)},${y(previous)} H${x(point)} V${y(point)}" fill="none" class="cg-series"/>`;
    }).join('');
    const dots = points.map(point => `${point.aggregated ? `<line x1="${x(point)}" x2="${x(point)}" y1="${y(point)}" y2="${y({...point,[key]:point[key+'_min']})}" class="cg-range"/>` : ''}${point.snapshot_id ? `<a href="#" data-cg-snapshot="${point.snapshot_id}" aria-label="Selecionar snapshot de ${date(point.at)}: ${point[key]}">` : ''}<circle cx="${x(point)}" cy="${y(point)}" r="${point.snapshot_id === S.snapshotId ? 5 : 3.5}" class="cg-point ${point.aggregated ? 'is-aggregated' : ''}"><title>${date(point.at)}: ${point[key]}${point.aggregated ? ' · pico do intervalo' : ''}</title></circle>${point.snapshot_id ? '</a>' : ''}`).join('');
    return `<figure class="cg-chart"><figcaption>${title} <span>máximo observado: ${Math.max(...values)}</span></figcaption><svg viewBox="0 0 ${width} ${height}" role="img" aria-label="${title}; somente observações registradas"><line x1="${left}" y1="${top}" x2="${left}" y2="${height-bottom}" class="cg-axis"/><line x1="${left}" y1="${height-bottom}" x2="${width-right}" y2="${height-bottom}" class="cg-axis"/><text x="${left-9}" y="${top+6}" text-anchor="end">${max}</text><text x="${left-9}" y="${height-bottom+4}" text-anchor="end">0</text>${segments}${dots}<text x="${left}" y="${height-6}">${clock(minTime)}</text><text x="${width-right}" y="${height-6}" text-anchor="end">${clock(maxTime)}</text></svg></figure>`;
  }

  function groups(title, rows, partial, unit) {
    return `<details data-cg-preserve="${unit}"><summary>${title}${partial ? ' · lista parcial' : ''}</summary><div class="cg-table-wrap"><table class="cg-table"><thead><tr><th>Método / rota</th><th>Quantidade</th><th>${unit === 'wait' ? 'Espera máxima' : 'Execução máxima'}</th></tr></thead><tbody>${rows.map(row => `<tr><td><code>${escape(row.method)} ${escape(row.route)}</code></td><td>${row.count}</td><td>${duration(row.max_ms)}</td></tr>`).join('') || '<tr><td colspan="3">Nenhuma rota listada neste snapshot.</td></tr>'}</tbody></table></div></details>`;
  }

  function detailMarkup(detail) {
    if (!detail) return '';
    const ep = detail.episode;
    const selected = detail.selected || detail.snapshots.find(row => row.id === S.snapshotId) || detail.snapshots[0];
    const end = Math.min(Date.now(), Date.parse(ep.last_at) + 300000);
    const start = Date.parse(ep.start_at) - 300000;
    const slow = selected?.slow_executions || [];
    const pagination = `<div class="cg-pagination"><button class="btn btn-secondary btn-sm" data-cg-snap-page="${Math.max(0,S.snapshotOffset-100)}" ${S.snapshotOffset ? '' : 'disabled'}>Snapshots anteriores</button><button class="btn btn-secondary btn-sm" data-cg-snap-page="${detail.next_offset}" ${detail.next_offset == null ? 'disabled' : ''}>Próximos snapshots</button></div>`;
    return `<section class="cg-investigation"><div class="cg-section-heading"><div><h3>Detalhe do episódio · PID ${ep.pid}</h3><p>${date(ep.start_at)} ${ep.end_at ? 'até '+date(ep.end_at) : '· fim não registrado'}${!detail.session_known ? ' · ativação da sessão não disponível' : ''}</p></div>
      <button class="btn btn-secondary btn-sm" data-availability-event-log data-log-env="${S.env}" data-log-at="${ep.start_at}" data-log-since="${new Date(start).toISOString()}" data-log-until="${new Date(end).toISOString()}" ${S.demo?'disabled aria-label="Extração de produção desabilitada nos dados fictícios"':''}>Extrair log</button></div>
      ${chart(detail.series,'running','Threads ocupadas',detail.expected_interval_seconds)}${chart(detail.series,'pending','Requisições aguardando',detail.expected_interval_seconds)}
      <p class="cg-caption">${detail.series_aggregated ? 'Gráfico resumido por intervalo com mínimos/máximos; transições preservadas. Consulte os snapshots para valores exatos.' : 'Somente pontos observados. Lacunas superiores à tolerância não são conectadas.'}${detail.page_limited_by_bytes ? ' Página limitada pelo tamanho dos registros; use os próximos snapshots para continuar.' : ''}</p>
      <div class="cg-snapshots" aria-label="Selecionar snapshot do episódio">${detail.snapshots.map(row => `<button class="cg-snapshot ${selected?.id === row.id ? 'is-selected' : ''}" data-cg-snapshot="${row.id}" aria-pressed="${selected?.id === row.id}"><time>${date(row.event_at)}</time><strong>${labels[row.state]}</strong><span>${row.running_requests}/${row.threads} ocupadas · ${row.pending_requests} aguardando</span></button>`).join('')}</div>${pagination}
      ${selected ? `<div class="cg-snapshot-detail" aria-live="polite"><h3>Snapshot selecionado</h3><p>${date(selected.event_at)} · ${labels[selected.state]} · ${selected.running_requests}/${selected.threads} executando · ${selected.pending_requests} aguardando</p>
      <p class="cg-caption">Ingestão: ${date(selected.ingested_at)}. Canais na deque: ${selected.dispatcher_queued_channels}; profundidade excedente: ${selected.waitress_queue_depth}. Conexões abertas não são contabilizadas aqui.</p>
      ${selected.request_totals_partial || selected.diagnostic_failures || selected.unlisted_requests ? `<p class="cg-warning">Diagnóstico parcial: ${selected.diagnostic_failures} falhas; ${selected.unlisted_requests} requisições fora das listas.${selected.request_totals_partial ? ' Totais também parciais.' : ''}</p>` : ''}
      ${groups('Rotas aguardando',selected.pending_routes,selected.pending_routes_partial,'wait')}${groups('Rotas em execução',selected.running_routes,selected.running_routes_partial,'run')}
      <h3>Execuções demoradas${selected.slow_executions_partial ? ' · lista parcial' : ''}</h3><div class="cg-table-wrap"><table class="cg-table"><thead><tr><th>Requisição / ID</th><th>Espera</th><th>Execução</th><th>Acumulado</th><th>Desconexão detectada</th></tr></thead><tbody>${slow.map(row => `<tr><td><code>${escape(row.method)} ${escape(row.route)}</code><small>ID ${escape(row.id)}</small></td><td>${duration(row.wait_ms)}</td><td>${duration(row.duration_ms)}</td><td>${duration(row.wait_ms+row.duration_ms)}</td><td>${row.client_disconnected ? 'Sim' : 'Não'}</td></tr>`).join('') || '<tr><td colspan="5">Nenhuma execução demorada listada.</td></tr>'}</tbody></table></div>
      <p class="cg-caption">Tempos ainda em andamento no snapshot. Execução inclui streaming e esperas de saída dentro do serviço; não confirma entrega final ao cliente. Uma rota demorada não comprova causa raiz.</p></div>` : ''}</section>`;
  }

  OC.renderCongestionContent = function () {
    const data = S.overview;
    return data ? `${collectionMarkup(data)}${snapshotSummary(data.latest,data)}${episodesMarkup(data)}${detailMarkup(S.detail)}` : '<p>Não foi possível carregar as evidências locais.</p>';
  };

  function paint() {
    if (!isActive()) return;
    const root = document.querySelector('[data-congestion-root]');
    if (!root) return;
    const content = root.querySelector('[data-cg-content]');
    const open = [...content.querySelectorAll('details[open]')].map(node => node.dataset.cgPreserve);
    const scroll = window.scrollY;
    const focused = document.activeElement?.closest?.('[data-cg-snapshot],[data-cg-episode]');
    const focusId = focused?.dataset.cgSnapshot || focused?.dataset.cgEpisode;
    content.innerHTML = OC.renderCongestionContent();
    content.querySelectorAll('details[data-cg-preserve]').forEach(node => { node.open = open.includes(node.dataset.cgPreserve); });
    const error = root.querySelector('[data-cg-error]'); error.hidden = !S.error; error.textContent = S.error;
    root.querySelector('[data-cg-env]').value = S.env;
    OC.bindCongestion(root); OC.bindAvailabilityEventLogs?.(root);
    if (focusId) [...content.querySelectorAll('[data-cg-snapshot],[data-cg-episode]')].find(node => (node.dataset.cgSnapshot || node.dataset.cgEpisode) === focusId)?.focus({preventScroll:true});
    window.scrollTo?.({top:scroll,behavior:'instant'});
  }

  async function request(url, signal) {
    const response = await fetch(url, { credentials:'same-origin', signal });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || `Falha na leitura local (HTTP ${response.status})`);
    return data;
  }

  OC.cancelCongestion = function () { S.controller?.abort(); S.generation++; S.busy = false; };
  OC.refreshCongestion = async function (options={}) {
    routeState();
    if (!isActive() || document.hidden || (!options.force && !S.follow && S.overview) || (S.busy && !options.force)) return;
    if (!document.querySelector('[data-congestion-root]')) OC.renderMonitoringView(OC.monitorState.payload || {});
    S.controller?.abort(); const controller = new AbortController(); S.controller = controller;
    const generation = ++S.generation; S.busy = true;
    let timedOut=false;
    const timeout=window.setTimeout(()=>{timedOut=true;controller.abort();},10000);
    try {
      if(S.demo){
        const fixture=OC.buildCongestionDemo({env:S.env,scenario:S.scenario,window:S.window,since:S.since,until:S.until,anchor:S.demoAnchor});
        S.overview=fixture.overview;
        if(!S.episodeId||S.follow||!fixture.details.has(S.episodeId))S.episodeId=S.overview.episodes[0]?.id;
        S.detail=fixture.details.get(S.episodeId)||null;
        if(S.detail){S.detail.selected=S.follow?S.detail.latest_snapshot:S.detail.snapshots.find(row=>row.id===S.snapshotId)||S.detail.snapshots[0];S.snapshotId=S.detail.selected.id;}
        S.error='';paint();return;
      }
      const query = new URLSearchParams({window:S.window,offset:S.episodeOffset});
      if (S.window==='custom') { query.set('since',S.since);query.set('until',S.until); }
      const data = await request(`/api/v1/monitoring/${S.env}/congestion?${query}`,controller.signal);
      if (generation !== S.generation || !isActive()) return;
      if (!S.resolved && !OC.currentRoute?.query?.env && data.enabled_environments?.length && !data.enabled_environments.includes(S.env)) {
        S.resolved=true; S.env=data.enabled_environments.includes('MAIN')?'MAIN':data.enabled_environments[0]; S.busy=false;
        return OC.refreshCongestion({force:true});
      }
      S.resolved=true; S.overview=data;
      if ((!S.episodeId || S.follow) && data.episodes.length) S.episodeId=data.episodes[0].id;
      if (S.episodeId) {
        if(S.follow){ const latestEpisode=data.episodes.find(ep=>ep.id===S.episodeId);S.snapshotOffset=Math.max(0,(latestEpisode?.snapshot_count||100)-100);S.snapshotId=null; }
        const q=new URLSearchParams({episode_id:S.episodeId,offset:S.snapshotOffset});
        if(S.snapshotId) q.set('snapshot_id',S.snapshotId);
        S.detail=await request(`/api/v1/monitoring/${S.env}/congestion-detail?${q}`,controller.signal);
        if (generation!==S.generation || !isActive()) return;
        if(S.follow && S.detail.latest_snapshot){S.detail.selected=S.detail.latest_snapshot;S.snapshotId=S.detail.latest_snapshot.id;}
        else if(!S.snapshotId) S.snapshotId=S.detail.snapshots[0]?.id || null;
      } else S.detail=null;
      S.error=''; paint();
    } catch(error) {
      if(generation!==S.generation || (error.name==='AbortError' && !timedOut)) return;
      S.error=`${error.message}. Os dados anteriores, quando presentes, permanecem como histórico.`; paint();
    } finally { window.clearTimeout(timeout);if(generation===S.generation) S.busy=false; }
  };

  function reset() { S.episodeId=S.snapshotId=null;S.episodeOffset=S.snapshotOffset=0;S.overview=S.detail=null; }
  OC.bindCongestion = function(root) {
    function bind(selector,event,fn){ root.querySelectorAll(selector).forEach(node=>{const key='cgBound'+event;if(node.dataset[key])return;node.dataset[key]='1';node.addEventListener(event,fn);}); }
    bind('[data-cg-env]','change',event=>{S.env=event.target.value;S.resolved=true;reset();OC.refreshCongestion({force:true});});
    bind('[data-cg-window]','change',event=>{S.window=event.target.value;root.querySelector('[data-cg-custom]').hidden=S.window!=='custom';if(S.window!=='custom'){reset();OC.refreshCongestion({force:true});}});
    bind('[data-cg-custom]','submit',event=>{event.preventDefault();const since=root.querySelector('[data-cg-since]').value,until=root.querySelector('[data-cg-until]').value;if(!since||!until)return;S.since=new Date(since).toISOString();S.until=new Date(until).toISOString();reset();OC.refreshCongestion({force:true});});
    bind('[data-cg-follow]','change',event=>{S.follow=event.target.checked;if(S.follow)OC.refreshCongestion({force:true});});
    bind('[data-cg-refresh]','click',()=>OC.refreshCongestion({force:true}));
    bind('[data-cg-demo]','click',()=>{S.demo=!S.demo;S.demoAnchor=Date.now();reset();OC.cancelCongestion();OC.renderMonitoringView(OC.monitorState.payload||{});OC.refreshCongestion({force:true});});
    bind('[data-cg-scenario]','change',event=>{S.scenario=event.target.value;reset();OC.refreshCongestion({force:true});});
    bind('[data-cg-episode]','click',event=>{S.episodeId=event.currentTarget.dataset.cgEpisode;S.snapshotId=null;S.snapshotOffset=0;pause(root);OC.refreshCongestion({force:true});});
    bind('[data-cg-snapshot]','click',event=>{event.preventDefault();S.snapshotId=event.currentTarget.dataset.cgSnapshot;pause(root);const existing=S.detail.snapshots.find(row=>row.id===S.snapshotId);if(existing){S.detail.selected=existing;paint();}else OC.refreshCongestion({force:true});});
    bind('[data-cg-ep-page]','click',event=>{S.episodeOffset=Number(event.currentTarget.dataset.cgEpPage);pause(root);OC.refreshCongestion({force:true});});
    bind('[data-cg-snap-page]','click',event=>{S.snapshotOffset=Number(event.currentTarget.dataset.cgSnapPage);S.snapshotId=null;pause(root);OC.refreshCongestion({force:true});});
  };
  function pause(root){S.follow=false;root.querySelector('[data-cg-follow]').checked=false;}
  // Exposed pure renderers for contract tests and offline visual QA.
  OC.congestionChart=chart;
  OC.congestionLogLink=function(line){
    let at=line.logged_at;
    try{const data=JSON.parse(String(line.line).split(' WARNING waitress.queue ')[1]);if(data.event_at)at=data.event_at;}catch{/* Legacy or malformed evidence uses the recorded clock. */}
    if(!Number.isFinite(Date.parse(at)))return '';
    return OC.buildAppPath('monitoring',null,{tab:'congestion',query:{env:line.environment,since:new Date(Date.parse(at)-300000).toISOString(),until:new Date(Math.min(Date.now(),Date.parse(at)+300000)).toISOString()}});
  };
  let resizeTimer;
  window.addEventListener?.('resize',()=>{window.clearTimeout(resizeTimer);resizeTimer=window.setTimeout(()=>{if(isActive() && S.overview)paint();},150);});
})();
