import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import { readFileSync } from 'node:fs';

const source = readFileSync(new URL('../public/js/congestion.js', import.meta.url), 'utf8');
const fixtures = readFileSync(new URL('../public/js/congestion-fixtures.js', import.meta.url), 'utf8');
const monitoring = readFileSync(new URL('../public/js/monitoring.js', import.meta.url), 'utf8');
const escapeHtml = value => String(value).replaceAll('&', '&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;');

function setup(fetcher = async () => ({ok:true,json:async()=>({environment:'MAIN',episodes:[],collection:{},coverage:{}})})) {
  const nodes = new Map();
  const make = () => ({ innerHTML:'',textContent:'',dataset:{},value:'',hidden:false,querySelectorAll:()=>[],focus(){},querySelector(selector){if(!nodes.has(selector))nodes.set(selector,make());return nodes.get(selector);} });
  const root=make();
  const OC={currentRoute:{view:'monitoring',tab:'congestion',query:{}},monitorState:{payload:{}},escapeHtml,
    buildAppPath:(_view,_env,{tab,query})=>`/monitoring/${tab}?${new URLSearchParams(query)}`,
    renderMonitoringView(){}, bindAvailabilityEventLogs(){}};
  const document={hidden:false,activeElement:null,querySelector:()=>root,getElementById:()=>root};
  const window={OpsConsole:OC,setTimeout,clearTimeout,scrollTo(){},addEventListener(){},scrollY:0};
  vm.runInNewContext(fixtures+source,{window,document,fetch:fetcher,URLSearchParams,AbortController,Date,console});
  return {OC,S:OC.congestionState,nodes};
}

function sample(state='recovered') {
  return {id:'snapshot',environment:'MAIN',pid:26368,event_at:'2026-11-19T19:56:36.777Z',ingested_at:'2026-11-19T19:56:37Z',
    state,running_requests:13,threads:16,pending_requests:0,slow_executions:[],running_routes:[],pending_routes:[],
    dispatcher_queued_channels:0,waitress_queue_depth:0,diagnostic_failures:0,unlisted_requests:0};
}

test('recovery is queue recovery, not confirmation of process health',()=>{
  const {OC,S}=setup();
  S.overview={latest:sample(),episodes:[],collection:{sources:[]},coverage:{}};
  const html=OC.renderCongestionContent();
  assert.match(html,/Fila esvaziada/);
  assert.match(html,/atendimentos ainda podem estar em execução/);
  assert.match(html,/>13<small> \/ 16/);
  assert.match(html,/16:56:36,777/);
  assert.doesNotMatch(html,/Saudável/);
});

test('partial lists and running times remain explicit; routes are escaped',()=>{
  const {OC,S}=setup();
  const snap={...sample('congestion_start'),slow_executions_partial:true,running_routes_partial:true,
    slow_executions:[{id:'26368-23',method:'GET',route:'/api/<script>/',wait_ms:812,duration_ms:3375,client_disconnected:false}]};
  S.overview={latest:snap,episodes:[],collection:{},coverage:{}};
  S.detail={episode:{pid:26368,start_at:snap.event_at,last_at:snap.event_at},snapshots:[snap],series:[],session_known:false};
  const html=OC.renderCongestionContent();
  assert.match(html,/lista parcial/);
  assert.match(html,/4,187 s/);
  assert.match(html,/&lt;script&gt;/);
  assert.match(html,/não comprova causa raiz/);
  assert.match(html,/Sem amostra recente/);
});

test('charts preserve short transitions and break lines across missing observations',()=>{
  const {OC}=setup();
  const points=[{at:'2026-11-19T19:56:36.706Z',pending:1,running:16,threads:16,snapshot_id:'a'},
    {at:'2026-11-19T19:56:36.777Z',pending:0,running:13,threads:16,snapshot_id:'b'}];
  let html=OC.congestionChart(points,'pending','Fila',5);
  assert.match(html,/data-cg-snapshot="a"/);
  assert.match(html,/data-cg-snapshot="b"/);
  assert.equal((html.match(/class="cg-series"/g)||[]).length,1);
  points[1].at='2026-11-19T20:00:36.777Z';
  html=OC.congestionChart(points,'pending','Fila',5);
  assert.doesNotMatch(html,/class="cg-series"/);
});

test('late responses cannot replace a newer environment and previous requests are aborted',async()=>{
  const pending=[];
  const {OC,S}=setup((url,options)=>new Promise(resolve=>pending.push({url,signal:options.signal,resolve})));
  const first=OC.refreshCongestion({force:true});
  S.env='HOM';
  const second=OC.refreshCongestion({force:true});
  assert.equal(pending[0].signal.aborted,true);
  pending[1].resolve({ok:true,json:async()=>({environment:'HOM',episodes:[],collection:{},coverage:{}})});
  await second;
  pending[0].resolve({ok:true,json:async()=>({environment:'MAIN',episodes:[],collection:{},coverage:{}})});
  await first;
  assert.equal(S.overview.environment,'HOM');
});

test('paused inspection sends no periodic requests and keeps snapshot selection',async()=>{
  let requests=0;
  const {OC,S}=setup(async()=>{requests++;throw Error('should not request');});
  S.routeKey=JSON.stringify([undefined,undefined,undefined,undefined]);
  S.follow=false;S.overview={episodes:[]};S.snapshotId='pinned';
  await OC.refreshCongestion();
  assert.equal(requests,0);assert.equal(S.snapshotId,'pinned');
});

test('links from logs use original event time rather than delayed ingestion',()=>{
  const {OC}=setup();
  const url=OC.congestionLogLink({environment:'MAIN',logged_at:'2026-11-19T21:00:00Z',
    line:'2026-11-19T19:56:36.706Z WARNING waitress.queue {"event_at":"2026-11-19T19:56:36.706Z"}'});
  const query=new URL(url,'http://localhost').searchParams;
  assert.equal(query.get('since'),'2026-11-19T19:51:36.706Z');
});

test('the dedicated tab bypasses generic Django metric refresh and is loaded by the feature loader',()=>{
  assert.ok(monitoring.indexOf('OC.currentRoute?.tab === "congestion"')<monitoring.indexOf('const force = options.force === true'));
  assert.match(monitoring,/await OC.refreshCongestion\(options\)/);
  assert.match(readFileSync(new URL('../public/js/router.js',import.meta.url),'utf8'),/"congestion"/);
  assert.match(readFileSync(new URL('../public/js/feature-loader.js',import.meta.url),'utf8'),/congestion.js\?v=/);
  const index=readFileSync(new URL('../public/index.html',import.meta.url),'utf8');
  assert.match(index,/router.js\?v=20261119-congestion-history/);
  assert.match(index,/feature-loader.js\?v=20261119-congestion-history/);
  const window={OpsConsole:{ENV_ORDER:['MAIN','DEV','HOM']}};
  vm.runInNewContext(readFileSync(new URL('../public/js/router.js',import.meta.url),'utf8'),{window,URLSearchParams});
  const route=window.OpsConsole.parseRoute('/monitoring/congestion?env=HOM');
  assert.equal(route.tab,'congestion');assert.equal(route.env,'HOM');
});

test('fictional scenarios never request production APIs and disable log extraction',async()=>{
  let calls=0;
  const {OC,S}=setup(async()=>{calls++;throw Error('production must not be read');});
  S.demo=true;
  for(const scenario of ['short','sustained','partial']){
    S.scenario=scenario;S.episodeId=null;
    await OC.refreshCongestion({force:true});
    assert.equal(S.error,'');
    assert.ok(S.overview.episodes.length);
    assert.match(OC.renderCongestion(),/Modo demonstração/);
    assert.match(OC.renderCongestionContent(),/disabled aria-label="Extração de produção desabilitada/);
    assert.ok(S.detail.snapshots.every(row=>row.slow_executions.every(exec=>exec.duration_ms>=2000)));
  }
  assert.equal(calls,0);
});

test('demo peaks, partial histories and short durations match the selected scenario',()=>{
  const {OC}=setup();
  const args={env:'HOM',window:'24h',anchor:Date.now()};
  const short=OC.buildCongestionDemo({...args,scenario:'short'});
  assert.equal(short.overview.episodes[0].duration_ms,71);
  const sustained=OC.buildCongestionDemo({...args,scenario:'sustained'});
  assert.equal(sustained.overview.episodes[0].max_pending,80);
  const partial=OC.buildCongestionDemo({...args,scenario:'partial'});
  assert.ok(partial.overview.episodes.some(ep=>!ep.start_known));
  assert.ok(partial.overview.episodes.some(ep=>!ep.end_at));
  for(const fixture of [short,sustained,partial]){
    for(const detail of fixture.details.values()){
      for(const row of detail.snapshots)assert.equal(row.running_routes.reduce((sum,route)=>sum+route.count,0),row.running_requests);
    }
  }
});

test('switching to demo invalidates a real request before it can request episode details',async()=>{
  const pending=[];
  const {OC,S}=setup((url,options)=>new Promise(resolve=>pending.push({url,signal:options.signal,resolve})));
  const real=OC.refreshCongestion({force:true});
  S.demo=true;
  OC.cancelCongestion();
  await OC.refreshCongestion({force:true});
  assert.equal(pending.length,1);
  assert.equal(pending[0].signal.aborted,true);
  pending[0].resolve({ok:true,json:async()=>({environment:'MAIN',episodes:[{id:'real-episode'}],collection:{},coverage:{}})});
  await real;
  assert.equal(pending.length,1);
  assert.match(S.episodeId,/^demo-/);
  assert.equal(S.error,'');
});
