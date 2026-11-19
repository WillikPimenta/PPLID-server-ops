import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";

const source = readFileSync(new URL("../public/js/availability-export.js", import.meta.url), "utf8");
const monitoring = readFileSync(new URL("../public/js/monitoring.js", import.meta.url), "utf8");
function setup() {
  const data = {
    window: "7d", since: "2026-11-11T00:00:00Z", until: "2026-11-18T00:00:00Z",
    api: { availabilityPct: 99.5, requests: 200, errors5xx: 1, periodVerified: true },
    health: { uptimePct: 100, samples: 2, successful: 2, coveragePct: 25, lastSampleAt: "2026-11-17T23:00:00Z" },
    impact: { incidentCount: 0, estimatedDowntimeSec: 0, unknownSec: 3600 },
    history: [
      { since: "2026-11-17T23:00:00-03:00", until: "2026-11-18T00:00:00-03:00", samples: 2, failures: 0, observedSec: 120, downSec: 0, coveragePct: 3.3 },
      { since: "2026-11-17T22:00:00Z", until: "2026-11-17T23:00:00Z", samples: 0, failures: 0, observedSec: 0, downSec: 0, coveragePct: 0 },
    ],
    timeline: Array.from({ length: 25 }, (_, i) => ({ kind: "event", title: `Evento ${i}`, startedAt: "2026-11-17T12:00:00Z" })),
    timelineTruncated: true,
  };
  const OC = { ENV_ORDER: ["MAIN", "DEV", "HOM"], monitorState: {
    availabilityWindow: "7d", selectedEnvs: ["MAIN"],
    payload: { availabilityByEnv: { MAIN: data, DEV: { ...data, health: { samples: 9000 } } },
      availabilityCurrentByEnv: { MAIN: { state: "healthy", checkedAt: "2020-01-01T00:00:00Z" } } },
  } };
  vm.runInNewContext(source, { window: { OpsConsole: OC } });
  return { OC, data };
}

test("summary respects selected environments and period and preserves zero vs missing", () => {
  const { OC, data } = setup();
  const csv = OC.buildAvailabilityExport("summary");
  assert.ok(csv.content.startsWith("\uFEFF"));
  assert.match(csv.content, /"99,5"/);
  assert.equal(csv.rows, 1);
  assert.match(csv.content, /"MAIN";"7d";"Não";"available"/);
  assert.match(csv.content, /"stale"/);
  assert.doesNotMatch(csv.content, /9000/);
  OC.monitorState.selectedEnvs = ["DEV", "HOM"];
  OC.monitorState.payload.availabilityByEnv.DEV = { ...data, window: "24h", api: { availabilityPct: 77 } };
  const json = JSON.parse(OC.buildAvailabilityExport("json").content);
  assert.equal(json.environments.length, 2);
  assert.equal(json.environments[0].data, null);
  assert.equal(json.environments[0].status, "unavailable");
  assert.equal(json.environments[1].data, null);
  assert.doesNotMatch(OC.buildAvailabilityExport("summary").content, /"77"/);
});

test("history exports every interval including gaps with UTC timestamps", () => {
  const { OC } = setup();
  const file = OC.buildAvailabilityExport("history");
  assert.equal(file.rows, 2);
  assert.match(file.content, /"2026-11-18T02:00:00.000Z"/);
  assert.match(file.content, /;"0";"0";"0";"0";"0"\r\n/);
  assert.match(file.filename, /^disponibilidade-historico-MAIN-7d-/);
});

test("events export all loaded events, escape CSV and neutralize formulas", () => {
  const { OC, data } = setup();
  data.timeline[0].title = '=HYPERLINK("a;b")\nlinha';
  data.timeline[1].durationSec = null;
  data.timeline[1].ongoing = false;
  const file = OC.buildAvailabilityExport("events");
  assert.equal(file.rows, 25);
  assert.match(file.content, /"'=HYPERLINK\(""a;b""\)\nlinha"/);
  assert.match(file.content, /"Eventos limitados"/);
  assert.match(file.content, /"Evento 24"/);
});

test("events export keeps deployment association and consolidation evidence", () => {
  const { OC, data } = setup();
  data.timeline = [{ kind: "outage", startedAt: "2026-11-18T12:00:00Z", deploy: { runId: "run-123" },
    fragmentCount: 3, relatedSignals: [{ title: "Backend offline" }, { title: "Backend recuperado" }] }];
  const csv = OC.buildAvailabilityExport("events");
  assert.equal(csv.rows, 1);
  assert.match(csv.content, /"run-123";"3";"2"/);
  assert.equal(JSON.parse(OC.buildAvailabilityExport("json").content).environments[0].data.timeline[0].relatedSignals.length, 2);
});

test("JSON preserves complete loaded data, truncation, cache and independent current state", () => {
  const { OC, data } = setup();
  data._cache = { stale: true, savedAt: "2026-11-17T10:00:00Z" };
  const report = JSON.parse(OC.buildAvailabilityExport("json").content);
  assert.equal(report.environments[0].status, "stale");
  assert.equal(report.environments[0].data.timeline.length, 25);
  assert.equal(report.environments[0].data.timelineTruncated, true);
  assert.equal(report.environments[0].data.impact.incidentCount, 0);
  assert.equal(report.environments[0].current.displayedState, "stale");
  assert.equal(report.environments[0].data._cache.savedAt, data._cache.savedAt);
  assert.ok(report.exportedAt);
});

test("demo exports simulated fixtures for all displayed environments without fetching real sources", () => {
  const { OC } = setup();
  vm.runInNewContext(monitoring.slice(monitoring.indexOf("  OC.buildAvailabilityDemo ="), monitoring.indexOf("\n  function renderAvailabilityView(")), { OC });
  OC.monitorState.availabilityDemo = true;
  for (const period of ["24h", "7d", "30d"]) {
    OC.monitorState.availabilityWindow = period;
    const file = OC.buildAvailabilityExport("json");
    const report = JSON.parse(file.content);
    assert.equal(report.demo, true);
    assert.equal(report.environments.length, 3);
    assert.ok(report.environments.every(entry => entry.data.demo && entry.data.window === period));
    assert.match(file.filename, /simulacao-/);
  }
});

test("download binds buttons, creates the right blob and releases its URL", () => {
  const { OC } = setup();
  const listeners = [], files = [], downloads = [], revoked = [];
  OC.toast = (...args) => { throw new Error(`Unexpected toast: ${args}`); };
  const link = { click() { downloads.push({ href: this.href, download: this.download }); }, remove() {} };
  vm.runInNewContext(source, {
    window: { OpsConsole: OC, setTimeout: callback => callback() },
    Blob: class { constructor(content, options) { files.push({ content, options }); } },
    URL: { createObjectURL: () => "blob:export", revokeObjectURL: url => revoked.push(url) },
    document: { createElement: () => link, body: { appendChild() {} } },
  });
  OC.bindAvailabilityExports({ querySelectorAll: () => [{ dataset: { availabilityExport: "summary" },
    addEventListener: (_, fn) => listeners.push(fn), closest: () => ({ removeAttribute() {} }) }] });
  listeners[0]();
  assert.equal(files[0].options.type, "text/csv;charset=utf-8");
  assert.equal(downloads[0].href, "blob:export");
  assert.match(downloads[0].download, /resumo-MAIN-7d-/);
  assert.deepEqual(revoked, ["blob:export"]);
});

test("empty event exports report the absence of records without creating a download", () => {
  const { OC, data } = setup();
  data.timeline = [];
  const notices = [], listeners = [];
  OC.toast = (...args) => notices.push(args);
  vm.runInNewContext(source, { window: { OpsConsole: OC } });
  OC.bindAvailabilityExports({ querySelectorAll: () => [{ dataset: { availabilityExport: "events" },
    addEventListener: (_, fn) => listeners.push(fn) }] });
  listeners[0]();
  assert.equal(notices.length, 1);
  assert.match(notices[0][0], /Não há registros/);
});

function eventLogContext(fetch) {
  const OC = { ENV_ORDER: ['MAIN', 'DEV', 'HOM'], monitorState: {} };
  const notices = [], downloads = [], revoked = [];
  OC.toast = (...args) => notices.push(args);
  const listeners = [];
  const button = { dataset: { logEnv: 'MAIN', logAt: '2026-11-18T12:00:00.000Z',
    logSince: '2026-11-18T11:55:00.000Z', logUntil: '2026-11-18T12:05:00.000Z' },
    textContent: 'Extrair log', disabled: false, addEventListener: (_, handler) => listeners.push(handler) };
  const link = { click() { downloads.push({ href: this.href, download: this.download }); }, remove() {} };
  vm.runInNewContext(source, { window: { OpsConsole: OC, setTimeout: fn => fn() }, fetch, URLSearchParams,
    URL: { createObjectURL: () => 'blob:event-log', revokeObjectURL: url => revoked.push(url) },
    document: { body: { appendChild() {} }, createElement: () => link } });
  const root = { querySelectorAll: () => [button] };
  OC.bindAvailabilityEventLogs(root);
  OC.bindAvailabilityEventLogs(root);
  assert.equal(listeners.length, 1);
  return { OC, button, click: () => listeners[0](), notices, downloads, revoked };
}

test('event log download uses the exact MAIN interval and releases its button and URL', async () => {
  let finish, calls = 0;
  const context = eventLogContext((url, options) => {
    calls++;
    const parsed = new URL(url, 'http://localhost');
    assert.equal(parsed.pathname, '/api/v1/monitoring/MAIN/availability-event-log');
    assert.equal(parsed.searchParams.get('since'), '2026-11-18T11:55:00.000Z');
    assert.equal(parsed.searchParams.get('until'), '2026-11-18T12:05:00.000Z');
    assert.equal(options.credentials, 'same-origin');
    return new Promise(resolve => { finish = resolve; });
  });
  const first = context.click();
  await context.click();
  assert.equal(calls, 1);
  assert.equal(context.button.disabled, true);
  finish({ ok: true, blob: async () => ({}) });
  await first;
  assert.match(context.downloads[0].download, /pplid-main-evento-.*\.log$/);
  assert.equal(context.button.disabled, false);
  assert.equal(context.button.textContent, 'Extrair log');
  assert.deepEqual(context.revoked, ['blob:event-log']);
});

test('failed log extraction shows the server error without downloading and can retry', async () => {
  const context = eventLogContext(async () => ({ ok: false, status: 401, json: async () => ({ error: 'Sessão expirada' }) }));
  await context.click();
  assert.equal(context.downloads.length, 0);
  assert.match(context.notices[0][0], /Sessão expirada/);
  assert.equal(context.button.disabled, false);
  await context.click();
  assert.equal(context.notices.length, 2);
});

test('simulated events cannot request production logs', async () => {
  const context = eventLogContext(() => { throw new Error('Unexpected request'); });
  context.OC.monitorState.availabilityDemo = true;
  await context.click();
  assert.equal(context.notices.length, 0);
  assert.equal(context.downloads.length, 0);
});

test('events without a deployment expose log extraction with five minutes of context', () => {
  const OC = { ENV_ORDER: ['MAIN'], monitorState: { selectedEnvs: ['MAIN'], availabilityWindow: '24h' }, escapeHtml: String, formatDate: String, buildAppPath: (_v,_e,opts) => '/monitoring/'+opts.tab };
  const context = { OC };
  const start = monitoring.indexOf('  function renderAvailabilityView(');
  const end = monitoring.indexOf('\n  function renderTabContent(', start);
  vm.runInNewContext(monitoring.slice(start, end) + '\nthis.render = renderAvailabilityView;', context);
  const html = context.render({ MAIN: { window: '24h', health: {}, timeline: [
    { kind: 'outage', startedAt: '2020-01-01T12:00:00Z', finishedAt: '2020-01-01T12:03:00Z', uncertain: true },
  ] } });
  assert.match(html, /data-availability-event-log/);
  assert.match(html, /data-log-env="MAIN"/);
  assert.match(html, /data-log-since="2020-01-01T11:55:00.000Z"/);
  assert.match(html, /data-log-until="2020-01-01T12:08:00.000Z"/);
  assert.match(html, /Extrair log/);
});
