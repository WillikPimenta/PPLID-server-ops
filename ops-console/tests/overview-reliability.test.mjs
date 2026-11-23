import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

const read = name => readFileSync(new URL(`../public/js/${name}`, import.meta.url), 'utf8');

function actionContext(confirm = true) {
  const alerts = [];
  const OC = { FETCH_TIMEOUT_MS: 8000 };
  const window = { OpsConsole: OC, confirm: () => confirm, alert: text => alerts.push(text) };
  vm.runInNewContext(read('actions.js'), { window, document: {}, setTimeout });
  const button = { disabled: false, classList: { add() {}, remove() {} } };
  return { OC, window, button, alerts };
}

test('cancelled confirmation and action failure release the button', async () => {
  const { OC, window, button, alerts } = actionContext(false);
  let calls = 0;
  OC.fetchJson = async () => { calls++; throw new Error('servidor indisponível'); };
  await OC.withActionButton(button, () => OC.runRedeploy('DEV', '', (err, result) => OC.afterActionRefresh(err, result, button)));
  assert.equal(button.disabled, false);
  assert.equal(calls, 0);
  window.confirm = () => true;
  await OC.withActionButton(button, () => OC.runRedeploy('DEV', '', (err, result) => OC.afterActionRefresh(err, result, button)));
  assert.equal(button.disabled, false);
  assert.equal(calls, 1);
  assert.match(alerts[0], /servidor indisponível/);
});

test('redeploy waits for server preflight and blocks duplicate POSTs', async () => {
  const { OC } = actionContext();
  let finish;
  let calls = 0;
  OC.fetchJson = (url, options) => {
    calls++;
    assert.equal(url, '/api/v1/actions/redeploy/DEV');
    assert.equal(options.timeoutMs, 240000);
    return new Promise(resolve => { finish = resolve; });
  };
  const first = OC.postAction('/api/v1/actions/redeploy/DEV', { async: true });
  await assert.rejects(OC.postAction('/api/v1/actions/redeploy/DEV', { async: true }), /em andamento/);
  assert.equal(calls, 1);
  finish({ ok: true, accepted: true });
  assert.equal((await first).accepted, true);
});

test('accepted deployment reports asynchronous worker failure', async () => {
  let scheduled;
  const messages = [];
  const OC = {};
  const window = { OpsConsole: OC, confirm: () => true };
  vm.runInNewContext(read('actions.js'), { window, document: {}, setTimeout: callback => { scheduled = callback; return 1; }, Date });
  OC.fetchJson = async () => ({ state: 'failed', error: 'mutex ocupado' });
  OC.showToast = message => messages.push(message);
  OC.trackAcceptedDeploy('DEV', 'console-123');
  await scheduled();
  assert.match(messages[0], /mutex ocupado/);
});

test('LAN clipboard fallback selects and copies the entire text', async () => {
  let appended;
  let selected = false;
  let removed = false;
  const parent = { appendChild: value => { appended = value; } };
  const document = { body: parent, createElement: () => ({ setAttribute() {}, focus() {}, select() { selected = true; }, remove() { removed = true; } }), execCommand: () => true };
  const window = { OpsConsole: {} };
  vm.runInNewContext(read('utils.js'), { window, document, navigator: {}, setTimeout, TextDecoder, Intl });
  const copied = await window.OpsConsole.copyText('primeira linha\núltima linha');
  assert.equal(copied, true);
  assert.equal(appended.value, 'primeira linha\núltima linha');
  assert.equal(selected, true);
  assert.equal(removed, true);
});

test('failed clipboard leaves text selected for manual copy', async () => {
  let selected = false;
  let removed = false;
  const warnings = [];
  const document = { body: { appendChild() {} }, createElement: () => ({ setAttribute() {}, focus() {}, select() { selected = true; }, remove() { removed = true; } }), execCommand: () => false };
  const window = { OpsConsole: {} };
  vm.runInNewContext(read('utils.js'), { window, document, navigator: { clipboard: { writeText: async () => { throw new Error('denied'); } } }, setTimeout, TextDecoder, Intl });
  window.OpsConsole.showToast = message => warnings.push(message);
  assert.equal(await window.OpsConsole.copyText('log completo'), false);
  assert.equal(selected, true);
  assert.equal(removed, false);
  assert.match(warnings[0], /Ctrl\+C/);
});

test('rebinding overview buttons does not execute an action twice', async () => {
  const listeners = [];
  const button = { dataset: {}, disabled: false, classList: { add() {}, remove() {} }, getAttribute: name => name === 'data-card-action' ? 'redeploy' : 'DEV', addEventListener: (_name, fn) => listeners.push(fn) };
  const document = { querySelectorAll: selector => selector === '[data-card-action]' ? [button] : [] };
  let actions = 0;
  const OC = { withActionButton: (_btn, fn) => fn(), runRedeploy: async () => { actions++; } };
  vm.runInNewContext(read('render.js'), { window: { OpsConsole: OC }, document, Set });
  OC.bindEnvCardEvents({});
  OC.bindEnvCardEvents({});
  assert.equal(listeners.length, 1);
  await listeners[0]({ stopPropagation() {} });
  assert.equal(actions, 1);
});

test('host summary failure retains the last values and marks them stale', async () => {
  let calls = 0;
  const OC = { currentRoute: { view: 'monitoring' }, lastHostSummary: { cpu: { usedPct: 64 }, collector: { status: 'ok' } },
    fetchJson: async () => { calls++; throw new Error('timeout'); } };
  vm.runInNewContext(read('render.js'), { window: { OpsConsole: OC }, document: {}, Set, Date });
  await OC.refreshHomeHostSummary();
  assert.equal(OC.lastHostSummary.cpu.usedPct, 64);
  assert.equal(OC.lastHostSummary.collector.status, 'stale');
  await OC.refreshHomeHostSummary();
  assert.equal(calls, 1);
});

test('missing host measurements are not displayed as zero usage', () => {
  let items;
  const root = { innerHTML: '' };
  const document = { getElementById: id => id === 'deploy-kpi' ? root : null };
  const OC = { ENV_ORDER: ['DEV'], lastHostSummary: { cpu: { usedPct: null }, memory: { usedPct: null }, collector: { status: 'stale' } },
    renderOpsKpiRow: values => { items = values; return ''; }, refreshHomeHostSummary() {} };
  vm.runInNewContext(read('render.js'), { window: { OpsConsole: OC }, document, Set, Date });
  OC.computeHomeKpis = () => ({ healthy: 0, total: 1, deploying: 0, alertCount: 0, occurredCount: 0 });
  OC.renderDeployHomeShell({});
  assert.equal(items.find(item => item.label === 'CPU do computador').value, 'Carregando…');
  assert.equal(items.find(item => item.label === 'Memória RAM').value, 'Carregando…');
});

test('restarting overview timer does not stop deploy-log polling', () => {
  const source = read('app.js');
  const start = source.indexOf('function stopAutoRefresh()');
  const end = source.indexOf('\nfunction startDurationTicker()', start);
  let stopped = 0;
  let nextTimer = 0;
  const OC = { authState: { locked: false }, refreshPaused: false, isDeployView: () => true, stopDeployLogPolling: () => stopped++ };
  const context = { OC, document: { hidden: false }, setTimeout: () => ++nextTimer, clearTimeout() {}, getRefreshInterval: () => 5000 };
  vm.runInNewContext(source.slice(start, end) + '\nthis.start = startAutoRefresh; this.stop = stopAutoRefresh;', context);
  context.start();
  context.start();
  assert.equal(stopped, 0);
  context.stop();
  assert.equal(stopped, 1);
});

test('overview timeout keeps last dashboard visible with clear status', async () => {
  const source = read('app.js');
  const start = source.indexOf('OC.refresh = async function refresh(');
  const end = source.indexOf('\nfunction onFilterChange()', start);
  const errors = [];
  const status = { textContent: '' };
  const OC = { authState: { locked: false }, currentRoute: { view: 'deploy' }, _refreshGeneration: 0,
    lastOverview: { generatedAt: '2026-11-18T12:00:00Z' }, formatDate: () => '09:00',
    fetchJson: async () => { throw Object.assign(new Error('expirou'), { code: 'aborted', timedOut: true }); },
    setGlobalError: (message, options) => { if (message) errors.push({ message, options }); } };
  const context = { OC, document: { getElementById: () => status }, AbortController, Promise };
  vm.runInNewContext(source.slice(start, end), context);
  await OC.refresh();
  assert.match(errors[0].message, /Exibindo dados de 09:00/);
  assert.equal(errors[0].options.preserveDashboard, true);
  assert.match(status.textContent, /últimos dados/);
});

test('fast console restart is detected by the server instance identifier', async () => {
  const source = read('console-update.js');
  const start = source.indexOf('  async function waitForConsoleRestart(');
  const end = source.indexOf('\n  OC.refreshConsoleUpdateBar =', start);
  let polls = 0;
  const OC = { consoleUpdateState: {}, fetchJson: async () => ({ serverInstanceId: ++polls === 1 ? 'old' : 'new' }) };
  const context = { OC, Date, Promise, setTimeout: fn => fn(), RESTART_TIMEOUT_MS: 180000, POLL_INTERVAL_MS: 1, beginActivity() {}, renderConsoleUpdateBar() {} };
  vm.runInNewContext(source.slice(start, end) + '\nthis.restart = waitForConsoleRestart;', context);
  assert.equal(await context.restart('old'), true);
  assert.equal(polls, 2);
});

test('a transient network failure does not confirm restart of the same server instance', async () => {
  const source = read('console-update.js');
  const start = source.indexOf('  async function waitForConsoleRestart(');
  const end = source.indexOf('\n  OC.refreshConsoleUpdateBar =', start);
  let polls = 0;
  const OC = { consoleUpdateState: {}, fetchJson: async () => {
    polls++;
    if (polls === 1) throw new Error('temporary network failure');
    return { serverInstanceId: polls === 2 ? 'old' : 'new' };
  } };
  const context = { OC, Date, Promise, setTimeout: fn => fn(), RESTART_TIMEOUT_MS: 180000, POLL_INTERVAL_MS: 1, beginActivity() {}, renderConsoleUpdateBar() {} };
  vm.runInNewContext(source.slice(start, end) + '\nthis.restart = waitForConsoleRestart;', context);
  assert.equal(await context.restart('old'), true);
  assert.equal(polls, 3);
});

test('automatic version checks back off after failure without losing the last status', async () => {
  const source = read('console-update.js');
  const fetchStart = source.indexOf('  OC.fetchConsoleUpdateStatus =');
  const fetchEnd = source.indexOf('\n  async function waitForApplyResult', fetchStart);
  const refreshStart = source.indexOf('  OC.refreshConsoleUpdateBar =');
  const refreshEnd = source.indexOf('\n  OC.runConsoleUpdateCheck =', refreshStart);
  let now = 100000, calls = 0;
  class FakeDate extends Date { static now() { return now; } }
  const OC = { currentRoute: { view: 'deploy' }, consoleUpdateState: { status: { currentSha: 'abc', supported: true }, busy: false },
    fetchJson: async () => { calls++; throw new Error('rede indisponível'); } };
  const context = { OC, Date: FakeDate, Promise, STATUS_URL: '/status', SILENT_STATUS_MIN_INTERVAL_MS: 15000,
    getBarRoot: () => ({}), setBarVisible() {}, renderConsoleUpdateBar() {}, setLastError() {}, endActivity() {}, clearLastError() {}, beginActivity() {} };
  vm.runInNewContext(source.slice(fetchStart, fetchEnd) + source.slice(refreshStart, refreshEnd), context);
  await OC.refreshConsoleUpdateBar({ silent: true });
  await OC.refreshConsoleUpdateBar({ silent: true });
  assert.equal(calls, 1);
  assert.equal(OC.consoleUpdateState.status.currentSha, 'abc');
  now += 16000;
  await OC.refreshConsoleUpdateBar({ silent: true });
  assert.equal(calls, 2);
});
