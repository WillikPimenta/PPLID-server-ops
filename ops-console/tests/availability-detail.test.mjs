import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import { readFileSync } from 'node:fs';

const source = readFileSync(new URL('../public/js/availability-detail.js', import.meta.url), 'utf8');
function setup(fetchMonitoringJson, demo = false) {
  const classes = { add() {}, remove() {}, toggle() {} };
  const close = { focus() {}, hasAttribute: name => name === 'data-av-detail-close', closest: () => null };
  const body = { scrollTop: 0 };
  const panel = { innerHTML: '', querySelector: selector => selector === '.av-detail-body' ? body : close, querySelectorAll: () => [] };
  const overlay = { querySelector: () => panel, hidden: true };
  const document = { activeElement: close, body: { classList: classes }, getElementById: () => overlay,
    querySelector: selector => selector.includes('data-av-detail-close') ? close : null,
    querySelectorAll: () => [] };
  const OC = { ENV_ORDER: ['MAIN', 'DEV', 'HOM'], monitorState: { selectedEnvs: ['DEV', 'HOM'], availabilityWindow: '24h', availabilityDemo: demo },
    currentRoute: { tab: 'availability' }, escapeHtml: String, formatDate: String, fetchMonitoringJson };
  vm.runInNewContext(source, { window: { OpsConsole: OC }, document, URLSearchParams, AbortController });
  return { OC, panel, overlay };
}

test('late responses cannot replace a newer interval and previous requests are aborted', async () => {
  const pending = [];
  const { OC } = setup((url, _fallback, options) => new Promise(resolve => pending.push({ url, signal: options.signal, resolve })));
  const first = OC.openAvailabilityDetail('DEV', '2026-11-01T01:00:00Z', '2026-11-01T01:30:00Z');
  const second = OC.openAvailabilityDetail('HOM', '2026-11-01T02:00:00Z', '2026-11-01T02:30:00Z');
  assert.ok(pending[0].signal.aborted);
  pending[2].resolve({ environment: 'HOM', health: { samples: 5 } });
  pending[3].resolve({ since: '2026-11-01T02:00:00Z', until: '2026-11-01T02:30:00Z', totals: { requests: 50, errors5xx: 0 } });
  await second;
  pending[0].resolve({ environment: 'DEV' });
  pending[1].resolve({});
  await first;
  assert.equal(OC.monitorState.availabilitySelection.env, 'HOM');
  assert.equal(OC.monitorState.availabilitySelection.data.environment, 'HOM');
  assert.equal(OC.monitorState.availabilitySelection.api.periodVerified, true);
});

test('closing or changing period invalidates in-flight detail', async () => {
  const pending = [];
  const { OC, overlay } = setup((_url, _fallback, options) => new Promise(resolve => pending.push({ resolve, signal: options.signal })));
  const request = OC.openAvailabilityDetail('DEV', '2026-11-01T01:00:00Z', '2026-11-01T01:30:00Z');
  OC.monitorState.availabilityWindow = '7d';
  OC.bindAvailabilityDetails({ querySelectorAll: () => [] });
  assert.equal(OC.monitorState.availabilitySelection, null);
  assert.equal(overlay.hidden, true);
  assert.equal(pending[0].signal.aborted, true);
  pending.forEach(item => item.resolve({}));
  await request;
  assert.equal(OC.monitorState.availabilitySelection, null);
});

test('API failures do not erase local detail and bounds must be verified', async () => {
  const { OC } = setup(async url => url.includes('api-routes') ? { totals: { requests: 100 }, since: '2026-10-31T00:00:00Z', until: '2026-11-01T00:00:00Z' } : { health: { samples: 4 }, impact: { incidentCount: 1 } });
  await OC.openAvailabilityDetail('DEV', '2026-11-01T01:00:00Z', '2026-11-01T01:30:00Z');
  const selection = OC.monitorState.availabilitySelection;
  assert.equal(selection.data.health.samples, 4);
  assert.equal(selection.api.periodVerified, false);
  const html = OC.renderAvailabilityDetail(selection);
  assert.match(html, /fonte não confirmou/);
  assert.match(html, /since=2026-11-01T01%3A00%3A00Z/);
  assert.match(html, /until=2026-11-01T01%3A30%3A00Z/);
});

test('demo drawer never requests real sources and includes the selected interval only', async () => {
  let requests = 0;
  const { OC } = setup(async () => { requests++; throw new Error('unexpected source'); }, true);
  const monitoring = readFileSync(new URL('../public/js/monitoring.js', import.meta.url), 'utf8');
  const demoCode = monitoring.slice(monitoring.indexOf('  OC.buildAvailabilityDemo ='), monitoring.indexOf('\n  function renderAvailabilityView('));
  vm.runInNewContext(demoCode, { OC });
  const fixture = OC.buildAvailabilityDemo('24h').DEV;
  const bucket = fixture.history.find(item => item.failures > 0);
  await OC.openAvailabilityDetail('DEV', bucket.since, bucket.until);
  assert.equal(requests, 0);
  assert.equal(OC.monitorState.availabilitySelection.data.history.length, 1);
  assert.match(OC.renderAvailabilityDetail(OC.monitorState.availabilitySelection), /Dados simulados/);
});

test('detail uses partial coverage styling and labels sample age relative to interval end', () => {
  const { OC } = setup(async () => ({}));
  const html = OC.renderAvailabilityDetail({ env: 'MAIN', since: '2026-11-18T10:00:00Z', until: '2026-11-18T10:30:00Z',
    data: { health: { coverageMethod: 'observed-duration-v2', coveragePct: 50, intervalSec: 30, actualIntervalSec: 45, largestGapSec: 900, lastSampleAgeSec: 20 },
      history: [{ since: '2026-11-18T10:00:00Z', samples: 1, failures: 1, observedSec: 30, downSec: 30, coveragePct: 50 }] } });
  assert.match(html, /is-failed is-partial/);
  assert.match(html, /50% de cobertura/);
  assert.match(html, /no fim do intervalo/);
  assert.match(html, /45s/);
  assert.match(html, /15min 0s/);
});
