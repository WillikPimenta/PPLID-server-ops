import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";

const source = readFileSync(new URL("../public/js/monitoring.js", import.meta.url), "utf8");
const OC = { escapeHtml: String, formatDate: String };
const context = {
  OC, ENV_COLORS: { MAIN: "blue" }, formatLatencyMs: String,
  formatChartAxisLabel: String,
  chartValueDomain: (values, slo) => ({ vMin: 0, vMax: Math.max(...values, slo), sloInScale: true }),
};
vm.runInNewContext(source.slice(source.indexOf("  function buildApiLatencyChart("), source.indexOf("  function renderApisOverviewSection("))
  + "\nthis.build = buildApiLatencyChart;", context);
const minute = (n, count, avg, max) => ({ at: new Date(Date.UTC(2026, 10, 18, 12, n)).toISOString(), requests: count, avgMs: avg, maxMs: max });
const series = points => ({ available: true, since: minute(0).at, until: minute(points.length).at, points, totals: { requests: 11, avgMs: 363.6 } });

test("a minute maximum above the limit is a spike even with a low average", () => {
  const built = context.build(series([minute(0, 11, 363.6, 3000)]), "MAIN", 2000);
  assert.equal(built.spikes.length, 1);
  assert.equal(built.spikes[0].v, 3000);
  assert.match(built.html, /Média por minuto/);
  assert.match(built.html, /Máximo por minuto/);
  assert.match(built.html, /Média do período: 363.6/);
  assert.doesNotMatch(built.html, /atual /);
});

test("missing minutes break paths while measured zero remains visible", () => {
  const built = context.build(series([minute(0, 1, 50, 60), minute(1, 0, null, null), minute(2, 1, 0, 0)]), "MAIN", 2000);
  const paths = [...built.html.matchAll(/<path d="([^"]+)"/g)];
  assert.equal(paths.length, 2);
  for (const path of paths) assert.equal((path[1].match(/M/g) || []).length, 2);
  const hover = OC._chartHoverData["api-MAIN"];
  assert.equal(hover.length, 3);
  assert.equal(hover[1].labels.requests, 0);
  assert.equal(hover[2].v, 0);
});

test("partial minute and empty/unavailable states clear previous hover data", () => {
  const built = context.build(series([{ ...minute(0, 1, 10, 20), partial: true }]), "MAIN", 2000);
  assert.match(built.html, /parcial/);
  assert.match(context.build(null, "MAIN", 2000).html, /indisponível/);
  assert.equal(OC._chartHoverData["api-MAIN"].length, 0);
  const empty = context.build(series([minute(0, 0, null, null)]), "MAIN", 2000);
  assert.match(empty.html, /Sem requisições/);
  assert.equal(empty.spikes.length, 0);
});

test("seven days retain minute peaks with a bounded number of SVG nodes", () => {
  const points = Array.from({ length: 10080 }, (_, i) => minute(i, 1, 50, i === 5001 ? 9000 : 100));
  const built = context.build(series(points), "MAIN", 2000);
  assert.equal(built.spikes.length, 1);
  assert.equal(built.spikes[0].v, 9000);
  assert.equal(OC._chartHoverData["api-MAIN"].length, 10080);
  assert.ok((built.html.match(/</g) || []).length < 100);
});

test("switching A to B to A cannot restore the first pending drill response", async () => {
  const requests = [], paints = [];
  const state = { apiRouteFilter: "GET /api/a/" };
  const local = {
    OC: { monitorState: state, escapeHtml: String, formatDate: String,
      fetchMonitoringJson: url => new Promise(resolve => requests.push({ url, resolve })) },
    document: { querySelector: () => ({}) }, URLSearchParams,
    paintSpikeContext: drill => paints.push(drill),
  };
  vm.runInNewContext(source.slice(source.indexOf("  async function showSpikeContext("), source.indexOf("  function restoreApiSpikeDrill("))
    + "\nthis.show = showSpikeContext;", local);
  const iso = minute(0).at;
  const first = local.show("MAIN", iso, 3000);
  state.apiRouteFilter = "GET /api/b/";
  const second = local.show("MAIN", iso, 3000);
  state.apiRouteFilter = "GET /api/a/";
  const latest = local.show("MAIN", iso, 3000);
  assert.match(requests[1].url, /method=GET&route=%2Fapi%2Fa%2F/);
  requests[0].resolve({ events: [] }); requests[1].resolve({ samples: [{ route: "old" }] });
  await first;
  assert.equal(paints.length, 0);
  requests[4].resolve({ events: [] }); requests[5].resolve({ samples: [{ route: "latest" }] });
  await latest;
  requests[2].resolve({ events: [] }); requests[3].resolve({ samples: [{ route: "B" }] });
  await second;
  assert.equal(paints.length, 1);
  assert.equal(state.apiSpikeDrill.samples[0].route, "latest");
});
