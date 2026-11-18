import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";

const source = readFileSync(new URL("../public/js/monitoring.js", import.meta.url), "utf8");
const fragment = source.slice(source.indexOf("  // Cache por URL completa"), source.indexOf("\n  OC.refreshMonitoring ="));
const store = new Map();
function setup(fetchJson) {
  const OC = { fetchJson, formatRelativeTime: () => "há pouco", _monitorCacheWarnings: [] };
  const window = { localStorage: { getItem: key => store.get(key), setItem: (key, value) => store.set(key, value) } };
  vm.runInNewContext(fragment, { OC, window, Map, Date, JSON, Error });
  return OC;
}

test("minute latency failures stay unavailable instead of silently restoring a historical series", async () => {
  store.clear();
  const url = "/api/v1/monitoring/MAIN/api-routes?window=1h&includeLatency=1";
  let oc = setup(async () => ({ latency: { available: true, points: [{ avgMs: 100 }] } }));
  await oc.fetchMonitoringJson(url);
  oc = setup(async () => ({ latency: { available: false, error: "unavailable", points: [] } }));
  const data = await oc.fetchMonitoringJson(url);
  assert.equal(data.latency.available, false);
  assert.equal(data.latency.points.length, 0);
  oc = setup(async () => { throw new Error("timeout"); });
  const saved = await oc.fetchMonitoringJson(url);
  assert.equal(saved._cache.stale, true);
  assert.equal(saved.latency.points[0].avgMs, 100);
});
test("preserva dados por URL após falha e reinício; resposta vazia substitui cache", async () => {
  store.clear();
  let oc = setup(async () => ({ routes: ["A"] }));
  await oc.fetchMonitoringJson("/api?env=DEV");
  oc = setup(async () => { throw new Error("timeout"); });
  assert.equal((await oc.fetchMonitoringJson("/api?env=DEV")).routes[0], "A");
  assert.match(oc._monitorCacheWarnings[0], /timeout/);
  assert.equal((await oc.fetchMonitoringJson("/api?env=HOM", { routes: [] })).routes.length, 0);
  oc = setup(async () => ({ routes: [] }));
  await oc.fetchMonitoringJson("/api?env=DEV");
  oc = setup(async () => { throw new Error("timeout"); });
  assert.equal((await oc.fetchMonitoringJson("/api?env=DEV")).routes.length, 0);
});
test("falha parcial preserva somente a parte afetada", async () => {
  store.clear();
  let oc = setup(async () => ({ envSummaries: [{ env: "DEV", summary: { api: { totals: { requests: 10 } } } }, { env: "HOM", summary: { api: { totals: { requests: 20 } } } }] }));
  await oc.fetchMonitoringJson("/dashboard");
  oc = setup(async () => ({ envSummaries: [{ env: "DEV", summary: { api: { error: "falhou" } } }, { env: "HOM", summary: { api: { totals: { requests: 30 } } } }] }));
  const result = await oc.fetchMonitoringJson("/dashboard");
  assert.equal(result.envSummaries[0].summary.api.totals.requests, 10);
  assert.equal(result.envSummaries[1].summary.api.totals.requests, 30);
  assert.match(oc._monitorCacheWarnings[0], /falhou/);
});
test("SLI usa os totais da janela e identifica falta de dados", () => {
  const start = source.indexOf("  function renderAvailabilityView(");
  const end = source.indexOf("\n  function renderTabContent(", start);
  const context = { OC: { monitorState: { selectedEnvs: ["DEV", "HOM"], availabilityWindow: "7d" }, escapeHtml: String, formatDate: String } };
  vm.runInNewContext(source.slice(start, end) + "\nthis.render = renderAvailabilityView;", context);
  const html = context.render({ DEV: { window: "7d", api: { requests: 100, errors5xx: 2, availabilityPct: 98, periodVerified: true }, health: { samples: 4, coveragePct: 10 }, timeline: [] }, HOM: { window: "7d", api: { unavailableReason: "Sem requisições" }, health: {}, timeline: [] } });
  assert.match(html, /98,00%/);
  assert.match(html, /Sem dados/);
  assert.match(html, /Coleta incompleta/);
  assert.match(html, /data-availability-window="30d"/);
});

test("demonstração cria cenários isolados para os três ambientes e períodos", () => {
  const start = source.indexOf("  OC.buildAvailabilityDemo =");
  const end = source.indexOf("\n  function renderTabContent(", start);
  const OC = { monitorState: { selectedEnvs: ["DEV"], availabilityWindow: "30d", availabilityDemo: true }, escapeHtml: String, formatDate: String };
  const context = { OC };
  vm.runInNewContext(source.slice(start, end) + "\nthis.render = renderAvailabilityView;", context);
  for (const period of ["24h", "7d", "30d"]) {
    const demo = OC.buildAvailabilityDemo(period);
    assert.deepEqual(Object.keys(demo), ["MAIN", "DEV", "HOM"]);
    assert.equal(demo.MAIN.window, period);
    assert.ok(demo.DEV.history.some(bucket => bucket.failures > 0));
    assert.ok(demo.HOM.history.some(bucket => bucket.samples === 0));
  }
  const html = context.render({});
  assert.match(html, /Dados simulados/);
  assert.match(html, /Voltar aos dados reais/);
  assert.match(html, /MAIN/);
  assert.match(html, /Indeterminada/);
});

test("rota dedicada resolve disponibilidade", () => {
  const window = { OpsConsole: { ENV_ORDER: ["MAIN", "DEV", "HOM"] } };
  vm.runInNewContext(readFileSync(new URL("../public/js/router.js", import.meta.url), "utf8"), { window, URLSearchParams });
  const route = window.OpsConsole.parseRoute("/monitoring/availability?env=DEV");
  assert.equal(route.tab, "availability");
  assert.equal(route.env, "DEV");
});

test('estado atual não é determinado pelo histórico e dados atuais antigos ficam sem confirmação', () => {
  const start = source.indexOf('  function renderAvailabilityView(');
  const end = source.indexOf('\n  function renderTabContent(', start);
  const OC = { monitorState: { selectedEnvs: ['DEV'], availabilityWindow: '24h' }, escapeHtml: String, formatDate: String };
  const context = { OC };
  vm.runInNewContext(source.slice(start, end) + '\nthis.render = renderAvailabilityView;', context);
  const history = { DEV: { window: '24h', health: { uptimePct: 0, samples: 5 }, timeline: [{ kind: 'outage', ongoing: true }] } };
  let html = context.render(history, { DEV: { state: 'healthy', checkedAt: new Date().toISOString(), maxAgeMs: 120000 } });
  assert.match(html, /Saudável agora/);
  assert.doesNotMatch(html, />Indisponível</);
  html = context.render(history, { DEV: { state: 'healthy', checkedAt: '2020-01-01T00:00:00Z', maxAgeMs: 120000 } });
  assert.match(html, /Sem confirmação recente/);
  assert.doesNotMatch(html, /Saudável agora/);
});

test('coverage bars distinguish partial, missing and predecessor evidence and show diagnostics', () => {
  const start = source.indexOf('  function renderAvailabilityView(');
  const end = source.indexOf('\n  function renderTabContent(', start);
  const OC = { monitorState: { selectedEnvs: ['DEV'], availabilityWindow: '24h' }, escapeHtml: String, formatDate: String };
  const context = { OC };
  vm.runInNewContext(source.slice(start, end) + '\nthis.render = renderAvailabilityView;', context);
  const history = [
    { since: '2026-11-18T10:00:00Z', until: '2026-11-18T10:30:00Z', samples: 10, failures: 0, observedSec: 600, coveragePct: 33.3 },
    { since: '2026-11-18T10:30:00Z', until: '2026-11-18T11:00:00Z', samples: 0, failures: 0, observedSec: 0, coveragePct: 0 },
    { since: '2026-11-18T11:00:00Z', until: '2026-11-18T11:00:20Z', samples: 0, failures: 0, observedSec: 20, coveragePct: 100 },
  ];
  const html = context.render({ DEV: { window: '24h', history, health: { coverageMethod: 'observed-duration-v2', intervalSec: 30,
    actualIntervalSec: 32, largestGapSec: 1800, lastSampleAgeSec: 12, coveragePct: 40, samples: 10 }, timeline: [] } });
  assert.match(html, /av-bar av-tone-ok is-partial/);
  assert.match(html, /av-bar av-tone-none/);
  assert.match(html, /av-bar av-tone-ok"/);
  assert.match(html, /33,3% de cobertura/);
  assert.match(html, /av-collection-diagnostics/);
  assert.match(html, /30min 0s/);
  assert.match(html, /32s/);
});

test('a consolidated deploy incident opens its interval with recovery context', () => {
  const start = source.indexOf('  function renderAvailabilityView(');
  const end = source.indexOf('\n  function renderTabContent(', start);
  const OC = { monitorState: { selectedEnvs: ['MAIN'], availabilityWindow: '24h' }, escapeHtml: String, formatDate: String };
  const context = { OC };
  vm.runInNewContext(source.slice(start, end) + '\nthis.render = renderAvailabilityView;', context);
  const html = context.render({ MAIN: { window: '24h', health: {}, timeline: [{ kind: 'outage',
    startedAt: '2026-11-18T12:00:00Z', finishedAt: '2026-11-18T12:04:00Z',
    deploy: { runId: 'run-123' }, fragmentCount: 3, relatedSignals: [{}, {}], uncertain: true,
  }] } });
  assert.match(html, /Indisponibilidade associada ao deploy/);
  assert.match(html, /3 episódio\(s\) consolidado\(s\)/);
  assert.match(html, /2 sinal\(is\) nos detalhes/);
  assert.match(html, /data-availability-since="2026-11-18T12:00:00Z"/);
  assert.match(html, /data-availability-until="2026-11-18T12:06:00.000Z"/);
});
