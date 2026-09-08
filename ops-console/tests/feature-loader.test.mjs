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
  assert.deepEqual(appended, [
    "/js/ops-perf.js",
    "/js/host.js",
    "/js/monitoring.js",
    "/js/monitor-drawer.js",
  ]);
});
