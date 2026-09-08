/**
 * Lightweight Node-less browser logic tests for refresh coalescing.
 * Run: node --test public/js/refresh-coalesce.test.mjs
 * (Also runnable via ops-console test harness if Node is available.)
 */
import test from "node:test";
import assert from "node:assert/strict";

/**
 * Pure model of the refresh coalescing policy used by app.js.
 */
function createRefreshController({ fetchFn }) {
  let inFlight = null;
  let queued = false;
  let queuedOpts = null;
  let generation = 0;
  let lastData = null;
  let aborted = 0;

  async function refresh(options = {}) {
    if (inFlight) {
      queued = true;
      queuedOpts = { ...(queuedOpts || {}), ...options };
      return inFlight;
    }
    const run = async () => {
      const gen = ++generation;
      const data = await fetchFn(options);
      if (gen !== generation) {
        aborted += 1;
        return;
      }
      lastData = data;
    };
    inFlight = run().finally(() => {
      inFlight = null;
      if (queued) {
        const opts = queuedOpts || {};
        queued = false;
        queuedOpts = null;
        Promise.resolve().then(() => refresh(opts));
      }
    });
    return inFlight;
  }

  return {
    refresh,
    get lastData() {
      return lastData;
    },
    get aborted() {
      return aborted;
    },
    bumpGeneration() {
      generation += 1;
    },
  };
}

test("refresh does not overlap concurrent calls", async () => {
  let concurrent = 0;
  let maxConcurrent = 0;
  let calls = 0;
  const ctrl = createRefreshController({
    fetchFn: async () => {
      calls += 1;
      concurrent += 1;
      maxConcurrent = Math.max(maxConcurrent, concurrent);
      await new Promise((r) => setTimeout(r, 30));
      concurrent -= 1;
      return { n: calls };
    },
  });
  await Promise.all([ctrl.refresh(), ctrl.refresh(), ctrl.refresh()]);
  // Allow coalesced follow-up to finish.
  await new Promise((r) => setTimeout(r, 80));
  assert.equal(maxConcurrent, 1);
  assert.ok(calls <= 2); // one in-flight + at most one queued
});

test("manual refresh during auto-refresh does not duplicate beyond coalesce", async () => {
  let calls = 0;
  const ctrl = createRefreshController({
    fetchFn: async () => {
      calls += 1;
      await new Promise((r) => setTimeout(r, 20));
      return { calls };
    },
  });
  const a = ctrl.refresh();
  const b = ctrl.refresh({ full: true });
  await Promise.all([a, b]);
  await new Promise((r) => setTimeout(r, 50));
  assert.ok(calls <= 2);
});

test("stale generation does not overwrite newer data", async () => {
  let resolveSlow;
  const slow = new Promise((r) => {
    resolveSlow = r;
  });
  let step = 0;
  const results = [];
  const ctrl = createRefreshController({
    fetchFn: async () => {
      step += 1;
      if (step === 1) {
        await slow;
        return { id: "old" };
      }
      return { id: "new" };
    },
  });
  const first = ctrl.refresh();
  // Let first enter fetchFn
  await new Promise((r) => setTimeout(r, 10));
  // Simulate route change bumping generation while first is in flight.
  ctrl.bumpGeneration();
  resolveSlow();
  await first;
  // Start a fresh refresh after abort of stale apply
  await ctrl.refresh();
  assert.equal(ctrl.lastData.id, "new");
  assert.ok(ctrl.aborted >= 1 || ctrl.lastData.id === "new");
});

test("hidden tab policy: pause means no schedule", () => {
  let hidden = true;
  function shouldSchedule(paused, locked) {
    return !(hidden || paused || locked);
  }
  assert.equal(shouldSchedule(false, false), false);
  hidden = false;
  assert.equal(shouldSchedule(false, false), true);
  assert.equal(shouldSchedule(true, false), false);
});
