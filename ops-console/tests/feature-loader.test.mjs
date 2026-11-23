import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";

test("feature loader preserves dependency order and reuses shared scripts", async () => {
  const appended = [];
  const existing = new Map();
  const document = {
    querySelector(selector) {
      const match = selector.match(/script\[src="(.+)"\]/);
      return match ? existing.get(match[1]) || null : null;
    },
    createElement() {
      const listeners = {};
      return {
        dataset: {},
        addEventListener(name, callback) {
          listeners[name] = callback;
        },
        _listeners: listeners,
      };
    },
    body: {
      appendChild(script) {
        appended.push(script.src);
        existing.set(script.src, script);
        if (script.src.includes("/host.js")) window.OpsConsole.refreshHost = () => {};
        if (script.src.includes("/monitoring.js")) window.OpsConsole.refreshMonitoring = () => {};
        queueMicrotask(() => script._listeners.load());
      },
    },
  };
  const window = {};
  const source = readFileSync(new URL("../public/js/feature-loader.js", import.meta.url), "utf8");
  vm.runInNewContext(source, { window, document, Map, Promise, Error });

  await window.OpsConsole.ensureFeature("host");
  assert.deepEqual(appended, ["/js/ops-perf.js", "/js/host.js"]);
  await window.OpsConsole.ensureFeature("host");
  assert.equal(appended.length, 2);

  await window.OpsConsole.ensureFeature("monitoring");
  assert.deepEqual(appended.map(url => url.split('?')[0]), [
    "/js/ops-perf.js",
    "/js/host.js",
    "/js/monitoring.js",
    "/js/availability-detail.js",
    "/js/availability-export.js",
    "/js/monitor-drawer.js",
    "/js/congestion-fixtures.js",
    "/js/congestion.js",
  ]);

  await window.OpsConsole.ensureFeature("deployDetails");
  await window.OpsConsole.ensureFeature("deployProgress");
  for (const script of ["/js/deploy-drawer.js?v=20261118-overview", "/js/drawer.js?v=20261118-overview"]) {
    assert.equal(appended.filter(value => value === script).length, 1);
  }
});

test('a failed lazy script can be retried after the connection recovers', async () => {
  const appended = [], existing = new Map();
  let failHost = true;
  const document = {
    querySelector(selector) { return existing.get(selector.match(/script\[src="(.+)"\]/)?.[1]) || null; },
    createElement() {
      const listeners = {};
      return { dataset: {}, addEventListener: (name, callback) => { listeners[name] = callback; },
        remove() { existing.delete(this.src); }, _listeners: listeners };
    },
    body: { appendChild(script) {
      appended.push(script.src);
      existing.set(script.src, script);
      queueMicrotask(() => {
        if (script.src === '/js/host.js' && failHost) { failHost = false; script._listeners.error(); }
        else { if (script.src === '/js/host.js') window.OpsConsole.refreshHost = () => {}; script._listeners.load(); }
      });
    } },
  };
  const window = {};
  vm.runInNewContext(readFileSync(new URL('../public/js/feature-loader.js', import.meta.url), 'utf8'), { window, document, Map, Promise, Error });
  await assert.rejects(window.OpsConsole.ensureFeature('host'), /Falha ao carregar/);
  await window.OpsConsole.ensureFeature('host');
  assert.equal(appended.filter(src => src === '/js/ops-perf.js').length, 1);
  assert.equal(appended.filter(src => src === '/js/host.js').length, 2);
});
